"""Personal value score (spec §7):  Score = max(P, I) × A × F,  P = 0.5E + 0.3C + 0.2M.
Only the user's model and the deal's own value feed the score. Revenue (affiliate, lead gen, sponsorship)
must never be an input here (spec §12: trust first, monetization second)."""
import math
from dataclasses import dataclass, field
from datetime import timedelta

from . import db, deals, entities, i18n, profile
from .config import DISLIKE_CUTOFF

# Dollar amount at which a deal "feels" substantial, by spending context.
VALUE_REF = {"delivery": 10, "cafe": 10, "grocery": 15, "dining": 15}
DEFAULT_VALUE_REF = 50
DEFAULT_FREE_DELIVERY_VALUE = 7


@dataclass
class Scored:
    deal: dict
    score: float
    P: float
    I: float
    A: float
    F: float
    E: float
    C: float
    M: float
    reasons: list[str] = field(default_factory=list)
    matched_intent: str | None = None
    lang: str = i18n.ZH

    def breakdown(self) -> dict:
        return {k: round(getattr(self, k), 3) for k in ("score", "P", "I", "A", "F", "E", "C", "M")}


def _norm_entity(raw: float) -> float:
    return (raw + 1) / 2


def value_strength(deal: dict, threshold: float) -> float:
    mech = deal.get("mechanic")
    pct = deal.get("percent_off")
    if mech == "bogo" and not pct:
        pct = 0.5
    if mech == "sweepstakes":
        return 0.3
    if pct:
        return 1 - math.exp(-1.6 * pct / max(threshold, 0.05))
    value = deal.get("est_value") or deal.get("face_value")
    if not value and mech == "free_delivery":
        value = DEFAULT_FREE_DELIVERY_VALUE
    if value:
        ref = VALUE_REF.get(deal.get("category"), DEFAULT_VALUE_REF)
        return 1 - math.exp(-value / ref)
    return 0.4


def attractiveness(deal: dict, threshold: float) -> float:
    ease = max(0.6, 1 - 0.05 * len(deal.get("conditions") or []))
    conf = deal.get("confidence") or 0.7
    if deal.get("status") == "unverified":
        conf *= 0.7
    return value_strength(deal, threshold) * ease * conf


def _deal_text(deal: dict) -> str:
    names = [entities.display_name(e) for e in deal.get("entity_ids") or []]
    return " ".join([deal.get("title") or "", deal.get("description") or "", *names]).lower()


def intent_match(intent: dict, deal: dict) -> float:
    meta = intent["meta"]
    if set(meta.get("entity_ids") or []) & set(deal.get("entity_ids") or []):
        return 1.0
    text = _deal_text(deal)
    if any(k and k in text for k in meta.get("keywords") or []):
        return 1.0
    dest = meta.get("destination")
    if dest and any(entities.metro_of(l) == dest for l in deal.get("locations") or []):
        return 0.8
    if deal.get("category") in (meta.get("categories") or []):
        return 0.7
    return 0.0


def _recently_recommended(user_id: str, deal_id: int, days: int = 14) -> bool:
    since = db.iso(db.now() - timedelta(days=days))
    r = db.conn().execute(
        "SELECT 1 FROM recommendations WHERE user_id=? AND deal_id=? AND created_at>=? AND channel!='chat' LIMIT 1",
        (user_id, deal_id, since),
    ).fetchone()
    return r is not None


PLACE_EN = {"toronto": "Toronto", "vancouver": "Vancouver", "montreal": "Montreal", "calgary": "Calgary",
            "new_york": "New York", "seattle": "Seattle", "bay_area": "Bay Area", "los_angeles": "Los Angeles",
            "taipei": "Taipei", "tokyo": "Tokyo", "osaka": "Osaka"}
PLACE_ZH = {"toronto": "多倫多", "vancouver": "溫哥華", "montreal": "蒙特婁", "calgary": "卡加利", "new_york": "紐約",
            "seattle": "西雅圖", "bay_area": "灣區", "los_angeles": "洛杉磯", "taipei": "台北", "tokyo": "東京", "osaka": "大阪"}


def _place(metro: str, lang: str) -> str:
    return (PLACE_ZH if lang == i18n.ZH else PLACE_EN).get(metro, metro)


def score_deal(user_id: str, deal: dict, *, intents: list[dict] | None = None) -> Scored:
    user = db.get_user(user_id) or {}
    threshold = user.get("discount_threshold") or 0.2
    lang = i18n.norm_lang(user.get("language"))
    intents = profile.active_intents(user_id) if intents is None else intents
    reasons: list[str] = []

    raw_entities = {e: profile.entity_affinity(user_id, e) for e in deal.get("entity_ids") or []}
    E = max((_norm_entity(v) for v in raw_entities.values()), default=_norm_entity(0.0))
    C = profile.context_affinity(user_id, deal.get("category"))
    M = profile.mechanic_affinity(user_id, deal.get("mechanic"))
    P = 0.5 * E + 0.3 * C + 0.2 * M

    I, matched = 0.0, None
    for it in intents:
        m = it["score"] * intent_match(it, deal)
        if m > I:
            I, matched = m, it
    A = attractiveness(deal, threshold)

    F = 1.0
    disliked = [e for e, v in raw_entities.items() if v < DISLIKE_CUTOFF]
    if disliked:
        F *= 0.1
        reasons.append(i18n.t("r_dislike", lang, name=entities.display_name(disliked[0])))
    left = deals.days_left(deal)
    if left is not None and 0 <= left <= 7 and (P >= 0.5 or I > 0):
        F *= 1.2
    if _recently_recommended(user_id, deal["id"]):
        F *= 0.5

    if matched:
        dest = matched["meta"].get("destination")
        label = matched["meta"].get("label") or matched["target"]
        trip = dest and intent_match(matched, deal) == 0.8
        reasons.insert(0, i18n.t("r_trip", lang, dest=_place(dest, lang)) if trip else i18n.t("r_intent", lang, label=label))
    for e, v in raw_entities.items():
        if v >= 0.5:
            reasons.append(i18n.t("r_like", lang, name=entities.display_name(e)))
            break
    if C >= 0.6:
        reasons.append(i18n.t("r_context", lang, cat=i18n.category(deal.get("category"), lang)))
    if M >= 0.7:
        reasons.append(i18n.t("r_mechanic", lang, mech=i18n.mechanic(deal.get("mechanic"), lang)))
    if left is not None and 0 <= left <= 7:
        reasons.append(i18n.t("r_today", lang) if left == 0 else i18n.t("r_days", lang, n=left))

    score = min(1.0, max(P, I) * A * F)
    return Scored(deal, score, P, I, A, F, E, C, M, reasons, matched["target"] if matched else None, lang)


def rank(user_id: str, *, category: str | None = None, merchant: str | None = None,
         keywords: list[str] | None = None, limit: int = 5, respect_location: bool = True) -> list[Scored]:
    metros = profile.user_metros(user_id) if respect_location else None
    intents = profile.active_intents(user_id)
    scored = [
        score_deal(user_id, d, intents=intents)
        for d in deals.candidates(user_id, category=category, merchant=merchant, keywords=keywords, metros=metros)
    ]
    scored.sort(key=lambda s: s.score, reverse=True)
    return scored[:limit]


def log_recommendations(user_id: str, items: list[Scored], channel: str) -> None:
    import json

    c = db.conn()
    for s in items:
        c.execute(
            "INSERT INTO recommendations (user_id, deal_id, score, breakdown, reasons, channel, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, s.deal["id"], s.score, json.dumps(s.breakdown()), json.dumps(s.reasons, ensure_ascii=False),
             channel, db.iso()),
        )
    c.commit()


def card(s: Scored) -> dict:
    """What a client renders for one recommendation (spec §8: why, value, deadline, conditions, source).
    Non-Chinese users get the English title and summary produced at extraction time."""
    d, lang = s.deal, s.lang
    en = lang == i18n.EN
    value = None
    if d.get("percent_off"):
        value = f"{round(d['percent_off'] * 100)}% off"
    elif d.get("face_value"):
        value = f"${d['face_value']:g}"
    elif d.get("est_value"):
        value = f"~${d['est_value']:g}" if en else f"約值 ${d['est_value']:g}"
    return {
        "deal_id": d["id"],
        "title": (d.get("title_en") or d["title"]) if en else d["title"],
        "summary": (d.get("description_en") if en else d.get("description")) or "",
        "original_title": d["title"],
        "merchant": d.get("merchant_name"),
        "category": d.get("category"),
        "mechanic": i18n.mechanic(d.get("mechanic"), lang),
        "value": value,
        "valid_until": d.get("valid_until"),
        "days_left": deals.days_left(d),
        "conditions": (d.get("conditions_en") or d.get("conditions") or []) if en else (d.get("conditions") or []),
        "locations": d.get("locations") or [],
        "url": d.get("url"),
        "uncertain": d.get("status") == "unverified" or (d.get("confidence") or 0) < 0.6 or bool(d.get("uncertain_fields")),
        "reasons": s.reasons,
        "score": round(s.score, 2),
        "breakdown": s.breakdown(),
        "lang": lang,
    }

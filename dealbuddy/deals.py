"""Deal pool: save with dedup, expiry, and candidate lookup (spec §5 處理規則)."""
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import db, entities
from .config import UNVERIFIED_AFTER_DAYS, settings

JSON_FIELDS = ("entity_ids", "conditions", "conditions_en", "locations", "uncertain_fields")


def today() -> date:
    return datetime.now(ZoneInfo(settings.timezone)).date()


def _d(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def get(deal_id: int) -> dict | None:
    d = db.row_to_dict(db.conn().execute("SELECT * FROM deals WHERE id=?", (deal_id,)).fetchone(), JSON_FIELDS)
    if d:
        d["merchant_name"] = entities.display_name(d["merchant_id"])
        d["sources"] = [
            dict(r) for r in db.conn().execute(
                "SELECT source_type, url, created_at FROM deal_sources WHERE deal_id=?", (deal_id,)
            ).fetchall()
        ]
    return d


def _overlaps(a_from, a_until, b_from, b_until) -> bool:
    lo = max(_d(a_from) or date.min, _d(b_from) or date.min)
    hi = min(_d(a_until) or date.max, _d(b_until) or date.max)
    return lo <= hi


def find_duplicate(merchant_id: str | None, mechanic: str, valid_from, valid_until, url: str | None = None) -> dict | None:
    if url:
        r = db.conn().execute(
            "SELECT deal_id FROM deal_sources WHERE url=? LIMIT 1", (url,)
        ).fetchone()
        if r:
            return get(r["deal_id"])
    if not merchant_id:
        return None
    rows = db.conn().execute(
        "SELECT id, valid_from, valid_until FROM deals WHERE merchant_id=? AND mechanic=? AND status!='expired'",
        (merchant_id, mechanic),
    ).fetchall()
    for r in rows:
        if _overlaps(r["valid_from"], r["valid_until"], valid_from, valid_until):
            return get(r["id"])
    return None


def save(deal: dict, *, source_type: str, url: str | None = None, raw: str | None = None,
         shared_by: str | None = None) -> tuple[int, bool]:
    """Store an extracted deal. Returns (deal_id, merged_into_existing)."""
    merchant_id = entities.resolve(deal.get("merchant") or "", "merchant") if deal.get("merchant") else None
    entity_ids = [merchant_id] if merchant_id else []
    for name in deal.get("entities") or []:
        eid = entities.resolve(name, "brand")
        if eid and eid not in entity_ids:
            entity_ids.append(eid)
    mechanic = deal.get("mechanic") or "other"
    url = url or deal.get("url")
    c = db.conn()
    dup = find_duplicate(merchant_id, mechanic, deal.get("valid_from"), deal.get("valid_until"), url)
    if dup:
        deal_id = dup["id"]
        # A second source is extra evidence the deal is real (spec §5 rule 1).
        conf = min(0.99, max(dup["confidence"] or 0, deal.get("confidence") or 0) + 0.05)
        fills = {}
        for f in ("valid_until", "valid_from", "est_value", "face_value", "percent_off", "url", "description",
                  "title_en", "description_en"):
            if not dup.get(f) and deal.get(f):
                fills[f] = deal[f]
        merged_entities = list(dict.fromkeys([*(dup["entity_ids"] or []), *entity_ids]))
        sets = ", ".join(f"{k}=?" for k in fills)
        c.execute(
            f"UPDATE deals SET confidence=?, entity_ids=?, updated_at=?, status='active'{', ' + sets if sets else ''} WHERE id=?",
            (conf, json.dumps(merged_entities), db.iso(), *fills.values(), deal_id),
        )
        merged = True
    else:
        cur = c.execute(
            """INSERT INTO deals (title, description, title_en, description_en, conditions_en, merchant_id, entity_ids, category, mechanic, percent_off,
                 face_value, est_value, conditions, locations, valid_from, valid_until, url, confidence,
                 uncertain_fields, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?) RETURNING id""",
            (
                deal.get("title") or "", deal.get("description") or "", deal.get("title_en"),
                deal.get("description_en"), json.dumps(deal.get("conditions_en") or [], ensure_ascii=False), merchant_id, json.dumps(entity_ids),
                deal.get("category") or "other", mechanic, deal.get("percent_off"), deal.get("face_value"),
                deal.get("est_value"), json.dumps(deal.get("conditions") or [], ensure_ascii=False),
                json.dumps(deal.get("locations") or [], ensure_ascii=False), deal.get("valid_from"),
                deal.get("valid_until"), url, deal.get("confidence", 0.7),
                json.dumps(deal.get("uncertain_fields") or []), db.iso(), db.iso(),
            ),
        )
        deal_id = cur.fetchone()["id"]
        merged = False
    c.execute(
        "INSERT INTO deal_sources (deal_id, source_type, url, raw_content, shared_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (deal_id, source_type, url, (raw or "")[:5000], shared_by, db.iso()),
    )
    c.commit()
    refresh_statuses()
    return deal_id, merged


def refresh_statuses() -> None:
    """Expire past-date deals; flag undated deals older than 14 days for re-verification (spec §5 rule 2)."""
    c = db.conn()
    t = today().isoformat()
    c.execute("UPDATE deals SET status='expired' WHERE valid_until IS NOT NULL AND valid_until < ? AND status!='expired'", (t,))
    cutoff = db.iso(db.now() - timedelta(days=UNVERIFIED_AFTER_DAYS))
    c.execute(
        "UPDATE deals SET status='unverified' WHERE valid_until IS NULL AND status='active' AND updated_at < ?",
        (cutoff,),
    )
    c.commit()


def location_ok(deal: dict, metros: set[str]) -> bool:
    locs = deal.get("locations") or []
    if not locs or not metros:
        return True
    for loc in locs:
        if entities.is_online(loc) or entities.metro_of(loc) in metros:
            return True
    return False


def candidates(user_id: str, *, category: str | None = None, merchant: str | None = None,
               keywords: list[str] | None = None, metros: set[str] | None = None) -> list[dict]:
    refresh_statuses()
    # Sponsored placements never enter organic ranking (spec §12: revenue stays out of the formula).
    q = "SELECT id FROM deals WHERE status IN ('active', 'unverified') AND COALESCE(sponsored, 0)=0"
    args: list = []
    if category:
        q += " AND category=?"
        args.append(category)
    ids = [r["id"] for r in db.conn().execute(q, args).fetchall()]
    merchant_id = entities.lookup(merchant) if merchant else None
    kws = [k.lower() for k in (keywords or []) if k and k.strip()]
    hidden = {
        r["deal_id"] for r in db.conn().execute(
            "SELECT deal_id FROM feedback_events WHERE user_id=? AND signal IN ('not_interested', 'used')", (user_id,)
        ).fetchall()
    }
    out = []
    for i in ids:
        if i in hidden:
            continue
        d = get(i)
        if metros is not None and not location_ok(d, metros):
            continue
        if merchant and merchant_id not in (d["entity_ids"] or []) and (merchant.lower() not in (d["merchant_name"] or "").lower()):
            continue
        if kws:
            hay = " ".join([d["title"] or "", d["description"] or "", d["merchant_name"] or "", d["category"] or "",
                            *[entities.display_name(e) for e in d["entity_ids"] or []]]).lower()
            if not any(k in hay for k in kws):
                continue
        out.append(d)
    return out


def days_left(deal: dict) -> int | None:
    until = _d(deal.get("valid_until"))
    return (until - today()).days if until else None

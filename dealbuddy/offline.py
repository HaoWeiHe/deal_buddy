"""Rule-based stand-in for the Claude agent, used when no ANTHROPIC_API_KEY is set.
It understands only simple phrasings, but exercises the whole pipeline (profile → ranking → cards → feedback)."""
import re

from . import db, entities, i18n, profile, ranking, tools
from .lexicon import CATEGORY_KEYWORDS, DISLIKE_WORDS, DONE_WORDS, INTENT_LABEL_EN, INTENT_TEMPLATES, LIKE_WORDS

ASK_WORDS = ["有什麼", "有沒有", "有啥", "推薦", "優惠", "优惠", "折扣", "羊毛", "好康", "deal", "特價", "特价", "?", "？", "嗎", "吗",
             "any ", "discount", "promo", "coupon", "sale"]


def _category_of(text: str) -> str | None:
    low = text.lower()
    best, hits = None, 0
    for cat, words in CATEGORY_KEYWORDS.items():
        n = sum(1 for w in words if w in low)
        if n > hits:
            best, hits = cat, n
    return best


def _after(text: str, words: list[str]) -> str | None:
    for w in words:
        i = text.lower().find(w)
        if i >= 0:
            rest = re.split(r"[，,。.!！?？;；\n]|但|可是|然後|然后|也|還有|还有", text[i + len(w):])[0]
            return rest.strip(" 的吃去用家") or None
    return None


def respond(user_id: str, text: str, extra_note: str | None = None) -> dict:
    ctx = tools.ToolContext(user_id)
    lang = (db.get_user(user_id) or {}).get("language")
    said: list[str] = []
    t = text.strip()

    if profile.is_new_user(user_id) and len(t) <= 12 and not any(w in t.lower() for w in ASK_WORDS):
        return {"reply": i18n.t("onboarding", lang), "shown": []}

    # City
    m = (re.search(r"(?:我住|住在|我在|人在)\s*([^\s，,。]{2,12}?)(?:[，,。\s]|$)", t)
         or re.search(r"(?:i live in|i'm in|i am in|based in)\s+([A-Za-z .]{2,20}?)(?:[,.!]|\s+and\b|$)", t, re.I))
    if m and entities.metros_in_text(m.group(1)):
        db.update_user(user_id, city=m.group(1).strip())
        said.append(i18n.t("said_city", lang, city=m.group(1).strip()))

    # Dislikes and likes (explicit statements)
    ents = [e for e in entities.find_in_text(t) if (entities.get(e) or {}).get("type") != "place"]
    if any(w in t.lower() for w in DISLIKE_WORDS):
        target = ents[0] if ents else _after(t, DISLIKE_WORDS)
        if target:
            name = entities.display_name(target) if target in ents else target
            tools.update_profile(user_id, {"action": "dislike", "target": name})
            said.append(i18n.t("said_dislike", lang, name=name))
    elif any(w in t.lower() for w in LIKE_WORDS):
        target = ents[0] if ents else _after(t, LIKE_WORDS)
        if target:
            name = entities.display_name(target) if target in ents else target
            tools.update_profile(user_id, {"action": "like", "target": name, "strength": 0.8})
            said.append(i18n.t("said_like", lang, name=name))
        cat = _category_of(t)
        if cat:
            profile.set_belief(user_id, "context", cat, 0.8, source="explicit", note=t[:40])

    # Intents: finished ones first, then new ones
    done = any(w in t.lower() for w in DONE_WORDS)
    for triggers, iid, label, cats, kws, sensitive in INTENT_TEMPLATES:
        if i18n.norm_lang(lang) == i18n.EN:
            label = INTENT_LABEL_EN.get(iid, label)
        if any(w in t.lower() for w in triggers):
            if done:
                if profile.close_intent(user_id, iid):
                    said.append(i18n.t("said_intent_done", lang, label=label))
            elif not profile.get_belief(user_id, "intent", iid) or profile.get_belief(user_id, "intent", iid)["status"] != "active":
                tools.update_profile(user_id, {"action": "add_intent", "target": iid, "label": label, "categories": cats,
                                               "keywords": kws, "sensitive": sensitive})
                said.append(i18n.t("said_intent", lang, label=label))
    trip = (re.search(r"(?:去|到)\s*([^\s，,。]{2,8}?)\s*(?:玩|旅遊|旅游|旅行|出差|度假)", t)
            or re.search(r"(?:trip to|going to|visiting|travel(?:l?ing)? to)\s+([A-Za-z .]{2,20}?)(?:[,.!]|\s+(?:next|this|in|on)\b|$)", t, re.I))
    if trip and not done:
        metros = entities.metros_in_text(trip.group(1))
        if metros:
            dest = metros[0]
            place = trip.group(1).strip()
            label = f"去{place}" if i18n.norm_lang(lang) == i18n.ZH else f"trip to {place}"
            tools.update_profile(user_id, {"action": "add_intent", "target": f"trip_{dest}", "label": label,
                                           "categories": ["travel", "dining"], "keywords": [], "destination": dest})
            said.append(i18n.t("said_trip", lang, dest=place))

    if said and profile.is_new_user(user_id) is False:
        db.update_user(user_id, onboarded=1)

    # Answer deal questions
    asking = any(w in t.lower() for w in ASK_WORDS) or (not said and not extra_note)
    cards: list[ranking.Scored] = []
    if asking:
        cat = _category_of(t)
        merchant = entities.display_name(ents[0]) if ents and not any(w in t.lower() for w in DISLIKE_WORDS) else None
        cards = ranking.rank(user_id, category=cat, merchant=merchant, limit=5)
        if not cards and (cat or merchant):
            cards = ranking.rank(user_id, limit=5)
        cards = [c for c in cards if c.score >= 0.1 and c.F > 0.1][:5]
        ctx.shown = [c.deal["id"] for c in cards]

    parts = []
    if said:
        s = i18n.t("list_sep", lang).join(said)
        parts.append(s[:1].upper() + s[1:] + i18n.t("period", lang))
    if asking:
        if not cards:
            parts.append(i18n.t("none_found", lang))
        else:
            parts.append(i18n.t("picked", lang))
            for c in cards:
                card = ranking.card(c)
                why = i18n.t("sep", lang).join(c.reasons[:2]) or i18n.t("r_default", lang)
                warn = i18n.t("uncertain_short", lang) if card["uncertain"] else ""
                parts.append(f"• {card['title']}: {why}{warn}" if i18n.norm_lang(lang) == i18n.EN else f"• {card['title']}：{why}{warn}")
    if extra_note:
        parts.insert(0, extra_note)
    return {"reply": "\n".join(parts) or i18n.t("ok", lang), "shown": ctx.shown}

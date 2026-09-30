"""One chat turn: store the message, ingest anything shared, let the friend answer, return reply + cards."""
import logging

from . import agent, db, deals, extract, i18n, offline, profile, ranking
from .config import settings

log = logging.getLogger(__name__)


def _share_signal(user_id: str, saved: list[dict]) -> None:
    """Sharing a deal is itself a strong interest signal (spec §4)."""
    for s in saved:
        d = deals.get(s["deal_id"])
        if not d:
            continue
        if d.get("merchant_id"):
            profile.set_belief(user_id, "entity", d["merchant_id"], delta=0.15, source="share", note=f"分享：{d['title'][:30]}")
        if d.get("category"):
            profile.set_belief(user_id, "context", d["category"], delta=0.1, source="share", note=f"分享：{d['title'][:30]}")


def _describe_share(result: dict) -> str:
    if not result["saved"]:
        if result["needs_screenshot"]:
            return "使用者分享了連結，但內容讀不到（小紅書/IG/FB 需要登入）。請他直接截圖給你。"
        return "使用者分享了內容，但沒有抽到具體優惠。"
    lines = ["使用者分享的內容已存進優惠庫："]
    for s in result["saved"]:
        lines.append(f"- deal_id {s['deal_id']}：{s['title']}（{'和既有優惠合併' if s['merged'] else '新優惠'}，到期 {s.get('valid_until') or '未知'}）")
    lines.append("跟他說你記下了、什麼時候提醒他；如果跟他的興趣或最近要做的事對得上就說出來。")
    return "\n".join(lines)


def handle(user_id: str, text: str = "", *, image_b64: str | None = None, image_type: str = "image/jpeg",
           lang_hint: str | None = None) -> dict:
    """lang_hint: the client's UI language (browser / Telegram app), used for a user's first message."""
    user = db.ensure_user(user_id)
    text = (text or "").strip()
    if lang_hint and not db.recent_messages(user_id, 1):
        db.update_user(user_id, language=i18n.norm_lang(lang_hint) if lang_hint.lower()[:2] in ("zh", "en") else lang_hint)
        user = db.get_user(user_id)
    detected = i18n.detect(text)
    current = (user.get("language") or "").lower()
    if detected and current[:2] in ("", "zh", "en") and not current.startswith(detected[:2]):
        # Switch between Chinese and English by what the user writes; other languages are set by the agent.
        db.update_user(user_id, language=detected)
        user = db.get_user(user_id)
    lang = user.get("language")
    db.add_message(user_id, "user", text or "[image]")

    note = None
    if image_b64 or extract.find_urls(text):
        result = extract.ingest(text, user_id=user_id, image_b64=image_b64, image_type=image_type, city=user.get("city"))
        _share_signal(user_id, result["saved"])
        note = _describe_share(result)
        if not settings.llm_enabled:
            note = (
                i18n.t("saved_n", lang, n=len(result["saved"])) if result["saved"]
                else i18n.t("need_screenshot" if result["needs_screenshot"] else "no_deal_in_share", lang)
            )

    if settings.llm_enabled:
        try:
            out = agent.respond(user_id, text or "(shared an image)", extra_note=note)
        except Exception:
            log.exception("agent failed; falling back to offline mode")
            out = offline.respond(user_id, text, extra_note=None)
    else:
        out = offline.respond(user_id, text, extra_note=note) if text else {"reply": note or i18n.t("ok", lang), "shown": []}

    shown = []
    for deal_id in out["shown"]:
        d = deals.get(deal_id)
        if d:
            shown.append(ranking.score_deal(user_id, d))
    ranking.log_recommendations(user_id, shown, channel="chat")
    db.add_message(user_id, "assistant", out["reply"])
    return {"reply": out["reply"], "cards": [ranking.card(s) for s in shown], "lang": i18n.norm_lang(lang)}

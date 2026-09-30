"""Telegram bot (spec §10: first interface). Long polling over the Bot API with httpx, no extra dependency.
Run:  TELEGRAM_BOT_TOKEN=... python -m dealbuddy.telegram_bot"""
import base64
import logging
import time

import httpx

from . import chat, db, deals, digest, feedback, i18n, metrics, profile
from .config import settings

log = logging.getLogger(__name__)

REASONS = ["merchant", "category", "too_far", "not_enough", "none"]


def _lang(uid: str) -> str:
    return i18n.norm_lang((db.get_user(uid) or {}).get("language"))


class Bot:
    def __init__(self, token: str):
        self.api = httpx.Client(base_url=f"https://api.telegram.org/bot{token}/", timeout=70)
        self.file_base = f"https://api.telegram.org/file/bot{token}/"
        self.offset = 0

    def call(self, method: str, **params):
        r = self.api.post(method, json=params)
        data = r.json()
        if not data.get("ok"):
            log.warning("telegram %s failed: %s", method, data)
        return data.get("result")

    # ---- sending ------------------------------------------------------------

    def send_text(self, chat_id, text: str, markup: dict | None = None):
        params = {"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True}
        if markup:
            params["reply_markup"] = markup
        return self.call("sendMessage", **params)

    def send_card(self, chat_id, card: dict):
        lines = [f"🏷 {card['title']}"]
        meta = " · ".join(x for x in [card.get("merchant"), card.get("mechanic"), card.get("value")] if x)
        if meta:
            lines.append(meta)
        lang = card.get("lang")
        if card.get("summary") and card["summary"] != card["title"]:
            lines.append(card["summary"])
        if card.get("valid_until"):
            left = i18n.t("days_left", lang, n=card["days_left"]) if card.get("days_left") is not None else ""
            lines.append(i18n.t("until", lang, date=card["valid_until"]) + left)
        if card["reasons"]:
            lines.append(i18n.t("why", lang) + i18n.t("sep", lang).join(card["reasons"]))
        if card["conditions"]:
            lines.append(i18n.t("conditions", lang) + "; ".join(card["conditions"]))
        if card.get("uncertain"):
            lines.append(i18n.t("uncertain", lang))
        if card.get("url"):
            lines.append(card["url"])
        d = card["deal_id"]
        markup = {"inline_keyboard": [[
            {"text": i18n.t(f"btn_{sig}", lang), "callback_data": f"fb:{d}:{sig}"}
            for sig in ("useful", "saved", "used", "not_interested")
        ]]}
        self.send_text(chat_id, "\n".join(lines), markup)

    def send_result(self, chat_id, result: dict):
        self.send_text(chat_id, result["reply"])
        for c in result["cards"]:
            self.send_card(chat_id, c)

    # ---- receiving ----------------------------------------------------------

    def _image(self, msg: dict) -> tuple[str, str] | None:
        photo = (msg.get("photo") or [None])[-1]
        doc = msg.get("document") if (msg.get("document") or {}).get("mime_type", "").startswith("image/") else None
        file_id = (photo or doc or {}).get("file_id")
        if not file_id:
            return None
        info = self.call("getFile", file_id=file_id)
        if not info:
            return None
        raw = httpx.get(self.file_base + info["file_path"], timeout=30).content
        mime = (doc or {}).get("mime_type") or "image/jpeg"
        return base64.b64encode(raw).decode(), mime

    def on_message(self, msg: dict):
        chat_id = msg["chat"]["id"]
        uid = f"tg:{msg['from']['id']}"
        db.ensure_user(uid, telegram_chat_id=str(chat_id))
        text = msg.get("text") or msg.get("caption") or ""
        if text.startswith("/start"):
            lang = (msg["from"].get("language_code") or "")
            if lang and not lang.startswith("zh") and not (db.get_user(uid) or {}).get("onboarded"):
                db.update_user(uid, language=lang)  # Telegram tells us the app language on first contact
            text = "嗨" if i18n.norm_lang(lang or "zh") == i18n.ZH else "hi"
        elif text.startswith("/me"):
            p, lang = profile.summary(uid), _lang(uid)
            sep = i18n.t("list_sep", lang)
            self.send_text(chat_id, i18n.t(
                "me_summary", lang, city=p["city"] or i18n.t("unknown", lang),
                likes=sep.join(x["name"] for x in p["likes"]) or "—",
                dislikes=sep.join(x["name"] for x in p["dislikes"]) or "—",
                intents=sep.join(x["label"] for x in p["intents"]) or "—",
            ) + i18n.t("saved_month", lang, amount=(m := metrics.savings(uid))["estimated_saved"], n=m["deals_used"]))
            return
        elif text.startswith("/today"):
            d = digest.build(uid)
            if d:
                self.send_text(chat_id, d["text"].split("\n")[0])
                for c in d["cards"]:
                    self.send_card(chat_id, c)
            else:
                self.send_text(chat_id, i18n.t("no_digest", _lang(uid)))
            return
        elif text.startswith("/forgetme"):
            lang = _lang(uid)
            db.delete_user(uid)
            self.send_text(chat_id, i18n.t("forgot", lang))
            return
        img = self._image(msg)
        self.call("sendChatAction", chat_id=chat_id, action="typing")
        result = chat.handle(uid, text, image_b64=img[0] if img else None, image_type=img[1] if img else "image/jpeg",
                             lang_hint=msg["from"].get("language_code"))
        self.send_result(chat_id, result)

    def on_callback(self, cb: dict):
        uid = f"tg:{cb['from']['id']}"
        parts = cb.get("data", "").split(":")
        msg = cb.get("message") or {}
        chat_id, message_id = msg.get("chat", {}).get("id"), msg.get("message_id")
        if len(parts) >= 3 and parts[0] == "fb":
            deal_id, signal = int(parts[1]), parts[2]
            lang = _lang(uid)
            if signal == "not_interested" and len(parts) == 3:
                kb = {"inline_keyboard": [[{"text": i18n.t(f"reason_{code}", lang), "callback_data": f"fb:{deal_id}:not_interested:{code}"}] for code in REASONS]}
                self.call("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id, reply_markup=kb)
                self.call("answerCallbackQuery", callback_query_id=cb["id"], text=i18n.t("ask_reason", lang))
                return
            reason = parts[3] if len(parts) > 3 and parts[3] != "none" else None
            if deals.get(deal_id):
                feedback.record(uid, deal_id, signal, reason)
            self.call("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id,
                      reply_markup={"inline_keyboard": [[{"text": i18n.t("btn_done", lang), "callback_data": "noop"}]]})
            self.call("answerCallbackQuery", callback_query_id=cb["id"], text=i18n.t("noted", lang))
        else:
            self.call("answerCallbackQuery", callback_query_id=cb["id"])

    # ---- proactive push ---------------------------------------------------------

    def push_due(self, *, ignore_hour: bool = False, instant: bool = True) -> int:
        """Send due daily digests (and instant pushes). Returns how many users got a message."""
        sent = 0
        now = digest.local_now()
        for uid in digest.due_users(now, ignore_hour=ignore_hour):
            user = db.get_user(uid) or {}
            db.update_user(uid, last_digest_date=now.date().isoformat())
            if not user.get("telegram_chat_id"):
                continue
            d = digest.build(uid)
            if d:
                sent += 1
                self.send_text(user["telegram_chat_id"], d["text"].split("\n")[0])
                for c in d["cards"]:
                    self.send_card(user["telegram_chat_id"], c)
        if not instant:
            return sent
        for r in db.conn().execute("SELECT * FROM users WHERE telegram_chat_id IS NOT NULL").fetchall():
            u = dict(r)
            if digest.in_quiet_hours(u, now.hour):
                continue
            d = digest.build(u["id"], instant=True)
            if d:
                sent += 1
                self.send_text(u["telegram_chat_id"], d["text"].split("\n")[0])
                for c in d["cards"]:
                    self.send_card(u["telegram_chat_id"], c)
        return sent

    def handle_update(self, u: dict) -> None:
        try:
            if "message" in u:
                self.on_message(u["message"])
            elif "callback_query" in u:
                self.on_callback(u["callback_query"])
        except Exception:
            log.exception("failed to handle update")

    def set_webhook(self, url: str, secret: str) -> dict:
        self.set_commands()
        return self.call("setWebhook", url=url, secret_token=secret, allowed_updates=["message", "callback_query"])

    def set_commands(self) -> None:
        self.call("setMyCommands", commands=[
            {"command": "today", "description": "Today's picks"},
            {"command": "me", "description": "What you know about me"},
            {"command": "forgetme", "description": "Delete all my data"},
        ])
        self.call("setMyCommands", language_code="zh", commands=[
            {"command": "today", "description": "今天的推薦"},
            {"command": "me", "description": "你眼中的我"},
            {"command": "forgetme", "description": "刪除我的所有資料"},
        ])

    def run(self):
        """Long polling for running locally. On Vercel the bot uses a webhook instead (see api.py)."""
        self.call("deleteWebhook")
        self.set_commands()
        last_push = 0.0
        while True:
            try:
                updates = self.call("getUpdates", offset=self.offset, timeout=50,
                                    allowed_updates=["message", "callback_query"]) or []
                for u in updates:
                    self.offset = u["update_id"] + 1
                    self.handle_update(u)
                if time.time() - last_push > 600:
                    last_push = time.time()
                    self.push_due()
            except httpx.HTTPError as e:
                log.warning("telegram polling error: %s", e)
                time.sleep(5)


def main():
    logging.basicConfig(level=logging.INFO)
    if not settings.telegram_token:
        raise SystemExit("請先設定 TELEGRAM_BOT_TOKEN（跟 @BotFather 申請）")
    db.init_db()
    Bot(settings.telegram_token).run()


if __name__ == "__main__":
    main()

"""Proactive push (spec §8 主動推薦節奏): one daily digest of at most 3 high-score deals, plus rare instant pushes.
Nothing is sent when nothing clears the bar."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import db, deals, feedback, i18n, ranking
from .config import INSTANT_PUSH_THRESHOLD, PUSH_THRESHOLD, settings


def local_now() -> datetime:
    return datetime.now(ZoneInfo(settings.timezone))


def in_quiet_hours(user: dict, hour: int) -> bool:
    start, end = user.get("quiet_start"), user.get("quiet_end")
    if start is None or end is None or start == end:
        return False
    return start <= hour or hour < end if start > end else start <= hour < end


def _pushed_recently(user_id: str, deal_id: int, days: int = 7) -> bool:
    since = db.iso(db.now() - timedelta(days=days))
    return db.conn().execute(
        "SELECT 1 FROM recommendations WHERE user_id=? AND deal_id=? AND channel IN ('digest', 'instant') AND created_at>=?",
        (user_id, deal_id, since),
    ).fetchone() is not None


def pick(user_id: str, *, instant: bool = False) -> list[ranking.Scored]:
    user = db.get_user(user_id) or {}
    if user.get("push_paused"):
        return []
    items = [s for s in ranking.rank(user_id, limit=30) if not _pushed_recently(user_id, s.deal["id"])]
    if instant:
        items = [
            s for s in items
            if s.score >= INSTANT_PUSH_THRESHOLD
            and ((deals.days_left(s.deal) is not None and deals.days_left(s.deal) <= 2) or s.matched_intent)
        ]
        return items[:1]
    return [s for s in items if s.score >= PUSH_THRESHOLD][: user.get("daily_push_limit") or 3]


def format_message(items: list[ranking.Scored], instant: bool = False) -> str:
    # The first line is what notification previews show, so it never names the deal (medical intents are sensitive).
    lang = items[0].lang if items else None
    head = i18n.t("instant_head", lang) if instant else i18n.t("digest_head", lang, n=len(items))
    lines = [head, ""]
    for s in items:
        card = ranking.card(s)
        why = i18n.t("sep", lang).join(s.reasons[:2])
        until = f" ({i18n.t('until', lang, date=card['valid_until'])})" if card.get("valid_until") else ""
        lines.append(f"• {card['title']}{until}")
        if why:
            lines.append(f"  {why}")
    return "\n".join(lines)


def build(user_id: str, *, instant: bool = False, log: bool = True) -> dict | None:
    feedback.sweep_ignored(user_id)
    items = pick(user_id, instant=instant)
    if not items:
        return None
    if log:
        ranking.log_recommendations(user_id, items, channel="instant" if instant else "digest")
    return {"text": format_message(items, instant), "cards": [ranking.card(s) for s in items]}


def due_users(now: datetime | None = None, *, ignore_hour: bool = False) -> list[str]:
    """Users whose daily digest is due: digest hour reached, not yet sent today, outside quiet hours.
    ignore_hour is for a once-a-day scheduler (Vercel Cron) that already fires at the chosen time."""
    now = now or local_now()
    today = now.date().isoformat()
    out = []
    for r in db.conn().execute("SELECT * FROM users").fetchall():
        u = dict(r)
        if u.get("last_digest_date") == today or in_quiet_hours(u, now.hour):
            continue
        if not ignore_hour and now.hour < settings.digest_hour:
            continue
        out.append(u["id"])
    return out


def run_daily(now: datetime | None = None) -> dict[str, dict]:
    now = now or local_now()
    sent = {}
    for uid in due_users(now):
        d = build(uid)
        db.update_user(uid, last_digest_date=now.date().isoformat())
        if d:
            sent[uid] = d
    return sent


if __name__ == "__main__":
    db.init_db()
    for uid, d in run_daily().items():
        print(f"== {uid}\n{d['text']}\n")

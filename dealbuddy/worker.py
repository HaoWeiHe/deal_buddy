"""Always-on worker for the host that runs xiaohongshu-mcp (deploy/xhs-host).

Vercel can't keep a logged-in browser, so this process runs next to the MCP and writes into the
same Postgres (DATABASE_URL) the Vercel app reads:

- every XHS_INTERVAL_HOURS: search 小紅書 for every user's city / intents / liked merchants, plus IG and RSS
- every WORKER_POLL_MINUTES: look for intents created since the last check and search for them right away
  (the Vercel function can't reach the MCP, so its background intent search is a no-op there)
- after new deals arrive: send Telegram instant pushes, which Vercel Hobby's once-a-day cron can't do

Run:  python -m dealbuddy.worker          (or --once for a single pass, e.g. from system cron)
"""
import argparse
import logging
import os
import time

from . import db, sources
from .config import settings

log = logging.getLogger("dealbuddy.worker")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


def new_intents(since: str) -> list[tuple[str, str, str]]:
    """(user_id, target, updated_at) of active intents touched at or after `since` (timestamps are per second)."""
    rows = db.conn().execute(
        "SELECT user_id, target, updated_at FROM user_beliefs WHERE layer='intent' AND status='active' AND updated_at>=?",
        (since,),
    ).fetchall()
    return [(r["user_id"], r["target"], r["updated_at"]) for r in rows]


class Worker:
    def __init__(self, src: sources.XiaohongshuSource | None = None, *, interval_hours: float | None = None,
                 poll_minutes: float | None = None):
        self.src = src or sources.XiaohongshuSource()
        self.interval = 3600 * (interval_hours if interval_hours is not None else _env_float("XHS_INTERVAL_HOURS", 6))
        self.poll = 60 * (poll_minutes if poll_minutes is not None else _env_float("WORKER_POLL_MINUTES", 10))
        self.last_full: float | None = None
        self.intent_mark = db.iso(db.now())
        self.intents_done: set[tuple[str, str, str]] = set()
        self.was_logged_in: bool | None = None

    def check_login(self) -> bool:
        ok = self.src.enabled and self.src.logged_in()
        if ok != self.was_logged_in:
            if ok:
                log.info("小紅書小號已登入")
            else:
                log.warning("小紅書小號沒有登入（或 MCP 連不上）。在主機上執行：docker compose run --rm worker "
                            "python -m dealbuddy.xhs_login，掃 data/xhs-login.png 的 QR code")
        self.was_logged_in = ok
        return ok

    def full_run(self) -> int:
        saved = 0
        if self.check_login():
            saved += len(sources.run_xiaohongshu(self.src))
        for name, fn in (("instagram", sources.run_instagram), ("rss", sources.run_rss)):
            try:
                saved += len(fn())
            except Exception:
                log.exception("%s source failed", name)
        return saved

    def intent_run(self) -> int:
        mark = db.iso(db.now())
        fresh = [i for i in new_intents(self.intent_mark) if i not in self.intents_done]
        self.intents_done = {i for i in self.intents_done if i[2] >= mark} | set(fresh)
        self.intent_mark = mark
        users = list(dict.fromkeys(uid for uid, _, _ in fresh))
        if not users or not self.check_login():
            return 0
        saved = 0
        for uid in users:
            saved += len(sources.search_for_intent(uid, src=self.src))
        return saved

    def push(self) -> int:
        if not settings.telegram_token:
            return 0
        from .telegram_bot import Bot

        try:
            return Bot(settings.telegram_token).push_due(instant=True)
        except Exception:
            log.exception("telegram push failed")
            return 0

    def tick(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        out = {"full": 0, "intent": 0, "pushed": 0}
        if self.last_full is None or now - self.last_full >= self.interval:
            self.last_full = now
            out["full"] = self.full_run()
        out["intent"] = self.intent_run()
        out["pushed"] = self.push()
        if out["full"] or out["intent"]:
            log.info("new deals: %s", out)
        return out

    def run_forever(self) -> None:
        log.info("worker started: full run every %.1fh, intent check every %.0f min",
                 self.interval / 3600, self.poll / 60)
        while True:
            try:
                self.tick()
            except Exception:
                log.exception("worker tick failed")
            time.sleep(self.poll)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--once", action="store_true", help="run one full pass and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    db.init_db()
    w = Worker()
    if args.once:
        print(w.tick())
    else:
        w.run_forever()


if __name__ == "__main__":
    main()

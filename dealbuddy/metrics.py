"""Success metrics (spec §2), led by 每人每月估計省下金額: the basis for future pricing (spec §12)."""
from datetime import timedelta

from . import db, deals

# Typical spend used to turn a % discount into dollars when the deal gives no amount.
TYPICAL_SPEND = {"dining": 40, "delivery": 30, "grocery": 80, "cafe": 6, "beauty": 60, "shopping": 60,
                 "electronics": 200, "travel": 300, "entertainment": 40, "healthcare": 150, "transport": 50}
DEFAULT_SPEND = 50


def estimated_saving(deal: dict) -> float:
    """Dollar value a user gets by using this deal. Sweepstakes count their expected value, which is small."""
    if deal.get("est_value"):
        return float(deal["est_value"])
    if deal.get("face_value") and deal.get("mechanic") != "sweepstakes":
        return float(deal["face_value"])
    spend = TYPICAL_SPEND.get(deal.get("category"), DEFAULT_SPEND)
    if deal.get("percent_off"):
        return round(spend * deal["percent_off"], 2)
    if deal.get("mechanic") == "bogo":
        return round(spend * 0.5, 2)
    if deal.get("mechanic") == "free_delivery":
        return 7.0
    return 0.0


def record_used(user_id: str, deal_id: int) -> float:
    """Called on a 「已使用」 feedback: store the estimated saving on that event."""
    amount = estimated_saving(deals.get(deal_id) or {})
    db.conn().execute(
        """UPDATE feedback_events SET saved_amount=? WHERE id=(
             SELECT MAX(id) FROM feedback_events WHERE user_id=? AND deal_id=? AND signal='used')""",
        (amount, user_id, deal_id),
    )
    db.conn().commit()
    return amount


def _month_start() -> str:
    t = deals.today()
    return t.replace(day=1).isoformat()


def savings(user_id: str, since: str | None = None) -> dict:
    since = since or _month_start()
    r = db.conn().execute(
        """SELECT COUNT(*) AS n, COALESCE(SUM(saved_amount), 0) AS total FROM feedback_events
           WHERE user_id=? AND signal='used' AND created_at >= ?""",
        (user_id, since),
    ).fetchone()
    return {"since": since, "deals_used": r["n"], "estimated_saved": round(r["total"], 2)}


def summary(user_id: str | None = None, days: int = 30) -> dict:
    """Spec §2 success metrics over the last `days`, for one user or everyone."""
    since = db.iso(db.now() - timedelta(days=days))
    where, args = ("AND user_id=?", [user_id]) if user_id else ("", [])
    recs = db.conn().execute(
        f"SELECT COUNT(DISTINCT user_id || ':' || deal_id) AS n FROM recommendations WHERE created_at>=? {where}",
        [since, *args],
    ).fetchone()["n"]
    fb = {
        r["signal"]: r["n"] for r in db.conn().execute(
            f"""SELECT signal, COUNT(DISTINCT user_id || ':' || deal_id) AS n FROM feedback_events
                WHERE created_at>=? {where} GROUP BY signal""",
            [since, *args],
        ).fetchall()
    }
    saved = db.conn().execute(
        f"SELECT COALESCE(SUM(saved_amount), 0) AS s FROM feedback_events WHERE signal='used' AND created_at>=? {where}",
        [since, *args],
    ).fetchone()["s"]
    users = db.conn().execute(
        f"SELECT COUNT(DISTINCT user_id) AS n FROM messages WHERE role='user' AND created_at>=? {where}",
        [db.iso(db.now() - timedelta(days=7)), *args],
    ).fetchone()["n"]
    positive = fb.get("useful", 0) + fb.get("saved", 0) + fb.get("used", 0)
    return {
        "window_days": days,
        "recommendations": recs,
        "relevant_rate": round(positive / recs, 3) if recs else None,  # target ≥ 40%
        "not_interested_rate": round(fb.get("not_interested", 0) / recs, 3) if recs else None,  # target ≤ 20%
        "deals_used": fb.get("used", 0),
        "estimated_saved": round(saved, 2),
        "weekly_active_users": users,
    }

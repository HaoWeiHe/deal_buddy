"""Rule-based feedback learning (spec §9). Every update is logged with its source event."""
from datetime import timedelta

from . import db, deals, profile

SIGNALS = ("useful", "saved", "used", "not_interested", "ignored")
REASONS = ("merchant", "category", "too_far", "not_enough", "mechanic")


def _tag(deal: dict, signal: str) -> str:
    return f"{signal}: {deal.get('title', '')[:40]}"


def record(user_id: str, deal_id: int, signal: str, reason: str | None = None) -> dict:
    if signal not in SIGNALS:
        raise ValueError(f"unknown signal {signal}")
    deal = deals.get(deal_id)
    if not deal:
        raise KeyError(deal_id)
    db.conn().execute(
        "INSERT INTO feedback_events (user_id, deal_id, signal, reason, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, deal_id, signal, reason, db.iso()),
    )
    db.conn().commit()
    note = _tag(deal, signal)
    merchant = deal.get("merchant_id")
    changes = []

    if signal in ("useful", "saved", "used"):
        step = 0.2 if signal == "used" else 0.1
        if merchant:
            changes.append(profile.set_belief(user_id, "entity", merchant, delta=step, source="feedback", note=note))
        changes.append(profile.set_belief(user_id, "context", deal["category"], delta=step, source="feedback", note=note))
        changes.append(profile.set_belief(user_id, "mechanic", deal["mechanic"], delta=step, source="feedback", note=note))
        _learn_threshold(user_id, deal, positive=True)
        if signal == "used":
            from . import metrics

            metrics.record_used(user_id, deal_id)
            # Close any intent this deal served (spec §9: 已使用 → 關閉對應意圖).
            from .ranking import intent_match

            for it in profile.active_intents(user_id):
                if intent_match(it, deal) >= 0.7:
                    profile.close_intent(user_id, it["target"], reason=f"used deal {deal_id}")
    elif signal == "not_interested":
        # Only lower the dimension the user named (spec §9).
        if reason == "merchant" and merchant:
            changes.append(profile.set_belief(user_id, "entity", merchant, delta=-0.3, source="feedback", note=note))
        elif reason == "category":
            changes.append(profile.set_belief(user_id, "context", deal["category"], delta=-0.2, source="feedback", note=note))
        elif reason == "mechanic":
            changes.append(profile.set_belief(user_id, "mechanic", deal["mechanic"], delta=-0.2, source="feedback", note=note))
        elif reason == "not_enough":
            _learn_threshold(user_id, deal, positive=False)
        elif reason is None and merchant:
            changes.append(profile.set_belief(user_id, "entity", merchant, delta=-0.1, source="feedback", note=note))
    return {"ok": True, "changes": [{"layer": c["layer"], "target": c["target"], "score": c["score"]} for c in changes]}


def _learn_threshold(user_id: str, deal: dict, positive: bool) -> None:
    pct = deal.get("percent_off")
    if not pct:
        return
    user = db.get_user(user_id) or {}
    t = user.get("discount_threshold") or 0.2
    if positive and pct < t:
        t = 0.7 * t + 0.3 * pct
    elif not positive:
        t = max(t, 0.7 * t + 0.3 * (pct + 0.05))
    db.update_user(user_id, discount_threshold=round(min(0.6, max(0.05, t)), 3))


def sweep_ignored(user_id: str, after_days: int = 3) -> int:
    """Pushed recommendations with no reaction after a few days count as ignored.
    Three ignored pushes in a row for one merchant lower it by 0.1 (a weak signal)."""
    cutoff = db.iso(db.now() - timedelta(days=after_days))
    rows = db.conn().execute(
        """SELECT r.deal_id FROM recommendations r
           WHERE r.user_id=? AND r.channel!='chat' AND r.created_at < ?
             AND NOT EXISTS (SELECT 1 FROM feedback_events f WHERE f.user_id=r.user_id AND f.deal_id=r.deal_id)
           GROUP BY r.deal_id""",
        (user_id, cutoff),
    ).fetchall()
    for r in rows:
        db.conn().execute(
            "INSERT INTO feedback_events (user_id, deal_id, signal, reason, created_at) VALUES (?, ?, 'ignored', NULL, ?)",
            (user_id, r["deal_id"], db.iso()),
        )
    db.conn().commit()
    # Check consecutive ignores per merchant.
    events = db.conn().execute(
        """SELECT f.signal, d.merchant_id FROM feedback_events f JOIN deals d ON d.id=f.deal_id
           WHERE f.user_id=? AND d.merchant_id IS NOT NULL ORDER BY f.id DESC""",
        (user_id,),
    ).fetchall()
    streak: dict[str, int] = {}
    broken: set[str] = set()
    for e in events:
        m = e["merchant_id"]
        if m in broken:
            continue
        if e["signal"] == "ignored":
            streak[m] = streak.get(m, 0) + 1
        else:
            broken.add(m)
    for m, n in streak.items():
        if n >= 3:
            profile.set_belief(user_id, "entity", m, delta=-0.1, source="feedback", note="連續忽略 3 次")
            # Reset the streak so the penalty applies once per three ignores.
            db.conn().execute(
                """UPDATE feedback_events SET signal='ignored_counted' WHERE user_id=? AND signal='ignored'
                   AND deal_id IN (SELECT id FROM deals WHERE merchant_id=?)""",
                (user_id, m),
            )
            db.conn().commit()
    return len(rows)

"""User model (spec §6): four layers of beliefs, each with score, source, timestamp, visible and editable."""
import json
from datetime import timedelta

from . import db, entities
from .config import BELIEF_HALF_LIFE_DAYS, DEFAULT_INTENT_DAYS

LAYERS = ("entity", "context", "mechanic", "intent")
DEFAULTS = {"entity": 0.0, "context": 0.3, "mechanic": 0.5, "intent": 0.0}
RANGES = {"entity": (-1.0, 1.0), "context": (0.0, 1.0), "mechanic": (0.0, 1.0), "intent": (0.0, 1.0)}

# Source strength (spec §6 "訊號來源與權重"). Inferred beliefs are capped until the user confirms them.
SOURCE_CAP = {"explicit": 1.0, "feedback": 1.0, "share": 0.8, "inferred": 0.6, "onboarding": 1.0, "manual": 1.0}


def _clamp(layer: str, score: float) -> float:
    lo, hi = RANGES[layer]
    return max(lo, min(hi, score))


def _row(r) -> dict:
    return db.row_to_dict(r, ("meta",))


def get_belief(user_id: str, layer: str, target: str) -> dict | None:
    r = db.conn().execute(
        "SELECT * FROM user_beliefs WHERE user_id=? AND layer=? AND target=?", (user_id, layer, target)
    ).fetchone()
    return _row(r) if r else None


def set_belief(
    user_id: str,
    layer: str,
    target: str,
    score: float | None = None,
    *,
    delta: float | None = None,
    source: str = "explicit",
    note: str = "",
    meta: dict | None = None,
    expires_at: str | None = None,
    status: str = "active",
) -> dict:
    """Write a belief. Pass `score` to set it, or `delta` to nudge the current effective value."""
    if layer not in LAYERS:
        raise ValueError(f"unknown layer {layer}")
    existing = get_belief(user_id, layer, target)
    old = effective_score(existing) if existing else DEFAULTS[layer]
    new = old + delta if delta is not None else float(score if score is not None else old)
    if layer == "entity":
        cap = SOURCE_CAP.get(source, 1.0)
        new = max(-cap, min(cap, new)) if source == "inferred" else new
    elif source == "inferred":
        new = min(new, SOURCE_CAP["inferred"])
    new = round(_clamp(layer, new), 3)
    merged_meta = {**(existing["meta"] if existing else {}), **(meta or {})}
    c = db.conn()
    c.execute(
        """INSERT INTO user_beliefs (user_id, layer, target, score, source, note, meta, status, updated_at, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(user_id, layer, target) DO UPDATE SET
             score=excluded.score, source=excluded.source, note=excluded.note, meta=excluded.meta,
             status=excluded.status, updated_at=excluded.updated_at,
             expires_at=COALESCE(excluded.expires_at, user_beliefs.expires_at)""",
        (user_id, layer, target, new, source, note, json.dumps(merged_meta, ensure_ascii=False), status,
         db.iso(), expires_at),
    )
    c.execute(
        """INSERT INTO belief_events (user_id, layer, target, old_score, new_score, source, note, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, layer, target, old, new, source, note, db.iso()),
    )
    c.commit()
    return get_belief(user_id, layer, target)


def delete_belief(user_id: str, layer: str, target: str) -> None:
    db.conn().execute("DELETE FROM user_beliefs WHERE user_id=? AND layer=? AND target=?", (user_id, layer, target))
    db.conn().commit()


def effective_score(belief: dict | None) -> float:
    """Long-term layers decay toward their default with a ~6 month half-life (spec §6)."""
    if not belief:
        return 0.0
    layer = belief["layer"]
    if layer == "intent":
        return belief["score"] if belief["status"] == "active" else 0.0
    updated = db.parse_dt(belief["updated_at"]) or db.now()
    age_days = max(0.0, (db.now() - updated).total_seconds() / 86400)
    base = DEFAULTS[layer]
    return base + (belief["score"] - base) * 0.5 ** (age_days / BELIEF_HALF_LIFE_DAYS)


def _layer_score(user_id: str, layer: str, target: str | None) -> float:
    if not target:
        return DEFAULTS[layer]
    b = get_belief(user_id, layer, target)
    return effective_score(b) if b else DEFAULTS[layer]


def entity_affinity(user_id: str, entity_id: str | None) -> float:
    return _layer_score(user_id, "entity", entity_id)


def context_affinity(user_id: str, category: str | None) -> float:
    return _layer_score(user_id, "context", category)


def mechanic_affinity(user_id: str, mechanic: str | None) -> float:
    return _layer_score(user_id, "mechanic", mechanic)


# ---- intents -------------------------------------------------------------

def add_intent(
    user_id: str,
    target: str,
    *,
    label: str = "",
    strength: float = 0.9,
    days: int | None = None,
    categories: list[str] | None = None,
    keywords: list[str] | None = None,
    entity_ids: list[str] | None = None,
    destination: str | None = None,
    sensitive: bool = False,
    source: str = "explicit",
    note: str = "",
) -> dict:
    """Spec §6 lifecycle: e.g. 「我該洗牙了」→ dental_cleaning, strength 0.9, 30 day default expiry."""
    expires = db.now() + timedelta(days=days or DEFAULT_INTENT_DAYS)
    meta = {
        "label": label or target,
        "categories": categories or [],
        "keywords": [k.lower() for k in (keywords or [])],
        "entity_ids": entity_ids or [],
        "destination": entities.metro_of(destination) if destination else None,
        "sensitive": sensitive,
    }
    return set_belief(
        user_id, "intent", target, strength, source=source, note=note or label, meta=meta,
        expires_at=db.iso(expires), status="active",
    )


def close_intent(user_id: str, target: str, reason: str = "done") -> bool:
    b = get_belief(user_id, "intent", target)
    if not b or b["status"] != "active":
        return False
    if b["meta"].get("sensitive"):
        # Medical intents are sensitive: drop them entirely once they are over (spec §11).
        delete_belief(user_id, "intent", target)
        _log(user_id, "intent", target, b["score"], 0.0, reason)
        return True
    set_belief(user_id, "intent", target, 0.0, source="explicit", note=reason, status="closed")
    return True


def _log(user_id, layer, target, old, new, note):
    db.conn().execute(
        """INSERT INTO belief_events (user_id, layer, target, old_score, new_score, source, note, created_at)
           VALUES (?, ?, ?, ?, ?, 'system', ?, ?)""",
        (user_id, layer, target, old, new, note, db.iso()),
    )
    db.conn().commit()


def expire_intents(user_id: str) -> None:
    rows = db.conn().execute(
        "SELECT * FROM user_beliefs WHERE user_id=? AND layer='intent' AND status='active'", (user_id,)
    ).fetchall()
    for r in rows:
        b = _row(r)
        exp = db.parse_dt(b["expires_at"])
        if exp and exp < db.now():
            close_intent(user_id, b["target"], reason="expired")


def active_intents(user_id: str) -> list[dict]:
    expire_intents(user_id)
    rows = db.conn().execute(
        "SELECT * FROM user_beliefs WHERE user_id=? AND layer='intent' AND status='active' ORDER BY score DESC",
        (user_id,),
    ).fetchall()
    return [_row(r) for r in rows]


def user_metros(user_id: str) -> set[str]:
    """Where deals are relevant: the user's home city plus destinations of active trips."""
    user = db.get_user(user_id) or {}
    metros = set()
    if user.get("city"):
        metros.add(entities.metro_of(user["city"]))
    for i in active_intents(user_id):
        if i["meta"].get("destination"):
            metros.add(i["meta"]["destination"])
    return {m for m in metros if m}


# ---- views -----------------------------------------------------------------

def beliefs(user_id: str, layer: str | None = None) -> list[dict]:
    q = "SELECT * FROM user_beliefs WHERE user_id=?"
    args: list = [user_id]
    if layer:
        q += " AND layer=?"
        args.append(layer)
    return [_row(r) for r in db.conn().execute(q + " ORDER BY layer, score DESC", args).fetchall()]


def summary(user_id: str) -> dict:
    """「好朋友認識的你」: what the app believes, with where each belief came from."""
    user = db.get_user(user_id) or {}
    out = {
        "city": user.get("city"),
        "discount_threshold": user.get("discount_threshold"),
        "push": {
            "daily_limit": user.get("daily_push_limit"),
            "paused": bool(user.get("push_paused")),
            "quiet_hours": [user.get("quiet_start"), user.get("quiet_end")],
        },
        "likes": [], "dislikes": [], "contexts": [], "mechanics": [], "intents": [],
    }
    for b in beliefs(user_id):
        eff = round(effective_score(b), 2)
        item = {"target": b["target"], "score": eff, "source": b["source"], "note": b["note"], "updated_at": b["updated_at"]}
        if b["layer"] == "entity":
            item["name"] = entities.display_name(b["target"])
            (out["likes"] if eff >= 0 else out["dislikes"]).append(item)
        elif b["layer"] == "context":
            out["contexts"].append(item)
        elif b["layer"] == "mechanic":
            out["mechanics"].append(item)
        elif b["status"] == "active":
            item.update(label=b["meta"].get("label"), expires_at=b["expires_at"],
                        destination=b["meta"].get("destination"))
            out["intents"].append(item)
    return out


def why(user_id: str, layer: str, target: str, limit: int = 5) -> list[dict]:
    """Answer 「你為什麼覺得我喜歡 X」 from the event log (spec §9)."""
    rows = db.conn().execute(
        """SELECT old_score, new_score, source, note, created_at FROM belief_events
           WHERE user_id=? AND layer=? AND target=? ORDER BY id DESC LIMIT ?""",
        (user_id, layer, target, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def is_new_user(user_id: str) -> bool:
    user = db.get_user(user_id) or {}
    return not user.get("onboarded") and not beliefs(user_id)

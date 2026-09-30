"""SQLite storage. Tables follow spec §10; SQL is kept portable so moving to Postgres later is mechanical."""
import json
import sqlite3
import threading
from datetime import datetime, timezone

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    display_name TEXT,
    city TEXT,
    language TEXT DEFAULT 'zh-Hant',
    quiet_start INTEGER DEFAULT 22,
    quiet_end INTEGER DEFAULT 9,
    daily_push_limit INTEGER DEFAULT 3,
    discount_threshold REAL DEFAULT 0.2,
    push_paused INTEGER DEFAULT 0,
    telegram_chat_id TEXT,
    last_digest_date TEXT,
    onboarded INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    type TEXT,
    name TEXT,
    aliases TEXT DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS user_beliefs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    layer TEXT,
    target TEXT,
    score REAL,
    source TEXT,
    note TEXT,
    meta TEXT DEFAULT '{}',
    status TEXT DEFAULT 'active',
    updated_at TEXT,
    expires_at TEXT,
    UNIQUE(user_id, layer, target)
);
CREATE TABLE IF NOT EXISTS belief_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    layer TEXT,
    target TEXT,
    old_score REAL,
    new_score REAL,
    source TEXT,
    note TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS deals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    description TEXT,
    title_en TEXT,
    description_en TEXT,
    conditions_en TEXT DEFAULT '[]',
    merchant_id TEXT,
    entity_ids TEXT DEFAULT '[]',
    category TEXT,
    mechanic TEXT,
    percent_off REAL,
    face_value REAL,
    est_value REAL,
    conditions TEXT DEFAULT '[]',
    locations TEXT DEFAULT '[]',
    valid_from TEXT,
    valid_until TEXT,
    url TEXT,
    confidence REAL DEFAULT 0.7,
    uncertain_fields TEXT DEFAULT '[]',
    status TEXT DEFAULT 'active',
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS deal_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id INTEGER,
    source_type TEXT,
    url TEXT,
    raw_content TEXT,
    shared_by TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    deal_id INTEGER,
    score REAL,
    breakdown TEXT,
    reasons TEXT,
    channel TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS feedback_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    deal_id INTEGER,
    signal TEXT,
    reason TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    role TEXT,
    content TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS source_seen (
    source TEXT,
    item_id TEXT,
    seen_at TEXT,
    PRIMARY KEY (source, item_id)
);
CREATE INDEX IF NOT EXISTS idx_beliefs_user ON user_beliefs(user_id);
CREATE INDEX IF NOT EXISTS idx_deals_status ON deals(status);
CREATE INDEX IF NOT EXISTS idx_recs_user ON recommendations(user_id, deal_id);
CREATE INDEX IF NOT EXISTS idx_msgs_user ON messages(user_id);
"""

_local = threading.local()
_override_target: str | None = None
_initialized: set[str] = set()


def _target() -> str:
    """A postgres:// URL (DATABASE_URL, e.g. Neon on Vercel) or a SQLite file path."""
    return _override_target or settings.database_url or settings.db_path


def is_postgres(target: str | None = None) -> bool:
    return (target or _target()).startswith(("postgres://", "postgresql://"))


def _pg_sql(sql: str) -> str:
    """Translate the SQLite dialect used in this codebase to Postgres."""
    ignore = "INSERT OR IGNORE INTO" in sql
    sql = sql.replace("INSERT OR IGNORE INTO", "INSERT INTO")
    sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
    sql = sql.replace("?", "%s")
    if ignore:
        sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    return sql


class PgConnection:
    """Just enough of the sqlite3.Connection surface for this app, over psycopg (autocommit)."""

    def __init__(self, url: str):
        import psycopg
        from psycopg.rows import dict_row

        self._c = psycopg.connect(url, autocommit=True, row_factory=dict_row)

    def execute(self, sql: str, args=()):
        cur = self._c.cursor()
        cur.execute(_pg_sql(sql), tuple(args))
        return cur

    def executescript(self, script: str) -> None:
        for stmt in script.split(";"):
            if stmt.strip():
                self.execute(stmt)

    def commit(self) -> None:
        pass

    def close(self) -> None:
        self._c.close()
def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or now()).isoformat(timespec="seconds")


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def use_database(target: str) -> None:
    """Point every thread at a different database, a file path or postgres URL (used by tests and scripts)."""
    global _override_target
    _override_target = target
    _initialized.discard(target)
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None
    init_db()


def conn():
    target = _target()
    c = getattr(_local, "conn", None)
    if c is None or getattr(_local, "path", None) != target:
        if is_postgres(target):
            c = PgConnection(target)
        else:
            c = sqlite3.connect(target, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
        _local.conn = c
        _local.path = target
        if target not in _initialized:
            # Serverless hosts may skip ASGI startup events, so the schema is ensured on first use.
            _initialized.add(target)
            init_db()
    return c


# Columns added after the first release; ALTER them into databases created earlier.
MIGRATIONS = [
    ("deals", "title_en", "TEXT"),
    ("deals", "description_en", "TEXT"),
    ("deals", "conditions_en", "TEXT DEFAULT '[]'"),
    ("deals", "sponsored", "INTEGER DEFAULT 0"),
    ("feedback_events", "saved_amount", "REAL"),
]


def init_db() -> None:
    c = conn()
    c.executescript(SCHEMA)
    for table, column, decl in MIGRATIONS:
        if is_postgres():
            c.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {decl}")
            continue
        cols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    c.commit()
    from .entities import seed_entities

    seed_entities()


def row_to_dict(row: sqlite3.Row | None, json_fields: tuple[str, ...] = ()) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for f in json_fields:
        if f in d and isinstance(d[f], str):
            try:
                d[f] = json.loads(d[f])
            except json.JSONDecodeError:
                pass
    return d


def ensure_user(user_id: str, **fields) -> dict:
    c = conn()
    row = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if row is None:
        city = fields.pop("city", None) or settings.default_city or None
        c.execute(
            "INSERT INTO users (id, city, created_at) VALUES (?, ?, ?)",
            (user_id, city, iso()),
        )
        c.commit()
    if fields:
        update_user(user_id, **fields)
    return get_user(user_id)


def get_user(user_id: str) -> dict | None:
    return row_to_dict(conn().execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())


USER_FIELDS = {
    "display_name", "city", "language", "quiet_start", "quiet_end", "daily_push_limit",
    "discount_threshold", "push_paused", "telegram_chat_id", "last_digest_date", "onboarded",
}


def update_user(user_id: str, **fields) -> None:
    fields = {k: v for k, v in fields.items() if k in USER_FIELDS}
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    conn().execute(f"UPDATE users SET {sets} WHERE id=?", (*fields.values(), user_id))
    conn().commit()


def delete_user(user_id: str) -> None:
    c = conn()
    for table, col in (
        ("user_beliefs", "user_id"), ("belief_events", "user_id"), ("recommendations", "user_id"),
        ("feedback_events", "user_id"), ("messages", "user_id"), ("users", "id"),
    ):
        c.execute(f"DELETE FROM {table} WHERE {col}=?", (user_id,))
    # Shared deals stay in the pool, but we drop who shared them and the raw content.
    c.execute("UPDATE deal_sources SET shared_by=NULL, raw_content=NULL WHERE shared_by=?", (user_id,))
    c.commit()


def add_message(user_id: str, role: str, content: str) -> None:
    conn().execute(
        "INSERT INTO messages (user_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (user_id, role, content, iso()),
    )
    conn().commit()


def recent_messages(user_id: str, limit: int = 20) -> list[dict]:
    rows = conn().execute(
        "SELECT role, content FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]

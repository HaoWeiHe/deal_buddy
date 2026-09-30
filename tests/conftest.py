import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.pop("ANTHROPIC_API_KEY", None)  # tests run the offline path
os.environ.pop("XHS_MCP_URL", None)

from dealbuddy import config, db  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config.settings, "anthropic_api_key", "")
    monkeypatch.setattr(config.settings, "xhs_url", "")
    monkeypatch.setattr(config.settings, "xhs_delay_seconds", 0)
    pg = os.environ.get("TEST_DATABASE_URL")  # run the suite against Postgres too
    if pg:
        import psycopg

        with psycopg.connect(pg, autocommit=True) as c:
            c.execute("DROP SCHEMA public CASCADE")
            c.execute("CREATE SCHEMA public")
        db.use_database(pg)
    else:
        db.use_database(str(tmp_path / "test.sqlite3"))
    yield

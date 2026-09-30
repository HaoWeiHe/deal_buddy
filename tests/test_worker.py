"""Always-on worker and QR login helper for the xiaohongshu-mcp host."""
import base64

import httpx

from dealbuddy import config, db, profile, sources, worker, xhs_login
from test_sources import fake_xhs


def test_worker_full_run_then_new_intent_search(monkeypatch):
    monkeypatch.setattr(config.settings, "telegram_token", "")
    src, calls = fake_xhs()
    w = worker.Worker(src, interval_hours=6, poll_minutes=10)
    db.ensure_user("u", city="Toronto")

    out = w.tick(now=1000)
    assert calls.count("/api/v1/feeds/search") >= 1 and out["intent"] == 0

    # Inside the interval: no full run, but a fresh intent triggers a search right away.
    before = calls.count("/api/v1/feeds/search")
    profile.add_intent("u", "dental_cleaning", label="洗牙", categories=["healthcare"], keywords=["牙"])
    out = w.tick(now=1000 + 600)
    assert out["full"] == 0
    assert calls.count("/api/v1/feeds/search") > before

    # Same intent is not searched twice.
    before = calls.count("/api/v1/feeds/search")
    w.tick(now=1000 + 1200)
    assert calls.count("/api/v1/feeds/search") == before


def test_worker_skips_xhs_when_logged_out(caplog):
    def handler(request):
        return httpx.Response(200, json={"success": True, "data": {"is_logged_in": False}})

    src = sources.XiaohongshuSource(base_url="http://xhs", http=httpx.Client(
        base_url="http://xhs", transport=httpx.MockTransport(handler)))
    w = worker.Worker(src)
    assert w.full_run() == 0
    assert "xhs_login" in caplog.text


def test_login_helper_saves_qr_and_waits(tmp_path):
    png = b"\x89PNG fake"
    state = {"polls": 0}

    def handler(request):
        if request.url.path == "/api/v1/login/status":
            state["polls"] += 1
            return httpx.Response(200, json={"success": True, "data": {"is_logged_in": state["polls"] > 2}})
        if request.url.path == "/api/v1/login/qrcode":
            img = "data:image/png;base64," + base64.b64encode(png).decode()
            return httpx.Response(200, json={"success": True, "data": {"img": img, "timeout": "4m0s", "is_logged_in": False}})
        return httpx.Response(404)

    src = sources.XiaohongshuSource(base_url="http://xhs", http=httpx.Client(
        base_url="http://xhs", transport=httpx.MockTransport(handler)))
    out = tmp_path / "qr.png"
    assert xhs_login.main(["--out", str(out)], src=src, poll_seconds=0) == 0
    assert out.read_bytes() == png


def test_login_timeout_parses_go_durations():
    assert xhs_login.parse_timeout("4m0s") == 240
    assert xhs_login.parse_timeout("300") == 300
    assert xhs_login.parse_timeout("1m30s") == 90
    assert xhs_login.parse_timeout(None) == 240

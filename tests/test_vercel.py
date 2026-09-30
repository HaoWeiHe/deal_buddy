"""Vercel-only surface: public-deployment guard, Telegram webhook, daily cron."""
from fastapi.testclient import TestClient

from dealbuddy import api, config, db, seed, telegram_bot


def test_public_deployment_requires_app_token(monkeypatch):
    monkeypatch.setattr(config.settings, "on_vercel", True)
    monkeypatch.setattr(config.settings, "app_token", "")
    c = TestClient(api.app)
    assert c.post("/chat", json={"user_id": "a", "text": "hi"}).status_code == 503
    monkeypatch.setattr(config.settings, "app_token", "s3cret")
    assert c.post("/chat", json={"user_id": "a", "text": "hi"}).status_code == 401
    assert c.post("/chat", json={"user_id": "a", "text": "hi"}, headers={"X-App-Token": "s3cret"}).status_code == 200


def test_telegram_webhook_checks_secret_and_handles_update(monkeypatch):
    monkeypatch.setattr(config.settings, "telegram_token", "t")
    monkeypatch.setattr(config.settings, "telegram_webhook_secret", "wh")
    sent = []
    monkeypatch.setattr(telegram_bot.Bot, "call", lambda self, method, **p: sent.append((method, p)) or {})
    c = TestClient(api.app)
    update = {"update_id": 1, "message": {"chat": {"id": 42}, "from": {"id": 7, "language_code": "en"}, "text": "hi"}}
    assert c.post("/telegram/webhook", json=update).status_code == 401
    assert c.post("/telegram/webhook", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": "wh"}).status_code == 200
    texts = [p.get("text", "") for m, p in sent if m == "sendMessage"]
    assert any("Which city" in t for t in texts)
    assert db.get_user("tg:7")["telegram_chat_id"] == "42"


def test_cron_sends_digest_once_per_day(monkeypatch):
    seed.seed_deals()
    seed.seed_demo_user("tg:9")
    db.update_user("tg:9", telegram_chat_id="99", quiet_start=0, quiet_end=0)
    monkeypatch.setattr(config.settings, "telegram_token", "t")
    monkeypatch.setattr(config.settings, "cron_secret", "cs")
    monkeypatch.setattr(config.settings, "feeds", [])
    sent = []
    monkeypatch.setattr(telegram_bot.Bot, "call", lambda self, method, **p: sent.append((method, p)) or {})
    c = TestClient(api.app)
    assert c.get("/cron/daily").status_code == 401
    r = c.get("/cron/daily", headers={"Authorization": "Bearer cs"}).json()
    assert r["digests_sent"] == 1
    assert any(p.get("chat_id") == "99" for m, p in sent if m == "sendMessage")
    assert c.get("/cron/daily", headers={"Authorization": "Bearer cs"}).json()["digests_sent"] == 0

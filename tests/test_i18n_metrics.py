"""Non-Chinese users, the savings metric, and the revenue/sponsorship guard."""
from fastapi.testclient import TestClient

from dealbuddy import api, db, deals, digest, feedback, i18n, metrics, ranking, seed


def test_language_detection_ignores_urls():
    assert i18n.detect("http://xhslink.com/a/abc123") is None
    assert i18n.detect("any delivery deals this week?") == "en"
    assert i18n.detect("最近有什麼優惠") == "zh-Hant"


def test_english_user_gets_english_everything():
    seed.seed_deals()
    c = TestClient(api.app)
    r = c.post("/chat", json={"user_id": "e", "text": "hi", "lang": "en-CA"}).json()
    assert "Which city" in r["reply"] and r["lang"] == "en"
    r = c.post("/chat", json={"user_id": "e", "text": "I live in Toronto, I don't like Tim Hortons, need a dentist soon"}).json()
    assert "no more Tim Hortons" in r["reply"]
    r = c.post("/chat", json={"user_id": "e", "text": "any deals for me?"}).json()
    top = r["cards"][0]
    assert top["title"] == "New patient cleaning, free $150 Amazon gift card"
    assert top["original_title"] == "新病人洗牙送 $150 Amazon 禮卡"
    assert any("planning" in x for x in top["reasons"])
    assert all(not any("一" <= ch <= "鿿" for ch in reason) for reason in top["reasons"])


def test_english_digest():
    seed.seed_deals()
    seed.seed_demo_user("u")
    db.update_user("u", language="en")
    d = digest.build("u")
    assert d["text"].startswith("2 deals worth a look today")


def test_other_language_set_by_agent_is_kept():
    from dealbuddy import chat

    db.ensure_user("f", language="fr")
    chat.handle("f", "quelles sont les promos de livraison cette semaine")
    assert db.get_user("f")["language"] == "fr"


def test_savings_metric_tracks_used_deals():
    ids = seed.seed_deals()
    seed.seed_demo_user("u")
    dental = next(i for i in ids if deals.get(i)["category"] == "healthcare")
    ramen = next(i for i in ids if deals.get(i)["merchant_id"] == "ippudo")
    feedback.record("u", dental, "used")
    feedback.record("u", ramen, "used")
    s = metrics.savings("u")
    assert s["deals_used"] == 2
    assert s["estimated_saved"] == 150 + 40 * 0.2
    m = TestClient(api.app).get("/metrics", params={"user_id": "u"}).json()
    assert m["this_month"]["estimated_saved"] == s["estimated_saved"]
    assert m["deals_used"] == 2


def test_sponsored_deals_never_enter_organic_ranking():
    ids = seed.seed_deals()
    seed.seed_demo_user("u")
    db.conn().execute("UPDATE deals SET sponsored=1 WHERE id=?", (ids[0],))
    assert ids[0] not in [s.deal["id"] for s in ranking.rank("u", limit=20)]

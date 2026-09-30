"""End-to-end through the HTTP API in offline mode (no API key)."""
from fastapi.testclient import TestClient

from dealbuddy import api, profile, seed


def client():
    return TestClient(api.app)


def test_chat_learns_and_answers():
    seed.seed_deals()
    c = client()
    r = c.post("/chat", json={"user_id": "t", "text": "我住多倫多，我不喜歡 Tim Hortons，下週要去洗牙"}).json()
    assert "不推" in r["reply"] and "洗牙" in r["reply"]
    p = c.get("/profile", params={"user_id": "t"}).json()
    assert p["city"] == "多倫多"
    assert p["dislikes"][0]["target"] == "tim_hortons"
    assert p["intents"][0]["target"] == "dental_cleaning"

    r = c.post("/chat", json={"user_id": "t", "text": "最近有什麼優惠？"}).json()
    titles = [x["title"] for x in r["cards"]]
    assert titles[0] == "新病人洗牙送 $150 Amazon 禮卡"
    assert "Tim Hortons 咖啡買一送一" not in titles


def test_share_text_is_ingested_and_counts_as_interest():
    c = client()
    r = c.post("/chat", json={"user_id": "s", "text": "https://example.invalid/x 星巴克 買一送一 到 12/31"}).json()
    assert "記下了" in r["reply"]
    deals_ = c.get("/deals").json()
    assert any(d["merchant_id"] == "starbucks" for d in deals_)
    assert profile.entity_affinity("s", "starbucks") > 0


def test_walled_link_asks_for_screenshot():
    r = client().post("/chat", json={"user_id": "w", "text": "http://xhslink.com/a/abc123"}).json()
    assert "截圖" in r["reply"]


def test_feedback_and_profile_patch_endpoints():
    ids = seed.seed_deals()
    c = client()
    assert c.post("/feedback", json={"user_id": "f", "deal_id": ids[0], "signal": "useful"}).status_code == 200
    assert c.post("/feedback", json={"user_id": "f", "deal_id": 9999, "signal": "useful"}).status_code == 404
    p = c.patch("/profile", params={"user_id": "f"}, json={
        "city": "Vancouver", "changes": [{"action": "like", "target": "Costco", "strength": 0.8}],
    }).json()
    assert p["city"] == "Vancouver" and p["likes"][0]["target"] in {"costco", "smile_dental_markham"}
    assert c.delete("/me", params={"user_id": "f"}).json()["ok"]


def test_new_user_gets_onboarding():
    r = client().post("/chat", json={"user_id": "n", "text": "嗨"}).json()
    assert "住哪個城市" in r["reply"]

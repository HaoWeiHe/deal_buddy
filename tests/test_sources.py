"""Xiaohongshu source against a fake xiaohongshu-mcp HTTP API."""
import httpx

from dealbuddy import db, deals, profile, sources


def fake_xhs():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/v1/login/status":
            return httpx.Response(200, json={"success": True, "data": {"is_logged_in": True}})
        if request.url.path == "/api/v1/feeds/search":
            return httpx.Response(200, json={"success": True, "data": {"feeds": [
                {"id": "n1", "xsecToken": "tok", "modelType": "note", "noteCard": {"displayTitle": "多伦多牙医"}},
                {"id": "n2", "xsecToken": "tok", "modelType": "note", "noteCard": {"displayTitle": "闲聊"}},
            ]}})
        if request.url.path == "/api/v1/feeds/detail":
            fid = __import__("json").loads(request.content)["feed_id"]
            note = {"n1": {"title": "多伦多新牙医", "desc": "新病人洗牙送 $100 Amazon 礼卡，到 12/31，Markham", "ipLocation": "加拿大"},
                    "n2": {"title": "今天天气好", "desc": "随便聊聊"}}[fid]
            return httpx.Response(200, json={"success": True, "data": {"feed_id": fid, "data": {"note": note}}})
        return httpx.Response(404)

    http = httpx.Client(base_url="http://xhs", transport=httpx.MockTransport(handler))
    return sources.XiaohongshuSource(base_url="http://xhs", http=http), calls


def test_xiaohongshu_search_ingests_deals_once():
    db.ensure_user("u", city="Toronto")
    profile.add_intent("u", "dental_cleaning", label="洗牙", categories=["healthcare"], keywords=["牙"])
    src, calls = fake_xhs()
    saved = sources.run_xiaohongshu(src)
    assert len(saved) == 1
    d = deals.get(saved[0]["deal_id"])
    assert d["mechanic"] == "gift_card" and d["url"].endswith("/explore/n1")
    assert d["sources"][0]["source_type"] == "xiaohongshu"
    # Second run: notes already seen, no more detail calls.
    n_detail = calls.count("/api/v1/feeds/detail")
    sources.run_xiaohongshu(src)
    assert calls.count("/api/v1/feeds/detail") == n_detail


def test_keywords_follow_city_and_intents():
    db.ensure_user("u", city="Toronto")
    profile.add_intent("u", "trip_vancouver", label="去溫哥華玩", destination="Vancouver")
    kws = sources.city_keywords(profile.user_metros("u"))
    assert "多伦多 羊毛" in kws and "温哥华 羊毛" in kws
    assert sources.intent_keywords("u") == ["温哥华 去溫哥華玩 优惠"]


def test_keywords_include_liked_merchants():
    db.ensure_user("u", city="Toronto")
    profile.set_belief("u", "entity", "tnt", 0.8)
    assert "多伦多 T&T 优惠" in sources.intent_keywords("u")

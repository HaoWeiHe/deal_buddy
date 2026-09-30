"""The spec §7 example table: which deals get pushed, answered on request, or suppressed."""
from dealbuddy import deals, ranking, seed
from dealbuddy.config import PUSH_THRESHOLD


def _scores():
    ids = seed.seed_deals()
    seed.seed_demo_user("u")
    out = {}
    for i in ids:
        d = deals.get(i)
        out[d["title"]] = ranking.score_deal("u", d)
    return out


def test_spec_example_outcomes():
    s = _scores()
    dental = s["新病人洗牙送 $150 Amazon 禮卡"]
    ramen = s["一風堂現金付款 8 折"]
    doordash = s["DoorDash 本週免運"]
    taiwan = s["T&T 消費滿 $100 抽長榮台灣來回機票"]
    tims = s["Tim Hortons 咖啡買一送一"]

    # 主動推
    assert dental.score >= PUSH_THRESHOLD
    assert ramen.score >= PUSH_THRESHOLD
    # 被問到時回答
    assert 0.2 <= doordash.score < PUSH_THRESHOLD
    assert taiwan.score < PUSH_THRESHOLD
    # 不推
    assert tims.score < 0.05
    assert dental.score > ramen.score > doordash.score > taiwan.score > tims.score


def test_intent_lifts_medium_interest_deal():
    s = _scores()
    dental = s["新病人洗牙送 $150 Amazon 禮卡"]
    assert dental.I > dental.P  # the current intent, not Amazon interest, carries it
    assert dental.matched_intent == "dental_cleaning"
    assert any("洗牙" in r for r in dental.reasons)


def test_disliked_merchant_is_downranked_and_explained():
    tims = _scores()["Tim Hortons 咖啡買一送一"]
    assert tims.F <= 0.12
    assert any("不喜歡" in r for r in tims.reasons)


def test_location_filter_and_trip_destination():
    from dealbuddy import profile

    seed.seed_deals()
    seed.seed_demo_user("u")
    titles = [s.deal["title"] for s in ranking.rank("u", limit=20)]
    assert "溫哥華列治文夜市門票半價" not in titles  # Richmond BC is not in Toronto
    assert "Uber Eats 新用戶 5 折" in titles  # online deals match anywhere

    profile.add_intent("u", "trip_vancouver", label="去溫哥華玩", categories=["travel", "dining"], destination="溫哥華")
    ranked = {s.deal["title"]: s for s in ranking.rank("u", limit=20)}
    assert "溫哥華列治文夜市門票半價" in ranked
    assert ranked["溫哥華列治文夜市門票半價"].I > 0


def test_discount_threshold_changes_attractiveness():
    from dealbuddy import db

    seed.seed_deals()
    seed.seed_demo_user("u")
    ramen = next(d for d in (deals.get(i) for i in range(1, 8)) if d and d["merchant_name"] == "Ippudo")
    before = ranking.score_deal("u", ramen).A
    db.update_user("u", discount_threshold=0.4)
    assert ranking.score_deal("u", ramen).A < before

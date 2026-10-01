"""Extraction, dedup, expiry, profile lifecycle, feedback and digest, all on the offline path."""
from datetime import timedelta

from dealbuddy import db, deals, digest, extract, feedback, profile, seed


def test_offline_extraction_of_a_shared_post():
    out = extract.extract_offline("Markham 新牙醫開幕！新病人洗牙送 $150 Amazon 禮卡，限有保險，到 10/31")
    d = out["deals"][0]
    assert d["mechanic"] == "gift_card"
    assert d["category"] == "healthcare"
    assert d["face_value"] == 150
    assert "Amazon" in d["entities"]
    assert d["valid_until"].endswith("-10-31")
    assert any("保險" in c for c in d["conditions"])
    assert "toronto" in [l.lower() for l in d["locations"]]


def test_chinese_discount_forms():
    assert extract._percent("全單 8 折") == 0.2
    assert extract._percent("85折") == 0.15
    assert extract._percent("20% off everything") == 0.2
    assert extract._mechanic("DoorDash 免運") == "free_delivery"
    assert extract._mechanic("咖啡買一送一") == "bogo"


def test_dedup_merges_sources_and_raises_confidence():
    base = {"title": "DoorDash 免運", "merchant": "DoorDash", "category": "delivery", "mechanic": "free_delivery",
            "valid_until": (deals.today() + timedelta(days=5)).isoformat(), "confidence": 0.7}
    a, merged_a = deals.save(base, source_type="user_share")
    b, merged_b = deals.save({**base, "title": "DD 本週免外送費", "merchant": "door dash"}, source_type="xiaohongshu")
    assert a == b and not merged_a and merged_b
    d = deals.get(a)
    assert d["confidence"] > 0.7
    assert len(d["sources"]) == 2


def test_expired_and_unverified_status():
    past = {"title": "old", "merchant": "X", "mechanic": "percent_off", "percent_off": 0.3,
            "valid_until": (deals.today() - timedelta(days=1)).isoformat()}
    i, _ = deals.save(past, source_type="seed")
    assert deals.get(i)["status"] == "expired"
    j, _ = deals.save({"title": "undated", "merchant": "Y", "mechanic": "bogo"}, source_type="seed")
    db.conn().execute("UPDATE deals SET updated_at=? WHERE id=?", (db.iso(db.now() - timedelta(days=20)), j))
    deals.refresh_statuses()
    assert deals.get(j)["status"] == "unverified"


def test_intent_lifecycle_and_sensitive_cleanup():
    db.ensure_user("u")
    profile.add_intent("u", "dental_cleaning", label="洗牙", sensitive=True, categories=["healthcare"])
    assert [i["target"] for i in profile.active_intents("u")] == ["dental_cleaning"]
    profile.close_intent("u", "dental_cleaning")
    assert profile.active_intents("u") == []
    assert profile.get_belief("u", "intent", "dental_cleaning") is None  # medical intent deleted, not kept

    profile.add_intent("u", "book_flight", label="訂機票", days=1)
    db.conn().execute("UPDATE user_beliefs SET expires_at=? WHERE target='book_flight'", (db.iso(db.now() - timedelta(hours=1)),))
    assert profile.active_intents("u") == []


def test_belief_decay_toward_default():
    db.ensure_user("u")
    profile.set_belief("u", "entity", "starbucks", 0.8)
    db.conn().execute("UPDATE user_beliefs SET updated_at=? WHERE target='starbucks'", (db.iso(db.now() - timedelta(days=180)),))
    assert abs(profile.entity_affinity("u", "starbucks") - 0.4) < 0.02


def test_feedback_rules():
    ids = seed.seed_deals()
    seed.seed_demo_user("u")
    doordash = next(i for i in ids if deals.get(i)["merchant_id"] == "doordash")
    before = profile.entity_affinity("u", "doordash")
    feedback.record("u", doordash, "useful")
    assert abs(profile.entity_affinity("u", "doordash") - (before + 0.1)) < 0.01

    dental = next(i for i in ids if deals.get(i)["category"] == "healthcare")
    feedback.record("u", dental, "used")
    assert all(i["target"] != "dental_cleaning" for i in profile.active_intents("u"))

    ubereats = next(i for i in ids if deals.get(i)["merchant_id"] == "ubereats")
    ctx_before = profile.context_affinity("u", "delivery")
    ent_before = profile.entity_affinity("u", "ubereats")
    feedback.record("u", ubereats, "not_interested", "category")
    assert profile.context_affinity("u", "delivery") < ctx_before
    assert profile.entity_affinity("u", "ubereats") == ent_before  # only the named dimension moves

    ramen = next(i for i in ids if deals.get(i)["merchant_id"] == "ippudo")
    feedback.record("u", ramen, "not_interested", "not_enough")
    assert (db.get_user("u")["discount_threshold"]) > 0.2


def test_why_log():
    db.ensure_user("u")
    profile.set_belief("u", "entity", "tim_hortons", -0.9, note="說不喜歡 Tim Hortons")
    assert profile.why("u", "entity", "tim_hortons")[0]["note"] == "說不喜歡 Tim Hortons"


def test_daily_digest_limits_and_skips_repeats():
    seed.seed_deals()
    seed.seed_demo_user("u")
    d = digest.build("u")
    assert d and 1 <= len(d["cards"]) <= 3
    assert "洗牙" not in d["text"].split("\n")[0]  # previews never show sensitive intents
    assert all(c["score"] >= 0.6 for c in d["cards"])
    again = digest.build("u")
    assert again is None or not ({c["deal_id"] for c in again["cards"]} & {c["deal_id"] for c in d["cards"]})


def test_digest_respects_pause_and_quiet_hours():
    seed.seed_deals()
    seed.seed_demo_user("u")
    db.update_user("u", push_paused=1)
    assert digest.build("u") is None
    assert digest.in_quiet_hours({"quiet_start": 22, "quiet_end": 9}, 23)
    assert digest.in_quiet_hours({"quiet_start": 22, "quiet_end": 9}, 3)
    assert not digest.in_quiet_hours({"quiet_start": 22, "quiet_end": 9}, 12)


def test_delete_me_removes_personal_data():
    seed.seed_deals()
    seed.seed_demo_user("u")
    db.delete_user("u")
    assert db.get_user("u") is None
    assert profile.beliefs("u") == []


def test_truncated_llm_extraction_says_why(monkeypatch):
    from types import SimpleNamespace as NS

    import pytest

    from dealbuddy import extract, llm

    resp = NS(stop_reason="max_tokens", content=[NS(type="text", text='{"deals": [{"title": "半')])
    monkeypatch.setattr(llm, "client", lambda: NS(messages=NS(create=lambda **kw: resp)))
    with pytest.raises(ValueError, match="max_tokens"):
        extract.extract_llm("很長的整理文")

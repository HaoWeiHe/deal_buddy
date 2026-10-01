"""The Claude agent loop, with the API replaced by a scripted fake (no network, no key)."""
from types import SimpleNamespace as NS

from dealbuddy import agent, chat, config, llm, profile, seed


def block(type_, **kw):
    return NS(type=type_, **kw)


def scripted(responses, seen):
    it = iter(responses)

    def fake_create(**kwargs):
        seen.append({**kwargs, "messages": list(kwargs["messages"])})  # the loop mutates the list
        return next(it)

    return fake_create


def test_agent_uses_tools_and_shows_cards(monkeypatch):
    seed.seed_deals()
    seed.seed_demo_user("u")
    seen = []
    responses = [
        NS(stop_reason="tool_use", content=[
            block("text", text="我查一下"),
            block("tool_use", id="t1", name="update_profile",
                  input={"action": "add_intent", "target": "trip_vancouver", "label": "去溫哥華玩",
                         "destination": "Vancouver", "categories": ["travel"], "confirmed": True}),
            block("tool_use", id="t2", name="search_deals", input={"limit": 3}),
        ]),
        NS(stop_reason="tool_use", content=[block("tool_use", id="t3", name="show_deals", input={"deal_ids": [1, 2]})]),
        NS(stop_reason="end_turn", content=[block("text", text="洗牙那個快到期了，趕快約！")]),
    ]
    monkeypatch.setattr(config.settings, "anthropic_api_key", "test")
    monkeypatch.setattr(llm, "create_agent_message", scripted(responses, seen))
    out = chat.handle("u", "我下個月要去溫哥華玩，最近有什麼好康？")

    assert out["reply"] == "洗牙那個快到期了，趕快約！"
    assert [c["deal_id"] for c in out["cards"]] == [1, 2]
    assert any(i["target"] == "trip_vancouver" for i in profile.active_intents("u"))
    # Both tool results went back in one user message, matched by id.
    results = seen[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"]
    # Stable prefix: system prompt cached, context block carried in the user turn.
    first = seen[0]
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "<context>" in first["messages"][-1]["content"][0]["text"]
    assert any(t.get("type") == "web_search_20260209" for t in first["tools"])


def test_agent_failure_falls_back_to_offline(monkeypatch):
    seed.seed_deals()
    monkeypatch.setattr(config.settings, "anthropic_api_key", "test")

    def boom(**_):
        raise RuntimeError("network down")

    monkeypatch.setattr(llm, "create_agent_message", boom)
    out = chat.handle("x", "最近有什麼外送優惠？")
    assert out["reply"]


def test_history_alternates_roles():
    from dealbuddy import db

    db.ensure_user("h")
    for role, text in [("assistant", "hi"), ("user", "a"), ("user", "b"), ("assistant", "c"), ("user", "d")]:
        db.add_message("h", role, text)
    msgs = agent._history("h")
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[0]["content"] == "a\nb"


def test_agent_stays_inside_the_time_budget(monkeypatch):
    seed.seed_deals()
    monkeypatch.setattr(config.settings, "anthropic_api_key", "test")
    monkeypatch.setattr(config.settings, "turn_seconds", 45)
    clock = iter([0, 0, 30, 40])  # start, step 1, step 2 (past half: no web search), step 3 (too late)
    monkeypatch.setattr(agent.time, "monotonic", lambda: next(clock))
    seen = []
    step = NS(stop_reason="tool_use", content=[block("tool_use", id="t", name="search_deals", input={"limit": 3})])
    monkeypatch.setattr(llm, "create_agent_message", scripted([step, step], seen))
    out = agent.respond("slow", "最近有什麼優惠")

    assert len(seen) == 2
    assert seen[0]["timeout"] == 45 and seen[1]["timeout"] == 15
    assert any(t.get("type") == "web_search_20260209" for t in seen[0]["tools"])
    assert not any(t.get("type") == "web_search_20260209" for t in seen[1]["tools"])
    assert out["reply"] == agent.i18n.t("timeout", None)

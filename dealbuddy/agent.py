"""The friend: a Claude tool-use agent (spec §8). Data and ranking come from tools, never from model memory."""
import json
import logging
import time

import anthropic

from . import db, deals, i18n, llm, profile, tools
from .config import settings

log = logging.getLogger(__name__)
MAX_STEPS = 10
# Don't start another model call with less time than this left in the turn.
MIN_STEP_SECONDS = 8

SYSTEM_PROMPT = """你是使用者的「好朋友」，一個很會薅羊毛、記得對方說過什麼的朋友。使用者會問你最近有什麼好康，你也會在看到他真的會在意的優惠時跟他說。
北極星：讓對方覺得「這本來就是我要做的事，現在剛好可以順便薅一下」。

怎麼做事：
- 優惠資料一律來自工具。回答前先用 search_deals 查優惠庫；結果不夠（少於 3 筆高分或完全沒有相關的）再用 web_search 即時搜尋。
  web_search 找到具體可用的優惠時，用 save_deal 存進優惠庫（附原文連結），存好後再 search_deals 一次拿到個人化排序和 deal_id。
- 決定要推薦哪些後，呼叫 show_deals 指定 deal_id（最多 5 個，最好的在前面），卡片和回饋按鈕會顯示在你的回覆下方。
- 每個推薦都說清楚：為什麼適合他（對上哪個興趣或最近要做的事）、值多少、何時到期、有什麼限制。這些資訊在卡片上也有，你的文字重點講「為什麼推給你」。
- 聽到使用者透露喜好、厭惡、常去的店、常用的平台、最近要做的事（洗牙、訂機票、搬家、去某城市玩）時，用 update_profile 記下來。
  使用者明確說的 confirmed=true；你從對話推測的 confirmed=false，而且要順口跟他確認一下。
  旅行計畫用 add_intent 並填 destination，這樣會開始推目的地的優惠。醫療相關意圖 sensitive=true。事情做完了用 close_intent。
- 使用者說「不喜歡 X」「這類不要再推」就立刻記下（dislike 或降低 context），不用再問。
- 使用者提到住哪個城市、推播太多太少、安靜時段，用 update_settings。
- 使用者問「你為什麼覺得我喜歡 X」時，用 get_profile 的 why_target 查來源再回答。
- 使用者說他用了某個優惠，就 record_feedback signal=used；問起省了多少，用 get_profile 的 savings_this_month 回答。
- 你不收錢推薦：排序只看適不適合他。不要因為任何商業理由推東西。

說話方式：
- 口語、簡短，像朋友傳訊息，不要條列一大串規格。會說「你上次說…」。
- 誠實：標了 uncertain 或 unverified 的優惠要說「這條我不確定還有沒有效，建議先確認」。沒有好優惠就直說沒有，不要硬湊。
- 分數低於 0.3 的優惠除非使用者直接問，不要推。對方討厭的商家不要推。
- 用使用者的語言回覆（context 裡的「使用者語言」，或他這則訊息用的語言）。使用者不一定懂中文：
  小紅書等中文來源的優惠要翻譯並摘要成他的語言，不要直接貼中文原文給不懂中文的人；商家名稱保留原文，並附上原文連結。
  對方換了慣用語言時用 update_settings 的 language 記下來（例如 en、zh-Hant、fr）。
- 小紅書、IG、Facebook 的連結你看不到內容時，請對方直接截圖給你。
"""

ONBOARDING_NOTE = """這是新朋友，還沒有任何認識。先自然地打招呼，然後分兩三次、每次一兩題地問（不要一次丟出全部）：
住在哪個城市、平常常吃什麼或常去哪幾家店、常用哪個外送平台、有沒有不喜歡的店、最近在忙什麼或準備做什麼（例如要看牙醫、要訂機票、要去哪裡玩）、喜歡哪種優惠（打折、免運、送禮卡、抽獎）。
每得到一個答案就用 update_profile / update_settings 記下；問完後 update_settings onboarded=true。如果對方一開口就在問優惠，先回答他的問題，再順口問一兩題。"""


def _context_block(user_id: str) -> str:
    user = db.get_user(user_id) or {}
    summary = profile.summary(user_id)
    lines = [
        f"今天：{deals.today().isoformat()}（{settings.timezone}）",
        f"使用者城市：{user.get('city') or '還不知道'}",
        f"使用者語言：{user.get('language') or 'zh-Hant'}",
    ]
    lines.append("你目前對他的認識：" + json.dumps(
        {k: summary[k] for k in ("likes", "dislikes", "contexts", "mechanics", "intents")}, ensure_ascii=False, default=str
    ))
    if profile.is_new_user(user_id):
        lines.append(ONBOARDING_NOTE)
    return "\n".join(lines)


def _history(user_id: str) -> list[dict]:
    msgs: list[dict] = []
    for m in db.recent_messages(user_id, limit=20):
        role = "assistant" if m["role"] == "assistant" else "user"
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"] += "\n" + m["content"]
        else:
            msgs.append({"role": role, "content": m["content"]})
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    return msgs


def _web_search_tool(user_id: str) -> dict:
    tool = {"type": "web_search_20260209", "name": "web_search", "max_uses": 3}
    city = (db.get_user(user_id) or {}).get("city")
    if city:
        tool["user_location"] = {"type": "approximate", "city": city, "timezone": settings.timezone}
    return tool


def respond(user_id: str, text: str, extra_note: str | None = None) -> dict:
    """Run one chat turn. `text` has already been stored in the messages table by the caller."""
    ctx = tools.ToolContext(user_id)
    lang = (db.get_user(user_id) or {}).get("language")
    messages = _history(user_id)
    turn_text = f"<context>\n{_context_block(user_id)}\n</context>"
    if extra_note:
        turn_text += f"\n<shared>\n{extra_note}\n</shared>"
    # The context goes in front of the newest user message so the system prompt and tools stay cacheable.
    if messages and messages[-1]["role"] == "user":
        messages[-1] = {"role": "user", "content": [
            {"type": "text", "text": turn_text}, {"type": "text", "text": messages[-1]["content"]},
        ]}
    else:
        messages.append({"role": "user", "content": [{"type": "text", "text": turn_text}, {"type": "text", "text": text}]})

    all_tools = [*tools.TOOL_DEFS, _web_search_tool(user_id)]
    reply = ""
    started = time.monotonic()
    for _ in range(MAX_STEPS):
        remaining = settings.turn_seconds - (time.monotonic() - started)
        if remaining < MIN_STEP_SECONDS:
            reply = i18n.t("timeout", lang)
            break
        # Web search is the slow part; past half the budget, answer from the deal pool only.
        step_tools = all_tools if remaining > settings.turn_seconds / 2 else list(tools.TOOL_DEFS)
        try:
            resp = llm.create_agent_message(
                model=settings.agent_model,
                max_tokens=8000,
                system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
                tools=step_tools,
                messages=messages,
                output_config={"effort": settings.agent_effort},
                timeout=remaining,
            )
        except anthropic.APITimeoutError:
            log.warning("agent turn ran out of time after %.0fs", time.monotonic() - started)
            reply = i18n.t("timeout", lang)
            break
        if resp.stop_reason == "refusal":
            reply = i18n.t("cant_help", lang)
            break
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason == "pause_turn":
            continue  # server-side web search wants another round
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if resp.stop_reason != "tool_use" or not tool_uses:
            reply = llm.text_of(resp)
            break
        results = []
        for b in tool_uses:
            try:
                out = tools.run_tool(ctx, b.name, dict(b.input or {}))
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": tools.dumps(out)})
            except Exception as e:  # report tool errors back to the model instead of failing the turn
                log.exception("tool %s failed", b.name)
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": f"error: {e}", "is_error": True})
        messages.append({"role": "user", "content": results})
    else:
        reply = reply or i18n.t("timeout", lang)
    return {"reply": reply or i18n.t("nothing", lang), "shown": ctx.shown}

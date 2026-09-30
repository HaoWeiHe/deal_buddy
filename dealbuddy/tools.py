"""Tools the friend agent can call (spec §8). Shared by the Claude agent and the offline fallback."""
import json
import threading

from . import db, entities, extract, feedback, profile, ranking
from .entities import CATEGORIES, MECHANICS

TOOL_DEFS = [
    {
        "name": "search_deals",
        "description": (
            "從優惠庫找候選優惠，回傳已經依這位使用者個人化排序的結果（含分數、理由、期限、條件）。"
            "回答任何優惠相關問題前都要先呼叫。可以用 category / merchant / keywords 縮小範圍；什麼都不給就是全部排序。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": CATEGORIES},
                "merchant": {"type": "string", "description": "商家或品牌名稱"},
                "keywords": {"type": "array", "items": {"type": "string"}, "description": "標題或描述裡要出現的關鍵字（中英文都可）"},
                "limit": {"type": "integer", "description": "最多幾筆，預設 5"},
            },
        },
    },
    {
        "name": "save_deal",
        "description": (
            "把一則優惠內容存進優惠庫：使用者貼上的優惠文字，或你用 web_search 找到的具體優惠。"
            "text 盡量完整（商家、內容、期限、條件、地區），url 放原文連結。回傳存好的 deal_id。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "url": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "update_profile",
        "description": (
            "更新你對使用者的認識。action：like / dislike（商家、品牌、菜系、地點）、set_context（常花錢的情境，target 是 category）、"
            "set_mechanic（偏好的優惠形式，target 是 mechanic）、add_intent（最近要做的事，例如洗牙、訂機票、去某城市旅行）、"
            "close_intent（事情做完了）、forget（刪掉一條）。使用者明確說的 confirmed=true；你自己推測的 confirmed=false，分數會被限制。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["like", "dislike", "set_context", "set_mechanic", "add_intent", "close_intent", "forget"]},
                "target": {"type": "string", "description": "商家名稱 / category / mechanic / 意圖 id（英文小寫底線，例如 dental_cleaning、trip_vancouver）"},
                "strength": {"type": "number", "description": "0 到 1，喜好或意圖強度；dislike 會自動變成負分"},
                "label": {"type": "string", "description": "add_intent：給人看的描述，例如「洗牙」「11 月去溫哥華玩」"},
                "days": {"type": "integer", "description": "add_intent：多少天後失效，預設 30"},
                "categories": {"type": "array", "items": {"type": "string", "enum": CATEGORIES}},
                "keywords": {"type": "array", "items": {"type": "string"}, "description": "add_intent：比對優惠用的關鍵字，中英文都放"},
                "destination": {"type": "string", "description": "add_intent：旅行目的地城市"},
                "sensitive": {"type": "boolean", "description": "醫療等敏感意圖設 true"},
                "confirmed": {"type": "boolean"},
                "layer": {"type": "string", "enum": ["entity", "context", "mechanic", "intent"], "description": "forget 時指定"},
            },
            "required": ["action", "target"],
        },
    },
    {
        "name": "get_profile",
        "description": "讀取「好朋友認識的你」：城市、喜好、厭惡、常花錢情境、偏好優惠形式、進行中的意圖、這個月靠用過的優惠估計省了多少。給 why_target 會回傳這條認識是怎麼來的。",
        "input_schema": {
            "type": "object",
            "properties": {"why_target": {"type": "string"}, "why_layer": {"type": "string", "enum": ["entity", "context", "mechanic", "intent"]}},
        },
    },
    {
        "name": "update_settings",
        "description": "更新使用者設定：常駐城市、慣用語言、稱呼、每天最多推幾則、安靜時段（0-23 點）、暫停主動推薦。",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "language": {"type": "string", "description": "使用者慣用語言代碼，例如 zh-Hant、en、fr"},
                "display_name": {"type": "string"},
                "daily_push_limit": {"type": "integer"},
                "quiet_start": {"type": "integer"},
                "quiet_end": {"type": "integer"},
                "push_paused": {"type": "boolean"},
                "onboarded": {"type": "boolean", "description": "冷啟動問答完成時設 true"},
            },
        },
    },
    {
        "name": "record_feedback",
        "description": "使用者對某則推薦的回饋：useful（有用）、saved（收藏）、used（已使用）、not_interested（不感興趣，可附 reason：merchant / category / mechanic / too_far / not_enough）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "deal_id": {"type": "integer"},
                "signal": {"type": "string", "enum": ["useful", "saved", "used", "not_interested"]},
                "reason": {"type": "string", "enum": ["merchant", "category", "mechanic", "too_far", "not_enough"]},
            },
            "required": ["deal_id", "signal"],
        },
    },
    {
        "name": "show_deals",
        "description": "回覆前呼叫：指定要在回覆下方顯示成卡片（附回饋按鈕）的 deal_id，依你推薦的順序，最多 5 個。沒有要推薦就不用呼叫。",
        "input_schema": {
            "type": "object",
            "properties": {"deal_ids": {"type": "array", "items": {"type": "integer"}}},
            "required": ["deal_ids"],
        },
    },
]


class ToolContext:
    """Per-turn state: which deals the agent chose to show."""

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.shown: list[int] = []


def _background(fn, *args) -> None:
    threading.Thread(target=fn, args=args, daemon=True).start()


def run_tool(ctx: ToolContext, name: str, args: dict) -> dict:
    uid = ctx.user_id
    if name == "search_deals":
        items = ranking.rank(
            uid, category=args.get("category"), merchant=args.get("merchant"),
            keywords=args.get("keywords"), limit=min(int(args.get("limit") or 5), 10),
        )
        return {"deals": [ranking.card(s) for s in items], "note": "score ≥ 0.6 是高價值；分數低的除非使用者直接問否則不用硬推"}

    if name == "save_deal":
        user = db.get_user(uid) or {}
        text = args["text"] + (f"\n{args['url']}" if args.get("url") else "")
        res = extract.ingest(text, user_id=uid, source_type="web_search" if args.get("url") else "user_share", city=user.get("city"))
        return res

    if name == "update_profile":
        return update_profile(uid, args)

    if name == "get_profile":
        from . import metrics

        out = profile.summary(uid)
        out["savings_this_month"] = metrics.savings(uid)
        if args.get("why_target"):
            layer = args.get("why_layer") or "entity"
            target = entities.lookup(args["why_target"]) or args["why_target"] if layer == "entity" else args["why_target"]
            out["why"] = profile.why(uid, layer, target)
        return out

    if name == "update_settings":
        fields = {k: v for k, v in args.items() if v is not None}
        for b in ("push_paused", "onboarded"):
            if b in fields:
                fields[b] = int(bool(fields[b]))
        db.update_user(uid, **fields)
        return {"ok": True, "user": {k: v for k, v in (db.get_user(uid) or {}).items() if k in db.USER_FIELDS}}

    if name == "record_feedback":
        return feedback.record(uid, int(args["deal_id"]), args["signal"], args.get("reason"))

    if name == "show_deals":
        ctx.shown = [int(i) for i in args.get("deal_ids", [])][:5]
        return {"ok": True}

    return {"error": f"unknown tool {name}"}


def update_profile(uid: str, args: dict) -> dict:
    action, target = args["action"], (args.get("target") or "").strip()
    source = "explicit" if args.get("confirmed", True) else "inferred"
    strength = args.get("strength")
    if action in ("like", "dislike"):
        eid = entities.resolve(target, "merchant")
        score = -abs(strength if strength is not None else 0.9) if action == "dislike" else abs(strength if strength is not None else 0.7)
        if action == "dislike" and source == "explicit":
            score = min(score, -0.9)  # spec §9: 明確說「我不喜歡 X」→ −0.9
        b = profile.set_belief(uid, "entity", eid, score, source=source, note=f"{action} {target}")
    elif action == "set_context":
        b = profile.set_belief(uid, "context", target, strength if strength is not None else 0.8, source=source, note=target)
    elif action == "set_mechanic":
        b = profile.set_belief(uid, "mechanic", target, strength if strength is not None else 0.8, source=source, note=target)
    elif action == "add_intent":
        ent_ids = [e for e in entities.find_in_text(" ".join([args.get("label") or "", *(args.get("keywords") or [])]))
                   if (entities.get(e) or {}).get("type") != "place"]
        b = profile.add_intent(
            uid, target, label=args.get("label") or target, strength=strength if strength is not None else 0.9,
            days=args.get("days"), categories=args.get("categories"), keywords=args.get("keywords"),
            entity_ids=ent_ids, destination=args.get("destination"), sensitive=bool(args.get("sensitive")),
            source=source,
        )
        from . import sources

        _background(sources.search_for_intent, uid)
    elif action == "close_intent":
        return {"ok": profile.close_intent(uid, target, reason="user said done")}
    elif action == "forget":
        layer = args.get("layer") or "entity"
        key = (entities.lookup(target) or target) if layer == "entity" else target
        profile.delete_belief(uid, layer, key)
        return {"ok": True}
    else:
        return {"error": f"unknown action {action}"}
    return {"ok": True, "belief": {"layer": b["layer"], "target": b["target"], "score": b["score"], "source": b["source"]}}


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)

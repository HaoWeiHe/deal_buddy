"""User-facing strings in Traditional Chinese and English. The app serves users who don't read Chinese too:
the Claude agent answers in any language, and fixed UI text falls back to English for non-Chinese users."""
import re

ZH, EN = "zh-Hant", "en"

STRINGS = {
    # recommendation reasons
    "r_dislike": {ZH: "你說過不喜歡 {name}，所以降權", EN: "you said you don't like {name}, so it's ranked down"},
    "r_trip": {ZH: "你{dest}的行程用得到", EN: "useful for your {dest} trip"},
    "r_intent": {ZH: "你最近要{label}", EN: "you're planning: {label}"},
    "r_like": {ZH: "你喜歡 {name}", EN: "you like {name}"},
    "r_context": {ZH: "你常在{cat}上花錢", EN: "you often spend on {cat}"},
    "r_mechanic": {ZH: "你對{mech}很有感", EN: "you love {mech} deals"},
    "r_today": {ZH: "今天到期", EN: "ends today"},
    "r_days": {ZH: "{n} 天後到期", EN: "ends in {n} days"},
    "r_default": {ZH: "條件不錯", EN: "solid deal"},
    # digest
    "digest_head": {ZH: "今天幫你挑了 {n} 個值得看的好康 🐑", EN: "{n} deals worth a look today 🐑"},
    "instant_head": {ZH: "有一個你可能會想馬上知道的好康 👀", EN: "Heads up, one deal you'll want to see now 👀"},
    "until": {ZH: "到 {date}", EN: "until {date}"},
    "days_left": {ZH: "（剩 {n} 天）", EN: " ({n} days left)"},
    "no_digest": {ZH: "今天沒有達標的好康，寧缺勿濫～", EN: "Nothing good enough today. Better none than noise!"},
    # cards / telegram
    "why": {ZH: "為什麼推給你：", EN: "Why you: "},
    "conditions": {ZH: "條件：", EN: "Conditions: "},
    "uncertain": {ZH: "⚠️ 這條我不確定還有沒有效，建議先確認", EN: "⚠️ Not sure this is still valid, double-check first"},
    "btn_useful": {ZH: "👍 有用", EN: "👍 Useful"},
    "btn_saved": {ZH: "⭐ 收藏", EN: "⭐ Save"},
    "btn_used": {ZH: "✅ 已用", EN: "✅ Used it"},
    "btn_not_interested": {ZH: "🙅 不感興趣", EN: "🙅 Not for me"},
    "btn_done": {ZH: "✓ 已記下", EN: "✓ Noted"},
    "ask_reason": {ZH: "可以說一下原因嗎？", EN: "Mind telling me why?"},
    "noted": {ZH: "記下了！", EN: "Noted!"},
    "reason_merchant": {ZH: "不喜歡這家", EN: "Don't like this place"},
    "reason_category": {ZH: "不需要這類", EN: "Don't need this kind"},
    "reason_too_far": {ZH: "太遠", EN: "Too far"},
    "reason_not_enough": {ZH: "折扣不夠", EN: "Discount too small"},
    "reason_none": {ZH: "就是不想要", EN: "Just not for me"},
    "me_summary": {
        ZH: "我認識的你：\n城市：{city}\n喜歡：{likes}\n不喜歡：{dislikes}\n最近要做：{intents}\n\n想改就直接跟我說，例如「我其實不討厭星巴克」。",
        EN: "What I know about you:\nCity: {city}\nLikes: {likes}\nDislikes: {dislikes}\nPlanning: {intents}\n\nJust tell me if anything's off, e.g. \"I actually like Starbucks\".",
    },
    "saved_month": {ZH: "\n這個月靠我推的優惠估計省了 ${amount:g}（用了 {n} 個）", EN: "\nThis month you've saved about ${amount:g} with {n} deal(s)"},
    "unknown": {ZH: "還不知道", EN: "not sure yet"},
    "forgot": {ZH: "你的資料都刪掉了。想重新開始就再跟我說話。", EN: "All your data is deleted. Say hi any time to start over."},
    # offline chat
    "onboarding": {
        ZH: "嗨！我是你的薅羊毛好朋友 🐑 先認識一下你：\n1. 你住哪個城市？\n2. 平常常吃哪幾家、常用哪個外送？\n3. 有沒有不喜歡的店？\n"
            "4. 最近有在準備做什麼嗎？（例如要洗牙、訂機票、去哪裡玩）\n直接用聊天的方式回我就好，例如「我住多倫多，常吃一風堂，不喜歡 Tim Hortons，下個月要去溫哥華玩」。",
        EN: "Hi! I'm your deal-hunting buddy 🐑 Let me get to know you:\n1. Which city do you live in?\n2. Where do you usually eat, and which delivery app do you use?\n"
            "3. Any places you don't like?\n4. Anything coming up? (a dental cleaning, booking flights, a trip)\n"
            "Just chat, e.g. \"I live in Toronto, love Ippudo, don't like Tim Hortons, going to Vancouver next month\".",
    },
    "picked": {ZH: "我幫你挑了這幾個：", EN: "Here's what I picked for you:"},
    "none_found": {
        ZH: "目前優惠庫裡沒有跟你對得上的，看到好的再跟你說！你也可以把小紅書或 IG 看到的優惠截圖丟給我。",
        EN: "Nothing that fits you right now. I'll tell you when something good shows up! You can also send me screenshots of deals you spot.",
    },
    "saved_n": {ZH: "收到！記下了 {n} 則優惠，到期前會提醒你。", EN: "Got it! Saved {n} deal(s), I'll remind you before they expire."},
    "need_screenshot": {ZH: "這個連結我讀不到內容，可以直接截圖給我嗎？", EN: "I can't open that link. Could you send me a screenshot instead?"},
    "no_deal_in_share": {ZH: "這則我沒看到具體的優惠耶。", EN: "I don't see a concrete deal in that one."},
    "said_city": {ZH: "記住了，你在{city}", EN: "got it, you're in {city}"},
    "said_dislike": {ZH: "好，{name} 以後不推了", EN: "OK, no more {name}"},
    "said_like": {ZH: "記下你喜歡 {name}", EN: "noted that you like {name}"},
    "said_intent": {ZH: "知道你最近要{label}，我會幫你盯著", EN: "I'll keep an eye out for {label}"},
    "said_intent_done": {ZH: "{label}搞定了，這類我先不盯了", EN: "{label} done, I'll stop watching for it"},
    "said_trip": {ZH: "去{dest}的話我也會順便找當地的優惠", EN: "I'll look for local deals in {dest} too"},
    "uncertain_short": {ZH: "（這條我不確定還有沒有效，建議先確認）", EN: " (not sure it's still valid, double-check)"},
    "ok": {ZH: "收到！", EN: "Got it!"},
    "sep": {ZH: "，", EN: ", "},
    "list_sep": {ZH: "、", EN: ", "},
    "period": {ZH: "。", EN: "."},
    "cant_help": {ZH: "這個我沒辦法幫忙，換個問題問我吧！", EN: "I can't help with that one, ask me something else!"},
    "nothing": {ZH: "嗯…我這次沒找到值得跟你說的。", EN: "Hmm, nothing worth telling you this time."},
    "timeout": {ZH: "我查了一圈還沒整理好，等等再問我一次？", EN: "Still pulling this together, ask me again in a bit?"},
    "error": {ZH: "糟糕，出錯了", EN: "Oops, something went wrong"},
}

CATEGORY = {
    "dining": ("外食", "dining out"), "delivery": ("外送", "delivery"), "grocery": ("買菜", "groceries"),
    "cafe": ("咖啡飲料", "coffee & drinks"), "healthcare": ("醫療保健", "health care"), "beauty": ("美容", "beauty"),
    "shopping": ("購物", "shopping"), "electronics": ("3C", "electronics"), "travel": ("旅遊", "travel"),
    "entertainment": ("娛樂", "entertainment"), "transport": ("交通", "transport"), "finance": ("金融", "finance"),
    "home": ("居家", "home"), "other": ("其他", "other"),
}
MECHANIC = {
    "percent_off": ("打折", "% off"), "amount_off": ("現折", "$ off"), "free_delivery": ("免運", "free delivery"),
    "gift_card": ("送禮卡", "gift card"), "bogo": ("買一送一", "BOGO"), "cashback": ("返現", "cashback"),
    "sweepstakes": ("抽獎", "sweepstakes"), "free_item": ("送贈品", "free item"), "points": ("點數", "points"),
    "other": ("優惠", "deal"),
}


def norm_lang(lang: str | None) -> str:
    return ZH if (lang or ZH).lower().startswith("zh") else EN


def t(key: str, lang: str | None, **kw) -> str:
    return STRINGS[key][norm_lang(lang)].format(**kw)


def category(cat: str | None, lang: str | None) -> str:
    zh, en = CATEGORY.get(cat or "other", CATEGORY["other"])
    return zh if norm_lang(lang) == ZH else en


def mechanic(mech: str | None, lang: str | None) -> str:
    zh, en = MECHANIC.get(mech or "other", MECHANIC["other"])
    return zh if norm_lang(lang) == ZH else en


_CJK = re.compile(r"[㐀-鿿豈-﫿]")
_LATIN_WORD = re.compile(r"[A-Za-z]{2,}")


def detect(text: str) -> str | None:
    """Confident guesses only: CJK text → Chinese; several Latin words and no CJK → English."""
    text = re.sub(r"https?://\S+", " ", text or "")
    cjk = len(_CJK.findall(text))
    words = len(_LATIN_WORD.findall(text))
    if cjk >= 2:
        return ZH
    if cjk == 0 and words >= 3:
        return EN
    return None

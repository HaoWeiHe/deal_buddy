"""Turn shared content (text, links, screenshots) into Deal records (spec §5).
LLM path: Claude Haiku 4.5 with a JSON schema. Offline path: keyword/regex heuristics."""
import calendar
import json
import logging
import re
from datetime import date
from html import unescape

import httpx

from . import deals, entities, llm
from .config import settings
from .entities import CATEGORIES, MECHANICS
from .lexicon import CATEGORY_KEYWORDS, MECHANIC_KEYWORDS

log = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://[^\s<>\"'，。！）)]+")
# Platforms that need a login / block scraping: we never crawl them, we only read what the user shared.
WALLED_DOMAINS = ("xiaohongshu.com", "xhslink.com", "instagram.com", "facebook.com", "fb.com", "fb.watch", "threads.net")

ENTITY_DEFAULT_CATEGORY = {
    "doordash": "delivery", "ubereats": "delivery", "skip": "delivery", "tnt": "grocery", "loblaws": "grocery",
    "nofrills": "grocery", "costco": "grocery", "tim_hortons": "cafe", "starbucks": "cafe", "sephora": "beauty",
    "best_buy": "electronics", "eva_air": "travel", "china_airlines": "travel", "air_canada": "travel",
    "mcdonalds": "dining", "shoppers": "shopping", "walmart": "shopping", "amazon": "shopping",
}

_nullable = lambda t: {"anyOf": [{"type": t}, {"type": "null"}]}  # noqa: E731

DEAL_SCHEMA = {
    "type": "object",
    "properties": {
        "deals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "短標題，繁體中文，例如「新病人洗牙送 $150 Amazon 禮卡」"},
                    "description": {"type": "string", "description": "一兩句話說明優惠內容，繁體中文"},
                    "title_en": {"type": "string", "description": "English title"},
                    "description_en": {"type": "string", "description": "One or two English sentences summarizing the deal"},
                    "conditions_en": {"type": "array", "items": {"type": "string"}},
                    "merchant": _nullable("string"),
                    "entities": {"type": "array", "items": {"type": "string"},
                                 "description": "其他相關品牌/平台/地點，例如禮卡品牌 Amazon、外送平台"},
                    "category": {"type": "string", "enum": CATEGORIES},
                    "mechanic": {"type": "string", "enum": MECHANICS},
                    "percent_off": _nullable("number"),
                    "face_value": _nullable("number"),
                    "est_value": _nullable("number"),
                    "conditions": {"type": "array", "items": {"type": "string"}},
                    "locations": {"type": "array", "items": {"type": "string"}},
                    "valid_from": _nullable("string"),
                    "valid_until": _nullable("string"),
                    "url": _nullable("string"),
                    "confidence": {"type": "number"},
                    "uncertain_fields": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "description", "title_en", "description_en", "conditions_en", "merchant", "entities", "category", "mechanic", "percent_off",
                             "face_value", "est_value", "conditions", "locations", "valid_from", "valid_until", "url",
                             "confidence", "uncertain_fields"],
                "additionalProperties": False,
            },
        },
        "needs_screenshot": {"type": "boolean"},
    },
    "required": ["deals", "needs_screenshot"],
    "additionalProperties": False,
}

EXTRACT_PROMPT = """你是優惠資訊抽取器。從使用者分享的內容中找出所有「具體、可使用」的優惠，輸出 JSON。
今天是 {today}。使用者所在城市：{city}。

規則：
- 只抽真的優惠（折扣、免運、送禮卡、買一送一、返現、抽獎、贈品、點數）。一般心得、廣告但沒有優惠內容就回空陣列。
- percent_off 用 0 到 1 的小數（8 折 = 0.2，買一送一不用填）。face_value 是面額（美元/加幣數字），est_value 是對一般人的預估現金價值（抽獎要乘上中獎機率，通常很低）。
- 日期一律轉成 YYYY-MM-DD；「月底」「本週日」這類相對日期依今天換算。沒寫就填 null。
- conditions 列出門檻與限制（限新客、需保險、滿 $50、限 app 下單…），每條一句。
- locations 寫城市或區域（例如 Toronto、Markham、Online）；分店地址只寫城市。
- merchant 是提供優惠的商家；entities 放其他相關品牌（例如禮卡是 Amazon 就放 Amazon）。保留原本的品牌寫法。
- 使用者不一定懂中文：title / description / conditions 用繁體中文，title_en / description_en / conditions_en 是對應的英文翻譯與摘要（原文是英文就照寫）。
  原文是小紅書的簡體中文心得時，description 要摘要重點，不要照抄口語。
- 不確定的欄位名稱放進 uncertain_fields，confidence 是整體可信度（0 到 1）。
- 內容看起來是被截斷的連結或只有標題、看不出優惠細節時，needs_screenshot 設為 true。
"""


def find_urls(text: str) -> list[str]:
    return URL_RE.findall(text or "")


def is_walled(url: str) -> bool:
    return any(d in url for d in WALLED_DOMAINS)


def fetch_page_text(url: str, max_chars: int = 8000) -> str | None:
    """Fetch a public page's visible text. Walled platforms are skipped on purpose (spec §4)."""
    if is_walled(url):
        return None
    try:
        r = httpx.get(url, timeout=10, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 DealBuddy/0.1"})
        r.raise_for_status()
    except httpx.HTTPError as e:
        log.info("fetch failed for %s: %s", url, e)
        return None
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", r.text)
    text = unescape(re.sub(r"(?s)<[^>]+>", " ", html))
    return re.sub(r"\s+", " ", text).strip()[:max_chars]


# ---- LLM path --------------------------------------------------------------

def extract_llm(text: str, *, image_b64: str | None = None, image_type: str = "image/jpeg",
                page_text: str | None = None, city: str | None = None) -> dict:
    content: list[dict] = []
    if image_b64:
        content.append({"type": "image", "source": {"type": "base64", "media_type": image_type, "data": image_b64}})
    body = f"使用者分享的內容：\n{text or '(只有圖片)'}"
    if page_text:
        body += f"\n\n連結頁面文字：\n{page_text}"
    content.append({"type": "text", "text": body})
    resp = llm.client().messages.create(
        model=settings.extract_model,
        max_tokens=4000,
        system=EXTRACT_PROMPT.format(today=deals.today().isoformat(), city=city or "未知"),
        messages=[{"role": "user", "content": content}],
        output_config={"format": {"type": "json_schema", "schema": DEAL_SCHEMA}},
    )
    if resp.stop_reason == "refusal":
        return {"deals": [], "needs_screenshot": False}
    return json.loads(llm.text_of(resp))


# ---- offline heuristics ------------------------------------------------------

def _parse_date(text: str) -> str | None:
    t = deals.today()
    m = re.search(r"(20\d\d)[-/.年](\d{1,2})[-/.月](\d{1,2})", text)
    if m:
        y, mo, d = map(int, m.groups())
        return _safe_date(y, mo, d)
    m = re.search(r"(?:到|至|截止|until|ends?|through|thru|valid till|till)\s*:?\s*(\d{1,2})[/月.](\d{1,2})", text, re.I)
    if not m:
        m = re.search(r"(\d{1,2})月(\d{1,2})[日號号]", text)
    if m:
        mo, d = map(int, m.groups())
        y = t.year if (mo, d) >= (t.month, t.day) else t.year + 1
        return _safe_date(y, mo, d)
    if "月底" in text or "end of the month" in text.lower() or "end of month" in text.lower():
        return date(t.year, t.month, calendar.monthrange(t.year, t.month)[1]).isoformat()
    months = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
    m = re.search(r"(?:until|ends?|through|by|till)\s+([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})", text, re.I)
    if m and m.group(1).lower() in months:
        mo, d = months[m.group(1).lower()], int(m.group(2))
        y = t.year if (mo, d) >= (t.month, t.day) else t.year + 1
        return _safe_date(y, mo, d)
    return None


def _safe_date(y, mo, d) -> str | None:
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def _mechanic(text: str) -> str:
    low = text.lower()
    for mech in ("free_delivery", "gift_card", "bogo", "sweepstakes", "cashback", "points"):
        if any(k in low for k in MECHANIC_KEYWORDS[mech]):
            return mech
    if _percent(text):
        return "percent_off"
    if re.search(r"\$\s?\d+\s*off|減\s?\$?\d+|减\s?\$?\d+|省\s?\$\d+|現折|现折", low):
        return "amount_off"
    if any(k in low for k in MECHANIC_KEYWORDS["free_item"]):
        return "free_item"
    return "other"


def _percent(text: str) -> float | None:
    m = re.search(r"(\d{1,2})\s*%\s*(?:off|折扣|優惠|优惠|off)?", text, re.I)
    if m:
        return int(m.group(1)) / 100
    m = re.search(r"(?<![\d$])([1-9])\s*([0-9])?\s*折", text)
    if m:
        a, b = m.group(1), m.group(2)
        paid = int(a + b) / 100 if b else int(a) / 10
        return round(1 - paid, 2)
    return None


def _dollar(text: str) -> float | None:
    m = re.search(r"\$\s?(\d+(?:\.\d+)?)", text)
    return float(m.group(1)) if m else None


def _category(text: str, entity_ids: list[str]) -> str:
    low = text.lower()
    best, hits = "other", 0
    for cat, words in CATEGORY_KEYWORDS.items():
        n = sum(1 for w in words if w in low)
        if n > hits:
            best, hits = cat, n
    if best == "other":
        for e in entity_ids:
            if e in ENTITY_DEFAULT_CATEGORY:
                return ENTITY_DEFAULT_CATEGORY[e]
    return best


CONDITION_MARKERS = ["限", "需", "須", "滿", "满", "new patient", "new customer", "minimum", "min.", "min order",
                     "requires", "only", "first order", "首單", "首单", "新客", "新病人", "保險", "保险", "insurance"]


def _conditions(text: str) -> list[str]:
    parts = re.split(r"[。；;\n！!]|(?<=[，,])", text)
    out = []
    for p in parts:
        p = p.strip(" ，,")
        if p and len(p) <= 60 and any(k in p.lower() for k in CONDITION_MARKERS):
            out.append(p)
    return out[:5]


def extract_offline(text: str) -> dict:
    if not text or not text.strip():
        return {"deals": [], "needs_screenshot": True}
    ids = entities.find_in_text(text)
    places = [e for e in ids if (entities.get(e) or {}).get("type") == "place"]
    brands = [e for e in ids if e not in places]
    mechanic = _mechanic(text)
    if mechanic == "other" and _dollar(text) is None:
        return {"deals": [], "needs_screenshot": bool(find_urls(text))}
    merchant = None
    # A brand named right before 禮卡 / gift card is what you receive, not who offers the deal.
    gift_brands = set()
    if mechanic == "gift_card":
        low = text.lower()
        for e in brands:
            for a in [entities.norm(entities.display_name(e)), *(entities.get(e) or {}).get("aliases", [])]:
                if re.search(re.escape(a) + r"\s*(禮卡|礼卡|禮品卡|gift ?card)", low):
                    gift_brands.add(e)
    for e in brands:
        if e not in gift_brands:
            merchant = entities.display_name(e)
            break
    others = [entities.display_name(e) for e in ids if entities.display_name(e) != merchant]
    pct = _percent(text) if mechanic in ("percent_off", "other") else None
    dollars = _dollar(text)
    first_line = re.sub(URL_RE, "", text.strip().splitlines()[0]).strip()[:60] or "分享的優惠"
    locs = [m for m in entities.metros_in_text(text)]
    if any(w in text.lower() for w in ("online", "線上", "线上", "app")):
        locs.append("Online")
    urls = find_urls(text)
    return {
        "deals": [{
            "title": first_line,
            "description": text.strip()[:500],
            "merchant": merchant,
            "entities": others,
            "category": _category(text, ids),
            "mechanic": mechanic if not (mechanic == "other" and pct) else "percent_off",
            "percent_off": pct,
            "face_value": dollars if mechanic in ("gift_card", "amount_off", "cashback") else None,
            "est_value": dollars if mechanic in ("gift_card", "amount_off", "cashback", "free_item") else None,
            "conditions": _conditions(text),
            "locations": locs,
            "valid_from": None,
            "valid_until": _parse_date(text),
            "url": urls[0] if urls else None,
            "confidence": 0.55,
            "uncertain_fields": ["offline_extraction"],
        }],
        "needs_screenshot": False,
    }


# ---- entry point ---------------------------------------------------------------

def ingest(text: str = "", *, user_id: str | None = None, image_b64: str | None = None,
           image_type: str = "image/jpeg", source_type: str = "user_share", city: str | None = None) -> dict:
    """Extract deals from shared content and store them. Returns a summary the chat layer can talk about."""
    urls = find_urls(text)
    page_text = None
    if urls and not image_b64:
        page_text = fetch_page_text(urls[0])
    if settings.llm_enabled:
        try:
            result = extract_llm(text, image_b64=image_b64, image_type=image_type, page_text=page_text, city=city)
        except Exception as e:  # network/API failure should not lose the share
            log.exception("LLM extraction failed, using offline extractor: %s", e)
            result = extract_offline(text + "\n" + (page_text or ""))
    else:
        result = extract_offline(text + ("\n" + page_text if page_text else ""))
    saved = []
    for d in result.get("deals", []):
        if not d.get("url") and urls:
            d["url"] = urls[0]
        deal_id, merged = deals.save(d, source_type=source_type, url=d.get("url"), raw=text, shared_by=user_id)
        saved.append({"deal_id": deal_id, "merged": merged, **{k: d.get(k) for k in ("title", "merchant", "mechanic", "category", "valid_until")}})
    walled = any(is_walled(u) for u in urls)
    return {
        "saved": saved,
        "needs_screenshot": bool(result.get("needs_screenshot")) or (walled and not saved and not image_b64),
    }

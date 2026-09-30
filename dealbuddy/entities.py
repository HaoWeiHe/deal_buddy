"""Entity normalization (spec §5 rule 4): different spellings of a merchant/brand/place map to one id."""
import json
import re
import unicodedata

from . import db

CATEGORIES = [
    "dining", "delivery", "grocery", "cafe", "healthcare", "beauty", "shopping",
    "electronics", "travel", "entertainment", "transport", "finance", "home", "other",
]

MECHANICS = [
    "percent_off", "amount_off", "free_delivery", "gift_card", "bogo", "cashback",
    "sweepstakes", "free_item", "points", "other",
]

# (id, type, display name, aliases). Aliases cover English, 繁體 and 简体 spellings.
SEED_ENTITIES = [
    ("tim_hortons", "merchant", "Tim Hortons", ["tim hortons", "timhortons", "tims", "tim's", "提姆", "提姆霍顿", "提姆霍頓"]),
    ("tnt", "merchant", "T&T 大統華", ["t&t", "t & t", "tnt", "t&t supermarket", "大統華", "大统华"]),
    ("doordash", "platform", "DoorDash", ["doordash", "door dash", "dd外送"]),
    ("ubereats", "platform", "Uber Eats", ["uber eats", "ubereats", "uber eat"]),
    ("skip", "platform", "SkipTheDishes", ["skipthedishes", "skip the dishes", "skip"]),
    ("amazon", "brand", "Amazon", ["amazon", "亞馬遜", "亚马逊"]),
    ("costco", "merchant", "Costco", ["costco", "好市多", "开市客", "開市客"]),
    ("starbucks", "merchant", "Starbucks", ["starbucks", "星巴克"]),
    ("shoppers", "merchant", "Shoppers Drug Mart", ["shoppers", "shoppers drug mart", "sdm"]),
    ("loblaws", "merchant", "Loblaws", ["loblaws", "loblaw"]),
    ("nofrills", "merchant", "No Frills", ["no frills", "nofrills"]),
    ("walmart", "merchant", "Walmart", ["walmart", "沃爾瑪", "沃尔玛"]),
    ("mcdonalds", "merchant", "McDonald's", ["mcdonald's", "mcdonalds", "麥當勞", "麦当劳"]),
    ("eva_air", "brand", "EVA Air 長榮航空", ["eva air", "長榮", "长荣", "長榮航空", "长荣航空"]),
    ("china_airlines", "brand", "China Airlines 華航", ["china airlines", "華航", "华航", "中華航空"]),
    ("air_canada", "brand", "Air Canada", ["air canada", "加航"]),
    ("sephora", "merchant", "Sephora", ["sephora", "絲芙蘭", "丝芙兰"]),
    ("best_buy", "merchant", "Best Buy", ["best buy", "bestbuy"]),
    ("taiwan", "place", "台灣", ["taiwan", "台灣", "台湾", "臺灣", "taipei", "台北", "臺北"]),
    ("japan", "place", "日本", ["japan", "日本", "tokyo", "東京", "东京", "osaka", "大阪"]),
]

# Cities and the metro area they belong to. Deals tagged with any name in a metro match users in that metro.
METROS = {
    "toronto": ["toronto", "多倫多", "多伦多", "gta", "大多倫多", "大多伦多", "markham", "萬錦", "万锦",
                "richmond hill", "列治文山", "north york", "北約克", "scarborough", "士嘉堡", "mississauga",
                "密西沙加", "vaughan", "旺市", "etobicoke", "oakville", "brampton", "pickering", "ajax"],
    "vancouver": ["vancouver", "溫哥華", "温哥华", "richmond", "列治文", "burnaby", "本拿比", "surrey", "coquitlam"],
    "montreal": ["montreal", "蒙特婁", "蒙特利尔"],
    "calgary": ["calgary", "卡加利", "卡尔加里"],
    "new_york": ["new york", "nyc", "紐約", "纽约", "flushing", "法拉盛", "manhattan", "brooklyn", "queens"],
    "seattle": ["seattle", "西雅圖", "西雅图", "bellevue"],
    "bay_area": ["san francisco", "舊金山", "旧金山", "bay area", "灣區", "湾区", "san jose", "cupertino"],
    "los_angeles": ["los angeles", "洛杉磯", "洛杉矶", "la", "irvine", "arcadia"],
    "taipei": ["taipei", "台北", "臺北", "新北"],
    "tokyo": ["tokyo", "東京", "东京"],
    "osaka": ["osaka", "大阪"],
}
ONLINE_WORDS = {"online", "線上", "线上", "全國", "全国", "nationwide", "canada-wide", "全加"}


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower().strip()
    return re.sub(r"[\s'’`\-_.]+", " ", text).strip()


def seed_entities() -> None:
    c = db.conn()
    for eid, etype, name, aliases in SEED_ENTITIES:
        c.execute(
            "INSERT OR IGNORE INTO entities (id, type, name, aliases) VALUES (?, ?, ?, ?)",
            (eid, etype, name, json.dumps([norm(a) for a in aliases], ensure_ascii=False)),
        )
    c.commit()


def _all() -> list[dict]:
    rows = db.conn().execute("SELECT * FROM entities").fetchall()
    return [db.row_to_dict(r, ("aliases",)) for r in rows]


def get(entity_id: str) -> dict | None:
    return db.row_to_dict(db.conn().execute("SELECT * FROM entities WHERE id=?", (entity_id,)).fetchone(), ("aliases",))


def display_name(entity_id: str | None) -> str:
    if not entity_id:
        return ""
    e = get(entity_id)
    return e["name"] if e else entity_id


def lookup(name: str) -> str | None:
    n = norm(name)
    if not n:
        return None
    for e in _all():
        if n == e["id"] or n == norm(e["name"]) or n in e["aliases"]:
            return e["id"]
    return None


def resolve(name: str, etype: str = "merchant") -> str | None:
    """Return the entity id for a name, creating a new entity when we have never seen it."""
    if not name or not name.strip():
        return None
    found = lookup(name)
    if found:
        return found
    n = norm(name)
    eid = re.sub(r"[^\w]+", "_", n).strip("_")[:60] or "entity"
    base, i = eid, 2
    while get(eid):
        eid, i = f"{base}_{i}", i + 1
    db.conn().execute(
        "INSERT INTO entities (id, type, name, aliases) VALUES (?, ?, ?, ?)",
        (eid, etype, name.strip(), json.dumps([n], ensure_ascii=False)),
    )
    db.conn().commit()
    return eid


def add_alias(entity_id: str, alias: str) -> None:
    e = get(entity_id)
    if not e:
        return
    a = norm(alias)
    if a and a not in e["aliases"]:
        e["aliases"].append(a)
        db.conn().execute(
            "UPDATE entities SET aliases=? WHERE id=?", (json.dumps(e["aliases"], ensure_ascii=False), entity_id)
        )
        db.conn().commit()


def find_in_text(text: str) -> list[str]:
    """Entity ids whose name or alias appears in free text (used by the offline extractor and NLU)."""
    t = norm(text)
    hits: list[tuple[int, str]] = []
    for e in _all():
        for a in [norm(e["name"]), *e["aliases"]]:
            if len(a) < 2:
                continue
            # Short latin aliases ("la", "skip", "tims") must match as whole words.
            if re.fullmatch(r"[a-z0-9 &]+", a):
                if re.search(rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])", t):
                    hits.append((len(a), e["id"]))
                    break
            elif a in t:
                hits.append((len(a), e["id"]))
                break
    hits.sort(reverse=True)
    seen: list[str] = []
    for _, eid in hits:
        if eid not in seen:
            seen.append(eid)
    return seen


def metro_of(place: str | None) -> str | None:
    n = norm(place or "")
    if not n:
        return None
    for metro, names in METROS.items():
        if n == metro or n in [norm(x) for x in names]:
            return metro
    return n


def metros_in_text(text: str) -> list[str]:
    t = norm(text)
    found = []
    for metro, names in METROS.items():
        for name in names:
            nn = norm(name)
            if re.fullmatch(r"[a-z ]+", nn):
                hit = re.search(rf"(?<![a-z]){re.escape(nn)}(?![a-z])", t) and len(nn) > 2
            else:
                hit = nn in t
            if hit:
                found.append(metro)
                break
    return found


def is_online(location: str) -> bool:
    return norm(location) in ONLINE_WORDS

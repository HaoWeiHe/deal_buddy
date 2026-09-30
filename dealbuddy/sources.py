"""Deal discovery from outside sources (spec §4):
- 小紅書: community xiaohongshu-mcp server (HTTP API), dedicated account, low-frequency keyword search.
- Instagram: official Graph API hashtag search (Business/Creator account).
- Public RSS feeds (e.g. deal forums).
Facebook has no usable channel; it only reaches us through user shares.

Run periodically:  python -m dealbuddy.sources
"""
import logging
import time
import xml.etree.ElementTree as ET

import httpx

from . import db, entities, extract, profile
from .config import settings

log = logging.getLogger(__name__)

# Xiaohongshu content is mostly Simplified Chinese, so search in Simplified.
METRO_SEARCH_NAME = {
    "toronto": "多伦多", "vancouver": "温哥华", "montreal": "蒙特利尔", "calgary": "卡尔加里",
    "new_york": "纽约", "seattle": "西雅图", "bay_area": "湾区", "los_angeles": "洛杉矶",
    "taipei": "台北", "tokyo": "东京", "osaka": "大阪",
}
CITY_KEYWORD_TEMPLATES = ["{city} 羊毛", "{city} 优惠", "{city} 折扣 活动"]


def _seen(source: str, item_id: str) -> bool:
    return db.conn().execute(
        "SELECT 1 FROM source_seen WHERE source=? AND item_id=?", (source, item_id)
    ).fetchone() is not None


def _mark_seen(source: str, item_id: str) -> None:
    db.conn().execute(
        "INSERT OR IGNORE INTO source_seen (source, item_id, seen_at) VALUES (?, ?, ?)", (source, item_id, db.iso())
    )
    db.conn().commit()


# ---- Xiaohongshu -------------------------------------------------------------

class XiaohongshuSource:
    name = "xiaohongshu"

    def __init__(self, base_url: str | None = None, token: str | None = None, http: httpx.Client | None = None):
        self.base_url = (base_url or settings.xhs_url).rstrip("/")
        headers = {"Authorization": f"Bearer {token or settings.xhs_token}"} if (token or settings.xhs_token) else {}
        self.http = http or httpx.Client(base_url=self.base_url, headers=headers, timeout=90)

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    def _data(self, resp: httpx.Response) -> dict:
        resp.raise_for_status()
        body = resp.json()
        if not body.get("success", True):
            raise RuntimeError(body.get("error") or body.get("message") or "xiaohongshu-mcp error")
        return body.get("data") or {}

    def logged_in(self) -> bool:
        try:
            data = self._data(self.http.get("/api/v1/login/status"))
        except Exception as e:
            log.warning("xiaohongshu-mcp not reachable: %s", e)
            return False
        return bool(data.get("is_logged_in", data.get("isLoggedIn", True)))

    def search(self, keyword: str, publish_time: str = "一周内") -> list[dict]:
        data = self._data(self.http.post("/api/v1/feeds/search", json={
            "keyword": keyword, "filters": {"sort_by": "综合", "publish_time": publish_time},
        }))
        return data.get("feeds") or []

    def recommended(self) -> list[dict]:
        return self._data(self.http.get("/api/v1/feeds/list")).get("feeds") or []

    def detail(self, feed_id: str, xsec_token: str) -> dict:
        data = self._data(self.http.post("/api/v1/feeds/detail", json={
            "feed_id": feed_id, "xsec_token": xsec_token, "load_all_comments": False,
        }))
        return (data.get("data") or {}).get("note") or {}

    def ingest_feeds(self, feeds: list[dict], budget: int, city: str | None = None) -> tuple[list[dict], int]:
        """Read up to `budget` unseen notes. Returns (saved deals, detail calls used)."""
        saved, used = [], 0
        for f in feeds:
            if budget <= 0:
                break
            fid, token = f.get("id"), f.get("xsecToken")
            if not fid or not token or f.get("modelType", "note") != "note" or _seen(self.name, fid):
                continue
            try:
                note = self.detail(fid, token)
            except Exception as e:
                log.warning("xiaohongshu detail failed for %s: %s", fid, e)
                continue
            finally:
                budget -= 1
                used += 1
                time.sleep(settings.xhs_delay_seconds)  # stay low-frequency to protect the account
            _mark_seen(self.name, fid)
            text = f"{note.get('title') or (f.get('noteCard') or {}).get('displayTitle') or ''}\n{note.get('desc') or ''}"
            if note.get("ipLocation"):
                text += f"\n(發文地區：{note['ipLocation']})"
            url = f"https://www.xiaohongshu.com/explore/{fid}"
            result = extract.ingest(f"{text}\n{url}", source_type=self.name, city=city)
            saved.extend(result["saved"])
        return saved, used


def city_keywords(metros: set[str]) -> list[str]:
    kws = list(settings.xhs_keywords)
    for m in sorted(metros):
        name = METRO_SEARCH_NAME.get(m)
        if name:
            kws += [t.format(city=name) for t in CITY_KEYWORD_TEMPLATES]
    return list(dict.fromkeys(kws))


def intent_keywords(user_id: str) -> list[str]:
    """Active intents turn into targeted searches, e.g. 「多伦多 洗牙 优惠」 (spec §6: 好朋友主動搜尋)."""
    user = db.get_user(user_id) or {}
    from .entities import metro_of

    home = METRO_SEARCH_NAME.get(metro_of(user.get("city")) or "", "")
    out = []
    for it in profile.active_intents(user_id):
        dest = it["meta"].get("destination")
        city = METRO_SEARCH_NAME.get(dest, "") if dest else home
        label = (it["meta"].get("label") or it["target"]).split("/")[0].strip()
        out.append(f"{city} {label} 优惠".strip())
    # The shared account also searches what each user likes, so personalization comes from our own model.
    likes = sorted((b for b in profile.beliefs(user_id, "entity") if profile.effective_score(b) >= 0.5),
                   key=profile.effective_score, reverse=True)
    for b in likes[:3]:
        e = entities.get(b["target"]) or {}
        if e.get("type") != "place":
            out.append(f"{home} {e.get('name', b['target']).split(' ')[0]} 优惠".strip())
    return out


def run_xiaohongshu(src: XiaohongshuSource | None = None) -> list[dict]:
    src = src or XiaohongshuSource()
    if not src.enabled or not src.logged_in():
        return []
    users = [dict(r) for r in db.conn().execute("SELECT id, city FROM users").fetchall()]
    metros = set()
    keywords: list[str] = []
    for u in users:
        metros |= profile.user_metros(u["id"])
        keywords += intent_keywords(u["id"])
    keywords = list(dict.fromkeys(keywords + city_keywords(metros)))
    budget = settings.xhs_max_details
    saved: list[dict] = []
    for kw in keywords:
        if budget <= 0:
            break
        try:
            feeds = src.search(kw)
        except Exception as e:
            log.warning("xiaohongshu search failed for %s: %s", kw, e)
            continue
        new, used = src.ingest_feeds(feeds, budget)
        budget -= used
        saved += new
        time.sleep(settings.xhs_delay_seconds)
    if settings.xhs_read_feed and budget > 0:
        try:
            saved += src.ingest_feeds(src.recommended(), budget)[0]
        except Exception as e:
            log.warning("xiaohongshu feed read failed: %s", e)
    return saved


def search_for_intent(user_id: str, src: XiaohongshuSource | None = None) -> list[dict]:
    """Called when a new intent appears, so the friend starts looking right away."""
    src = src or XiaohongshuSource()
    if not src.enabled or not src.logged_in():
        return []
    saved = []
    for kw in intent_keywords(user_id)[:2]:
        try:
            saved += src.ingest_feeds(src.search(kw, publish_time="半年内"), budget=3)[0]
        except Exception as e:
            log.warning("xiaohongshu intent search failed for %s: %s", kw, e)
    return saved


# ---- Instagram Graph API -------------------------------------------------------

def run_instagram(http: httpx.Client | None = None) -> list[dict]:
    if not (settings.ig_user_id and settings.ig_access_token):
        return []
    http = http or httpx.Client(base_url=f"https://graph.facebook.com/{settings.ig_graph_version}", timeout=30)
    auth = {"user_id": settings.ig_user_id, "access_token": settings.ig_access_token}
    saved = []
    for tag in settings.ig_hashtags:
        try:
            r = http.get("/ig_hashtag_search", params={**auth, "q": tag})
            r.raise_for_status()
            ids = r.json().get("data") or []
            if not ids:
                continue
            r = http.get(f"/{ids[0]['id']}/recent_media",
                         params={**auth, "fields": "id,caption,permalink,timestamp", "limit": 30})
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("instagram hashtag %s failed: %s", tag, e)
            continue
        for m in r.json().get("data") or []:
            if not m.get("caption") or _seen("instagram", m["id"]):
                continue
            _mark_seen("instagram", m["id"])
            res = extract.ingest(f"{m['caption']}\n{m.get('permalink', '')}", source_type="instagram")
            saved += res["saved"]
    return saved


# ---- RSS ---------------------------------------------------------------------------

def run_rss(feeds: list[str] | None = None) -> list[dict]:
    saved = []
    for url in feeds if feeds is not None else settings.feeds:
        try:
            r = httpx.get(url, timeout=20, follow_redirects=True, headers={"User-Agent": "DealBuddy/0.1"})
            r.raise_for_status()
            root = ET.fromstring(r.content)
        except Exception as e:
            log.warning("feed %s failed: %s", url, e)
            continue
        for item in root.iter("item"):
            link = (item.findtext("link") or "").strip()
            title = (item.findtext("title") or "").strip()
            key = link or title
            if not key or _seen("rss", key):
                continue
            _mark_seen("rss", key)
            desc = extract.re.sub(r"<[^>]+>", " ", item.findtext("description") or "")
            res = extract.ingest(f"{title}\n{desc[:1500]}\n{link}", source_type="rss")
            saved += res["saved"]
    return saved


def run_all() -> dict:
    return {
        "xiaohongshu": run_xiaohongshu(),
        "instagram": run_instagram(),
        "rss": run_rss(),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db.init_db()
    out = run_all()
    print({k: len(v) for k, v in out.items()})

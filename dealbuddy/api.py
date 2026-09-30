"""HTTP API for the web chat and other clients (spec §10 對外 API).
Run:  uvicorn dealbuddy.api:app --reload"""
from contextlib import asynccontextmanager
from pathlib import Path

import hmac

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import chat, db, deals, digest, extract, feedback, metrics, profile, sources, telegram_bot, tools
from .config import settings



@asynccontextmanager
async def lifespan(_app):
    db.init_db()
    yield


app = FastAPI(title="好朋友優惠 DealBuddy", version="0.1.0", lifespan=lifespan)
WEB = Path(__file__).parent / "web"


def _same(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a, b)


def auth(x_app_token: str | None = Header(default=None)) -> None:
    """Shared token for the seed-user pilot. Real per-user auth comes with a proper client.
    A public deployment (Vercel) refuses to run without one, so strangers can't spend the API budget."""
    if settings.app_token:
        if not _same(x_app_token, settings.app_token):
            raise HTTPException(status_code=401, detail="bad app token")
    elif settings.on_vercel:
        raise HTTPException(status_code=503, detail="set DEALBUDDY_APP_TOKEN before using a public deployment")


class ChatIn(BaseModel):
    user_id: str
    text: str = ""
    image_base64: str | None = None
    image_media_type: str = "image/jpeg"
    lang: str | None = None  # client UI language, e.g. navigator.language


class FeedbackIn(BaseModel):
    user_id: str
    deal_id: int
    signal: str
    reason: str | None = None


class ProfileChange(BaseModel):
    action: str  # like / dislike / set_context / set_mechanic / add_intent / close_intent / forget
    target: str
    strength: float | None = None
    label: str | None = None
    days: int | None = None
    destination: str | None = None
    layer: str | None = None


class ProfilePatch(BaseModel):
    city: str | None = None
    display_name: str | None = None
    daily_push_limit: int | None = None
    quiet_start: int | None = None
    quiet_end: int | None = None
    push_paused: bool | None = None
    changes: list[ProfileChange] = []


class IngestIn(BaseModel):
    text: str = ""
    user_id: str | None = None
    image_base64: str | None = None
    image_media_type: str = "image/jpeg"


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB / "index.html")


@app.get("/health")
def health():
    return {"ok": True, "db": "postgres" if settings.database_url else "sqlite", "llm": settings.llm_enabled,
            "xiaohongshu": bool(settings.xhs_url)}


@app.post("/chat", dependencies=[Depends(auth)])
def post_chat(body: ChatIn):
    if not body.text.strip() and not body.image_base64:
        raise HTTPException(400, "text or image required")
    return chat.handle(body.user_id, body.text, image_b64=body.image_base64, image_type=body.image_media_type,
                       lang_hint=body.lang)


@app.post("/feedback", dependencies=[Depends(auth)])
def post_feedback(body: FeedbackIn):
    db.ensure_user(body.user_id)
    try:
        return feedback.record(body.user_id, body.deal_id, body.signal, body.reason)
    except KeyError:
        raise HTTPException(404, "deal not found")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/profile", dependencies=[Depends(auth)])
def get_profile(user_id: str):
    db.ensure_user(user_id)
    return {**profile.summary(user_id), "savings_this_month": metrics.savings(user_id),
            "language": (db.get_user(user_id) or {}).get("language")}


@app.patch("/profile", dependencies=[Depends(auth)])
def patch_profile(user_id: str, body: ProfilePatch):
    db.ensure_user(user_id)
    fields = body.model_dump(exclude={"changes"}, exclude_none=True)
    if "push_paused" in fields:
        fields["push_paused"] = int(fields["push_paused"])
    db.update_user(user_id, **fields)
    for c in body.changes:
        tools.update_profile(user_id, {**c.model_dump(exclude_none=True), "confirmed": True})
    return profile.summary(user_id)


@app.delete("/me", dependencies=[Depends(auth)])
def delete_me(user_id: str):
    db.delete_user(user_id)
    return {"ok": True}


@app.get("/deals", dependencies=[Depends(auth)])
def list_deals(status: str = "active"):
    deals.refresh_statuses()
    rows = db.conn().execute("SELECT id FROM deals WHERE status=? ORDER BY id DESC LIMIT 200", (status,)).fetchall()
    return [deals.get(r["id"]) for r in rows]


@app.post("/deals/ingest", dependencies=[Depends(auth)])
def ingest(body: IngestIn):
    return extract.ingest(body.text, user_id=body.user_id, image_b64=body.image_base64, image_type=body.image_media_type)


@app.post("/digest/preview", dependencies=[Depends(auth)])
def digest_preview(user_id: str):
    return digest.build(user_id, log=False) or {"text": None, "cards": []}


@app.get("/metrics", dependencies=[Depends(auth)])
def get_metrics(user_id: str | None = None, days: int = 30):
    """Spec §2 success metrics; with user_id also this month's estimated savings."""
    out = metrics.summary(user_id, days)
    if user_id:
        out["this_month"] = metrics.savings(user_id)
    return out


@app.post("/sources/run", dependencies=[Depends(auth)])
def run_sources():
    return {k: len(v) for k, v in sources.run_all().items()}


# ---- Vercel: Telegram webhook and cron ---------------------------------------------

def _bot() -> "telegram_bot.Bot":
    if not settings.telegram_token:
        raise HTTPException(503, "TELEGRAM_BOT_TOKEN not set")
    return telegram_bot.Bot(settings.telegram_token)


@app.post("/telegram/webhook", include_in_schema=False)
async def telegram_webhook(request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    if not _same(x_telegram_bot_api_secret_token, settings.telegram_webhook_secret):
        raise HTTPException(401, "bad webhook secret")
    _bot().handle_update(await request.json())
    return {"ok": True}


@app.post("/telegram/setup", dependencies=[Depends(auth)])
def telegram_setup(request: Request):
    """Point Telegram at this deployment's webhook. Call once after deploying."""
    if not settings.telegram_webhook_secret:
        raise HTTPException(503, "TELEGRAM_WEBHOOK_SECRET not set")
    url = str(request.base_url).rstrip("/").replace("http://", "https://") + "/telegram/webhook"
    return {"webhook": url, "result": _bot().set_webhook(url, settings.telegram_webhook_secret)}


@app.get("/cron/daily", include_in_schema=False)
def cron_daily(authorization: str | None = Header(default=None)):
    """Vercel Cron (once a day): pull RSS / IG sources, then send each user's daily digest on Telegram."""
    if not _same(authorization, f"Bearer {settings.cron_secret}" if settings.cron_secret else None):
        raise HTTPException(401, "bad cron secret")
    found = {"instagram": len(sources.run_instagram()), "rss": len(sources.run_rss())}
    sent = _bot().push_due(ignore_hour=True, instant=False) if settings.telegram_token else 0
    return {"sources": found, "digests_sent": sent}

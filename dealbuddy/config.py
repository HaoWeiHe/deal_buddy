"""Runtime settings, all read from environment variables (see .env.example)."""
import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


@dataclass
class Settings:
    # Postgres when DATABASE_URL (or Vercel's POSTGRES_URL) is set; otherwise a local SQLite file.
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL") or _env("POSTGRES_URL"))
    db_path: str = field(
        default_factory=lambda: _env("DEALBUDDY_DB") or ("/tmp/dealbuddy.sqlite3" if _env("VERCEL") else "dealbuddy.sqlite3")
    )
    on_vercel: bool = field(default_factory=lambda: bool(_env("VERCEL")))
    cron_secret: str = field(default_factory=lambda: _env("CRON_SECRET"))
    telegram_webhook_secret: str = field(default_factory=lambda: _env("TELEGRAM_WEBHOOK_SECRET"))
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    # Spec §10: chat agent on Sonnet 5.5, bulk extraction / matching on Haiku 4.5.
    agent_model: str = field(default_factory=lambda: _env("DEALBUDDY_AGENT_MODEL", "claude-sonnet-5-5"))
    extract_model: str = field(default_factory=lambda: _env("DEALBUDDY_EXTRACT_MODEL", "claude-haiku-4-5"))
    agent_effort: str = field(default_factory=lambda: _env("DEALBUDDY_AGENT_EFFORT", "medium"))
    # Vercel stops the function at 60s (vercel.json maxDuration); a chat turn has to answer before that.
    turn_seconds: float = field(default_factory=lambda: float(_env("DEALBUDDY_TURN_SECONDS", "45")))
    use_fallbacks: bool = field(default_factory=lambda: _env("DEALBUDDY_FALLBACKS", "1") == "1")
    telegram_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    app_token: str = field(default_factory=lambda: _env("DEALBUDDY_APP_TOKEN"))
    timezone: str = field(default_factory=lambda: _env("DEALBUDDY_TZ", "America/Toronto"))
    default_city: str = field(default_factory=lambda: _env("DEALBUDDY_DEFAULT_CITY", ""))
    digest_hour: int = field(default_factory=lambda: int(_env("DEALBUDDY_DIGEST_HOUR", "11")))
    feeds: list[str] = field(
        default_factory=lambda: [f for f in _env("DEALBUDDY_FEEDS").split(",") if f.strip()]
    )
    # Xiaohongshu via the community xiaohongshu-mcp server's HTTP API (spec §4), logged in with a dedicated account.
    xhs_url: str = field(default_factory=lambda: _env("XHS_MCP_URL"))  # e.g. http://localhost:18060
    xhs_token: str = field(default_factory=lambda: _env("XHS_MCP_TOKEN"))
    xhs_keywords: list[str] = field(
        default_factory=lambda: [k.strip() for k in _env("XHS_KEYWORDS").split(",") if k.strip()]
    )
    xhs_read_feed: bool = field(default_factory=lambda: _env("XHS_READ_FEED", "0") == "1")
    xhs_max_details: int = field(default_factory=lambda: int(_env("XHS_MAX_DETAILS_PER_RUN", "10")))
    # How many pictures of each note the extractor reads (posters and screenshots carry most deal details).
    xhs_max_images: int = field(default_factory=lambda: int(_env("XHS_MAX_IMAGES", "4")))
    xhs_delay_seconds: float = field(default_factory=lambda: float(_env("XHS_DELAY_SECONDS", "5")))
    # Instagram Graph API hashtag search (needs a Business/Creator account).
    ig_user_id: str = field(default_factory=lambda: _env("IG_USER_ID"))
    ig_access_token: str = field(default_factory=lambda: _env("IG_ACCESS_TOKEN"))
    ig_hashtags: list[str] = field(
        default_factory=lambda: [h.strip().lstrip("#") for h in _env("IG_HASHTAGS", "torontodeals").split(",") if h.strip()]
    )
    ig_graph_version: str = field(default_factory=lambda: _env("IG_GRAPH_VERSION", "v21.0"))

    @property
    def llm_enabled(self) -> bool:
        return bool(self.anthropic_api_key)


settings = Settings()


# Ranking constants (spec §7). Initial values; tune after the two-week pilot.
PUSH_THRESHOLD = 0.6
INSTANT_PUSH_THRESHOLD = 0.85
DAILY_PUSH_LIMIT = 3
DISLIKE_CUTOFF = -0.5
BELIEF_HALF_LIFE_DAYS = 180
UNVERIFIED_AFTER_DAYS = 14
DEFAULT_INTENT_DAYS = 30
DEFAULT_DISCOUNT_THRESHOLD = 0.20

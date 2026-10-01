"""Thin wrapper around the Anthropic SDK. Everything LLM-related is optional: without ANTHROPIC_API_KEY the app
runs in offline mode (keyword extraction + rule-based chat) so it can be tried and tested locally."""
import logging

from .config import settings

log = logging.getLogger(__name__)
_client = None
_fallbacks_ok = True


def client():
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


def create_agent_message(**kwargs):
    """Chat-agent request. Opts into server-side refusal fallbacks (Sonnet 5.5, Claude API only);
    if the account or platform rejects that beta, retries once without it and stops sending it."""
    global _fallbacks_ok
    import anthropic

    c = client()
    timeout = kwargs.pop("timeout", None)
    if timeout is not None:
        # A retry after a timeout would blow the turn's time budget, so give up on the first one.
        c = c.with_options(timeout=timeout, max_retries=0)

    if settings.use_fallbacks and _fallbacks_ok:
        try:
            return c.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
            )
        except anthropic.BadRequestError as e:
            if "fallback" not in str(e).lower():
                raise
            log.warning("server-side fallbacks rejected, continuing without them: %s", e)
            _fallbacks_ok = False
    return c.beta.messages.create(**kwargs)


def text_of(message) -> str:
    return "".join(b.text for b in message.content if getattr(b, "type", None) == "text").strip()

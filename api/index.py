"""Vercel entrypoint: every request is routed here (see vercel.json) and served by the FastAPI app."""
from dealbuddy.api import app  # noqa: F401

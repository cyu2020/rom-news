from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_DEFAULT_BASE = "https://space.ai-builders.com/backend/v1"
_DEFAULT_MODEL = "grok-4-fast"


def project_root() -> Path:
    """Repo root (parent of src/)."""
    return Path(__file__).resolve().parent.parent.parent


def load_env() -> None:
    env_path = project_root() / ".env"
    if env_path.is_file():
        load_dotenv(env_path)


def get_token() -> str:
    load_env()
    token = os.environ.get("AI_BUILDER_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "AI_BUILDER_TOKEN is not set. Add it to .env in the project root."
        )
    return token


def api_base_url() -> str:
    load_env()
    return os.environ.get("AI_BUILDERS_BASE_URL", _DEFAULT_BASE).rstrip("/")


def default_model() -> str:
    load_env()
    return os.environ.get("ROM_NEWSLETTER_MODEL", _DEFAULT_MODEL).strip() or _DEFAULT_MODEL


def get_buttondown_api_key() -> str:
    load_env()
    token = os.environ.get("BUTTONDOWN_API_KEY", "").strip()
    if not token:
        raise RuntimeError(
            "BUTTONDOWN_API_KEY is not set. Add it to .env or the environment."
        )
    return token

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def project_root() -> Path:
    """Repo root (parent of src/)."""
    return Path(__file__).resolve().parent.parent.parent


def load_env() -> None:
    env_path = project_root() / ".env"
    if env_path.is_file():
        load_dotenv(env_path)


def llm_base_url() -> str:
    """OpenAI-compatible API base URL (no trailing slash). Required: ``LLM_BASE_URL``."""
    load_env()
    url = os.environ.get("LLM_BASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "LLM_BASE_URL is not set. Add it to .env (e.g. AI Builders or OpenRouter `/v1` base)."
        )
    return url.rstrip("/")


def llm_api_key() -> str:
    """API key for the chat endpoint. Required: ``LLM_API_KEY``."""
    load_env()
    key = os.environ.get("LLM_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "LLM_API_KEY is not set. Add it to .env in the project root."
        )
    return key


def llm_model() -> str:
    """Default chat model id. Required: ``LLM_MODEL`` (unless you pass ``--model``)."""
    load_env()
    m = os.environ.get("LLM_MODEL", "").strip()
    if not m:
        raise RuntimeError(
            "LLM_MODEL is not set. Add it to .env or pass --model on the command line."
        )
    return m


def get_buttondown_api_key() -> str:
    load_env()
    token = os.environ.get("BUTTONDOWN_API_KEY", "").strip()
    if not token:
        raise RuntimeError(
            "BUTTONDOWN_API_KEY is not set. Add it to .env or the environment."
        )
    return token

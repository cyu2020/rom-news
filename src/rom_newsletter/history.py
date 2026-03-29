from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from rom_newsletter.search import _canonical_url


def load_seen_urls(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if isinstance(data, list):
        return {_canonical_url(u) for u in data if isinstance(u, str)}
    if isinstance(data, dict) and "urls" in data:
        raw = data["urls"]
        if isinstance(raw, list):
            return {_canonical_url(u) for u in raw if isinstance(u, str)}
    return set()


def save_seen_urls(path: Path, urls: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sorted_urls = sorted(urls)
    path.write_text(
        json.dumps({"urls": sorted_urls, "count": len(sorted_urls)}, indent=2),
        encoding="utf-8",
    )


def merge_history(path: Path, new_urls: list[str]) -> None:
    seen = load_seen_urls(path)
    for u in new_urls:
        if not u or not isinstance(u, str):
            continue
        try:
            if urlparse(u).scheme in ("http", "https"):
                seen.add(_canonical_url(u))
        except ValueError:
            continue
    save_seen_urls(path, seen)

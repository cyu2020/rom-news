from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from rom_newsletter.search import _canonical_url


def read_json(path: Path, default: dict | None = None) -> dict:
    if not path.exists():
        return {} if default is None else default
    # Corrupt publication state must stop the run, not silently permit duplicate sends.
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return data


def write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_seen_urls(path: Path, *, exclude_issue: str | None = None) -> set[str]:
    if not path.is_file():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):  # Explicitly supplied legacy ledgers remain readable.
        raw = data
    elif isinstance(data, dict):
        if "issues" in data:
            raw = [url for key, row in data["issues"].items() if key != exclude_issue for url in row["urls"]]
        else:
            raw = data.get("urls", [])
    else:
        raise ValueError(f"Invalid history in {path}")
    return {_canonical_url(u) for u in raw if isinstance(u, str)}


def merge_history(path: Path, new_urls: list[str], *, issue_key: str | None = None) -> None:
    data = read_json(path) if path.exists() else {}
    urls = sorted({_canonical_url(u) for u in new_urls if urlparse(u).scheme in ("http", "https")})
    if issue_key is not None:
        issues = data.setdefault("issues", {})
        old = issues.get(issue_key, {}).get("urls", [])
        # Archive edits must not erase the record of stories already emailed to readers.
        issues[issue_key] = {"urls": sorted(set(old) | set(urls))}
        all_urls = sorted({u for row in issues.values() for u in row["urls"]})
    else:
        all_urls = sorted(load_seen_urls(path) | set(urls))
    data.update({"urls": all_urls, "count": len(all_urls)})
    write_json_atomic(path, data)

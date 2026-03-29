from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# Tavily / discovery behavior
KIND_ARXIV = "arxiv"
KIND_NVIDIA = "nvidia"
KIND_SIEMENS = "siemens"
KIND_ANSYS = "ansys"
KIND_GENERIC = "generic"

CATEGORY_PAPERS = "papers"
CATEGORY_INDUSTRY = "industry"


@dataclass(frozen=True)
class Source:
    """A configured news / paper source."""

    label: str
    url: str
    host: str
    category: str = CATEGORY_INDUSTRY
    kind: str = KIND_GENERIC
    id: str | None = None
    rss: str | None = None
    rss_feed_hosts: frozenset[str] = frozenset()
    #: When True, fetch listing HTML at ``url`` (see ``newsroom_listings``) instead of relying on that domain from Tavily alone.
    newsroom_listing: bool = False


def _normalize_host(host: str) -> str:
    return host.lower().removeprefix("www.")


def infer_kind_from_host(host: str) -> str:
    h = host.lower()
    if "arxiv" in h:
        return KIND_ARXIV
    if "nvidia" in h:
        return KIND_NVIDIA
    if "siemens" in h:
        return KIND_SIEMENS
    if "ansys" in h:
        return KIND_ANSYS
    return KIND_GENERIC


def infer_category_from_kind(kind: str) -> str:
    return CATEGORY_PAPERS if kind == KIND_ARXIV else CATEGORY_INDUSTRY


def effective_source_kind(s: Source) -> str:
    """Explicit `kind` when set to a known value; otherwise infer from host."""
    k = (s.kind or "").strip().lower()
    if k in ("", KIND_GENERIC):
        return infer_kind_from_host(s.host)
    if k in (KIND_ARXIV, KIND_NVIDIA, KIND_SIEMENS, KIND_ANSYS):
        return k
    return infer_kind_from_host(s.host)


def _parse_feed_hosts(raw: Any) -> frozenset[str]:
    if not isinstance(raw, list):
        return frozenset()
    out: set[str] = set()
    for h in raw:
        if isinstance(h, str) and h.strip():
            out.add(_normalize_host(h.strip()))
    return frozenset(out)


def parse_sources_json(path: Path) -> list[Source]:
    """Parse versioned sources.json."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("sources.json must be a JSON object")
    ver = data.get("version", 1)
    if ver != 1:
        raise ValueError(f"Unsupported sources.json version: {ver}")
    raw_list = data.get("sources")
    if not isinstance(raw_list, list) or not raw_list:
        raise ValueError('sources.json must contain a non-empty "sources" array')
    out: list[Source] = []
    for i, item in enumerate(raw_list):
        if not isinstance(item, dict):
            raise ValueError(f"sources[{i}] must be an object")
        label = item.get("label")
        url = item.get("url")
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"sources[{i}].label is required")
        if not isinstance(url, str) or not url.strip():
            raise ValueError(f"sources[{i}].url is required")
        url = url.strip().rstrip("/")
        parsed = urlparse(url)
        if not parsed.netloc:
            raise ValueError(f"sources[{i}].url must have a host")
        host = _normalize_host(parsed.netloc)
        kind = item.get("kind")
        if kind is not None:
            if not isinstance(kind, str) or not kind.strip():
                raise ValueError(f"sources[{i}].kind must be a non-empty string")
            kind = kind.strip()
        else:
            kind = infer_kind_from_host(host)
        cat = item.get("category")
        if cat is not None:
            if not isinstance(cat, str) or not cat.strip():
                raise ValueError(f"sources[{i}].category must be a non-empty string")
            category = cat.strip()
        else:
            category = infer_category_from_kind(kind)
        sid = item.get("id")
        if sid is not None and not isinstance(sid, str):
            raise ValueError(f"sources[{i}].id must be a string")
        rss = item.get("rss")
        if rss is not None:
            if not isinstance(rss, str) or not rss.strip():
                raise ValueError(f"sources[{i}].rss must be a non-empty string")
            rss = rss.strip()
        fh = _parse_feed_hosts(item.get("feed_hosts"))
        nrl = item.get("newsroom_listing")
        newsroom_listing = bool(nrl) if nrl is not None else False
        out.append(
            Source(
                label=label.strip(),
                url=url,
                host=host,
                category=category,
                kind=kind,
                id=sid.strip() if isinstance(sid, str) else None,
                rss=rss,
                rss_feed_hosts=fh,
                newsroom_listing=newsroom_listing,
            )
        )
    return out


def load_sources(path: Path) -> list[Source]:
    """Load from ``sources.json`` (JSON object with ``version`` and ``sources`` array)."""
    suf = path.suffix.lower()
    if suf in (".json", ".jsonc"):
        return parse_sources_json(path)
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:512].lstrip()
    except OSError as e:
        raise FileNotFoundError(path) from e
    if head.startswith("{"):
        return parse_sources_json(path)
    raise ValueError(
        f"Sources file must be JSON ({path.name}): use sources.json or pass a path whose contents start with '{{'"
    )


def resolve_default_sources_path(root: Path) -> Path:
    """Default config path: ``<root>/sources.json``."""
    return root / "sources.json"


def allowed_hosts(sources: list[Source]) -> frozenset[str]:
    return frozenset(s.host for s in sources)


def host_allowed(url: str, hosts: frozenset[str]) -> bool:
    try:
        netloc = _normalize_host(urlparse(url).netloc)
    except ValueError:
        return False
    if not netloc:
        return False
    if netloc in hosts:
        return True
    return any(netloc.endswith("." + h) for h in hosts)


def source_category_for_url(url: str, sources: list[Source]) -> str:
    """Return ``sources.json`` ``category`` for the source whose host matches the URL (subdomain-aware)."""
    try:
        netloc = _normalize_host(urlparse(url).netloc)
    except ValueError:
        return CATEGORY_INDUSTRY
    if not netloc:
        return CATEGORY_INDUSTRY
    for s in sources:
        h = s.host
        if netloc == h or netloc.endswith("." + h):
            return s.category
    return CATEGORY_INDUSTRY


def feed_entries_from_sources(sources: list[Source]) -> list[tuple[str, frozenset[str]]]:
    """RSS feed URLs declared on sources (optional per-entry rss + feed_hosts)."""
    out: list[tuple[str, frozenset[str]]] = []
    seen: set[str] = set()
    for s in sources:
        if not s.rss:
            continue
        u = s.rss.strip()
        if not u or u in seen:
            continue
        seen.add(u)
        extras = s.rss_feed_hosts
        out.append((u, extras))
    return out


def feed_url_to_category_map(sources: list[Source]) -> dict[str, str]:
    """Map RSS feed URL → source `category` for tagging RSS hits."""
    m: dict[str, str] = {}
    for s in sources:
        if not s.rss:
            continue
        u = s.rss.strip()
        if u:
            m[u] = s.category
    return m

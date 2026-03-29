from __future__ import annotations

import email.utils
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from rom_newsletter.sources import CATEGORY_INDUSTRY, _normalize_host
from rom_newsletter.search import SearchHit, _canonical_url

# Fallback feeds after ``sources.json`` per-source ``rss`` (deduped by URL; first wins).
DEFAULT_FEED_URLS: list[str] = [
    "https://nvidianews.nvidia.com/rss.xml",
]


def _parse_http_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        t = email.utils.parsedate_to_datetime(s)
        if t.tzinfo is None:
            return t.replace(tzinfo=timezone.utc)
        return t.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_iso_z(s: str) -> datetime | None:
    s = s.strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _rss_items(root: ET.Element) -> list[ET.Element]:
    # RSS 2.0
    channel = root.find("channel")
    if channel is not None:
        return channel.findall("item")
    # Atom 1.0
    atom_ns = "http://www.w3.org/2005/Atom"
    if root.tag == f"{{{atom_ns}}}feed" or root.tag.endswith("feed"):
        return root.findall(f".//{{{atom_ns}}}entry")
    return []


def _hit_from_rss_item(
    item: ET.Element,
    feed_url: str,
    *,
    source_category: str = CATEGORY_INDUSTRY,
) -> tuple[SearchHit | None, datetime | None]:
    title = (item.findtext("title") or "").strip()
    link_text = (item.findtext("link") or "").strip()
    link_href = None
    # Atom: <link href="..."/>
    for ln in item.findall("{http://www.w3.org/2005/Atom}link"):
        if ln.get("href"):
            link_href = ln.get("href", "").strip()
            break
    url = link_href or link_text
    pub_raw = (
        item.findtext("pubDate")
        or item.findtext("{http://purl.org/dc/elements/1.1/}date")
        or ""
    )
    if not pub_raw.strip():
        pub_el = item.find("{http://www.w3.org/2005/Atom}published")
        if pub_el is not None and pub_el.text:
            pub_raw = pub_el.text
        else:
            upd = item.find("{http://www.w3.org/2005/Atom}updated")
            if upd is not None and upd.text:
                pub_raw = upd.text
    pub_dt = _parse_http_date(pub_raw.strip()) or _parse_iso_z(pub_raw.strip())
    desc = (item.findtext("description") or item.findtext("summary") or item.findtext("{http://www.w3.org/2005/Atom}summary") or "").strip()
    content = (desc[:8000] if desc else title) or url
    if not url:
        return None, pub_dt
    return (
        SearchHit(
            url=_canonical_url(url),
            title=title or url,
            content=content,
            keyword=f"rss:{feed_url}",
            raw_score=None,
            source_category=source_category,
        ),
        pub_dt,
    )


def _feed_host(feed_url: str) -> str:
    return _normalize_host(urlparse(feed_url).netloc)


def _feed_allowed(
    feed_url: str,
    *,
    sources_hosts: frozenset[str],
    extra_hosts: frozenset[str],
) -> bool:
    """Feed URL host must appear in sources_hosts or in per-source ``feed_hosts``."""
    fh = _feed_host(feed_url)
    allowed = sources_hosts | extra_hosts
    if fh in allowed:
        return True
    return any(fh.endswith("." + h) for h in allowed)


def fetch_rss_hits(
    feed_entries: list[tuple[str, frozenset[str]]],
    start: datetime,
    end: datetime,
    *,
    allowed_feed_hosts: frozenset[str],
    feed_url_category: dict[str, str] | None = None,
    timeout: float = 45.0,
) -> tuple[list[SearchHit], list[dict[str, Any]]]:
    """Fetch RSS/Atom feeds and keep items whose published time falls in [start, end] UTC.

    Items are trusted by **feed origin** (allowlisted feed host). Article URLs may point
    off-domain (e.g. press pick-ups), so we do not filter by item link host.

    Each entry is ``(feed_url, extra_hosts)`` where ``extra_hosts`` augments the allowlist
    for feeds whose hostname is not in ``sources.json`` (e.g. Synopsys for Ansys-related news).

    ``feed_url_category`` maps feed URL → ``sources.json`` ``category`` for composer routing.
    """
    hits: list[SearchHit] = []
    errors: list[dict[str, Any]] = []
    seen: set[str] = set()
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for feed_url, extra_hosts in feed_entries:
            if not _feed_allowed(
                feed_url, sources_hosts=allowed_feed_hosts, extra_hosts=extra_hosts
            ):
                errors.append(
                    {
                        "feed": feed_url,
                        "error": "feed host not in allowlist (set feed_hosts on the source in sources.json)",
                    }
                )
                continue
            try:
                r = client.get(feed_url)
                r.raise_for_status()
                root = ET.fromstring(r.content)
            except Exception as e:
                errors.append({"feed": feed_url, "error": str(e)})
                continue
            fc = (feed_url_category or {}).get(feed_url, CATEGORY_INDUSTRY)
            for item in _rss_items(root):
                pair = _hit_from_rss_item(item, feed_url, source_category=fc)
                if pair[0] is None:
                    continue
                hit, pub_dt = pair
                if pub_dt is not None:
                    if not (start <= pub_dt <= end):
                        continue
                else:
                    continue
                if hit.url in seen:
                    continue
                seen.add(hit.url)
                hits.append(hit)
    return hits, errors

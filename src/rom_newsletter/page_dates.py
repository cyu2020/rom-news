from __future__ import annotations

import email.utils
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from rom_newsletter.search import SearchHit

_MAX_HTML_BYTES = 600_000
_UA = (
    "Mozilla/5.0 (compatible; rom-newsletter/1.0; +https://github.com/) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Meta / JSON-LD / <time> — attribute order varies across CMSs.
_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r'<meta[^>]+(?:property|name)=["\']article:published_time["\'][^>]+content=["\']([^"\']+)["\']',
        re.I,
    ),
    re.compile(
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']article:published_time["\']',
        re.I,
    ),
    re.compile(
        r'<meta[^>]+property=["\']og:published_time["\'][^>]+content=["\']([^"\']+)["\']',
        re.I,
    ),
    re.compile(
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:published_time["\']',
        re.I,
    ),
    re.compile(
        r'<meta[^>]+(?:property|name)=["\']date["\'][^>]+content=["\']([^"\']+)["\']',
        re.I,
    ),
    re.compile(
        r'<meta[^>]+(?:property|name)=["\']og:updated_time["\'][^>]+content=["\']([^"\']+)["\']',
        re.I,
    ),
    re.compile(r'<time[^>]+datetime=["\']([^"\']+)["\']', re.I),
    re.compile(r'"datePublished"\s*:\s*"([^"]+)"', re.I),
    re.compile(r'"dateModified"\s*:\s*"([^"]+)"', re.I),
]

# Visible English dates (Webflow / newsrooms): "March 17, 2026" or "17 March 2026"
_RE_MONTH_DAY_YEAR = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+"
    r"(\d{1,2}),\s+(\d{4})\b",
    re.I,
)
_RE_DAY_MONTH_YEAR = re.compile(
    r"\b(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})\b",
    re.I,
)

_LD_JSON_SCRIPT = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>([\s\S]*?)</script>',
    re.I,
)


def parse_datetime_string(raw: str) -> datetime | None:
    """Parse ISO-8601 or HTTP-date strings to UTC (public for search API item dates)."""
    return _parse_datetime_string_impl(raw)


def _parse_datetime_string_impl(raw: str) -> datetime | None:
    s = raw.strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = email.utils.parsedate_to_datetime(s)
        except (TypeError, ValueError, OverflowError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _strip_html_comments(html: str) -> str:
    return re.sub(r"<!--[\s\S]*?-->", "", html)


def _datetime_from_english_matches(chunk: str) -> datetime | None:
    """First calendar date in document order (after comment strip)."""
    candidates: list[tuple[int, datetime]] = []
    for m in _RE_MONTH_DAY_YEAR.finditer(chunk):
        try:
            s = f"{m.group(1)} {m.group(2)}, {m.group(3)}"
            dt = datetime.strptime(s, "%B %d, %Y").replace(tzinfo=timezone.utc)
            candidates.append((m.start(), dt))
        except ValueError:
            continue
    for m in _RE_DAY_MONTH_YEAR.finditer(chunk):
        try:
            s = f"{m.group(1)} {m.group(2)} {m.group(3)}"
            dt = datetime.strptime(s, "%d %B %Y").replace(tzinfo=timezone.utc)
            candidates.append((m.start(), dt))
        except ValueError:
            continue
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def _find_date_in_json_obj(obj: Any) -> datetime | None:
    if isinstance(obj, dict):
        for key in ("datePublished", "dateModified", "uploadDate", "dateCreated"):
            v = obj.get(key)
            if isinstance(v, str) and v.strip():
                dt = parse_datetime_string(v)
                if dt is not None:
                    return dt
        for v in obj.values():
            dt = _find_date_in_json_obj(v)
            if dt is not None:
                return dt
    elif isinstance(obj, list):
        for item in obj:
            dt = _find_date_in_json_obj(item)
            if dt is not None:
                return dt
    return None


def _datetime_from_json_ld(html: str) -> datetime | None:
    for m in _LD_JSON_SCRIPT.finditer(html):
        raw = m.group(1).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        dt = _find_date_in_json_obj(data)
        if dt is not None:
            return dt
    return None


def published_datetime_from_search_item(item: dict[str, Any]) -> datetime | None:
    """If the search API includes a publish time on a result dict, parse it."""
    for key in (
        "published_date",
        "published_time",
        "published",
        "publishedAt",
        "pub_date",
        "date",
        "article_date",
    ):
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            dt = parse_datetime_string(v)
            if dt is not None:
                return dt
        if isinstance(v, (int, float)):
            try:
                return datetime.fromtimestamp(float(v), tz=timezone.utc)
            except (OSError, ValueError, OverflowError):
                continue
    return None


def extract_published_datetime(html: str) -> datetime | None:
    """Best-effort published time from raw HTML (structured meta → JSON-LD → visible English dates)."""
    chunk = html if len(html) <= _MAX_HTML_BYTES else html[:_MAX_HTML_BYTES]
    for pat in _PATTERNS:
        m = pat.search(chunk)
        if m:
            dt = _parse_datetime_string_impl(m.group(1))
            if dt is not None:
                return dt
    dt = _datetime_from_json_ld(chunk)
    if dt is not None:
        return dt
    # Webflow / CMS: date in body text; strip comments so we do not match "Last Published" in <!-- ... -->
    visible = _strip_html_comments(chunk)
    return _datetime_from_english_matches(visible)


def _fetch_html(url: str, *, timeout: float) -> str | None:
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.netloc:
            return None
    except ValueError:
        return None
    headers = {"User-Agent": _UA, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"}
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
            r = client.get(url)
            if r.status_code >= 400:
                return None
            return r.text
    except httpx.HTTPError:
        return None


def _classify_hit(
    hit: SearchHit,
    start: datetime,
    end: datetime,
    *,
    timeout: float,
) -> tuple[str, SearchHit | None]:
    """Return ('keep', hit) | ('drop', None) | ('no_date', hit)."""
    html = _fetch_html(hit.url, timeout=timeout)
    if html is None:
        return ("no_date", hit)
    dt = extract_published_datetime(html)
    if dt is None:
        return ("no_date", hit)
    if start <= dt <= end:
        return ("keep", hit)
    return ("drop", None)


def filter_tavily_hits_by_page_date(
    hits: list[SearchHit],
    start: datetime,
    end: datetime,
    *,
    max_workers: int = 6,
    timeout: float = 15.0,
    drop_undated: bool = True,
) -> tuple[list[SearchHit], dict[str, Any]]:
    """
    Drop Tavily hits whose fetched HTML shows a published date outside [start, end] UTC.
    By default, hits with no parseable date are also dropped. Set *drop_undated* to False to keep them.
    """
    if not hits:
        return [], {
            "enabled": True,
            "input_count": 0,
            "output_count": 0,
            "dropped_outside_window": 0,
            "kept_no_parseable_date": 0,
            "kept_inside_window": 0,
            "dropped_undated": 0,
        }

    workers = max(1, min(32, max_workers))
    stats: dict[str, Any] = {
        "enabled": True,
        "input_count": len(hits),
        "dropped_outside_window": 0,
        "kept_no_parseable_date": 0,
        "kept_inside_window": 0,
        "dropped_undated": 0,
        "drop_undated": drop_undated,
    }
    slot: list[SearchHit | None] = [None] * len(hits)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        fut_to_idx = {
            ex.submit(_classify_hit, h, start, end, timeout=timeout): i
            for i, h in enumerate(hits)
        }
        for fut in as_completed(fut_to_idx):
            i = fut_to_idx[fut]
            orig = hits[i]
            try:
                kind, hit = fut.result()
            except Exception:
                stats["kept_no_parseable_date"] += 1
                slot[i] = orig
                continue
            if kind == "keep":
                stats["kept_inside_window"] += 1
                slot[i] = hit
            elif kind == "no_date":
                if drop_undated:
                    stats["dropped_undated"] += 1
                    slot[i] = None
                else:
                    stats["kept_no_parseable_date"] += 1
                    slot[i] = hit
            else:
                stats["dropped_outside_window"] += 1
                slot[i] = None

    out = [h for h in slot if h is not None]
    stats["output_count"] = len(out)
    return out, stats

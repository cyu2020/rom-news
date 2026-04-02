from __future__ import annotations

import email.utils
import json
import re
from datetime import datetime, timezone
from typing import Any

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

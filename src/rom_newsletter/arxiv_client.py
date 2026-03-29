from __future__ import annotations

import os
import random
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

import httpx

from rom_newsletter.config import load_env
from rom_newsletter.sources import CATEGORY_PAPERS
from rom_newsletter.search import SearchHit

ARXIV_API = "https://export.arxiv.org/api/query"
_ATOM = "{http://www.w3.org/2005/Atom}"

# arXiv occasionally returns 503/502/504/429 or is slow to stream the Atom XML; backoff retries help.
_RETRYABLE_STATUS = frozenset({429, 502, 503, 504})
_MAX_ATTEMPTS = 8
_RETRY_BASE_SEC = 1.0


def _arxiv_default_user_agent() -> str:
    """arXiv asks for a identifying User-Agent (see https://arxiv.org/help/api/user-manual)."""
    return (
        "rom-newsletter/0.1 "
        "(+https://arxiv.org/help/api/user-manual#Quickstart; open-source newsletter generator)"
    )


def _arxiv_request_headers() -> dict[str, str]:
    load_env()
    ua = os.environ.get("ROM_NEWSLETTER_ARXIV_USER_AGENT", "").strip()
    if not ua:
        ua = _arxiv_default_user_agent()
    return {"User-Agent": ua}


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = (response.headers.get("Retry-After") or "").strip()
    if not raw or not raw.isdigit():
        return None
    return float(raw)


def _backoff_seconds_for_status(
    attempt: int, status_code: int, response: httpx.Response
) -> float:
    """429 needs much longer waits than 502/503; honor Retry-After when present."""
    base = _RETRY_BASE_SEC * (2**attempt) + random.uniform(0, 0.35)
    if status_code != 429:
        return base
    ra = _retry_after_seconds(response)
    if ra is not None:
        return min(max(base, ra, 5.0), 600.0)
    # Shared CI IPs are often rate-limited; cap per-wait so the job does not run for hours.
    long_wait = max(base, 45.0 * (2**attempt) + random.uniform(0, 15.0))
    return min(long_wait, 180.0)


def _get_arxiv_api(
    client: httpx.Client,
    *,
    params: dict[str, Any],
) -> httpx.Response:
    for attempt in range(_MAX_ATTEMPTS):
        try:
            r = client.get(ARXIV_API, params=params)
        except httpx.TimeoutException:
            if attempt < _MAX_ATTEMPTS - 1:
                delay = _RETRY_BASE_SEC * (2**attempt) + random.uniform(0, 0.35)
                time.sleep(delay)
                continue
            raise
        if r.status_code in _RETRYABLE_STATUS and attempt < _MAX_ATTEMPTS - 1:
            delay = _backoff_seconds_for_status(attempt, r.status_code, r)
            time.sleep(delay)
            continue
        r.raise_for_status()
        return r


def _arxiv_topic_query() -> str:
    """Lucene-style query for ROM / SciML / digital twins (broad OR chain)."""
    return (
        "("
        'all:"reduced order" OR all:"model order reduction" OR all:"reduced-order" OR '
        'all:"digital twin" OR all:"digital twins" OR '
        'all:"scientific machine learning" OR all:"physics-informed" OR all:"physics informed" OR '
        'all:"neural operator" OR all:"Fourier neural operator" OR all:"DeepONet" OR '
        'all:"surrogate model" OR all:"POD" OR all:"POD-Galerkin" OR '
        "cat:cs.LG OR cat:cs.CE OR cat:cs.NA OR cat:physics.comp-ph OR cat:math.NA"
        ")"
    )


def _submitted_date_clause(start: datetime, end: datetime) -> str:
    """arXiv submittedDate range in GMT, minute resolution per API manual."""
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be timezone-aware")
    su = start.astimezone(timezone.utc).strftime("%Y%m%d%H%M")
    eu = end.astimezone(timezone.utc).strftime("%Y%m%d%H%M")
    return f"submittedDate:[{su} TO {eu}]"


def build_arxiv_search_query(start: datetime, end: datetime) -> str:
    return f"{_arxiv_topic_query()} AND {_submitted_date_clause(start, end)}"


def _text(el: ET.Element | None) -> str:
    if el is None or el.text is None:
        return ""
    return el.text.strip()


def _entry_to_hit(entry: ET.Element, keyword: str) -> SearchHit | None:
    title = _text(entry.find(f"{_ATOM}title"))
    summary = _text(entry.find(f"{_ATOM}summary"))
    published = _text(entry.find(f"{_ATOM}published"))
    link_el = None
    for link in entry.findall(f"{_ATOM}link"):
        if link.get("rel") in (None, "alternate") and link.get("href"):
            link_el = link
            break
    if link_el is None:
        for link in entry.findall(f"{_ATOM}link"):
            if link.get("href"):
                link_el = link
                break
    if link_el is None:
        return None
    url = (link_el.get("href") or "").strip()
    if not url:
        return None
    excerpt = f"Published: {published}\n{summary}"[:8000]
    return SearchHit(
        url=url,
        title=title or url,
        content=excerpt,
        keyword=keyword,
        raw_score=None,
        source_category=CATEGORY_PAPERS,
    )


def fetch_arxiv_hits(
    start: datetime,
    end: datetime,
    *,
    max_results: int = 25,
    timeout: float = 180.0,
) -> tuple[list[SearchHit], dict[str, Any]]:
    """Query arXiv API with submittedDate filter; returns hits + raw metadata.

    *timeout* is the **read** timeout in seconds (export.arxiv.org can be slow; CI may need 120s+).
    If ``ROM_NEWSLETTER_ARXIV_READ_TIMEOUT`` is set in the environment (after ``load_env()``), it overrides *timeout*.
    """
    load_env()
    read_sec = timeout
    if (raw := os.environ.get("ROM_NEWSLETTER_ARXIV_READ_TIMEOUT", "").strip()):
        read_sec = float(raw)
    q = build_arxiv_search_query(start, end)
    params = {
        "search_query": q,
        "start": 0,
        "max_results": min(max(1, max_results), 2000),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    kw = "arxiv-api:submittedDate"
    # Long read timeout; short connect so dead connections fail fast.
    client_timeout = httpx.Timeout(connect=20.0, read=read_sec, write=30.0, pool=60.0)
    headers = _arxiv_request_headers()
    with httpx.Client(
        timeout=client_timeout,
        headers=headers,
        follow_redirects=True,
    ) as client:
        try:
            r = _get_arxiv_api(client, params=params)
        except httpx.HTTPStatusError as e:
            if e.response is not None and e.response.status_code in _RETRYABLE_STATUS:
                extra = ""
                if e.response.status_code == 429:
                    extra = (
                        " Rate limits are stricter from shared IPs (e.g. GitHub Actions); "
                        "set ROM_NEWSLETTER_ARXIV_USER_AGENT to something unique, wait, or use --no-arxiv."
                    )
                raise RuntimeError(
                    f"arXiv API returned HTTP {e.response.status_code} after {_MAX_ATTEMPTS} attempts."
                    f"{extra}"
                ) from e
            raise
        except httpx.TimeoutException as e:
            raise RuntimeError(
                f"arXiv API timed out after {_MAX_ATTEMPTS} attempts (read timeout {read_sec}s per try). "
                "Retry later, set ROM_NEWSLETTER_ARXIV_READ_TIMEOUT, or use --no-arxiv."
            ) from e
        body = r.text
    root = ET.fromstring(body)
    hits: list[SearchHit] = []
    seen: set[str] = set()
    for entry in root.findall(f"{_ATOM}entry"):
        hit = _entry_to_hit(entry, kw)
        if hit is None:
            continue
        u = hit.url
        if u in seen:
            continue
        seen.add(u)
        hits.append(hit)
    meta = {
        "api": ARXIV_API,
        "search_query": q,
        "requested_max_results": params["max_results"],
        "returned": len(hits),
    }
    return hits, meta

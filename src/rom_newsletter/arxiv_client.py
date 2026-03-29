from __future__ import annotations

import random
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

import httpx

from rom_newsletter.sources import CATEGORY_PAPERS
from rom_newsletter.search import SearchHit

ARXIV_API = "https://export.arxiv.org/api/query"
_ATOM = "{http://www.w3.org/2005/Atom}"

# arXiv occasionally returns 503/502/504; brief backoff retries usually succeed.
_RETRYABLE_STATUS = frozenset({429, 502, 503, 504})
_MAX_ATTEMPTS = 5
_RETRY_BASE_SEC = 1.0


def _get_arxiv_api(
    client: httpx.Client,
    *,
    params: dict[str, Any],
) -> httpx.Response:
    for attempt in range(_MAX_ATTEMPTS):
        r = client.get(ARXIV_API, params=params)
        if r.status_code in _RETRYABLE_STATUS and attempt < _MAX_ATTEMPTS - 1:
            delay = _RETRY_BASE_SEC * (2**attempt) + random.uniform(0, 0.35)
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
    timeout: float = 60.0,
) -> tuple[list[SearchHit], dict[str, Any]]:
    """Query arXiv API with submittedDate filter; returns hits + raw metadata."""
    q = build_arxiv_search_query(start, end)
    params = {
        "search_query": q,
        "start": 0,
        "max_results": min(max(1, max_results), 2000),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    kw = "arxiv-api:submittedDate"
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        try:
            r = _get_arxiv_api(client, params=params)
        except httpx.HTTPStatusError as e:
            if e.response is not None and e.response.status_code in _RETRYABLE_STATUS:
                raise RuntimeError(
                    f"arXiv API returned HTTP {e.response.status_code} after {_MAX_ATTEMPTS} attempts "
                    "(their export service is often overloaded). Retry in a few minutes, or use --no-arxiv."
                ) from e
            raise
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

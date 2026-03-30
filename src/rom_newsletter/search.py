from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from rom_newsletter.config import api_base_url
from rom_newsletter.sources import (
    CATEGORY_INDUSTRY,
    CATEGORY_PAPERS,
    Source,
    host_allowed,
    source_category_for_url,
)


@dataclass
class SearchHit:
    """Search result; `source_category` mirrors `category` from sources.json (papers | industry)."""

    url: str
    title: str
    content: str
    keyword: str
    raw_score: float | None = None
    source_category: str = CATEGORY_INDUSTRY

    def to_prompt_block(self) -> str:
        score = f" (score={self.raw_score})" if self.raw_score is not None else ""
        return (
            f"URL: {self.url}\n"
            f"Title: {self.title}\n"
            f"From-query: {self.keyword}{score}\n"
            f"Excerpt: {self.content}\n"
        )


def _canonical_url(url: str) -> str:
    try:
        p = urlparse(url.strip())
    except ValueError:
        return url.strip()
    if not p.netloc:
        return url.strip()
    path = p.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunparse((p.scheme, p.netloc.lower(), path, "", p.query, ""))


def _topic_clause() -> str:
    return (
        '(digital twin OR "digital twins" OR "reduced order" OR "reduced-order" OR ROM OR '
        '"scientific machine learning" OR "physics-informed" OR "physics informed" OR '
        '"Physics AI" OR "AI Physics" OR '
        '"operator learning" OR "neural operator" OR "surrogate model")'
    )


def build_keywords(sources: list[Source]) -> list[str]:
    """Same broad Tavily queries as :func:`build_tavily_keywords` (alias for older call sites)."""
    return build_tavily_keywords(sources, include_arxiv_web=True)


def tavily_keyword_category_pairs(
    sources: list[Source], *, include_arxiv_web: bool = False
) -> list[tuple[str, str]]:
    """(keyword, default_category) for Tavily requests.

    Uses a small set of **broad** topic queries (no ``site:``). Host filtering is optional in
    :func:`normalize_hits` (``allow_off_source``); :func:`source_category_for_url` assigns
    ``papers`` vs ``industry`` when the URL matches a configured source.

    ``include_arxiv_web`` is kept for API compatibility and does not change the keyword list.
    """
    _ = include_arxiv_web  # broad queries; arXiv preprints come from the Atom API
    if not sources:
        return []
    topic = _topic_clause()
    return [
        (
            f'{topic} (engineering simulation OR CAE OR "digital twin" OR SciML)',
            CATEGORY_INDUSTRY,
        ),
        (
            f'{topic} ("Physics AI" OR "surrogate model" OR "neural operator" OR Omniverse OR FNO)',
            CATEGORY_INDUSTRY,
        ),
    ]


def tavily_keyword_to_category(sources: list[Source], *, include_arxiv_web: bool = False) -> dict[str, str]:
    """Map Tavily keyword string → default category (URL-based classification preferred when ``sources`` is passed to ``normalize_hits``)."""
    return dict(tavily_keyword_category_pairs(sources, include_arxiv_web=include_arxiv_web))


def build_tavily_keywords(sources: list[Source], *, include_arxiv_web: bool = False) -> list[str]:
    """Broad Tavily queries; optional host restriction and per-URL category apply in :func:`normalize_hits`."""
    return [k for k, _ in tavily_keyword_category_pairs(sources, include_arxiv_web=include_arxiv_web)]


def merge_hits_ordered(*groups: list[SearchHit]) -> list[SearchHit]:
    """Concatenate groups in order; dedupe by canonical URL (first wins)."""
    seen: set[str] = set()
    out: list[SearchHit] = []
    for group in groups:
        for h in group:
            key = _canonical_url(h.url)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                SearchHit(
                    url=key,
                    title=h.title,
                    content=h.content,
                    keyword=h.keyword,
                    raw_score=h.raw_score,
                    source_category=h.source_category,
                )
            )
    return out


def filter_unseen(hits: list[SearchHit], seen_urls: set[str]) -> tuple[list[SearchHit], int]:
    """Drop hits whose canonical URL appears in seen_urls."""
    out: list[SearchHit] = []
    skipped = 0
    for h in hits:
        u = _canonical_url(h.url)
        if u in seen_urls:
            skipped += 1
            continue
        out.append(h)
    return out, skipped


def _flatten_tavily_results(response_obj: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    results = response_obj.get("results")
    if isinstance(results, list):
        for item in results:
            if isinstance(item, dict):
                out.append(item)
    return out


def _hit_from_item(
    item: dict[str, Any], keyword: str, *, source_category: str = CATEGORY_INDUSTRY
) -> SearchHit | None:
    url = item.get("url") or item.get("href") or ""
    if not url or not isinstance(url, str):
        return None
    title = str(item.get("title") or item.get("name") or "")
    content = str(
        item.get("content")
        or item.get("snippet")
        or item.get("raw_content")
        or ""
    )
    score = item.get("score")
    raw_score: float | None
    if isinstance(score, (int, float)):
        raw_score = float(score)
    else:
        raw_score = None
    return SearchHit(
        url=url.strip(),
        title=title.strip() or url,
        content=content.strip()[:8000],
        keyword=keyword,
        raw_score=raw_score,
        source_category=source_category,
    )


def run_search(
    token: str,
    keywords: list[str],
    *,
    max_results: int = 6,
    timeout: float = 120.0,
) -> dict[str, Any]:
    base = api_base_url()
    url = f"{base}/search/"
    payload = {"keywords": keywords, "max_results": max_results}
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    with httpx.Client(timeout=timeout) as client:
        r = client.post(url, json=payload, headers=headers)
        r.raise_for_status()
        return r.json()


def normalize_hits(
    search_json: dict[str, Any],
    allowed: frozenset[str],
    *,
    allow_off_source: bool = False,
    keyword_category: dict[str, str] | None = None,
    sources: list[Source] | None = None,
    date_window: tuple[datetime, datetime] | None = None,
) -> tuple[list[SearchHit], list[dict[str, Any]]]:
    """Return deduped hits and API error entries.

    Unless *allow_off_source* is true, drop hits whose URL host is not in *allowed*.

    When *sources* is set, ``SearchHit.source_category`` is taken from the matching source’s
    ``category`` (URL host); unknown hosts default to :data:`~rom_newsletter.sources.CATEGORY_INDUSTRY`.

    If *date_window* is set and the API returns a publish time on a result item, drop
    hits outside ``[start, end]`` before building the hit (cheap pre-filter).
    """
    errors: list[dict[str, Any]] = []
    raw_err = search_json.get("errors")
    if isinstance(raw_err, list):
        errors.extend([e for e in raw_err if isinstance(e, dict)])

    seen: set[str] = set()
    hits: list[SearchHit] = []
    queries = search_json.get("queries")
    if not isinstance(queries, list):
        return hits, errors

    if date_window is not None:
        from rom_newsletter.page_dates import published_datetime_from_search_item
    else:
        published_datetime_from_search_item = None  # type: ignore[assignment]

    for block in queries:
        if not isinstance(block, dict):
            continue
        keyword = str(block.get("keyword") or "")
        response = block.get("response")
        if not isinstance(response, dict):
            continue
        kw_cat = CATEGORY_INDUSTRY
        if keyword_category is not None:
            kw_cat = keyword_category.get(keyword, CATEGORY_INDUSTRY)
        for item in _flatten_tavily_results(response):
            if date_window is not None and published_datetime_from_search_item is not None:
                api_dt = published_datetime_from_search_item(item)
                if api_dt is not None:
                    w_start, w_end = date_window
                    if not (w_start <= api_dt <= w_end):
                        continue
            sh = _hit_from_item(item, keyword, source_category=kw_cat)
            if sh is None:
                continue
            if not allow_off_source and not host_allowed(sh.url, allowed):
                continue
            cat = (
                source_category_for_url(sh.url, sources)
                if sources is not None
                else sh.source_category
            )
            key = _canonical_url(sh.url)
            if key in seen:
                continue
            seen.add(key)
            hits.append(
                SearchHit(
                    url=key,
                    title=sh.title,
                    content=sh.content,
                    keyword=sh.keyword,
                    raw_score=sh.raw_score,
                    source_category=cat,
                )
            )
    return hits, errors


def hits_to_bundle_text(hits: list[SearchHit]) -> str:
    parts = [h.to_prompt_block() for h in hits]
    return "\n---\n".join(parts) if parts else "(no search results; do not invent items)"


def split_hits_by_source_category(
    hits: list[SearchHit],
) -> tuple[list[SearchHit], list[SearchHit]]:
    """Research (papers) vs industry, using `source_category` from discovery."""
    research: list[SearchHit] = []
    industry: list[SearchHit] = []
    for h in hits:
        if h.source_category == CATEGORY_PAPERS:
            research.append(h)
        else:
            industry.append(h)
    return research, industry


def hits_to_split_bundle_text(hits: list[SearchHit]) -> tuple[str, str]:
    """Separate prompt bundles aligned with `sources.json` categories."""
    research, industry = split_hits_by_source_category(hits)
    r_text = hits_to_bundle_text(research)
    i_text = hits_to_bundle_text(industry)
    return r_text, i_text


def search_artifacts_dump(hits: list[SearchHit], search_json: dict[str, Any]) -> str:
    """JSON for debugging / audit trail (Tavily-only)."""
    return json.dumps(
        {
            "hit_count": len(hits),
            "hits": [
                {
                    "url": h.url,
                    "title": h.title,
                    "keyword": h.keyword,
                    "score": h.raw_score,
                }
                for h in hits
            ],
            "errors": search_json.get("errors"),
            "combined_answer": search_json.get("combined_answer"),
        },
        indent=2,
    )


def pipeline_report_json(
    *,
    window: dict[str, str],
    arxiv: dict[str, Any] | None,
    rss: dict[str, Any] | None,
    newsroom: dict[str, Any] | None = None,
    tavily: dict[str, Any] | None,
    merged_hits: list[SearchHit],
    skipped_seen: int,
    theme_filter: dict[str, Any] | None = None,
    phase_timings_ms: dict[str, float] | None = None,
) -> str:
    """Full discovery audit: arXiv API + RSS + newsroom listings + Tavily + merge."""
    payload: dict[str, Any] = {
        "window_utc": window,
        "arxiv": arxiv,
        "rss": rss,
        "newsroom": newsroom,
        "tavily": tavily,
        "merged": {
            "hit_count": len(merged_hits),
            "skipped_seen": skipped_seen,
            "hits": [
                {
                    "url": h.url,
                    "title": h.title,
                    "keyword": h.keyword,
                    "score": h.raw_score,
                    "source_category": h.source_category,
                }
                for h in merged_hits
            ],
        },
    }
    if theme_filter is not None:
        payload["theme_filter"] = theme_filter
    if phase_timings_ms:
        payload["phase_timings_ms"] = phase_timings_ms
    return json.dumps(payload, indent=2, ensure_ascii=False)

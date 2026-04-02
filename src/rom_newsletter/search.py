from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse, urlunparse

from rom_newsletter.sources import CATEGORY_INDUSTRY, CATEGORY_PAPERS


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


def pipeline_report_json(
    *,
    window: dict[str, str],
    arxiv: dict[str, Any] | None,
    rss: dict[str, Any] | None,
    newsroom: dict[str, Any] | None = None,
    merged_hits: list[SearchHit],
    skipped_seen: int,
    theme_filter: dict[str, Any] | None = None,
    phase_timings_ms: dict[str, float] | None = None,
) -> str:
    """Full discovery audit: arXiv API + RSS + newsroom listings + merge."""
    payload: dict[str, Any] = {
        "window_utc": window,
        "arxiv": arxiv,
        "rss": rss,
        "newsroom": newsroom,
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

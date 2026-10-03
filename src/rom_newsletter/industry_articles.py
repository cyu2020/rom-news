"""Retrieve article bodies and keep relevant passages within the industry's prompt budget."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from html.parser import HTMLParser

import httpx

from rom_newsletter.relevance import theme_score
from rom_newsletter.search import SearchHit

_SKIP = {"nav", "header", "footer", "aside", "script", "style", "noscript", "form", "svg"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}
_BLOCK = {"p", "div", "section", "h1", "h2", "h3", "h4", "li", "figure", "blockquote", "br"}
_BODY_CLASSES = {"entry-content", "post-content", "article-content", "article-body", "w-richtext"}


class _ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool, list[str] | None]] = []
        self.candidates: list[tuple[int, list[str]]] = []

    def _append(self, text: str):
        for _, _, parts in self.stack:
            if parts is not None:
                parts.append(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set((attrs.get("class") or "").split())
        blocked = (
            (bool(self.stack) and self.stack[-1][1]) or tag in _SKIP
            or "hidden" in attrs or attrs.get("aria-hidden") == "true"
            or bool(classes & {"related-posts", "sharedaddy", "table-of-contents"})
        )
        if not blocked and tag in _BLOCK:
            self._append("\n\n")
        priority = 4 if classes & _BODY_CLASSES else 3 if tag == "article" else 2 if tag == "main" else 0
        parts = [] if priority and not blocked else None
        if parts is not None:
            self.candidates.append((priority, parts))
        if tag not in _VOID:
            self.stack.append((tag, blocked, parts))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                if not self.stack[i][1] and tag in _BLOCK:
                    self._append("\n\n")
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self.stack and not self.stack[-1][1]:
            self._append(data)


def extract_article_text(html: str, *, max_chars: int = 24000) -> str:
    """Prefer article-content containers, then article/main; never score the whole page."""
    parser = _ArticleParser()
    parser.feed(html)
    candidates = []
    for priority, parts in parser.candidates:
        text = "\n\n".join(
            p for part in "".join(parts).split("\n\n") if (p := " ".join(part.split()))
        )
        if text:
            candidates.append((priority, len(text), text))
    return max(candidates, default=(0, 0, ""))[2][:max_chars]


def enrich_industry_hits(hits: list[SearchHit], *, timeout: float = 15.0, max_workers: int = 6):
    """Best effort for already date-filtered hits; preserve identity, order and teaser on failure."""
    def fetch(hit):
        try:
            with httpx.Client(
                timeout=timeout, follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; rom-newsletter/1.0)"},
            ) as client:
                response = client.get(hit.url)
                response.raise_for_status()
            if len(response.content) > 2_000_000:
                return hit, "article exceeds 2 MB limit"
            body = extract_article_text(response.text)
            if not body:
                return hit, "no article body found; retained discovery excerpt"
            date_notes = re.findall(r"Date \(UTC\): \d{4}-\d{2}-\d{2}", hit.content)
            content = "\n\n".join([*date_notes, body])
            return replace(hit, content=content), None
        except (httpx.HTTPError, OSError, ValueError) as exc:
            return hit, str(exc)

    with ThreadPoolExecutor(max_workers=max(1, min(16, max_workers))) as pool:
        results = list(pool.map(fetch, hits))
    return [hit for hit, _ in results], {
        "input": len(hits),
        "enriched": sum(error is None for _, error in results),
        "errors": [{"url": hit.url, "error": error} for hit, error in results if error is not None],
    }


def industry_excerpt(hit: SearchHit, max_chars: int, weighted_patterns=None) -> SearchHit:
    """Retain theme evidence and nearby context instead of truncating the article's opening."""
    if len(hit.content) <= max_chars:
        return hit
    paragraphs = [p.strip() for p in hit.content.split("\n\n") if p.strip()]
    ranked = sorted(
        range(len(paragraphs)),
        key=lambda i: -theme_score(replace(hit, title="", content=paragraphs[i]), weighted_patterns),
    )
    anchors = [i for i in ranked if theme_score(replace(hit, title="", content=paragraphs[i]), weighted_patterns)]
    if not anchors:
        return replace(hit, content=hit.content[:max_chars])
    order = list(dict.fromkeys([
        *anchors,
        *(j for i in anchors for j in (i - 1, i + 1) if 0 <= j < len(paragraphs)),
        *range(len(paragraphs)),
    ]))
    selected: dict[int, str] = {}
    used = 0
    for i in order:
        paragraph = paragraphs[i]
        cost = len(paragraph) + (2 if selected else 0)
        if used + cost <= max_chars:
            selected[i] = paragraph
            used += cost
        elif not selected:
            selected[i] = paragraph[:max_chars]
            used = max_chars
    return replace(hit, content="\n\n".join(selected[i] for i in sorted(selected)))

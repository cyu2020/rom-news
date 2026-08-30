"""Fetch article URLs from vendor newsroom listing pages (Webflow HTML, sitemaps, or static site cards).

Optional direct discovery for sources with ``newsroom_listing: true`` in ``sources.json``
(same idea as per-source RSS: direct discovery for that vendor).

Supported ``sources.json`` ``id`` values: ``physicsx``, ``neural-concept``, ``emmi-ai``, ``siemens``,
``p1-ai``, ``luminary``, ``vinci4d``, ``akselos``.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

import httpx

from rom_newsletter.page_dates import extract_published_datetime, parse_datetime_string
from rom_newsletter.search import SearchHit
from rom_newsletter.sources import CATEGORY_INDUSTRY, Source

_UA = (
    "Mozilla/5.0 (compatible; rom-newsletter/1.0; +https://github.com/) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Siemens news.siemens.com listing is mostly client-rendered; use official en-us sitemap (loc + lastmod).
SIEMENS_NEWS_SITEMAP_URL = "https://news.siemens.com/en-us/sitemap-en-us.xml"
_NS_SM = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


def _fetch_html(url: str, timeout: float) -> str:
    with httpx.Client(timeout=timeout, headers={"User-Agent": _UA}, follow_redirects=True) as c:
        r = c.get(url)
        r.raise_for_status()
        return r.text


def _article_title_from_html(html: str) -> str | None:
    m = re.search(
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        html,
        re.I,
    )
    if not m:
        m = re.search(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
            html,
            re.I,
        )
    if m:
        t = m.group(1).strip()
        if t:
            return t[:500]
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I)
    if m:
        t = m.group(1).strip()
        return t[:500] if t else None
    return None


def _parse_dmy_slash(s: str) -> datetime | None:
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s.strip())
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return datetime(y, mo, d, tzinfo=timezone.utc)
    except ValueError:
        return None


def _parse_physicsx_listing_date(s: str) -> datetime | None:
    s = s.strip()
    if not s:
        return None
    dt = _parse_dmy_slash(s)
    if dt is not None:
        return dt
    for fmt in ("%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return parse_datetime_string(s)


def parse_physicsx_newsroom(html: str, listing_url: str) -> list[tuple[str, str, datetime | None]]:
    base = f"{urlparse(listing_url).scheme}://{urlparse(listing_url).netloc}"
    out: list[tuple[str, str, datetime | None]] = []
    seen: set[str] = set()
    for block in html.split("w-dyn-item"):
        m = re.search(r'href="(/newsroom/[^"]+)"', block)
        if not m:
            continue
        path = m.group(1)
        if path.rstrip("/") == "/newsroom":
            continue
        url = urljoin(base, path)
        if url in seen:
            continue
        seen.add(url)
        tm = re.search(r'class="news-item-title"[^>]*>([^<]+)</div>', block)
        dm = re.search(r'class="news-item-date"[^>]*>([^<]+)</div>', block)
        title = tm.group(1).strip() if tm else path.rsplit("/", 1)[-1].replace("-", " ")
        dt = _parse_physicsx_listing_date(dm.group(1)) if dm else None
        out.append((url, title, dt))
    return out


def parse_neural_concept_press(html: str) -> list[tuple[str, str, datetime | None]]:
    """Press-releases index at ``https://www.neuralconcept.com/press-releases``.

    Each card renders ``n-blog_card-category`` (the publish date "July 1, 2026"),
    ``n-blog_card-title`` and an ``n-blog_card-link`` to ``/press-release/<slug>/``.
    The calendar date is on the listing, so no per-article fetch is needed.
    """
    base = "https://www.neuralconcept.com"
    out: list[tuple[str, str, datetime | None]] = []
    seen: set[str] = set()
    for block in html.split("w-dyn-item"):
        lm = re.search(r'href="(/press-release/[^"]+)"', block)
        if not lm:
            continue
        path = lm.group(1)
        url = urljoin(base, path.rstrip("/") + "/")
        if url in seen:
            continue
        seen.add(url)
        dm = re.search(r'class="n-blog_card-category"[^>]*>([^<]+)<', block)
        dt = None
        if dm:
            s = dm.group(1).strip()
            for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y"):
                try:
                    dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    continue
        tm = re.search(r'class="n-blog_card-title"[^>]*>([\s\S]*?)</div>', block)
        if tm:
            title = re.sub(r"<[^>]+>", "", tm.group(1))
            title = re.sub(r"\s+", " ", title).strip()
        else:
            title = path
        out.append((url, title, dt))
    return out


def parse_siemens_news_sitemap(xml_text: str) -> list[tuple[str, str, datetime | None]]:
    """Parse ``news.siemens.com`` regional sitemap: ``<loc>`` + ``<lastmod>``.

    Title is derived from the URL slug (listing pages do not expose a static article list).
    """
    out: list[tuple[str, str, datetime | None]] = []
    seen: set[str] = set()
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    for url_el in root.findall(f"{_NS_SM}url"):
        loc = url_el.find(f"{_NS_SM}loc")
        if loc is None or not (loc.text or "").strip():
            continue
        url = loc.text.strip()
        if "news.siemens.com" not in url or "/en-us/" not in url:
            continue
        path = urlparse(url).path.rstrip("/")
        parts = [p for p in path.split("/") if p]
        if len(parts) < 2 or parts[0].lower() != "en-us":
            continue
        slug = parts[-1]
        if not slug or slug.lower() == "en-us":
            continue
        lm = url_el.find(f"{_NS_SM}lastmod")
        dt: datetime | None = None
        if lm is not None and (lm.text or "").strip():
            dt = parse_datetime_string(lm.text.strip())
        title = " ".join(slug.replace("-", " ").split()).title()
        if url in seen:
            continue
        seen.add(url)
        out.append((url, title, dt))
    return out


def _p1_press_url(url: str) -> bool:
    """Homepage ``p-1.ai`` links to external press; keep those, not social/job/media embeds."""
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return False
        h = (p.netloc or "").lower()
        path = p.path or ""
        if "linkedin.com" in h:
            return False
        if "businesswire.com" in h:
            return True
        if "fortune.com" in h:
            return True
        if "arxiv.org" in h and "/abs/" in path:
            return True
        if "corememory.com" in h and "/p/" in path:
            return True
        return False
    except ValueError:
        return False


def _p1_title_placeholder(url: str) -> str:
    try:
        path = urlparse(url).path.rstrip("/")
        seg = path.split("/")[-1] or path or url
        seg = unquote(seg)
        t = seg.replace("-", " ").replace("_", " ").strip()
        return t[:240] if t else url
    except Exception:
        return url


def parse_p1_ai_homepage(html: str) -> list[tuple[str, str, None]]:
    """Curated press links on ``https://p-1.ai/`` (no dedicated ``/news`` section)."""
    out: list[tuple[str, str, None]] = []
    seen: set[str] = set()
    for m in re.finditer(r'href="(https?://[^"]+)"', html):
        url = m.group(1).strip()
        if not _p1_press_url(url):
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append((url, _p1_title_placeholder(url), None))
    return out


def parse_emmi_news(html: str) -> list[tuple[str, str, None]]:
    base = "https://www.emmi.ai"
    out: list[tuple[str, str, None]] = []
    seen: set[str] = set()
    for block in html.split("w-dyn-item"):
        m = re.search(r'href="(/news/[^"]+)"', block)
        if not m:
            continue
        path = m.group(1)
        if path.rstrip("/") == "/news":
            continue
        url = urljoin(base, path)
        if url in seen:
            continue
        seen.add(url)
        tm = re.search(r'class="[^"]*heading[^"]*"[^>]*>([^<]+)<', block)
        title = tm.group(1).strip() if tm else path.rsplit("/", 1)[-1]
        out.append((url, title, None))
    return out


def parse_luminary_press_resources(
    html: str, listing_url: str
) -> list[tuple[str, str, datetime | None]]:
    """Luminary Astro listing: press cards marked ``class="resource-card" data-category="Press"``.

    The listing markup previously used ``data-tag="Press"`` + a ``MM.DD.YYYY`` span + ``<h3>``,
    but now renders cards as ``<div class="resource-card" data-category="Press" ...>``
    with an ``<a href="..." data-hover="card">``. Press cards link to ``/resources/...`` (or an
    external press pickup), carry no calendar date on the listing, so each article page is
    fetched downstream for its published timestamp.
    """
    base = f"{urlparse(listing_url).scheme}://{urlparse(listing_url).netloc}"
    out: list[tuple[str, str, datetime | None]] = []
    seen: set[str] = set()
    for m in re.finditer(
        r'class="resource-card"\s+data-category="Press"[^>]*>.*?'
        r'<a\s+href="([^"]+)"\s+data-hover="card"',
        html,
        re.DOTALL | re.I,
    ):
        href = m.group(1).strip()
        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            href = urljoin(base, href)
        url = href.split("#", 1)[0].split("?", 1)[0]
        if not url.lower().startswith(("http://", "https://")):
            continue
        if url in seen:
            continue
        seen.add(url)
        title = (
            urlparse(url).path.rstrip("/").split("/")[-1]
            or url
        )
        out.append((url, title[:500], None))
    return out


def _vinci_title_from_url(url: str) -> str:
    try:
        path = urlparse(url).path.rstrip("/")
        slug = path.split("/")[-1] or path or url
        slug = unquote(slug)
        t = slug.replace("-", " ").replace("_", " ").strip()
        return t[:500] if t else url
    except Exception:
        return url


def parse_vinci_news_listing(html: str) -> list[tuple[str, str, None]]:
    """Newsroom/blog index: article URLs under ``/news/<slug>/`` or ``/blog/<slug>/``.

    The article index moved from ``/news/`` to ``/blog/`` (``getvinci.ai/news`` now
    301-redirects to ``getvinci.ai/blog``); accept both paths so stale links still parse.
    """
    out: list[tuple[str, str, None]] = []
    seen: set[str] = set()
    for m in re.finditer(
        r'href="(https://www\.getvinci\.ai/(?:news|blog)/([a-z0-9][a-z0-9_-]*)/)"',
        html,
        re.I,
    ):
        url = m.group(1).strip().rstrip("/") + "/"
        if url in seen:
            continue
        seen.add(url)
        out.append((url, _vinci_title_from_url(url), None))
    return out


# Single-path posts on akselos.com news/resources listing (exclude site chrome).
_AKSELOS_SINGLE_SEGMENT_SKIP = frozenset(
    {
        "contact-us",
        "feed",
        "privacy",
        "resources",
        "software-release-notes",
        "spm",
        "wind-power",
        "our-commitment-to-cybersecurity",
        "wp-json",
        "xmlrpc.php",
    }
)


def parse_akselos_resources_news(html: str, listing_url: str) -> list[tuple[str, str, None]]:
    """Resource hub with News filter: article URLs as ``/slug/`` (single segment), not white papers."""
    out: list[tuple[str, str, None]] = []
    seen: set[str] = set()
    for m in re.finditer(r'href="(https?://(?:www\.)?akselos\.com/[^"?#]+)', html, re.I):
        raw = m.group(1).strip().rstrip("/")
        try:
            p = urlparse(raw)
        except ValueError:
            continue
        h = (p.netloc or "").lower()
        if h.removeprefix("www.") != "akselos.com":
            continue
        parts = [x for x in p.path.strip("/").split("/") if x]
        if len(parts) != 1:
            continue
        seg = parts[0].lower()
        if seg in _AKSELOS_SINGLE_SEGMENT_SKIP:
            continue
        if seg.endswith((".php", ".xml")):
            continue
        url = urljoin(f"https://akselos.com/", parts[0] + "/")
        if url in seen:
            continue
        seen.add(url)
        title = parts[0].replace("-", " ").replace("_", " ").strip().title()[:500]
        out.append((url, title, None))
    return out


def _parser_for_source_id(sid: str | None) -> str | None:
    return {
        "physicsx": "physicsx",
        "neural-concept": "neural_concept",
        "emmi-ai": "emmi",
        "siemens": "siemens",
        "p1-ai": "p1_ai",
        "luminary": "luminary",
        "vinci4d": "vinci",
        "akselos": "akselos",
    }.get((sid or "").strip())


def fetch_newsroom_hits(
    sources: list[Source],
    start_utc: datetime,
    end_utc: datetime,
    *,
    timeout: float = 25.0,
    max_workers: int = 10,
) -> tuple[list[SearchHit], dict[str, Any]]:
    """Return hits from newsroom listings, filtered to ``[start_utc, end_utc]``."""
    pending: list[
        tuple[Source, str, str, str, datetime | None]
    ] = []  # src, keyword, url, title, listing_dt or None
    errors: list[dict[str, str]] = []
    per_source_counts: dict[str, dict[str, int]] = {}

    for s in sources:
        if not s.newsroom_listing:
            continue
        pid = _parser_for_source_id(s.id)
        if not pid:
            errors.append(
                {
                    "id": s.id or "",
                    "error": (
                        "newsroom_listing requires id physicsx | neural-concept | emmi-ai | "
                        "siemens | p1-ai | luminary | vinci4d | akselos"
                    ),
                }
            )
            continue
        key = s.id or s.label
        try:
            if pid == "siemens":
                html = _fetch_html(SIEMENS_NEWS_SITEMAP_URL, timeout)
            else:
                html = _fetch_html(s.url, timeout)
        except (httpx.HTTPError, OSError) as e:
            errors.append(
                {
                    "id": key,
                    "url": SIEMENS_NEWS_SITEMAP_URL if pid == "siemens" else s.url,
                    "error": str(e),
                }
            )
            continue

        if pid == "physicsx":
            candidates = parse_physicsx_newsroom(html, s.url)
        elif pid == "neural_concept":
            candidates = parse_neural_concept_press(html)
        elif pid == "siemens":
            candidates = parse_siemens_news_sitemap(html)
        elif pid == "p1_ai":
            candidates = parse_p1_ai_homepage(html)
        elif pid == "luminary":
            candidates = parse_luminary_press_resources(html, s.url)
        elif pid == "vinci":
            candidates = parse_vinci_news_listing(html)
        elif pid == "akselos":
            candidates = parse_akselos_resources_news(html, s.url)
        else:
            candidates = parse_emmi_news(html)

        kw = f"newsroom:{pid}"
        n_raw = len(candidates)
        for url, title, listing_dt in candidates:
            pending.append((s, kw, url, title, listing_dt))
        per_source_counts[key] = {"listing_candidates": n_raw}

    # Fetch article HTML only when listing did not include a calendar date
    need_fetch: list[tuple[int, str]] = []
    for i, row in enumerate(pending):
        if row[4] is None:
            need_fetch.append((i, row[2]))

    resolved_idx: dict[int, datetime | None] = {}
    resolved_title: dict[int, str] = {}

    if need_fetch:
        tw = max(1, min(32, max_workers))

        def fetch_one(item: tuple[int, str]) -> tuple[int, datetime | None, str | None]:
            idx, article_url = item
            try:
                html = _fetch_html(article_url, timeout)
                return (
                    idx,
                    extract_published_datetime(html),
                    _article_title_from_html(html),
                )
            except (httpx.HTTPError, OSError):
                return idx, None, None

        with ThreadPoolExecutor(max_workers=tw) as ex:
            futs = [ex.submit(fetch_one, x) for x in need_fetch]
            for fut in as_completed(futs):
                idx, dt, title_opt = fut.result()
                resolved_idx[idx] = dt
                if title_opt:
                    resolved_title[idx] = title_opt

    hits: list[SearchHit] = []
    dropped_no_date = 0
    dropped_outside = 0
    for i, (s, kw, url, title, listing_dt) in enumerate(pending):
        dt = listing_dt if listing_dt is not None else resolved_idx.get(i)
        if i in resolved_title:
            title = resolved_title[i]
        if dt is None:
            dropped_no_date += 1
            continue
        if not (start_utc <= dt <= end_utc):
            dropped_outside += 1
            continue
        date_note = dt.date().isoformat()
        content = f"{title}\nDate (UTC): {date_note}"
        hits.append(
            SearchHit(
                url=url,
                title=title,
                content=content,
                keyword=kw,
                raw_score=None,
                source_category=s.category or CATEGORY_INDUSTRY,
            )
        )

    meta: dict[str, Any] = {
        "hit_count": len(hits),
        "dropped_no_date": dropped_no_date,
        "dropped_outside_window": dropped_outside,
        "per_source": per_source_counts,
        "errors": errors,
    }
    return hits, meta

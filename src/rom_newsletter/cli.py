from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

from rom_newsletter.arxiv_client import fetch_arxiv_hits
from rom_newsletter.compose import (
    compose_newsletter,
    newsletter_to_json_dict,
    openai_client,
)
from rom_newsletter.config import llm_model, project_root
from rom_newsletter.dates import utc_window_for_week
from rom_newsletter.history import load_seen_urls, merge_history
from rom_newsletter.newsroom_listings import fetch_newsroom_hits
from rom_newsletter.relevance import apply_theme_filter
from rom_newsletter.render import render_html
from rom_newsletter.rss_client import DEFAULT_FEED_URLS, fetch_rss_hits
from rom_newsletter.search import (
    filter_unseen,
    hits_to_split_bundle_text,
    merge_hits_ordered,
    pipeline_report_json,
)
from rom_newsletter.sources import (
    KIND_ARXIV,
    allowed_hosts,
    effective_source_kind,
    feed_entries_from_sources,
    feed_url_to_category_map,
    load_sources,
    resolve_default_sources_path,
)
from rom_newsletter.topic import load_topic_for_run


def _parse_date(s: str | None) -> date:
    if not s:
        return date.today()
    return date.fromisoformat(s)


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


def _log_phase_timings(
    *,
    arxiv_ms: float,
    rss_ms: float,
    newsroom_ms: float,
    compose_ms: float | None = None,
) -> None:
    """Wall-clock ms per pipeline phase (stderr; discovery order matches run)."""
    parts = [
        f"arXiv={arxiv_ms:.0f}ms",
        f"RSS={rss_ms:.0f}ms",
        f"newsroom={newsroom_ms:.0f}ms",
    ]
    if compose_ms is not None:
        parts.append(f"compose={compose_ms:.0f}ms")
    print("rom-newsletter phase timings: " + " ".join(parts), file=sys.stderr)


def _has_arxiv(sources: list) -> bool:
    return any(effective_source_kind(s) == KIND_ARXIV for s in sources)


def _collect_feed_entries(sources: list) -> list[tuple[str, frozenset[str]]]:
    """Merge per-source ``rss`` from ``sources.json`` (first), then built-in defaults; dedupe by URL."""
    merged: list[tuple[str, frozenset[str]]] = []
    seen: set[str] = set()
    for url, extras in feed_entries_from_sources(sources):
        if url not in seen:
            seen.add(url)
            merged.append((url, extras))
    for u in DEFAULT_FEED_URLS:
        if u not in seen:
            seen.add(u)
            merged.append((u, frozenset()))
    return merged


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Build a weekly ROM / SciML / Digital Twins newsletter via an OpenAI-compatible LLM API (LLM_BASE_URL, LLM_API_KEY, LLM_MODEL)."
    )
    p.add_argument(
        "--sources",
        type=Path,
        default=None,
        help="Path to sources.json (default: <repo>/sources.json)",
    )
    p.add_argument(
        "--topic",
        type=Path,
        default=None,
        help="Topic profile JSON (default: env ROM_NEWSLETTER_TOPIC or <repo>/topic.json; else built-in ROM defaults)",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: <repo>/dist)",
    )
    p.add_argument("--date", dest="week_date", default=None, help="Week ISO date YYYY-MM-DD (window ends this day)")
    p.add_argument(
        "--window-days",
        type=int,
        default=7,
        help="UTC window length ending on --date (default: 7)",
    )
    p.add_argument(
        "--model",
        default=None,
        help="Chat model id (overrides LLM_MODEL when set)",
    )
    p.add_argument(
        "--arxiv-max",
        type=int,
        default=25,
        help="Max arXiv API results (1–2000; default 25)",
    )
    p.add_argument(
        "--dry-run-search",
        action="store_true",
        help="Run discovery only; print summary and write *-search.json; skip LLM",
    )
    p.add_argument(
        "--no-arxiv",
        action="store_true",
        help="Skip arXiv API (submittedDate) fetch",
    )
    p.add_argument(
        "--no-rss",
        action="store_true",
        help="Skip RSS/Atom feeds (per-source rss in sources.json + defaults)",
    )
    p.add_argument(
        "--no-newsroom",
        action="store_true",
        help="Skip newsroom listing scrapes (sources with newsroom_listing in sources.json)",
    )
    p.add_argument(
        "--no-skip-seen",
        action="store_true",
        help="Do not exclude URLs listed in the history file",
    )
    p.add_argument(
        "--history-file",
        type=Path,
        default=None,
        help="Seen-URL ledger JSON (default: <repo>/.rom-newsletter/seen_urls.json)",
    )
    p.add_argument(
        "--refine",
        action="store_true",
        help="Second LLM pass to tighten citations against excerpts",
    )
    p.add_argument(
        "--template-dir",
        type=Path,
        default=None,
        help="Override Jinja template directory",
    )
    p.add_argument(
        "--theme-min-score",
        type=int,
        default=2,
        help="Keep non-arXiv hits with ROM/SciML/twin/CAE theme score >= N (0=off, rank only). Default: 2",
    )
    p.add_argument(
        "--theme-floor-non-arxiv",
        type=int,
        default=5,
        help="Target minimum non-arXiv hits; backfill only from scores >= --theme-backfill-min-score",
    )
    p.add_argument(
        "--theme-backfill-min-score",
        type=int,
        default=1,
        help="When backfilling to reach the floor, only add hits with theme score >= this",
    )
    p.add_argument(
        "--max-non-arxiv-hits",
        type=int,
        default=48,
        help="Max non-arXiv hits passed to the model after ranking (arXiv not capped)",
    )

    args = p.parse_args(argv)
    arxiv_max = max(1, min(2000, args.arxiv_max))
    if arxiv_max != args.arxiv_max:
        print(f"Clamped --arxiv-max to {arxiv_max}.", file=sys.stderr)

    root = project_root()
    try:
        topic = load_topic_for_run(root, args.topic)
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        sys.exit(1)

    sources_path = args.sources or resolve_default_sources_path(root)
    if not sources_path.is_file():
        print(f"Missing sources file: {sources_path}", file=sys.stderr)
        sys.exit(1)

    out_dir = args.output_dir or (root / "dist")
    out_dir.mkdir(parents=True, exist_ok=True)

    week_d = _parse_date(args.week_date)
    week_label = f"Week of {week_d.isoformat()}"
    stamp = week_d.isoformat()

    if args.window_days < 1:
        print("--window-days must be >= 1", file=sys.stderr)
        sys.exit(1)

    start_utc, end_utc = utc_window_for_week(week_d, window_days=args.window_days)
    window_meta = {
        "start": start_utc.isoformat(),
        "end": end_utc.isoformat(),
        "window_days": args.window_days,
    }

    try:
        sources = load_sources(sources_path)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"Failed to load sources: {e}", file=sys.stderr)
        sys.exit(1)
    if not sources:
        print("No sources in config (JSON `sources` array is empty).", file=sys.stderr)
        sys.exit(1)

    allow = allowed_hosts(sources)
    history_path = args.history_file or (root / ".rom-newsletter" / "seen_urls.json")
    seen: set[str] = set() if args.no_skip_seen else load_seen_urls(history_path)

    arxiv_ms = rss_ms = newsroom_ms = 0.0

    arxiv_meta: dict | None = None
    arxiv_hits: list = []
    if not args.no_arxiv and _has_arxiv(sources) and topic.arxiv_search_query is not None:
        t_arxiv = time.perf_counter()
        arxiv_hits, arxiv_meta = fetch_arxiv_hits(
            start_utc,
            end_utc,
            max_results=arxiv_max,
            topic_query=topic.arxiv_search_query,
        )
        arxiv_ms = _elapsed_ms(t_arxiv)
        arxiv_meta = {**(arxiv_meta or {}), "hit_count": len(arxiv_hits)}

    rss_meta: dict | None = None
    rss_hits: list = []
    if not args.no_rss:
        t_rss = time.perf_counter()
        feed_entries = _collect_feed_entries(sources)
        rss_hits, rss_errors = fetch_rss_hits(
            feed_entries,
            start_utc,
            end_utc,
            allowed_feed_hosts=allow,
            feed_url_category=feed_url_to_category_map(sources),
        )
        rss_ms = _elapsed_ms(t_rss)
        rss_meta = {
            "hit_count": len(rss_hits),
            "errors": rss_errors,
            "feeds": [
                {"url": u, "extra_feed_hosts": sorted(eh)}
                for u, eh in feed_entries
            ],
        }

    newsroom_hits: list = []
    newsroom_meta: dict | None = None
    if not args.no_newsroom:
        t_newsroom = time.perf_counter()
        newsroom_hits, newsroom_meta = fetch_newsroom_hits(
            sources,
            start_utc,
            end_utc,
            timeout=15.0,
            max_workers=6,
        )
        newsroom_ms = _elapsed_ms(t_newsroom)

    merged = merge_hits_ordered(arxiv_hits, rss_hits, newsroom_hits)
    merged, skipped_seen = filter_unseen(merged, seen)

    if topic.theme_disabled and args.theme_min_score > 0:
        print(
            "rom-newsletter: topic.theme.disabled is true; effective --theme-min-score is 0.",
            file=sys.stderr,
        )
    theme_min = 0 if topic.theme_disabled else max(0, args.theme_min_score)
    merged, theme_stats = apply_theme_filter(
        merged,
        min_score=theme_min,
        max_non_arxiv=max(1, args.max_non_arxiv_hits),
        floor_non_arxiv=max(0, args.theme_floor_non_arxiv),
        backfill_min_score=max(0, args.theme_backfill_min_score),
        weighted_patterns=topic.theme_patterns,
    )

    phase_timings_ms = {
        "arxiv": round(arxiv_ms, 1),
        "rss": round(rss_ms, 1),
        "newsroom": round(newsroom_ms, 1),
    }

    report = pipeline_report_json(
        window=window_meta,
        arxiv=arxiv_meta,
        rss=rss_meta,
        newsroom=newsroom_meta,
        merged_hits=merged,
        skipped_seen=skipped_seen,
        theme_filter=theme_stats,
        phase_timings_ms=phase_timings_ms,
    )

    base = f"newsletter-{stamp}"
    search_path = out_dir / f"{base}-search.json"
    search_path.write_text(report, encoding="utf-8")

    if args.dry_run_search:
        _log_phase_timings(
            arxiv_ms=arxiv_ms,
            rss_ms=rss_ms,
            newsroom_ms=newsroom_ms,
            compose_ms=None,
        )
        print(f"Window (UTC): {window_meta['start']} .. {window_meta['end']}")
        print(
            f"arXiv hits: {len(arxiv_hits)}  RSS hits: {len(rss_hits)}  "
            f"Newsroom hits: {len(newsroom_hits)}"
        )
        print(f"Merged (after skip-seen + theme filter): {len(merged)}  skipped_seen: {skipped_seen}")
        if theme_stats:
            print(f"Theme filter: {theme_stats}")
        print(f"Wrote {search_path}")
        return

    try:
        model = args.model.strip() if args.model else llm_model()
    except RuntimeError as e:
        print(e, file=sys.stderr)
        sys.exit(1)

    research_bundle, industry_bundle = hits_to_split_bundle_text(merged)
    client = openai_client()
    t_compose = time.perf_counter()
    draft = compose_newsletter(
        client,
        model=model,
        research_bundle=research_bundle,
        industry_bundle=industry_bundle,
        week_hint=week_label,
        refine=args.refine,
        topic=topic,
    )
    compose_ms = _elapsed_ms(t_compose)
    _log_phase_timings(
        arxiv_ms=arxiv_ms,
        rss_ms=rss_ms,
        newsroom_ms=newsroom_ms,
        compose_ms=compose_ms,
    )

    json_path = out_dir / f"{base}.json"
    json_path.write_text(
        json.dumps(newsletter_to_json_dict(draft), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    html_str = render_html(
        draft,
        template_dir=args.template_dir,
        industry_heading=topic.sections.industry_heading,
        research_heading=topic.sections.research_heading,
        week_label=week_label,
    )
    html_path = out_dir / f"{base}.html"
    html_path.write_text(html_str, encoding="utf-8")

    merge_history(history_path, [h.url for h in merged])

    print(f"Wrote {html_path}")
    print(f"Wrote {json_path}")
    print(f"Wrote {search_path}")
    print(f"Updated history: {history_path}")


if __name__ == "__main__":
    main()

import json
from datetime import UTC, date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from rom_newsletter import arxiv_client, cli
from rom_newsletter.compose import NewsletterDraft, compose_newsletter, validate_citations
from rom_newsletter.dates import utc_window_for_week
from rom_newsletter.history import load_seen_urls, merge_history, read_json
from rom_newsletter.relevance import apply_theme_filter, rank_research_hits, theme_score
from rom_newsletter.search import SearchHit, _canonical_url, budget_hits, hits_to_split_bundle_text
from rom_newsletter.topic import load_topic_for_run

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"


def paper(url="https://arxiv.org/abs/2610.00002", title="Neural operator for simulation"):
    return SearchHit(url, title, "Operator learning for fluid simulation", "arxiv", source_category="papers")


def draft(url="https://arxiv.org/abs/2610.00002"):
    return NewsletterDraft.model_validate(
        {
            "subject": "Operator learning for simulation",
            "industry_news": {"intro": "No relevant industry updates", "subsections": []},
            "research_papers": {
                "intro": "A new method",
                "subsections": [{"title": "Fluid operators", "body": "A new fluid operator", "links": [{"url": url}]}],
            },
        }
    )


def summary_json():
    return json.dumps({
        "subject": draft().subject,
        "industry_intro": "No relevant industry stories were selected for this issue.",
        "research_intro": draft().research_papers.intro,
    })


@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://arxiv.org/abs/2610.00002v3", "https://arxiv.org/abs/2610.00002"),
        ("https://arxiv.org/pdf/2610.00002v3.pdf", "https://arxiv.org/abs/2610.00002"),
        ("https://export.arxiv.org/pdf/math/0301234v2", "https://arxiv.org/abs/math/0301234"),
        ("https://vendor.test/story/?utm_source=email&item=7#top", "https://vendor.test/story?item=7"),
    ],
)
def test_canonical_identity(url, expected):
    assert _canonical_url(url) == expected


def test_inclusive_week_rolls_across_month():
    start, end = utc_window_for_week(date(2026, 10, 4), window_days=7)
    assert start.isoformat() == "2026-09-28T00:00:00+00:00"
    assert end.isoformat() == "2026-10-04T23:59:59+00:00"
    assert start.tzinfo == UTC


@pytest.mark.parametrize("days", [0, -1])
def test_window_rejects_invalid_length(days):
    with pytest.raises(ValueError):
        utc_window_for_week(date(2026, 10, 4), window_days=days)


def test_arxiv_paginates_full_window_then_ranks(monkeypatch):
    requested = []

    def response(client, *, params):
        requested.append(params.copy())
        page = 1 if params["start"] == 0 else 2
        return httpx.Response(200, text=(FIXTURES / f"arxiv-page-{page}.xml").read_text())

    monkeypatch.setattr(arxiv_client, "_get_arxiv_api", response)
    monkeypatch.setattr(arxiv_client.time, "sleep", lambda _: None)
    start, end = utc_window_for_week(date(2026, 10, 4), window_days=7)
    hits, meta = arxiv_client.fetch_arxiv_hits(start, end, max_results=10, page_size=2)
    assert [r["start"] for r in requested] == [0, 2]
    assert not meta["truncated"] and meta["pages"] == 2
    kept, stats = rank_research_hits(hits, max_hits=1)
    assert kept[0].url == "https://arxiv.org/abs/2609.99999"
    assert stats["below_threshold"] == 1


def test_arxiv_reports_truncation(monkeypatch):
    monkeypatch.setattr(
        arxiv_client,
        "_get_arxiv_api",
        lambda *a, **kw: httpx.Response(200, text=(FIXTURES / "arxiv-page-1.xml").read_text()),
    )
    start, end = utc_window_for_week(date(2026, 10, 4), window_days=7)
    _, meta = arxiv_client.fetch_arxiv_hits(start, end, max_results=2)
    assert meta["truncated"] and meta["total_results"] == 3


def test_query_does_not_include_whole_ml_category():
    topic = load_topic_for_run(ROOT, None)
    assert "cat:cs.LG" not in topic.arxiv_search_query
    assert 'all:"physics-informed"' in topic.arxiv_search_query


def test_query_metadata_cannot_inflate_relevance():
    hit = SearchHit("https://vendor.test/digital-twin", "Phone launch", "Camera upgrades", "ROM digital twin")
    assert theme_score(hit) == 0
    hits, stats = apply_theme_filter([hit], min_score=2, floor_non_arxiv=0)
    assert not hits and stats["non_arxiv_dropped"] == 1


def test_excerpt_and_track_budget():
    hits = [paper(f"https://arxiv.org/abs/2610.{i:05d}") for i in range(20)]
    kept, stats = budget_hits(hits, 12, 600)
    assert 0 < len(kept) < len(hits)
    assert stats["chars"] <= 600
    assert all(len(h.content) <= 12 for h in kept)


@pytest.mark.parametrize("url", ["https://invented.test/paper", "javascript:alert(1)"])
def test_unknown_or_unsafe_citations_fail(url):
    with pytest.raises(ValueError):
        validate_citations(draft(url), [paper()])


def test_cross_category_and_missing_citations_fail():
    d = draft()
    hit = paper()
    hit.source_category = "industry"
    with pytest.raises(ValueError, match="not in selected inputs"):
        validate_citations(d, [hit])
    d.research_papers.subsections[0].links = []
    with pytest.raises(ValueError, match="requires 1-3"):
        validate_citations(d, [paper()])


def test_empty_tracks_are_valid():
    d = NewsletterDraft(
        subject="Quiet week",
        industry_news={"intro": "No updates", "subsections": []},
        research_papers={"intro": "No updates", "subsections": []},
    )
    assert validate_citations(d, []) == []


@pytest.mark.parametrize("refine", [False, True])
@pytest.mark.parametrize("invalid_kind", ["missing", "too_many", "unknown", "cross_category"])
def test_compose_repairs_invalid_citations_in_final_output(refine, invalid_kind):
    good = draft().model_dump_json()
    bad_draft = draft()
    links = bad_draft.research_papers.subsections[0].links
    if invalid_kind == "missing":
        links.clear()
    elif invalid_kind == "too_many":
        links.extend(links * 3)
    elif invalid_kind == "unknown":
        links[0].url = "https://invented.test/paper"
    else:
        links[0].url = "https://vendor.test/story"
    bad = bad_draft.model_dump_json()
    client = Mock()
    client.chat.completions.create.side_effect = [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])
        for text in (([good, bad, good] if refine else [bad, good]) + [summary_json()])
    ]
    research, industry = hits_to_split_bundle_text([paper()])
    result = compose_newsletter(
        client,
        model="test",
        research_bundle=research,
        industry_bundle=industry,
        week_hint="Week",
        topic=load_topic_for_run(ROOT, None),
        input_hits=[paper(), SearchHit("https://vendor.test/story", "Story", "Text", "rss", source_category="industry")],
        refine=refine,
    )
    assert result.subject == draft().subject
    assert result.research_papers == draft().research_papers
    repair_prompt = client.chat.completions.create.call_args_list[-2].kwargs["messages"][1]["content"]
    assert "Validation error:" in repair_prompt and research in repair_prompt


def test_citation_repair_is_bounded_and_rejects_invalid_or_malformed_responses():
    bad = draft("https://invented.test/paper").model_dump_json()
    responses = [bad, "not json", bad, bad]
    client = Mock()
    client.chat.completions.create.side_effect = [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))]) for text in responses
    ]
    with pytest.raises(ValueError, match="Citation repair failed after 3 attempts.*not in selected inputs"):
        compose_newsletter(
            client, model="test", research_bundle="research", industry_bundle="industry", week_hint="Week",
            topic=load_topic_for_run(ROOT, None), input_hits=[paper()],
        )
    assert client.chat.completions.create.call_count == 4


@pytest.mark.parametrize("removal_stage", ["refine", "citation_repair"])
def test_summary_only_sees_final_stories_after_removal(removal_stage):
    initial = draft().model_dump()
    initial["subject"] = "Crash CAE"
    initial["research_papers"]["intro"] = "Thirteen preprints including crash CAE"
    initial["research_papers"]["subsections"].append({
        "title": "Crash CAE", "body": "Discarded candidate",
        "links": [{"url": "https://arxiv.org/abs/2610.99999"}],
    })
    client = Mock()
    client.chat.completions.create.side_effect = [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])
        for text in [json.dumps(initial), draft().model_dump_json(), summary_json()]
    ]
    result = compose_newsletter(
        client, model="test", research_bundle="Candidate pool includes crash CAE",
        industry_bundle="Unused vendor candidates", week_hint="Week",
        topic=load_topic_for_run(ROOT, None), input_hits=[paper()], refine=removal_stage == "refine",
    )
    messages = client.chat.completions.create.call_args.kwargs["messages"]
    final_stories = json.loads(messages[1]["content"].removeprefix("Final stories:\n"))
    assert final_stories == {
        "industry_news": {"subsections": []},
        "research_papers": {"subsections": draft().research_papers.model_dump()["subsections"]},
    }
    assert "crash CAE" not in messages[1]["content"]
    assert "counts of papers, sources, or stories" in messages[0]["content"]
    assert result.subject == draft().subject
    assert result.research_papers == draft().research_papers
    assert result.industry_news.intro == "No relevant industry stories were selected for this issue."


@pytest.mark.parametrize("invalid_summary", ["not json", "", "{}", json.dumps({
    "subject": "x" * 90, "industry_intro": "Industry", "research_intro": "Research",
}), json.dumps({
    "subject": "Subject", "industry_intro": "Industry", "research_intro": "Research",
    "research_papers": {"subsections": []},
})])
def test_summary_retries_invalid_responses_without_changing_stories(invalid_summary):
    client = Mock()
    client.chat.completions.create.side_effect = [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])
        for text in [draft().model_dump_json(), invalid_summary, summary_json()]
    ]
    result = compose_newsletter(
        client, model="test", research_bundle="research", industry_bundle="", week_hint="Week",
        topic=load_topic_for_run(ROOT, None), input_hits=[paper()],
    )
    assert result.research_papers == draft().research_papers
    assert client.chat.completions.create.call_count == 3


def test_summary_failure_is_bounded_without_returning_stale_metadata():
    client = Mock()
    client.chat.completions.create.side_effect = [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])
        for text in [draft().model_dump_json(), "{}", "{}", "{}", "{}"]
    ]
    with pytest.raises(ValueError, match="Newsletter summary failed after 3 repair attempts"):
        compose_newsletter(
            client, model="test", research_bundle="research", industry_bundle="", week_hint="Week",
            topic=load_topic_for_run(ROOT, None), input_hits=[paper()],
        )
    assert client.chat.completions.create.call_count == 5


def test_empty_issue_has_deterministic_summary():
    client = Mock()
    client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=json.dumps({
            "industry_news": {"subsections": []}, "research_papers": {"subsections": []},
        })),
    )])
    result = compose_newsletter(
        client, model="test", research_bundle="", industry_bundle="", week_hint="Week",
        topic=load_topic_for_run(ROOT, None), input_hits=[],
    )
    assert result.subject == "No selected stories this week"
    assert result.research_papers.intro == "No relevant research papers were selected for this issue."
    assert result.industry_news.intro == "No relevant industry stories were selected for this issue."
    assert client.chat.completions.create.call_count == 1


def test_generation_does_not_mark_candidates_published(tmp_path, monkeypatch):
    hit = paper()
    dropped = paper("https://arxiv.org/abs/2610.11111")
    monkeypatch.setattr(
        cli, "fetch_arxiv_hits", lambda *a, **kw: ([hit, dropped], {"truncated": False, "coverage_unknown": False})
    )
    monkeypatch.setattr(cli, "openai_client", lambda: None)
    monkeypatch.setattr(cli, "llm_model", lambda: "test")
    monkeypatch.setattr(cli, "compose_newsletter", lambda *a, **kw: draft())
    history = tmp_path / "published.json"
    cli.main(
        [
            "--date",
            "2026-10-04",
            "--no-rss",
            "--no-newsroom",
            "--output-dir",
            str(tmp_path),
            "--history-file",
            str(history),
        ]
    )
    assert not history.exists()
    manifest = read_json(tmp_path / "newsletter-2026-10-04-selection.json")
    assert manifest["selected_urls"] == [hit.url]
    assert len(read_json(tmp_path / "newsletter-2026-10-04-search.json")["discovered"]["hits"]) == 2
    from rom_newsletter.buttondown_publish import validate_issue_files

    validate_issue_files(tmp_path, "2026-10-04")
    html_path = tmp_path / "newsletter-2026-10-04.html"
    html_path.write_text(html_path.read_text() + "edited")
    with pytest.raises(ValueError, match="no longer matches"):
        validate_issue_files(tmp_path, "2026-10-04")


def test_published_history_keeps_other_weeks_and_allows_same_issue_regeneration(tmp_path):
    path = tmp_path / "history.json"
    merge_history(path, ["http://arxiv.org/abs/2610.00002v1"], issue_key="rom:2026-10-04")
    merge_history(path, ["https://vendor.test/item"], issue_key="rom:2026-09-27")
    assert load_seen_urls(path, exclude_issue="rom:2026-10-04") == {"https://vendor.test/item"}
    merge_history(path, [], issue_key="rom:2026-10-04")
    assert "https://arxiv.org/abs/2610.00002" in load_seen_urls(path)


def test_corrupt_publication_history_fails_closed(tmp_path):
    path = tmp_path / "history.json"
    path.write_text("broken JSON")
    with pytest.raises(ValueError):
        load_seen_urls(path)


def test_incomplete_coverage_blocks_composition_but_keeps_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cli, "fetch_arxiv_hits", lambda *a, **kw: ([paper()], {"truncated": True, "coverage_unknown": False})
    )
    compose = Mock()
    monkeypatch.setattr(cli, "compose_newsletter", compose)
    with pytest.raises(RuntimeError, match="coverage is incomplete"):
        cli.main(
            [
                "--date",
                "2026-10-04",
                "--no-rss",
                "--no-newsroom",
                "--output-dir",
                str(tmp_path),
                "--history-file",
                str(tmp_path / "published.json"),
            ]
        )
    assert not compose.called
    assert (tmp_path / "newsletter-2026-10-04-search.json").exists()


def test_html_titles_and_link_labels_are_escaped():
    from rom_newsletter.render import render_html

    d = draft()
    d.research_papers.subsections[0].title = "<script>alert(1)</script>"
    d.research_papers.subsections[0].links[0].label = "<img src=x>"
    rendered = render_html(d)
    assert "<script>" not in rendered and "<img src=x>" not in rendered
    assert "&lt;script&gt;" in rendered

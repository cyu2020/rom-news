import json

import httpx
import pytest

from rom_newsletter import cli
from rom_newsletter.industry_articles import enrich_industry_hits, extract_article_text
from rom_newsletter.search import SearchHit


AKSELOS_URL = "https://akselos.com/industrial-ai"


@pytest.mark.parametrize("discovery", ["newsroom", "rss"])
def test_cli_recovers_akselos_body_before_filter_and_excerpt_budget(tmp_path, monkeypatch, discovery):
    """A vague teaser must not hide the relevant section late in an article."""
    teaser = "Industrial AI means something different depending on who's using the term."
    hits = [
        SearchHit(AKSELOS_URL, "Industrial AI, Hype or Reality?", teaser, "newsroom:akselos"),
        SearchHit("https://vendor.test/gaming", "Cloud gaming", "Cute critters", "rss"),
    ]
    html = (
        '<html><nav>Digital twins and neural operators</nav><main><article>'
        f'<p>{teaser}</p><p>' + "General industrial operations and business context. " * 60
        + '</p><h2>Akselos and Industrial AI</h2><p>The solver is RB-FEA, '
        'reduced basis finite element analysis, licensed exclusively from MIT.</p>'
        '<p>Machine learning reconstructs loads, then a physics solver computes structural response.</p>'
        '</article><aside>Neural operators</aside></main><footer>Digital twins</footer></html>'
    )

    def get(client, url, **kwargs):
        body = html if url == AKSELOS_URL else (
            '<nav>Digital twins</nav><article><p>Cloud gaming for cute critters.</p></article>'
        )
        return httpx.Response(200, text=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.Client, "get", get)
    monkeypatch.setattr(cli, "fetch_arxiv_hits", lambda *a, **kw: ([], {}))
    if discovery == "newsroom":
        monkeypatch.setattr(cli, "fetch_newsroom_hits", lambda *a, **kw: (hits, {}))
    else:
        monkeypatch.setattr(cli, "fetch_rss_hits", lambda *a, **kw: (hits, []))
    cli.main([
        "--date", "2026-09-20", "--no-rss" if discovery == "newsroom" else "--no-newsroom",
        "--no-skip-seen", "--dry-run-search",
        "--output-dir", str(tmp_path),
    ])
    audit = json.loads((tmp_path / "newsletter-2026-09-20-search.json").read_text())
    inputs = audit["composition_inputs"]
    assert [h["url"] for h in inputs] == [AKSELOS_URL]
    assert "reduced basis finite element analysis" in inputs[0]["content"]
    assert "Machine learning reconstructs loads" in inputs[0]["content"]
    assert len(inputs[0]["content"]) <= 1800
    assert audit["industry_enrichment"]["enriched"] == 2


def test_extractor_ignores_chrome_related_links_and_hidden_content():
    html = '''<main><article><h1>Cloud gaming</h1><div class="entry-content">
    <p>Games &amp; entertainment.</p><script>Digital twins</script>
    <p hidden>Neural operators</p><aside>Finite element</aside></div>
    <nav>Simulation software</nav></article><aside>Reduced-order modeling</aside></main>'''
    assert extract_article_text(html) == "Games & entertainment."


@pytest.mark.parametrize("failure", ["http", "no_body"])
def test_enrichment_failure_preserves_original_excerpt_and_records_reason(monkeypatch, failure):
    hit = SearchHit(AKSELOS_URL, "Industrial AI", "Finite element analysis", "newsroom:akselos")

    def get(client, url, **kwargs):
        return httpx.Response(
            503 if failure == "http" else 200,
            text="<nav>Digital twins</nav>", request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.Client, "get", get)
    hits, meta = enrich_industry_hits([hit])
    assert hits == [hit]
    assert meta["enriched"] == 0 and meta["errors"][0]["url"] == AKSELOS_URL

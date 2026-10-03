# Newsroom listing parsers

Sources with **`newsroom_listing: true`** in [`sources.json`](../sources.json) trigger built-in discovery in [`src/rom_newsletter/newsroom_listings.py`](../src/rom_newsletter/newsroom_listings.py). This is **orthogonal** to [`topic.json`](../topic.json): parsers are **site-specific**, not topic-specific.

## Registry (`id` → implementation)

The `id` field on a source row selects a parser. The internal map is `_parser_for_source_id` → branch in `fetch_newsroom_hits`:

| `sources.json` `id` | Parser function | Notes |
|---------------------|-----------------|-------|
| `physicsx` | `parse_physicsx_newsroom` | Listing HTML from source `url` |
| `neural-concept` | `parse_neural_concept_press` | Press releases index (`/press-releases`) |
| `emmi-ai` | `parse_emmi_news` | Default branch for `emmi` |
| `siemens` | `parse_siemens_news_sitemap` | Fetches fixed `news.siemens.com` en-us sitemap (not `url` body) |
| `p1-ai` | `parse_p1_ai_homepage` | Press links from `p-1.ai` homepage |
| `luminary` | `parse_luminary_press_resources` | Press cards on `luminary.ai/resources` |
| `vinci4d` | `parse_vinci_wp_posts` | WordPress REST API (`/wp-json/wp/v2/posts`) — Blog/News posts with publish dates; mirrors `akselos`. HTML listing (`getvinci.ai/news`→`/blog`) is behind Cloudflare challenges/403 from some robot IPs, so the API is used instead |
| `akselos` | `parse_akselos_wp_posts` | WordPress REST API (`/wp-json/wp/v2/posts`) — complete Blogs+In-the-news list with publish dates, not the category-filtered listing page |

Newsroom discovery first attaches the article's `og:description`/meta description. After date filtering, merging, and seen-URL exclusion, both newsroom and RSS industry hits are enriched with article bodies by `industry_articles.py` before relevance scoring. Retrieval is best-effort with six workers and a 15-second request timeout. The extractor prefers article-content containers, then `article` or `main`, excluding navigation, headers, footers, scripts, hidden content, and sidebars. Responses above 2 MB are rejected and extracted text is capped at 24,000 characters. If retrieval or extraction fails, the discovery excerpt is retained and the reason is recorded in the audit's `industry_enrichment` field.

Industry prompt excerpts prioritize paragraphs containing the topic's weighted theme patterns and nearby context, preserving their original order within the per-story budget. This keeps relevant technical sections that appear late in an article, such as Akselos's RB-FEA discussion in “Industrial AI, Hype or Reality?”. Relevance thresholds and date windows are unchanged.

Unknown or missing `id` with `newsroom_listing: true` produces an error entry in the newsroom metadata (see `fetch_newsroom_hits`).

## Adding a new vendor

1. **Implement** a function that returns a list of `(url, title, listing_dt | None)` tuples, following the patterns in `newsroom_listings.py` (date on listing vs fetched article page).
2. **Register** the `id` in `_parser_for_source_id` and add a branch in `fetch_newsroom_hits` that calls your parser.
3. **Document** the new `id` in this file and in the module docstring at the top of `newsroom_listings.py`.
4. **Configure** `sources.json`: `newsroom_listing: true`, `id`, and a valid `url` (unless the parser uses a fixed sitemap URL like Siemens).

No changes to `topic.json` are required for a new newsroom parser.

# Newsroom listing parsers

Sources with **`newsroom_listing: true`** in [`sources.json`](../sources.json) trigger built-in discovery in [`src/rom_newsletter/newsroom_listings.py`](../src/rom_newsletter/newsroom_listings.py). This is **orthogonal** to [`topic.json`](../topic.json): parsers are **site-specific**, not topic-specific.

## Registry (`id` → implementation)

The `id` field on a source row selects a parser. The internal map is `_parser_for_source_id` → branch in `fetch_newsroom_hits`:

| `sources.json` `id` | Parser function | Notes |
|---------------------|-----------------|-------|
| `physicsx` | `parse_physicsx_newsroom` | Listing HTML from source `url` |
| `neural-concept` | `parse_neural_concept_press` | Press HTML |
| `emmi-ai` | `parse_emmi_news` | Default branch for `emmi` |
| `siemens` | `parse_siemens_news_sitemap` | Fetches fixed `news.siemens.com` en-us sitemap (not `url` body) |
| `p1-ai` | `parse_p1_ai_homepage` | Press links from `p-1.ai` homepage |
| `luminary` | `parse_luminary_press_resources` | Press cards on `luminary.ai/resources` |
| `vinci4d` | `parse_vinci_news_listing` | `getvinci.ai/news` |
| `akselos` | `parse_akselos_resources_news` | News-filter resource hub |

Unknown or missing `id` with `newsroom_listing: true` produces an error entry in the newsroom metadata (see `fetch_newsroom_hits`).

## Adding a new vendor

1. **Implement** a function that returns a list of `(url, title, listing_dt | None)` tuples, following the patterns in `newsroom_listings.py` (date on listing vs fetched article page).
2. **Register** the `id` in `_parser_for_source_id` and add a branch in `fetch_newsroom_hits` that calls your parser.
3. **Document** the new `id` in this file and in the module docstring at the top of `newsroom_listings.py`.
4. **Configure** `sources.json`: `newsroom_listing: true`, `id`, and a valid `url` (unless the parser uses a fixed sitemap URL like Siemens).

No changes to `topic.json` are required for a new newsroom parser.

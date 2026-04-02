# Custom topic profiles (`topic.json`)

This project separates **where you read** ([`sources.json`](../sources.json) — feeds, domains, `newsroom_listing`, `category`) from **what the newsletter is about** ([`topic.json`](../topic.json) at the repo root, or another file via CLI / env).

## Checklist for a new vertical

1. **Sources** — Copy or edit `sources.json`: add RSS, `feed_hosts`, and per-row `category` (`papers` vs `industry`) as needed. Clear or replace built-in default RSS in code only if you rely entirely on JSON (see `rss_client.DEFAULT_FEED_URLS`).
2. **arXiv** — Set `arxiv.search_query` to the Lucene body (categories, `all:` terms, etc.) **without** the `submittedDate` range; the app appends the date window. **Disable** arXiv for non-science topics: set `"arxiv": null` (or omit the body so the query is empty—see schema). **CLI** `--no-arxiv` still skips the API regardless of profile.
3. **Theme scoring** — Under `theme`, either set `"disabled": true` (no score filtering; effective `--theme-min-score` becomes 0) or provide `patterns` as `{ "pattern": "regex", "weight": int }` (same idea as the former built-in ROM/SciML list). If `disabled` is false and `patterns` is empty or missing, **ROM default patterns** are used (backward compatible).
4. **Compose** — Fill `compose.editor_intro`, `subject_rules`, `classification_block`, and `refine_system_extra` so the model knows audience, subject-line rules, and how to split industry vs research.
5. **Section titles** — `sections.industry_heading` and `sections.research_heading` feed the Jinja template (stored JSON keys remain `industry_news` / `research_papers`).
6. **Buttondown fallback** — `buttondown.fallback_subject` is used when the generated JSON has no `subject` (e.g. `rom-newsletter-buttondown` / `load_html_and_subject`).
7. **Validation** — `topic.json` should conform to [`topic.schema.json`](../topic.schema.json); your editor can use `"$schema"` to offer hints.

## How the app loads the profile

- **Default:** `<repo>/topic.json` if the file exists; otherwise **built-in ROM** defaults (same behavior as the shipped `topic.json`).
- **Env:** `ROM_NEWSLETTER_TOPIC` — path to a JSON file (overrides default path when set).
- **CLI:** `rom-newsletter --topic /path/to/topic.json` **overrides** env and default path.

`rom-newsletter-buttondown` accepts `--topic` for the same fallback subject behavior when the newsletter JSON omits `subject`.

## Example non-ROM stub

Use this as a starting point for a different niche (replace strings and queries; set `arxiv` to `null` if unused):

```json
{
  "version": 1,
  "name": "example-vertical",
  "arxiv": null,
  "theme": {
    "disabled": false,
    "patterns": [
      { "pattern": "robot", "weight": 2 },
      { "pattern": "manipulation", "weight": 2 }
    ]
  },
  "compose": {
    "editor_intro": "You are an editor for a weekly robotics ML briefing.",
    "subject_rules": "Subject under 90 characters; concrete weekly headline.",
    "classification_block": "Classify excerpts into industry_news vs research_papers using the provided tags only.",
    "refine_system_extra": "Keep citations aligned with excerpts."
  },
  "sections": {
    "industry_heading": "Industry",
    "research_heading": "Research"
  },
  "buttondown": {
    "fallback_subject": "Weekly — robotics ML"
  }
}
```

## Newsroom listing parsers (not in `topic.json`)

Vendor-specific **HTML/sitemap** discovery lives in code, not in the topic file. See [`newsroom_listings.md`](newsroom_listings.md) for the **id → parser** registry and how to add a new vendor.

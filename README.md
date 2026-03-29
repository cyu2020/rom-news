# ROM newsletter agent

Generates a weekly-style briefing on **reduced-order modeling**, **scientific machine learning**, and **digital twins** using the [AI Builders Space API](https://space.ai-builders.com/backend) plus **arXiv** and optional **RSS**:

1. **arXiv** — Official Atom API with **`submittedDate:[start TO end]`** in UTC (no duplicate web-scrape of arXiv).
2. **RSS** — Feeds come from optional **`rss`** (and **`feed_hosts`**) on entries in [`sources.json`](sources.json), then small built-in defaults in code (deduped by URL). **Per-source `rss` wins** if the same feed URL appears twice. The feed URL’s hostname must match a domain from your sources list, **or** set **`feed_hosts`** (e.g. `news.synopsys.com` for [Synopsys Simulation & Analysis RSS](https://news.synopsys.com/home?pagetemplate=rss&category=778)). Items are filtered by **published date** inside the window; article links may be off-domain.
3. **Newsroom listings** (optional) — Sources with **`newsroom_listing: true`** use built-in discovery (HTML listing parsers for **`id`** `physicsx`, `neural-concept`, `emmi-ai`; **`id`** **`siemens`** uses **`news.siemens.com/en-us/sitemap-en-us.xml`**; **`id`** **`p1-ai`** pulls curated **press** links from the **`p-1.ai`** homepage—BusinessWire, Fortune, arXiv, etc.—because there is no `/news` index). Dates come from the listing (PhysicsX), article pages (Neural Concept, Emmi, P-1), or **sitemap `<lastmod>`** (Siemens). Use **`--no-newsroom`** to skip.
4. **Tavily** — `POST /v1/search/` with a few **broad** topic keywords (ROM / SciML / twins / operators, etc.). **By default** results may come from **any domain** (open web). Use **`--tavily-sources-only`** to restrict hits to hosts listed in [`sources.json`](sources.json). Each hit is tagged **`papers`** vs **`industry`** when the URL matches a source host; otherwise it is treated as **industry** for the LLM split. No server-side time range in the API. **By default**, each Tavily result URL is fetched and hits are **dropped** if the parsed HTML publish date is outside the UTC window (`article:published_time` / JSON-LD / `<time>`), or if **no** publish date can be parsed. If the search API returns a date on a result object, that is used as a cheap pre-filter first. Use **`--keep-tavily-undated`** to keep pages where the date could not be parsed (noisier). Use **`--no-filter-tavily-by-page-date`** to skip these fetches (faster; more stale URLs possible). **`*-search.json`** reports **`hits_after_normalize`** (after normalize, including optional host filter when `--tavily-sources-only`) vs **`hit_count`** (after page-date filter); if you only run Tavily (`--no-arxiv` / `--no-rss` / …) and see **`hits_after_normalize` > 0** but **`hit_count` 0**, the HTML date step removed everything—try **`--keep-tavily-undated`**.
5. **Dedupe** — Merged list excludes URLs already recorded in [`.rom-newsletter/seen_urls.json`](.rom-newsletter/seen_urls.json) (disable with `--no-skip-seen`).
6. **Theme filter** — Non–arXiv hits get a **theme score** (keywords for digital/virtual twins, ROM, SciML, PINNs, operators, CAE, simulation platforms, etc.). Hits below `--theme-min-score` are dropped; optional **backfill** only from scores ≥ `--theme-backfill-min-score`. arXiv hits are never scored out.
7. **Compose** — `POST /v1/chat/completions` → structured JSON: **Research Papers** and **Industry News**, each with an intro and up to five subsections (title, body, links); subject line only (no global intro/takeaway; citations live under each subsection). Discovery tags each hit with **`sources.json` `category`** (`papers` vs `industry`); the LLM receives **two excerpt blocks** (research vs industry) so sections stay aligned with configured sources. The model is instructed to **skip** off-topic industry stories.
8. **Render** — Jinja2 → single HTML file (**Industry News** block first, then **Research Papers**).

## Setup

- Python **3.11+** (project uses **3.13** in `.venv` per team convention).
- API token **`AI_BUILDER_TOKEN`** in `.env` at the repo root (never commit this file).

```bash
cd /path/to/rom-news
/home/cheng-yu/.local/bin/uv venv -p 3.13 .venv
/home/cheng-yu/.local/bin/uv pip install --python .venv/bin/python -e .
source .venv/bin/activate   # optional; or invoke .venv/bin/python -m rom_newsletter
```

Optional environment variables:

| Variable | Purpose |
|----------|---------|
| `AI_BUILDERS_BASE_URL` | Override API base (default `https://space.ai-builders.com/backend/v1`) |
| `ROM_NEWSLETTER_MODEL` | Default chat model (default `grok-4-fast`) |
| `BUTTONDOWN_API_KEY` | Buttondown token for `rom-newsletter-buttondown` (local or CI) |
| `BUTTONDOWN_API_VERSION` | Optional `X-API-Version` for Buttondown (e.g. `2026-04-01`) |
| `ROM_NEWSLETTER_ARXIV_READ_TIMEOUT` | Override arXiv HTTP **read** timeout in seconds (default **180**; raise in CI if `export.arxiv.org` is slow) |

## Usage

**Full pipeline** (discovery → LLM → HTML + JSON under `dist/`):

```bash
rom-newsletter --date 2025-03-20
```

**Time window** — UTC range ending on `--date` (inclusive of that calendar day’s end):

- `--window-days 7` (default): last 7 days through `--date`.

**Search-only** (writes `*-search.json` with arXiv + RSS + newsroom + Tavily breakdown; no LLM):

```bash
rom-newsletter --dry-run-search
```

**Large arXiv pulls** can dominate the LLM context — the default cap is **`--arxiv-max 25`**; raise if you need more:

```bash
rom-newsletter --arxiv-max 100
```

**Other flags**

| Flag | Purpose |
|------|---------|
| `--model gemini-2.5-pro` | Override chat model |
| `--max-results 8` | Tavily `max_results` per keyword (1–20) |
| `--arxiv-max N` | Max results from the arXiv API (1–2000; default `25`) |
| `--no-arxiv` | Skip arXiv API |
| `--no-rss` | Skip RSS feeds |
| `--no-tavily` | Skip AI Builders web search |
| `--no-newsroom` | Skip newsroom listing scrapes (`newsroom_listing` in sources) |
| `--no-skip-seen` | Ignore `.rom-newsletter/seen_urls.json` |
| `--history-file PATH` | Custom seen-URL ledger |
| `--sources PATH` | `sources.json` (default: `<repo>/sources.json`) |
| `--tavily-sources-only` | Restrict Tavily hits to hosts in `sources.json` (default: open web) |
| `--no-filter-tavily-by-page-date` | Disable Tavily page fetch + date filter (default is **on**) |
| `--filter-tavily-by-page-date` | Deprecated no-op (filtering is default) |
| `--keep-tavily-undated` | With page-date filter, keep hits with no parseable date (default: drop undated) |
| `--tavily-date-workers` | Parallelism for Tavily page-date fetches (default 6) |
| `--tavily-date-timeout` | Per-URL HTTP timeout in seconds (default 15) |
| `--theme-min-score N` | Non-arXiv hits need theme score ≥ N to keep (default `2`; `0` = rank/cap only) |
| `--theme-floor-non-arxiv` | Soft floor for non-arXiv count after filtering (default 5); backfill uses `--theme-backfill-min-score` |
| `--theme-backfill-min-score` | Minimum theme score for backfill rows (default 1; avoids score-0 filler) |
| `--max-non-arxiv-hits` | Cap non-arXiv hits after ranking (default 48) |
| `--refine` | Second LLM pass on citations |
| `--output-dir`, `--template-dir` | Paths |

Outputs (for `--date 2025-03-20`):

- `dist/newsletter-2025-03-20.html`
- `dist/newsletter-2025-03-20.json`
- `dist/newsletter-2025-03-20-search.json` — full discovery audit (window, per-source counts, **`theme_filter`** stats + scored samples, merged URLs)
- `.rom-newsletter/seen_urls.json` — updated after each successful run (unless `--dry-run-search`). Commit this file if you want deduplication shared across machines; add `.rom-newsletter/` to `.gitignore` if you prefer a local-only ledger.

## Scheduled runs (GitHub Actions + Buttondown)

The workflow [`.github/workflows/weekly-newsletter.yml`](.github/workflows/weekly-newsletter.yml) runs **every Monday 14:00 UTC** (adjust the `cron` expression if you want a different time or timezone). It:

1. Sets **`WEEK_END`** to the **previous Sunday** in UTC (`date -u -d 'last Sunday'`), matching `rom-newsletter --date` as the **end** of the inclusive UTC window ([`dates.py`](src/rom_newsletter/dates.py) behavior).
2. Runs `rom-newsletter --date "$WEEK_END" --no-skip-seen --output-dir dist --no-tavily`.
3. Publishes `dist/newsletter-<WEEK_END>.html` to [Buttondown](https://buttondown.com/) via `rom-newsletter-buttondown`, using the **`subject`** from `dist/newsletter-<WEEK_END>.json`.

### GitHub Actions setup

1. **Commit the workflow** — Push [`.github/workflows/weekly-newsletter.yml`](.github/workflows/weekly-newsletter.yml) to your GitHub repo’s **default** branch so Actions can discover it.
2. **Enable workflows** — In the repo on GitHub: **Settings** → **Actions** → **General**. Under **Actions permissions**, allow Actions to run (adjust **Fork** pull request settings if you use forks).
3. **Add repository secrets** — **Settings** → **Secrets and variables** → **Actions** → **New repository secret** for each row below:

| Secret | Purpose |
|--------|---------|
| `AI_BUILDER_TOKEN` | Same as local `.env`; required for the LLM + discovery. |
| `BUTTONDOWN_API_KEY` | Buttondown API token ([API keys](https://buttondown.com/keys)); creates the email with `status: about_to_send` (send to subscribers). |

4. **Config in the repo** — Keep [`sources.json`](sources.json) at the repo root (the workflow does not pass `--sources`). Commit `templates/` and anything else `rom-newsletter` needs the same way you do locally.
5. **Optional: change schedule** — Edit the `cron` line in the workflow file. Times are **UTC**; GitHub does not guarantee execution to the exact minute.
6. **Test early** — **Actions** → **Weekly newsletter** → **Run workflow**. Optionally set **week end date** to a `YYYY-MM-DD` you have already validated locally; leave it empty to use “last Sunday UTC” (same as the scheduled run).
7. **Scheduled runs caveat** — GitHub may **disable** scheduled workflows on repositories with **no activity** for a long time; the schedule is **best-effort** and can drift slightly.

**Manual run (after setup):** Actions → *Weekly newsletter* → *Run workflow* → optional **week end date** ISO `YYYY-MM-DD` (overrides the default “last Sunday UTC”).

**Upload HTML locally after a normal run** (requires `BUTTONDOWN_API_KEY` in `.env` or the environment):

```bash
rom-newsletter-buttondown --output-dir dist --date 2026-03-20
```

Use **`--draft`** to create a Buttondown **draft** instead of sending. **`--dry-run`** loads files and prints subject/body size only.

Optional env **`BUTTONDOWN_API_VERSION`** (e.g. `2026-04-01`) is passed as `X-API-Version` if you pin API behavior; see [Buttondown API versioning](https://docs.buttondown.com/api-versioning).

The publish step sends **`X-Buttondown-Live-Dangerously: true`** so the first programmatic send to subscribers under newer API versions succeeds, and so edge-case HTML is accepted (see [creating an email](https://docs.buttondown.com/api-emails-create)).

When the run succeeds, **`dist/`** is uploaded as a workflow artifact. If **Generate newsletter** fails before writing files, there may be nothing to upload; the workflow is configured to **ignore** a missing `dist/` folder so the job does not fail twice.

### Troubleshooting workflow failures

| Symptom | What to check |
|---------|----------------|
| **Process completed with exit code 1** | Open the **Generate newsletter** step log (that is usually what failed). Confirm **`AI_BUILDER_TOKEN`** is set under Actions secrets and matches a valid token. Run the same command locally with the same `--date`. |
| **arXiv / API errors** | Transient **503** or **read timeouts** from `export.arxiv.org` can fail the step; the client retries with backoff and uses a **180s** read timeout by default. Set **`ROM_NEWSLETTER_ARXIV_READ_TIMEOUT`** (e.g. `300`) in Actions env or repo variables, or retry the workflow. See `--no-arxiv` to skip arXiv. |
| **Node.js 20 deprecation annotations** | The workflow sets **`FORCE_JAVASCRIPT_ACTIONS_TO_NODE24`** and pins newer action versions; warnings can still appear until GitHub changes defaults—see the [GitHub changelog](https://github.blog/changelog/2025-09-19-deprecation-of-node-20-on-github-actions-runners/). |
| **No artifact / “No files were found”** | Normal if generation failed: **`dist/`** was never created. Fix the failing step first; the artifact upload will not fail the job when `dist/` is absent. |

## Sources config (`sources.json`)

The config file is **[`sources.json`](sources.json)** at the repo root unless you pass **`--sources`**.

- **`version`**: must be `1`.
- **`sources`**: array of entries with **`label`** and **`url`** (HTTP(S)); optional **`id`**, **`category`** (e.g. `papers` / `industry`), **`kind`** (`arxiv` \| `nvidia` \| `siemens` \| `ansys` \| `generic` or omitted to infer from the URL host), **`rss`**, **`feed_hosts`** (extra hostnames allowed for RSS item links when they differ from the feed host), and **`newsroom_listing`** (boolean: built-in newsroom discovery instead of Tavily; **`id`** `siemens` uses the en-us sitemap; **`id`** `p1-ai` reads press links from the `p-1.ai` homepage).

Optional **[`sources.schema.json`](sources.schema.json)** documents the shape for editors that support JSON Schema.

## RSS feeds

Declare **`rss`** (and optional **`feed_hosts`**) on the matching entry in **`sources.json`**. The CLI merges **per-source RSS first**, then a small built-in default list in code, **deduplicating by feed URL**.

The **feed URL’s hostname** must match a host from your sources list, **unless** you add **`feed_hosts`**: an array of normalized hostnames allowed for that feed (needed when item links use a different domain than the feed).

Example (Synopsys feed with off-domain item links — same pattern as the **`synopsys-simulation-rss`** entry in [`sources.json`](sources.json)):

```json
{
  "id": "synopsys-simulation-rss",
  "label": "Synopsys (Simulation & Analysis RSS)",
  "url": "https://news.synopsys.com/",
  "category": "industry",
  "kind": "generic",
  "rss": "https://news.synopsys.com/home?pagetemplate=rss&category=778",
  "feed_hosts": ["news.synopsys.com"]
}
```

### Siemens & Altair

**Siemens** is covered by **`newsroom_listing`** in [`sources.json`](sources.json) (en-us sitemap), not RSS. **Altair** has no feed in this repo; Tavily can still surface **`altair.com`** on the open web, or add a **generic** source entry if you want that host tagged from config—optionally add **`rss`** once you have a stable feed URL.

## Limitations

- **Tavily** has no documented date-range parameter; **page-date filtering is on by default** (HTML fetch + parse: meta tags, **`og:published_time`**, JSON-LD `datePublished`, and visible English dates like `March 17, 2026`). Use **`--no-filter-tavily-by-page-date`** to disable. Undated pages are **dropped** by default; use **`--keep-tavily-undated`** if you need JS-rendered or paywalled pages that still have no parseable date in the raw HTML.
- Treat generated text as a **draft**; verify claims from primary sources.

## Project layout

- `src/rom_newsletter/` — CLI, arXiv, RSS, optional newsroom listings, Tavily merge, compose, render, [`buttondown_publish`](src/rom_newsletter/buttondown_publish.py)
- `.github/workflows/weekly-newsletter.yml` — Monday cron + Buttondown publish
- `templates/newsletter.html.j2` — HTML layout
- `sources.json` — categorized sources and RSS feeds (required unless `--sources` points elsewhere)
- `sources.schema.json` — optional JSON Schema for `sources.json`

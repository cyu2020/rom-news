# ROM newsletter agent

Generates a weekly-style briefing on **reduced-order modeling**, **scientific machine learning**, and **digital twins** from sources such as **arXiv**, selected **RSS**, and selected **newsroom** listing:

1. **arXiv** — Official Atom API with `**submittedDate:[start TO end]`** in UTC (no duplicate web-scrape of arXiv).
2. **RSS** — Feeds come from optional `**rss`** (and `**feed_hosts**`) on entries in `[sources.json](sources.json)`, then small built-in defaults in code (deduped by URL). **Per-source `rss` wins** if the same feed URL appears twice. The feed URL’s hostname must match a domain from your sources list, **or** set `**feed_hosts`** (e.g. `news.synopsys.com` for [Synopsys Simulation & Analysis RSS](https://news.synopsys.com/home?pagetemplate=rss&category=778)). Items are filtered by **published date** inside the window; article links may be off-domain.
3. **Newsroom listings** (optional) — Sources with `**newsroom_listing: true`** use built-in discovery: HTML listing parsers for `**id**` `physicsx`, `neural-concept`, `emmi-ai`, `**luminary**` (Press cards on `**luminary.ai/resources**`), `**vinci4d**` (`**getvinci.ai/news**`), `**akselos**` (News-filter resource hub); `**id**` `**siemens**` uses `**news.siemens.com/en-us/sitemap-en-us.xml**`; `**id**` `**p1-ai**` pulls curated **press** links from the `**p-1.ai`** homepage. Dates come from the listing (PhysicsX, Luminary Press cards), article pages (Neural Concept, Emmi, P-1, Vinci, Akselos when missing on the index), or **sitemap `<lastmod>`** (Siemens). Use `**--no-newsroom**` to skip.
4. **Dedupe** — Merged list excludes URLs already recorded in `[.rom-newsletter/seen_urls.json](.rom-newsletter/seen_urls.json)` (disable with `--no-skip-seen`).
5. **Theme filter** — Non–arXiv hits get a **theme score** (keywords for digital/virtual twins, ROM, SciML, PINNs, operators, CAE, simulation platforms, etc.). Hits below `--theme-min-score` are dropped; optional **backfill** only from scores ≥ `--theme-backfill-min-score`. arXiv hits are never scored out.
6. **Compose** — `POST /v1/chat/completions` → structured JSON: **Research Papers** and **Industry News**, each with an intro and up to five subsections (title, body, links); subject line only (no global intro/takeaway; citations live under each subsection). Discovery tags each hit with `**sources.json` `category`** (`papers` vs `industry`); the LLM receives **two excerpt blocks** (research vs industry) so sections stay aligned with configured sources. The model is instructed to **skip** off-topic industry stories.
7. **Render** — Jinja2 → single HTML file (**Industry News** block first, then **Research Papers**).

## Setup

- Python **3.11+** (project uses **3.13** in `.venv` per team convention).
- **LLM (OpenAI-compatible chat API)** — set the three variables below in `.env` at the repo root (never commit this file). Use any provider that exposes `**POST …/chat/completions`** with the OpenAI SDK (e.g. **OpenRouter** `https://openrouter.ai/api/v1`, or a local OpenAI-compatible server). There are **no** built-in defaults; every value must come from the environment.


| Variable       | Purpose                                                         |
| -------------- | --------------------------------------------------------------- |
| `LLM_BASE_URL` | API base URL including `/v1` (no trailing slash).               |
| `LLM_API_KEY`  | Bearer token for that API.                                      |
| `LLM_MODEL`    | Default model id for compose (override per run with `--model`). |


```bash
cd /path/to/rom-news
uv venv -p 3.13 .venv
uv pip install --python .venv/bin/python -e .
source .venv/bin/activate   # optional; or invoke .venv/bin/python -m rom_newsletter
```

`**--dry-run-search**` does not call the LLM, so `LLM_*` are not required for that mode. A **full** run needs all three `LLM_*` variables (or `--model` plus `LLM_BASE_URL` and `LLM_API_KEY`).

Other environment variables:


| Variable                            | Purpose                                                                                                                                                                      |
| ----------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `BUTTONDOWN_API_KEY`                | Buttondown token for `rom-newsletter-buttondown` (local or CI)                                                                                                               |
| `BUTTONDOWN_API_VERSION`            | Optional `X-API-Version` for Buttondown (e.g. `2026-04-01`)                                                                                                                  |
| `ROM_NEWSLETTER_ARXIV_READ_TIMEOUT` | Override arXiv HTTP **read** timeout in seconds (default **180**; raise in CI if `export.arxiv.org` is slow)                                                                 |
| `ROM_NEWSLETTER_ARXIV_USER_AGENT`   | Custom `User-Agent` for arXiv API calls ([API manual](https://arxiv.org/help/api/user-manual)); helps avoid **429** rate limits from shared IPs (e.g. GitHub Actions)        |
| `ROM_NEWSLETTER_TOPIC`              | Path to a `topic.json` profile (arXiv query, theme patterns, compose copy, section titles, Buttondown fallback subject). Overrides the default `<repo>/topic.json` when set. |


## Custom topics (`topic.json`)

Discovery strings, arXiv Lucene body, theme regexes, composer instructions, HTML section headings, and the Buttondown subject fallback can live in `**[topic.json](topic.json)`** (or another file via `**--topic**` / `**ROM_NEWSLETTER_TOPIC**`). `**[sources.json](sources.json)**` stays the place for feeds and URL allowlists. See `**[docs/custom-topic.md](docs/custom-topic.md)**` for a checklist, loading rules, and a stub profile. Newsroom parsers are documented separately in `**[docs/newsroom_listings.md](docs/newsroom_listings.md)**` (vendor-specific code, not driven by `topic.json`).

## Usage

**Full pipeline** (discovery → LLM → HTML + JSON under `dist/`):

```bash
rom-newsletter --date 2025-03-20
```

**Time window** — UTC range ending on `--date` (inclusive of that calendar day’s end):

- `--window-days 7` (default): last 7 days through `--date`.

**Search-only** (writes `*-search.json` with arXiv + RSS + newsroom breakdown; no LLM):

```bash
rom-newsletter --dry-run-search
```

**Large arXiv pulls** can dominate the LLM context — the default cap is `**--arxiv-max 25`**; raise if you need more:

```bash
rom-newsletter --arxiv-max 100
```

**Other flags**


| Flag                             | Purpose                                                                                                            |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `--model <id>`                   | Override `LLM_MODEL` for this run                                                                                  |
| `--arxiv-max N`                  | Max results from the arXiv API (1–2000; default `25`)                                                              |
| `--no-arxiv`                     | Skip arXiv API                                                                                                     |
| `--no-rss`                       | Skip RSS feeds                                                                                                     |
| `--no-newsroom`                  | Skip newsroom listing scrapes (`newsroom_listing` in sources)                                                      |
| `--no-skip-seen`                 | Ignore `.rom-newsletter/seen_urls.json`                                                                            |
| `--history-file PATH`            | Custom seen-URL ledger                                                                                             |
| `--sources PATH`                 | `sources.json` (default: `<repo>/sources.json`)                                                                    |
| `--topic PATH`                   | Topic profile JSON (default: env `ROM_NEWSLETTER_TOPIC` or `<repo>/topic.json`; if missing, built-in ROM defaults) |
| `--theme-min-score N`            | Non-arXiv hits need theme score ≥ N to keep (default `2`; `0` = rank/cap only)                                     |
| `--theme-floor-non-arxiv`        | Soft floor for non-arXiv count after filtering (default 5); backfill uses `--theme-backfill-min-score`             |
| `--theme-backfill-min-score`     | Minimum theme score for backfill rows (default 1; avoids score-0 filler)                                           |
| `--max-non-arxiv-hits`           | Cap non-arXiv hits after ranking (default 48)                                                                      |
| `--refine`                       | Second LLM pass on citations                                                                                       |
| `--output-dir`, `--template-dir` | Paths                                                                                                              |


Outputs (for `--date 2025-03-20`):

- `dist/newsletter-2025-03-20.html`
- `dist/newsletter-2025-03-20.json`
- `dist/newsletter-2025-03-20-search.json` — full discovery audit (window, per-source counts, `**theme_filter`** stats + scored samples, merged URLs)
- `.rom-newsletter/seen_urls.json` — updated after each successful run (unless `--dry-run-search`). Commit this file if you want deduplication shared across machines; add `.rom-newsletter/` to `.gitignore` if you prefer a local-only ledger.

## Performance

Runs can take **many minutes** when discovery is heavy (arXiv retries, many RSS feeds, newsroom article fetches) or when the **compose** step receives a **large excerpt bundle** (especially with `--no-skip-seen` or high `--arxiv-max`).

### Tips with today’s CLI

- `**--dry-run-search`** — Stops after writing `*-search.json` (no LLM). If this is already slow, time is going to **arXiv / RSS / newsroom**; if it is fast but the full run is not, the bottleneck is mostly **compose** (and occasionally JSON repair).
- **Seen-URL ledger** — Avoid `**--no-skip-seen`** for routine runs so fewer URLs reach the theme filter and the model (smaller prompts, faster generation).
- **Discovery scope** — Use `**--no-arxiv`**, `**--no-rss**`, or `**--no-newsroom**` when you only need part of the pipeline.
- **Caps** — Lower `**--arxiv-max`** and `**--max-non-arxiv-hits**` to shrink the prompt; raise them only when you need depth.
- **arXiv** — Tuning `**ROM_NEWSLETTER_ARXIV_READ_TIMEOUT`** and `**ROM_NEWSLETTER_ARXIV_USER_AGENT**` (see table above) can reduce wall time when the API is slow or returning **429**.

### Phase timings

Each run logs wall-clock **milliseconds per phase** to **stderr**, for example:

`rom-newsletter phase timings: arXiv=1200ms RSS=3400ms newsroom=8000ms compose=45000ms`

Skipped phases show **0ms** (e.g. `**--no-rss`**). `**compose**` is included only on **full** runs (after the LLM); `**--dry-run-search`** omits it. The written `***-search.json**` includes a `**phase_timings_ms**` object for **discovery** only (arXiv, RSS, newsroom), since that file is produced before compose.

### Future improvements (not implemented yet)

#### Performance & reliability

Ideas that would speed up or stabilize runs without changing the overall product shape:


| Area            | Idea                                                                                                                                                                                               |
| --------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **RSS**         | Fetch feeds **in parallel** (thread pool or async) instead of strictly sequential HTTP requests; optional per-feed **ETag** / `**If-Modified-Since`** caching to skip unchanged feeds.             |
| **Newsroom**    | Fetch multiple `**newsroom_listing`** sources concurrently where safe; cap the number of **article HTML** fetches used only for date resolution, or reuse dates from sitemap/listing when present. |
| **Compose**     | **Truncate or cap** per-hit excerpt length and/or total characters sent to the chat model; optionally **rank** hits and send only the top *N* per category to stay under a token budget.           |
| **Compose**     | **Streaming** responses where the API supports it (faster time-to-first-token; less impact on total generation time).                                                                              |
| **Compose**     | Optional **smaller/faster default model** for weekly cron when quality tradeoffs are acceptable; keep `**--model`** for manual "best" runs.                                                        |
| **Reliability** | Narrower **JSON schema** or constrained decoding to reduce **second-call JSON healing** in `compose`.                                                                                              |


#### Content additions


| Area                               | Idea                                                                                                                                                                                                                                                                                         |
| ---------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Events calendar**                | A dedicated section on upcoming conferences, workshops, and seminars relevant to the topics (e.g. MORe, USACM, ECCOMAS, WCCM, SIAM, etc.). Could be sourced from a curated list of event-feed URLs (WikiCFP RSS, conference sites) and filtered by submission/attendance deadline proximity. |
| **Open-source releases**           | Track new GitHub releases and PyPI packages in the ecosystem (pyMOR, libROM, SciML.ai, PhysicsNeMo, etc.) by monitoring GitHub release RSS feeds for a curated set of repos.                                                                                                                 |
| **Video content spotlight**        | Surface new lecture uploads, recorded conference talks, and tutorial videos from key channels (Steve Brunton, etc.). YouTube exposes per-channel RSS feeds that could be treated like any other RSS source.                                                                                  |
| **Preprint → publication tracker** | When a paper that appeared in a prior issue is formally published in a journal, surface it again with the DOI. Implementable by periodically polling Semantic Scholar or CrossRef for the arXiv IDs stored in the seen-URL ledger.                                                           |


#### Editorial quality


| Area                           | Idea                                                                                                                                                                                                                                                 |
| ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Emerging themes clustering** | Before the compose step, automatically cluster papers into sub-themes (e.g. "operator learning", "physics-informed surrogates for fluids", "ROM for structural mechanics") so the LLM can produce a more coherent narrative rather than a flat list. |
| **Executive summary / TL;DR**  | A 3–4 sentence paragraph synthesizing the week's most significant development across research and industry, placed at the very top of the email.                                                                                                     |
| **Social signal boost**        | Weight arXiv hits by Semantic Scholar citation velocity or social-mention counts in the window so genuinely "buzzy" preprints rank above routine submissions.                                                                                        |


#### Product & infrastructure


| Area                               | Idea                                                                                                                                              |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Newsletter archive with search** | Incrementally build a static HTML index (or JSON manifest) over all past `dist/` outputs so past issues are keyword-searchable without a backend. |
| **Source health monitoring**       | Track per-source hit counts across runs and emit a warning when a source has been silent for N consecutive weeks (dead feed, broken scraper).     |
| **Reader feedback loop**           | Buttondown exposes click data via API. Aggregate which story types attract clicks over time and use that signal to adjust theme scoring weights.  |


## Scheduled runs (GitHub Actions + Buttondown)

The workflow `[.github/workflows/weekly-newsletter.yml](.github/workflows/weekly-newsletter.yml)` runs **every Monday 14:00 UTC** (adjust the `cron` expression if you want a different time or timezone). It:

1. Sets `**WEEK_END`** to the **previous Sunday** in UTC (`date -u -d 'last Sunday'`), matching `rom-newsletter --date` as the **end** of the inclusive UTC window (`[dates.py](src/rom_newsletter/dates.py)` behavior).
2. Runs `rom-newsletter --date "$WEEK_END" --no-skip-seen --output-dir dist`.
3. Publishes `dist/newsletter-<WEEK_END>.html` to [Buttondown](https://buttondown.com/) via `rom-newsletter-buttondown`, using the `**subject`** from `dist/newsletter-<WEEK_END>.json`.

### GitHub Actions setup

1. **Commit the workflow** — Push `[.github/workflows/weekly-newsletter.yml](.github/workflows/weekly-newsletter.yml)` to your GitHub repo’s **default** branch so Actions can discover it.
2. **Enable workflows** — In the repo on GitHub: **Settings** → **Actions** → **General**. Under **Actions permissions**, allow Actions to run (adjust **Fork** pull request settings if you use forks).
3. **Add repository secrets** — **Settings** → **Secrets and variables** → **Actions** → **New repository secret** for each row below:


| Secret               | Purpose                                                                                                                               |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `LLM_BASE_URL`       | Same as local `.env` (e.g. OpenRouter base URL).                                                                                      |
| `LLM_API_KEY`        | Same as local `.env`; required for compose.                                                                                           |
| `LLM_MODEL`          | Default model id for the workflow run.                                                                                                |
| `BUTTONDOWN_API_KEY` | Buttondown API token ([API keys](https://buttondown.com/keys)); creates the email with `status: about_to_send` (send to subscribers). |


1. **Config in the repo** — Keep `[sources.json](sources.json)` at the repo root (the workflow does not pass `--sources`). Commit `templates/` and anything else `rom-newsletter` needs the same way you do locally.
2. **Optional: change schedule** — Edit the `cron` line in the workflow file. Times are **UTC**; GitHub does not guarantee execution to the exact minute.
3. **Test early** — **Actions** → **Weekly newsletter** → **Run workflow**. Optionally set **week end date** to a `YYYY-MM-DD` you have already validated locally; leave it empty to use “last Sunday UTC” (same as the scheduled run).
4. **Scheduled runs caveat** — GitHub may **disable** scheduled workflows on repositories with **no activity** for a long time; the schedule is **best-effort** and can drift slightly.
5. **arXiv in CI (optional)** — If `export.arxiv.org` returns **429** from GitHub’s shared IPs, add a repository **variable** `**ROM_NEWSLETTER_ARXIV_USER_AGENT`** (Settings → Secrets and variables → Actions → Variables) with a unique string (your repo URL or contact). The weekly workflow passes it into the generate step automatically.

**Manual run (after setup):** Actions → *Weekly newsletter* → *Run workflow* → optional **week end date** ISO `YYYY-MM-DD` (overrides the default “last Sunday UTC”).

**Upload HTML locally after a normal run** (requires `BUTTONDOWN_API_KEY` in `.env` or the environment):

```bash
rom-newsletter-buttondown --output-dir dist --date 2026-03-20
```

Use `**--draft**` to create a Buttondown **draft** instead of sending. `**--dry-run`** loads files and prints subject/body size only.

By default the publish script **updates in place**: if an email for the week already exists (matched by the `Week of <date>` label rendered in the HTML body), it PATCHes that email's subject and body so the web archive reflects the regenerated issue — it does **not** create a new email and does **not** re-send to subscribers. Only when no prior email exists does it create one. Pass `**--no-dedupe`** to always create a new email, or when publishing a template that lacks the week label (in-place update is skipped automatically if the label isn't in the HTML).

Optional env `**BUTTONDOWN_API_VERSION**` (e.g. `2026-04-01`) is passed as `X-API-Version` if you pin API behavior; see [Buttondown API versioning](https://docs.buttondown.com/api-versioning).

The publish step sends `**X-Buttondown-Live-Dangerously: true**` so the first programmatic send to subscribers under newer API versions succeeds, and so edge-case HTML is accepted (see [creating an email](https://docs.buttondown.com/api-emails-create)).

When the run succeeds, `**dist/**` is uploaded as a workflow artifact. If **Generate newsletter** fails before writing files, there may be nothing to upload; the workflow is configured to **ignore** a missing `dist/` folder so the job does not fail twice.

### Troubleshooting workflow failures


| Symptom                                 | What to check                                                                                                                                                                                                                                                                                                                                                                                               |
| --------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Process completed with exit code 1**  | Open the **Generate newsletter** step log (that is usually what failed). Confirm `**LLM_BASE_URL`**, `**LLM_API_KEY**`, and `**LLM_MODEL**` are set under Actions secrets and match a working local `.env`. Run the same command locally with the same `--date`.                                                                                                                                            |
| **arXiv / API errors**                  | **503**, **429** (rate limit), or **read timeouts** from `export.arxiv.org`: the client retries with backoff, `**Retry-After`**, a descriptive **User-Agent**, and longer waits for **429**. Set `**ROM_NEWSLETTER_ARXIV_USER_AGENT`** to a unique string for your project in CI. Set `**ROM_NEWSLETTER_ARXIV_READ_TIMEOUT**` if reads are slow. Use `**--no-arxiv**` in the workflow if arXiv stays flaky. |
| **Node.js 20 deprecation annotations**  | The workflow sets `**FORCE_JAVASCRIPT_ACTIONS_TO_NODE24`** and pins newer action versions; warnings can still appear until GitHub changes defaults—see the [GitHub changelog](https://github.blog/changelog/2025-09-19-deprecation-of-node-20-on-github-actions-runners/).                                                                                                                                  |
| **No artifact / “No files were found”** | Normal if generation failed: `**dist/`** was never created. Fix the failing step first; the artifact upload will not fail the job when `dist/` is absent.                                                                                                                                                                                                                                                   |
| **Buttondown 503 / HTML error page**    | Transient outage on Buttondown’s side (response may be Heroku “Application Error” HTML). `rom-newsletter-buttondown` **retries** 429/502/503/504; **re-run the workflow** later or publish locally when `api.buttondown.com` is healthy.                                                                                                                                                                    |


## Sources config (`sources.json`)

The config file is `**[sources.json](sources.json)`** at the repo root unless you pass `**--sources**`.

- `**version**`: must be `1`.
- `**sources**`: array of entries with `**label**` and `**url**` (HTTP(S)); optional `**id**`, `**category**` (e.g. `papers` / `industry`), `**kind**` (`arxiv`  `nvidia`  `siemens`  `ansys`  `generic` or omitted to infer from the URL host), `**rss**`, `**feed_hosts**` (extra hostnames allowed for RSS item links when they differ from the feed host), and `**newsroom_listing**` (boolean: built-in newsroom discovery; requires a supported `**id**` — see `[newsroom_listings.py](src/rom_newsletter/newsroom_listings.py)` and bullet 3 above).

Optional `**[sources.schema.json](sources.schema.json)**` documents the shape for editors that support JSON Schema.

## RSS feeds

Declare `**rss**` (and optional `**feed_hosts**`) on the matching entry in `**sources.json**`. The CLI merges **per-source RSS first**, then a small built-in default list in code, **deduplicating by feed URL**.

The **feed URL’s hostname** must match a host from your sources list, **unless** you add `**feed_hosts`**: an array of normalized hostnames allowed for that feed (needed when item links use a different domain than the feed).

Example (Synopsys feed with off-domain item links — same pattern as the `**synopsys-simulation-rss**` entry in `[sources.json](sources.json)`):

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

## Limitations

- Treat generated text as a **draft**; verify claims from primary sources.

## Project layout

- `src/rom_newsletter/` — CLI, arXiv, RSS, optional newsroom listings, compose, render, `[buttondown_publish](src/rom_newsletter/buttondown_publish.py)`
- `.github/workflows/weekly-newsletter.yml` — Monday cron + Buttondown publish
- `templates/newsletter.html.j2` — HTML layout
- `sources.json` — categorized sources and RSS feeds (required unless `--sources` points elsewhere)
- `sources.schema.json` — optional JSON Schema for `sources.json`


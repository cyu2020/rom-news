# Product brief: ROM newsletter agent

**Purpose:** This document is a **product / requirements definition** for rebuilding the **rom-newsletter** system with a coding agent or fresh team. It describes *what* the product does, *why*, and *behavioral contracts*—not implementation trivia unless it affects requirements.

---

## 1. Product summary

**Name (working):** `rom-newsletter`  

**One-liner:** A CLI tool that assembles a **weekly-style email/HTML briefing** on **reduced-order modeling (ROM)**, **scientific machine learning (SciML)**, and **digital twins**, by combining **curated sources**, **arXiv**, **RSS/Atom**, and **vendor web search (Tavily via AI Builders API)**, then **composing** copy with an LLM and **rendering** HTML.

**Primary user:** A technical editor or engineer who wants a **draft** newsletter for a fixed **UTC time window** (e.g. last 7 days ending on a chosen calendar date).

---

## 2. Goals and non-goals

### Goals

- **Configurable sources** as first-class data (`sources.json`: categories, kinds, optional RSS).
- **Deterministic discovery** where possible: arXiv by `submittedDate`; RSS by item `pubDate`/Atom times; Tavily by broad topic keywords on the **open web** (optional **restrict to `sources.json` hosts**), then **post-filter** by fetched-page publish date.
- **Two editorial sections:** **Research Papers** vs **Industry News**, each with **intro + up to 5 subsections** (title, body, links)—no global “letter intro” or “overall takeaway” (by design).
- **Traceability:** LLM must use only provided excerpts/URLs; structured JSON output; optional second-pass “refine.”
- **Deduplication** across runs via a **seen-URL ledger** (JSON file).
- **Audit artifact:** a `*-search.json` capturing window, per-channel stats, merged hits, theme filter stats.

### Non-goals

- Not a general-purpose news aggregator; **theme heuristics** narrow industry noise.
- Not guaranteed **recency** for every URL (Tavily ranking, JS sites, paywalls).
- Not a substitute for human fact-checking; output is a **draft**.

---

## 3. External dependencies (contracts)

| Dependency | Role |
|------------|------|
| **AI Builders / Space API** | `Authorization: Bearer <token>`; base URL configurable (`AI_BUILDERS_BASE_URL`, default `…/backend/v1`). Endpoints: `POST /chat/completions` (OpenAI-compatible), `POST /search/` (Tavily-style keyword search; request includes `keywords[]`, `max_results`). |
| **arXiv Atom API** | `GET https://export.arxiv.org/api/query` with Lucene query including `submittedDate:[start TO end]` in UTC. |
| **HTTP** | RSS fetch, Tavily result page fetch for date parsing, optional HTML render path. |

**Secrets:** `AI_BUILDER_TOKEN` in `.env` (never commit).

---

## 4. Time window (hard requirement)

- User supplies **`--date`** = end **calendar day** of the window (inclusive end-of-day UTC).
- **`--window-days`** (default `7`): window is **`[start_utc, end_utc]`** where `end_utc` is end of `--date` in UTC, and `start_utc` is `(window_days - 1)` days earlier at **00:00 UTC**.

All discovery filters (arXiv, RSS item time, Tavily page-date filter) must use **this same window**.

---

## 5. Source configuration

### 5.1 Files

- **`sources.json`**: `version: 1`, `sources[]` array (default path `<repo>/sources.json`; override with `--sources`). Per-source **`rss`** and **`feed_hosts`** are the only RSS configuration.
- Optional **`sources.schema.json`** for IDE validation.

### 5.2 Source record (conceptual schema)

Each source must have **`label`**, **`url`** (HTTP(S)). Derived: **`host`** (normalized, no leading `www.` for matching).

Optional:

| Field | Meaning |
|-------|--------|
| `id` | Stable string for diffs/tooling. |
| `category` | `papers` \| `industry` (default infer: `papers` if kind is arXiv else `industry`). |
| `kind` | `arxiv` \| `nvidia` \| `siemens` \| `ansys` \| `generic` (or infer from host). Drives **Tavily query templates**. |
| `rss` | Feed URL for this vendor; merged into RSS list. |
| `feed_hosts` | Extra hostnames allowed for **RSS item links** when the feed host differs from article URLs. |

### 5.3 Discovery routing rules

- **Tavily** uses a small set of **broad** keywords (no per-source `site:`); RSS and newsroom listing stages still pull vendor pages directly. By default Tavily may return **any** domain; **`--tavily-sources-only`** restricts to configured hosts. **`source_category`** comes from the matching source’s **`category`** when the URL host matches a source row; otherwise **industry**.
- **Newsroom listing** — Optional **`newsroom_listing: true`** on a source; vendor-specific discovery (listing HTML, sitemap, or **`p-1.ai`** homepage press links) keyed by **`id`**. Hits are filtered to the UTC window using listing/sitemap dates and/or **`extract_published_datetime`** on article pages.
- **RSS feed list** = per-source `rss` entries (deduped by URL) **first**, then built-in default feed URLs in code **last** (dedup by URL; first occurrence wins).

---

## 6. Discovery pipeline (functional requirements)

### 6.1 Unified hit model

Represent every candidate item as a **`SearchHit`** (conceptually): `url`, `title`, `content` (excerpt), `keyword` (provenance), optional score, and **`source_category`** mirroring `sources.json` `category` (`papers` \| `industry`).

**Merge order:** arXiv hits → RSS hits → newsroom hits → Tavily hits; **dedupe by canonical URL** (first wins).

### 6.2 arXiv

- Only if config includes an arXiv source (`kind` / host resolves to arXiv).
- Query must include broad ROM/SciML/twin terms **and** `submittedDate` in the UTC window.
- **`--arxiv-max`** caps results (default **25**, clamp 1–2000).
- Each hit tagged **`source_category: papers`**.

### 6.3 RSS

- Fetch each allowlisted feed URL; parse RSS 2.0 and Atom.
- **Feed host** must be allowlisted against **source hosts** or `feed_hosts` for that feed.
- Keep items whose **published time** parses and falls in `[start_utc, end_utc]`; drop items with no parseable date.
- Atom: prefer **`published`** before **`updated`** for the timestamp.
- Item **`source_category`** from the map **feed URL → source `category`** (from `sources.json`); any feed URL not in that map defaults to **`industry`**.

### 6.4 Newsroom listings (optional)

- For sources with **`newsroom_listing: true`**, run vendor-specific discovery (not always the raw **`url`** page).
- **PhysicsX:** listing HTML at **`url`**; dates from **`news-item-date`**. **Neural Concept:** **Press Releases** cards only. **Emmi:** `/news/…` from listing. **Siemens:** **`news.siemens.com/en-us/sitemap-en-us.xml`** (homepage is JS-heavy); filter by **`<lastmod>`**; titles from URL slugs. **P-1 AI:** press links on **`https://p-1.ai/`** (BusinessWire, Fortune, arXiv, etc.); dates/titles from **article fetch**.
- If no listing/sitemap date, fetch the article page and use **`extract_published_datetime`** for the UTC window filter.
- **`--no-newsroom`** disables this stage.

### 6.5 Tavily (web search)

- Build a **small fixed set** of broad keyword strings (shared **topic clause**: ROM, SciML, twins, physics-informed, neural operators, surrogates, **Physics AI**, etc., plus extra OR-groups for CAE / Omniverse / FNO-style terms). **No** `site:` in the query string; by default results may be from **any** host.
- **`POST /search/`** with `keywords` list and **`max_results`** per keyword (CLI **`--max-results`**, default 6, max 20).
- Normalize API response to hits; optionally **restrict** by URL host to configured sources (**`--tavily-sources-only`**). Set **`SearchHit.source_category`** from the **`sources.json`** row whose **host** matches the result URL when a match exists; otherwise **`industry`**.
- **Optional:** if API returns a publish time on a result object, drop items **outside** the window **before** HTML fetch (cheap pre-filter).

### 6.6 Tavily page-date filter (default ON)

For each Tavily hit **after** normalization:

1. Fetch the **HTML** of the result URL.
2. Extract **best-effort published datetime** in order:
   - Meta: `article:published_time`, `og:published_time`, `date`, etc.
   - JSON-LD scripts: `datePublished` / `dateModified` / `uploadDate` (recursive walk).
   - **Visible English dates** in HTML after stripping `<!-- comments -->`: e.g. `March 17, 2026`, `17 March 2026` — use **first match in document order** (not “smallest year”).
3. **Decision:**
   - Parsed date **inside** window → **keep**.
   - Parsed date **outside** window → **drop**.
   - **No** parseable date → **drop** by default (**strict**); **`--keep-tavily-undated`** keeps them (noisier).
4. **Fetch failure** (no HTML) → treat as undated → **drop** under default strict behavior.

**Opt-out:** **`--no-filter-tavily-by-page-date`** skips the whole fetch/parse step (faster, more stale URLs).

**Tuning:** `--tavily-date-workers`, `--tavily-date-timeout`.

### 6.7 Seen URLs

- Load `.rom-newsletter/seen_urls.json` (or `--history-file`).
- After merge, **drop** hits whose URL is in the ledger unless **`--no-skip-seen`**.
- On successful full run (not dry-run search), **append** new URLs to the ledger.

### 6.8 Theme filter (non–arXiv)

- Score **title + content + url + keyword** with weighted regexes (ROM, twins, SciML, CAE, simulation platforms, etc.).
- **Never** drop arXiv hits for low theme score.
- Parameters: **`--theme-min-score`**, **`--theme-floor-non-arxiv`**, **`--theme-backfill-min-score`**, **`--max-non-arxiv-hits`**.

---

## 7. Composition (LLM)

- **Input:** two **separate excerpt bundles**:
  - **Research:** hits with `source_category == papers`.
  - **Industry:** hits with `source_category == industry`.
- **System prompt:** JSON-only output; schema **must** include:
  - `subject` (short)
  - `research_papers`: `{ intro, subsections[1..5] }` each subsection `{ title, body, links[] }`
  - `industry_news`: same shape
- **Rules:** no invented facts; URLs must come from excerpts; skip off-topic industry items; **no** global intro/takeaway.
- **Optional:** **`--refine`** second pass with same bundle + draft JSON.
- **Client:** OpenAI-compatible SDK pointed at AI Builders base URL + bearer token.

---

## 8. Rendering

- **Jinja2** template → single **HTML** file (e.g. `templates/newsletter.html.j2`). Section order in HTML: **Industry News**, then **Research Papers**.
- **No** separate “Sources” block in HTML; links appear under each subsection in the draft.

---

## 9. CLI outputs (contract)

For `--date YYYY-MM-DD` and default `dist/`:

| Output | Description |
|--------|-------------|
| `dist/newsletter-<date>.html` | Rendered newsletter |
| `dist/newsletter-<date>.json` | Serialized draft (Pydantic `model_dump`) |
| `dist/newsletter-<date>-search.json` | Full discovery audit (window, arXiv meta, RSS meta, newsroom meta, Tavily block, merged hits with `source_category`, theme stats) |
| `.rom-newsletter/seen_urls.json` | Updated URL ledger (unless `--dry-run-search`) |

**`--dry-run-search`:** run discovery **only**; write `*-search.json`; **no** LLM or HTML; **do not** update ledger.

---

## 10. Key modules (rebuild map)

| Area | Responsibility |
|------|----------------|
| `config.py` | Project root, API base, token, default model. |
| `sources.py` | Load `sources.json`, `Source` model, `effective_source_kind`, `feed_url_to_category_map`, allowed hosts. |
| `dates.py` | `utc_window_for_week`. |
| `arxiv_client.py` | arXiv API → `SearchHit` + metadata. |
| `rss_client.py` | RSS/Atom fetch + window filter. |
| `newsroom_listings.py` | Listing-page fetch + parsers for PhysicsX / Neural Concept / Emmi; window filter. |
| `search.py` | Tavily keywords, `normalize_hits`, `merge_hits_ordered`, `SearchHit`, `pipeline_report_json`. |
| `page_dates.py` | HTML fetch + `extract_published_datetime` + `filter_tavily_hits_by_page_date`. |
| `relevance.py` | Theme score + `apply_theme_filter`. |
| `compose.py` | Pydantic `NewsletterDraft`, `compose_newsletter`, optional refine. |
| `render.py` | HTML from draft + template. |
| `history.py` | Seen URL ledger. |
| `cli.py` | Argparse, orchestration. |

---

## 11. Known limitations (accept or document)

- Tavily has **no** API-level date range; recency depends on **ranking + page-date filter**.
- **Strict undated dropping** removes many vendor pages when HTML has no parseable date.
- Theme filter is **regex-based**, not an LLM classifier.
- RSS/Tavily **article** URLs may be off-domain; RSS uses feed allowlist semantics.

---

## 12. Rebuild checklist (for a coding agent)

1. Python 3.11+, package `httpx`, `openai`, `pydantic`, `jinja2`, `python-dotenv`; entrypoint `rom-newsletter`.
2. Implement `sources.json` v1 + `load_sources` (JSON only).
3. Implement UTC window helper.
4. **arXiv** + **RSS** + optional **newsroom** + **Tavily** merge with `SearchHit.source_category` and dedupe.
5. Tavily: broad keywords + optional host restriction; URL → `source_category` from matching source when present; optional API date pre-filter.
6. Optional newsroom listing stage + merge order before Tavily.
7. **Page-date filter** default on; `extract_published_datetime` with meta → JSON-LD → English dates after comment strip; `drop_undated` default **true**; `--keep-tavily-undated` to relax.
8. Seen-history + theme filter + compose split bundles + render + search audit JSON.
9. Document env vars and CLI flags in `README.md`.
10. Ship `sources.schema.json` optional.

---

## 13. Version

This brief matches the **rom-newsletter** codebase as of the **product brief** addition; when rebuilding, re-validate against **`README.md`** and **`src/rom_newsletter/cli.py`** for flag names (e.g. `--keep-tavily-undated` vs deprecated `--tavily-drop-undated` as no-op).

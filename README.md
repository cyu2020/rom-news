# ROM newsletter agent

A weekly briefing for engineers and researchers working in **reduced-order modeling (ROM)**, **scientific machine learning (SciML)**, and **digital twins**. It discovers papers and industry announcements, ranks relevant stories, composes a cited draft, renders HTML, and publishes through Buttondown.

The pipeline keeps **discovered candidates**, **composition inputs**, **cited stories**, and **published stories** separate. Generating a draft does not mark its candidates as published.

## Quick start

Requires Python **3.11+** and [uv](https://docs.astral.sh/uv/). Scheduled runs use Python 3.13. The committed `uv.lock` makes dependency installation reproducible.

```bash
uv sync --frozen --extra dev

# Inspect discovery and selection without calling an LLM.
uv run rom-newsletter --date 2026-10-04 --dry-run-search

# Generate a cited HTML draft and its audit files.
uv run rom-newsletter --date 2026-10-04 --refine

# Validate generated files without contacting Buttondown.
uv run rom-newsletter-buttondown --date 2026-10-04 --dry-run

# Create a Buttondown draft for review.
uv run rom-newsletter-buttondown --date 2026-10-04 --draft

# Queue that same draft for subscribers after review.
uv run rom-newsletter-buttondown --date 2026-10-04
```

Set the following in a repository-root `.env` or the environment. Never commit `.env`.

| Variable | Purpose |
| --- | --- |
| `LLM_BASE_URL` | OpenAI-compatible chat API base URL, including `/v1` where required |
| `LLM_API_KEY` | API token for composition |
| `LLM_MODEL` | Model ID; overridable with `--model` |
| `BUTTONDOWN_API_KEY` | Required for creating, updating, or sending a Buttondown email |

Discovery-only mode does not require LLM credentials. Publishing dry-run does not require Buttondown credentials.

## How selection works

1. **Discover within a UTC window.** `--date` is the final day, not the first day. The default seven-day window for `2026-10-04` is September 28 through October 4, inclusive.
2. **Retrieve arXiv candidates across the window.** The shipped topic query uses specific ROM/SciML/twin phrases rather than OR-ing entire categories such as `cs.LG`. The client pages through results, spaces API requests, and records total matches and coverage. The default retrieval cap is **400** candidates; this is separate from the composition shortlist.
3. **Discover industry stories.** Sources in [sources.json](sources.json) supply RSS feeds and supported newsroom listings. Newsroom discovery already uses a worker pool; RSS fetches remain sequential. Publication dates are checked against the window. Siemens sitemap dates are modification dates and can represent edits to older stories.
4. **Skip stories published in other issues.** The published-history ledger is applied before ranking. Stories from the issue being regenerated remain eligible, so rerunning the same week does not empty its content.
5. **Rank by relevance.** Weighted theme patterns score titles and excerpts; URLs and query labels cannot inflate scores. Research needs score **2** and is capped at **25** stories. Industry needs score **2** and is capped at **20**. Automatic industry backfill is disabled by default, so a quiet week stays short. Scores and decisions are included in the audit.
6. **Bound model inputs.** Each excerpt is capped at **1,800 characters** and each track's source bundle at **30,000 characters**. Stories that cannot fit are omitted. These are character budgets, not exact tokenizer limits.
7. **Compose and validate.** Research and industry inputs are separate. Every substantive subsection must cite **1–3 supplied sources from its own track**. Unknown, unsafe, missing, or cross-track citations fail validation after composition and optional refinement. Empty tracks use an honest intro and zero subsections.
8. **Render and record selection.** HTML, JSON, exact input excerpts, selected URLs, and file hashes are saved. Publishing verifies those hashes and citations again before any write to Buttondown.

If arXiv matches exceed the retrieval cap, or total coverage cannot be established, full generation stops after writing the audit. Increase `--arxiv-max`, narrow the query, or explicitly use `--allow-incomplete-arxiv` if partial coverage is acceptable. Search-only mode still produces diagnostics.

Citation checks establish **source provenance**, not whether every sentence follows from its source. The optional `--refine` pass reviews claims against the provided excerpts; human review remains useful for metrics, technical comparisons, and vendor claims.

## Configuration and CLI

[topic.json](topic.json) controls the arXiv query, theme weights, editorial instructions, section headings, and fallback subject. A missing default topic file uses built-in ROM defaults. Explicit custom paths must exist. See [custom topics](docs/custom-topic.md).

[sources.json](sources.json) controls source categories, feeds, and newsroom discovery. A source needs `label` and `url`, with optional `id`, `category`, `kind`, `rss`, `feed_hosts`, and `newsroom_listing`. The feed hostname must be allowed by the source configuration; `feed_hosts` adds permitted feed hosts. Article links can be off-domain. Per-source RSS entries win over duplicate built-in feed URLs. See [newsroom parsers](docs/newsroom_listings.md) and the JSON schemas for editor assistance.

| Generation option | Default / behavior |
| --- | --- |
| `--date YYYY-MM-DD` | Window ending date; defaults to the local calendar date |
| `--window-days N` | 7 inclusive UTC days |
| `--arxiv-max N` | Retrieve up to 400 candidates; range 1–2000 |
| `--max-research-hits N` | Send at most 25 ranked research stories to composition |
| `--research-min-score N` | Research relevance threshold: 2 |
| `--max-non-arxiv-hits N` | Industry shortlist cap: 20 |
| `--theme-min-score N` | Industry threshold: 2; 0 disables filtering but retains ranking/cap |
| `--theme-floor-non-arxiv N` | Industry backfill target: 0 (disabled) |
| `--theme-backfill-min-score N` | Explicitly enabled backfill requires score ≥1 by default |
| `--excerpt-chars N` | Per-story excerpt cap: 1800 |
| `--prompt-chars-per-track N` | Per-track source bundle cap: 30000 |
| `--allow-incomplete-arxiv` | Explicitly permit capped/unknown arXiv coverage |
| `--dry-run-search` | Discovery/selection only; no LLM or history mutation |
| `--refine` | Additional LLM review; final citations are still checked in code |
| `--model ID` | Override `LLM_MODEL` |
| `--no-arxiv`, `--no-rss`, `--no-newsroom` | Skip discovery channels |
| `--no-skip-seen` | Include stories published in other issues |
| `--history-file PATH` | Override published-history path |
| `--sources PATH`, `--topic PATH` | Override source/topic configuration |
| `--output-dir PATH`, `--template-dir PATH` | Override output/template paths |

A custom profile with `theme.disabled: true` disables both research and industry relevance thresholds; caps and prompt budgets still apply.

Additional environment settings:

| Variable | Purpose |
| --- | --- |
| `ROM_NEWSLETTER_TOPIC` | Custom topic profile; `--topic` takes precedence |
| `ROM_NEWSLETTER_ARXIV_USER_AGENT` | Identifying arXiv User-Agent, useful on shared CI IPs |
| `ROM_NEWSLETTER_ARXIV_READ_TIMEOUT` | arXiv read timeout in seconds; default 180 |
| `BUTTONDOWN_API_VERSION` | Optional Buttondown `X-API-Version` header |

## Outputs and publication history

For `--date 2026-10-04`, generation writes:

- `dist/newsletter-2026-10-04.html` — rendered issue.
- `dist/newsletter-2026-10-04.json` — structured newsletter.
- `dist/newsletter-2026-10-04-search.json` — discovery candidates, per-channel diagnostics, research scores, industry selection statistics, exact composition inputs, prompt budgets, and discovery timings.
- `dist/newsletter-2026-10-04-selection.json` — issue key (`<topic-name>:<week-end>`), cited URLs, history path, and HTML/JSON hashes.

Publishing maintains:

- `.rom-newsletter/publications.json` — Buttondown email IDs, statuses, cited URLs, and pending create/send outcomes.
- `.rom-newsletter/published_urls.json` — URLs accepted for publication, grouped by issue. Queued emails count as published for deduplication; drafts do not. Archive edits preserve URLs already emailed in that issue.

arXiv abstract/PDF URLs and version suffixes normalize to one paper identity. Common tracking parameters are removed from article URLs while meaningful query parameters are preserved.

**Migration:** the old `seen_urls.json` recorded all composition candidates and is no longer the default. It is not automatically imported because it cannot distinguish omitted stories from published ones. An explicit `--history-file` can still read a legacy list/URL ledger for discovery. When publishing, use the new issue-aware ledger rather than reusing legacy state. Existing Buttondown issues can be recovered by their legacy `Week of <date>` body label for the default ROM topic.

## Dependable Buttondown publishing

Publishing creates a **draft first**, saves its email ID, and then queues that same ID. A deterministic issue key and source list are also stored in Buttondown metadata. The publisher uses a local file lock, and the weekly workflow serializes runs sharing publication state.

On reruns:

- A saved email ID is reused; a missing/deleted saved email stops the run rather than triggering a replacement send.
- If local state is missing, Buttondown metadata is searched before creating anything. Multiple matches stop the run for inspection.
- A draft can be updated and later queued using the same ID.
- An already-sent email receives a subject/body archive update without a status change or resend.
- A queued, scheduled, or in-flight email is left untouched; its stored source list is used for history rather than a newly generated draft's citations.

An uncertain create or send is reconciled with a read. If acceptance cannot be confirmed, pending state is saved and the run stops. **POST creation and send transitions are never blindly retried.** Lookup reads retry transient failures. This reduces duplicate-send risk; it is not a provider-backed exactly-once guarantee across arbitrary independent machines.

| Publishing option | Behavior |
| --- | --- |
| `--draft` | Create/update a draft without queuing it; existing sent issues remain sent |
| `--dry-run` | Validate generated files and print a summary; no API call |
| `--state-file PATH` | Override `.rom-newsletter/publications.json` |
| `--history-file PATH` | Override the manifest's publication-history path, useful when moving artifacts between machines |
| `--api-version VERSION` | Override the environment's Buttondown API version |

The old `--no-dedupe` escape hatch was removed so normal reruns always recover the existing issue. Publishing requires the generated selection manifest and search audit; regenerate older standalone HTML files before publishing. Editing generated HTML/JSON invalidates the manifest hashes. An issue with no cited stories can be saved as a draft but cannot be sent.

The API request uses Buttondown's documented `X-Buttondown-Live-Dangerously` header for programmatic sending. Never run the publishing command without `--draft` or `--dry-run` unless you intend to send to subscribers.

## Scheduled runs and checks

[Weekly newsletter](.github/workflows/weekly-newsletter.yml) runs every **Monday at 14:00 UTC**, using the previous Sunday as the window end. In Chicago this is 9 a.m. during daylight saving time and 8 a.m. during standard time. Scheduled execution is best-effort and may be delayed.

1. Enable GitHub Actions for the repository.
2. Add repository secrets: `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, and `BUTTONDOWN_API_KEY`.
3. Optionally add repository variable `ROM_NEWSLETTER_ARXIV_USER_AGENT` with an identifying project URL/contact.
4. For a manual run, open **Actions → Weekly newsletter → Run workflow** and optionally enter a week-ending date. This workflow publishes to subscribers.

The workflow restores publication state, installs locked dependencies, runs regression tests, generates with `--refine`, validates publication files, and publishes. It uses the default relevance thresholds and published-story deduplication. State is saved even after failures, and issue artifacts plus state JSON are uploaded for recovery.

GitHub Actions cache is **best-effort storage** and can be evicted. Buttondown identity lookup still protects issue reruns if the cache disappears, but restoring the publication ledger from the last workflow artifact preserves cross-issue story deduplication. Local machines and CI do not automatically share history; use the same state files for consistent selection. Do not run independent publishers for the same issue concurrently.

[Checks](.github/workflows/checks.yml) runs offline regression tests and lint checks for pull requests and pushes to `main`. Tests use saved arXiv feeds and mocked Buttondown responses; they do not send email or call an LLM.

```bash
uv sync --frozen --extra dev
uv run --frozen ruff check src tests
uv run --frozen pytest -q
```

Tests cover pagination and truncation, inclusive date windows, relevance and prompt budgets, URL identity, citation provenance after refinement, output integrity, publication history, draft-to-send transitions, reruns, deleted IDs, and accepted/unconfirmed API timeouts.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| arXiv 429/503/timeouts | Use an identifying User-Agent, wait for the service to recover, or adjust the read timeout. API reads already retry with backoff. |
| Incomplete arXiv coverage | Inspect the search audit; increase the candidate cap, narrow the query, or explicitly allow partial coverage. |
| Citation validation failure | Inspect the model draft/excerpts and revise the prompt/model. Unsupported links are not silently removed before sending. |
| Hash/manifest mismatch | Regenerate HTML and JSON together; rerun publishing dry-run. |
| Unconfirmed create/send | Inspect the saved email ID and pending state in the workflow artifact and Buttondown. A subsequent run can recover if the email becomes visible or its send is confirmed. Clear a pending flag only after independently confirming that the corresponding operation was not accepted; preserve the email ID. |
| Deleted email ID or multiple matching issues | Resolve the Buttondown records explicitly; the publisher will not create a replacement automatically. |
| Missing state cache | Restore `.rom-newsletter/*.json` from the latest artifact to preserve story history; issue identity can also be recovered from Buttondown metadata. |
| Slow generation | Compare phase timings; reduce shortlist/excerpt budgets, or use discovery-only mode to isolate network delays. |

## Future work — not implemented

These ideas extend the completed retrieval → ranking → citation validation → publication milestone.

| Area | Proposed work |
| --- | --- |
| **Structured paper spotlight** | Problem, method, baseline, reported accuracy/speedup, limitations, and code availability. Use “not reported” when evidence is missing. |
| **Engineering implications** | Explain effects on simulation workflows, training-data needs, geometry generalization, and deployment; separate editorial interpretation from source claims. |
| **Evidence labels** | Distinguish preprints, vendor announcements, reproducible benchmarks, and customer case studies; attribute vendor claims explicitly. |
| **Open-source releases** | Curated release coverage for ROM/SciML tools such as pyMOR, libROM, SciML, and PhysicsNeMo. |
| **Searchable archive** | Static issue index with topic/company/method filters and keyword search. |
| **Events calendar** | Relevant workshops, conferences, seminars, and submission deadlines from curated sources. |
| **Video spotlight** | Selected lecture/tutorial uploads and recorded conference talks. |
| **Publication tracking** | Revisit previously covered preprints when journal publication/DOI metadata appears. |
| **Theme clustering and diversity** | Cluster candidates before composition, avoid repetitive coverage, and rank for novelty as well as relevance. |
| **Executive summary** | Optional short synthesis of the week's strongest research and industry developments. |
| **Source health monitoring** | Historical source hit/error counts; alerts for stale feeds or broken parsers. |
| **Reader feedback** | Use aggregate click/feedback signals to guide editorial selection without allowing popularity to override relevance or evidence quality. |
| **RSS performance and caching** | Parallel feed requests and conditional HTTP caching; cache newsroom article/date lookups too. Newsroom source concurrency already exists. |
| **Composition efficiency** | Provider-supported constrained JSON output, explicit token budgeting, usage/cost reporting, and model comparisons on a fixed evaluation set. Character/excerpt budgets already exist. |
| **Editorial evaluation** | Human-labeled examples for relevance, claim support, usefulness, and coverage; track quality across prompt/model changes. |
| **Durable shared state** | Replace best-effort CI cache with durable storage and stronger coordination across independent publishers. |
| **Repository onboarding** | A checked-in example issue and preview image, contribution guide, and additional packaging validation for distribution beyond an editable checkout. |

## Project layout

- `src/rom_newsletter/` — CLI, discovery, relevance, composition, rendering, history, publishing.
- `templates/newsletter.html.j2` — industry-first email layout.
- `topic.json`, `sources.json` and their schemas — topic/source configuration.
- `tests/` — offline fixtures and regression checks.
- `.github/workflows/` — weekly publication and PR checks.
- `uv.lock` — reproducible dependencies.
- `docs/` — custom topic, newsroom, and agent conventions.

Generated prose is a draft based on source excerpts. Verify important technical or commercial claims in the primary source before relying on them.

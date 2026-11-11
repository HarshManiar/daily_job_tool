# Firsthand — Plan

Legend: `[model/subagent]`. Every delegation passes `model` explicitly on the Agent call. ☐ = todo, ☑ = done (with "done by: <model>").
Each task = one subagent run, ends with tests passing + one commit. The main thread (Opus) reviews every diff before commit; for risky diffs (schema, closing, dedupe, CrawlClient) review is mandatory and noted.

Dependencies are in `(after …)`. Tasks without mutual dependencies run in parallel.

---

## Phase 0 — Plan ✅ awaiting approval

- ☑ **0.1** `[opus/main]` Create `.claude/agents/{architect,builder,grunt}.md`. — done by: opus
- ☑ **0.2** `[opus/main]` Probe ATS endpoints and robots.txt (results in ARCHITECTURE §5). — done by: opus
- ☑ **0.3** `[opus/main]` Write `docs/ARCHITECTURE.md`. — done by: opus
- ☑ **0.4** `[opus/main]` Write `docs/PLAN.md`. — done by: opus
- ☐ **0.5** You approve, or answer Q1–Q5 in ARCHITECTURE §13.

---

## Phase 1 — Skeleton + Greenhouse/Lever/Ashby → end-to-end slice

**Goal:** ~200 companies crawled, normalized, stored, shown in a searchable feed.

- ☐ **1.0** `[haiku/grunt]` Write `docs/ENDPOINTS.md` from ARCHITECTURE §5 + the probe log (one section per ATS: URL, method, params, pagination, robots, sample fields). *AC:* every ATS in §5 has a section; no endpoint claims beyond §5. **Also the routing check:** after this run, the main thread inspects the subagent transcript to confirm it ran on Haiku, and reports to you.
- ☐ **1.1** `[haiku/grunt]` Scaffold: `brew install uv postgresql@17` instructions in README, `uv init` (py3.12), deps from ARCH §1, ruff + pytest + pytest-asyncio config, Makefile (`make dev|test|lint|db-up|migrate`), `.env.example`, `.gitignore`, directory tree from ARCH §2 with empty modules. *AC:* `uv run pytest` (0 tests ok), `uv run ruff check` clean.
- ☐ **1.2** `[sonnet/builder]` (after 1.1) Config (`pydantic-settings`), DB session (asyncpg), Alembic set up, **initial migration = all Phase 1 tables in ARCH §3** (companies, sources, blocked_domains, postings, jobs, crawl_runs, company_daily_stats, robots_cache, fx_rates). pytest fixture creating a throwaway test database. *AC:* `make migrate` up/down/up on an empty DB; test asserts all tables + indexes exist. **Opus review: schema.**
- ☐ **1.3** `[sonnet/builder]` (after 1.1) `CrawlClient`: UA, robots via protego + `robots_cache` (DB-optional in-memory for tests), per-host token bucket with overrides, retries/backoff/Retry-After, host circuit, size cap, request accounting, `FixtureTransport` for replay. *AC:* respx tests for robots allow/deny/5xx-deny, 429 with Retry-After, retry exhaustion, rate-limit timing (fake clock), host circuit trip/reset. **Opus review.**
- ☐ **1.4** `[sonnet/builder]` (after 1.3) Adapter base (`base.py` exactly as ARCH §4), adapter registry, `firsthand fixtures record <adapter> <token>` CLI (saves responses + request metadata to `tests/fixtures/<adapter>/<token>/`, refuses >500 KB), and a test asserting no adapter module imports httpx. *AC:* tests pass; record command works against Greenhouse with a small board.
- ☐ **1.5** `[haiku/grunt]` (after 1.4) Record fixtures: 3 small boards each for Greenhouse, Lever (incl. one EU if found), Ashby, plus one nonexistent token per ATS (404 fixture). Main thread supplies the token list. *AC:* fixture dirs exist, each ≤500 KB, README listing them.
- ☐ **1.6a** `[sonnet/builder]` (after 1.5) Greenhouse adapter + offline tests (mapping, empty, 404, malformed record). Confirm the pay-transparency detail param, and if it exists, use it via `fetch_detail` for new postings only.
- ☐ **1.6b** `[sonnet/builder]` (after 1.5) Lever adapter (US/EU region via `config.region`, pagination) + tests.
- ☐ **1.6c** `[sonnet/builder]` (after 1.5) Ashby adapter (compensation, workplaceType) + tests.
- ☐ **1.7a** `[haiku/grunt]` (after 1.1) Data files: `data/titles.yaml` (abbreviations, seniority keywords, level suffixes), `data/functions.yaml` (keyword → function), `data/tech_tags.yaml` (~300 tags with aliases, `requires_context` for ambiguous ones per ARCH §6.10), `data/blocked_domains.csv` (aggregators), Phase‑1 location table (countries + aliases, US/CA/AU states, top ~500 cities with country). *AC:* YAML loads; schema documented at the top of each file. **Opus spot-check 30 entries.**
- ☐ **1.7b** `[sonnet/builder]` (after 1.7a) Normalizer part 1: text sanitize, title_norm, seniority, function, employment type. Golden test file with ≥200 titles (grunt may draft the titles; builder owns expected values). *AC:* ≥95% golden accuracy.
- ☐ **1.7c** `[sonnet/builder]` (after 1.7a) Normalizer part 2: locations v1, remote type + regions, tech tags. Golden tests ≥100 location strings. 
- ☐ **1.7d** `[sonnet/builder]` (after 1.2) Normalizer part 3: salary (structured + regex v1), annualization, FX (ECB fetch command + `fx_rates`), sanity window. Golden tests ≥150 salary strings.
- ☐ **1.8** `[sonnet/builder]` (after 1.2, 1.6*, 1.7*) Ingest pipeline: upsert postings with payload hash, detail-on-new hook, normalize → jobs, dedupe (ARCH §7 incl. explicit-link + same-source suffixing), closing with `complete` flag + mass-disappearance guard, reopen within 14 days, crawl_runs row. `firsthand renormalize`. *AC:* DB tests replaying fixture sequences: new → unchanged → one removed (missed 1) → removed again (closed) → reappears (reopened); suspicious drop not closed. **Opus review: closing/dedupe.**
- ☐ **1.9** `[sonnet/builder]` (after 1.8) Worker: SKIP LOCKED claim, concurrency, interval/priority/jitter, per-source breaker, `firsthand crawl --once [--source ID|--adapter X|--limit N]`, `firsthand worker`. *AC:* tests with fake adapters: claims don't double-run across 2 workers; breaker opens after 5 failures; interval math.
- ☐ **1.10** `[sonnet/builder]` (after 1.2) Registry importer: `firsthand registry import data/seeds.csv` (idempotent upsert, domain normalization to registrable domain, blocked_domains enforced), `firsthand registry verify` (hits each source once, records board name + count). *AC:* tests; re-import is no-op.
- ☐ **1.11** `[haiku/grunt]` (after 1.10) Compile `data/seeds.csv` with ~250 tech companies on Greenhouse/Lever/Ashby (from yc-oss data + their public board URLs), run `registry verify`, drop failures. *AC:* ≥200 verified rows; failures listed in commit message.
- ☐ **1.12** `[sonnet/builder]` (after 1.8) Web v1: FastAPI + Jinja + HTMX + Tailwind standalone CLI. Feed (newest first, keyset pagination), keyword search (FTS + trigram on title/company), job detail page with "Apply on {company}" link, `/bot` page, `/api/v1/jobs`. Own visual identity per ARCH header. *AC:* route tests; Lighthouse-ish sanity: feed page < 300 ms server time on seeded DB with 20k jobs (synthetic).
- ☐ **1.13** `[opus/main]` Live run on the ~200 companies, review a 50-job sample for normalization quality, fix-or-file issues, **Phase 1 report** (companies, jobs, adapter success rates, coverage %s, what broke, Phase 2 plan). Wait for go-ahead.

---

## Phase 2 — Registry growth + ATS detection + more adapters

**Goal:** 2,000+ companies, Workday support, adapter health dashboard.

- ☐ **2.1** `[haiku/grunt]` Fetch the yc-oss companies dataset + 2–3 public company lists (licenses recorded in `docs/SOURCES.md`) → `data/candidates.csv` (name, domain, source, tags, team size, country). Dedupe by domain. No aggregator domains.
- ☐ **2.2** `[haiku/grunt]` Record homepage/careers-page fixtures for ~40 companies covering every ATS + some custom pages (list provided by main thread) for detector tests.
- ☐ **2.3** `[sonnet/builder]` (after 2.2) Detector per ARCH §9: candidate pages, redirect/iframe/script/link/inline patterns, `match_url` on every adapter (even ones not yet implemented — pattern-only stubs), slug probing with identity verification, confidence. `firsthand detect <domain|csv> --out results.csv`. *AC:* ≥95% correct on the 40 fixtures, 0 false positives on the "custom page" fixtures. **Opus review.**
- ☐ **2.4** `[haiku/grunt]` (after 2.1, 2.3) Run detector over candidates in batches of 200 (rate-limited, logs kept), produce `data/detected.csv`.
- ☐ **2.5** `[opus/main]` (after 2.4) Review 100 random detections + all probe-based hits; tune rules or send fixes to builder; import accepted rows.
- ☐ **2.6a–f** `[sonnet/builder]` (parallel, each after fixtures from 2.7) Adapters: **a** Workable, **b** Recruitee, **c** Teamtailor (RSS + JSON-LD), **d** Personio (XML), **e** BambooHR (verify endpoint first; if no public JSON, report and stop), **f** SmartRecruiters via public pages + JSON-LD (only if Q5 = keep).
- ☐ **2.7** `[haiku/grunt]` Record fixtures for 2.6a–f and 2.8–2.9 (tokens from detected.csv).
- ☐ **2.8** `[sonnet/builder]` Workday adapter: parse tenant/wdN/site from URL, list pagination by 20, sitemap-first diff, detail-on-new, `complete` semantics, 0.5 req/s host override. **Opus review.**
- ☐ **2.9** `[sonnet/builder]` Oracle Recruiting Cloud adapter.
- ☐ **2.10** `[sonnet/builder]` Stretch: iCIMS, SuccessFactors, Jobvite adapters (HTML/sitemap + JSON-LD). Split into three runs if attempted.
- ☐ **2.11** `[sonnet/builder]` Adaptive scheduling (EWMA → interval/priority), `not_found`×3 → broken + re-detect queue.
- ☐ **2.12** `[sonnet/builder]` `firsthand report` + `/admin` (basic auth): sources by status, failing sources, per-adapter success rate 24h/7d, jobs opened/closed per day, normalizer coverage.
- ☐ **2.13** `[opus/main]` Scale run to 2,000+ companies, **Phase 2 report**. Wait.

---

## Phase 3 — Product features

- ☐ **3.1** `[sonnet/builder]` `SearchFilters` model + query builder for all filters (ARCH §10), filter UI (HTMX partial updates, URL state), supporting indexes. *AC:* each filter has a DB test; combined-filter query p95 < 300 ms on 150k synthetic jobs (EXPLAIN in PR notes).
- ☐ **3.2** `[sonnet/builder]` Nightly stats job → `company_daily_stats`; company list + company pages with open roles + 90-day trend (server-rendered SVG sparkline).
- ☐ **3.3** `[sonnet/builder]` Auth: users/plans/auth_tokens/sessions migration, magic-link login, email backend abstraction (console in dev). **Opus review: security.**
- ☐ **3.4** `[sonnet/builder]` Saved searches CRUD ("save this search" from any filtered view), plan limits enforced.
- ☐ **3.5** `[sonnet/builder]` Alerts: matcher, daily digest, instant 15-min batches, `alert_deliveries` idempotency, `plans.alert_delay`, unsubscribe. *AC:* tests for idempotency and cursor advance.
- ☐ **3.6** `[sonnet/builder]` RSS/Atom: per saved search (token) and anonymous filter URLs.
- ☐ **3.7** `[sonnet/builder]` Badges: high-salary percentile job, hidden-gem flag, "Hidden gems" view. `[haiku/grunt]` builds `data/well_known.csv` (~1,500 big brands) first.
- ☐ **3.8** `[sonnet/builder]` Removal flow: `/remove` form, domain-email verification, admin approve, block + hide within the same transaction.
- ☐ **3.9** `[haiku/grunt]` Static pages: about, privacy, `/bot` copy, removal policy; README updates.
- ☐ **3.10** `[sonnet/builder]` Deploy: Dockerfile, compose (postgres, web, worker, caddy), backups, `make deploy` for the chosen host (per Q2). 
- ☐ **3.11** `[opus/main]` **Phase 3 report**. Wait.

---

## Phase 4 — Generic fallback + quality

- ☐ **4.1** `[opus/architect]` Refine the generic-extraction design (budgets, when to escalate tiers, LLM cost cap) as a diff to ARCH.
- ☐ **4.2** `[sonnet/builder]` `jsonld` adapter: careers page + sitemap discovery → JobPosting JSON-LD → RawJob.
- ☐ **4.3** `[sonnet/builder]` `html` adapter: link-list heuristics on careers pages → detail pages → title/location/description extraction.
- ☐ **4.4** `[sonnet/builder]` Playwright fallback, opt-in per source, page budget, robots still enforced, no interaction beyond load + scroll.
- ☐ **4.5** `[sonnet/builder]` LLM extraction with `claude-haiku-5-5`, cached in `llm_extractions` by page hash, monthly $ cap, only when 4.2–4.4 fail.
- ☐ **4.6** `[haiku/grunt]` Download GeoNames dumps + build loader script; `[sonnet/builder]` full geocoder replacing the v1 table.
- ☐ **4.7** `[haiku/grunt]` Draft labeled golden sets (300 salary strings, 300 locations) from real crawled data; `[opus/main]` spot-checks 15%; `[sonnet/builder]` salary/location parser v2 against them.
- ☐ **4.8** `[opus/main]` Dedupe tuning with 200 labeled candidate pairs; adjust thresholds.
- ☐ **4.9** `[opus/main]` **Phase 4 report**.

---

## Delegation log

| Task | Subagent | Model requested | Model observed | Notes |
|---|---|---|---|---|
| 0.1–0.4 | main | opus | opus | — |

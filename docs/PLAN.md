# Daily Job Tool — Plan (lean edition)

Legend: `[model/subagent]`. ☐ = todo, ☑ = done (with "done by: <model>").
Each task = one subagent run, ends with tests passing + one commit.

## Usage-saving rules (apply to every phase)

1. **Main thread runs on Sonnet during implementation** (`/model sonnet`). Switch to Opus (`/model opus`) only for: phase-end reports, the 2.5 detection review, and design changes. When a Sonnet main thread needs a deep review, it delegates to the `architect` subagent with `model: "opus"` instead of switching.
2. **Every delegation passes `model` explicitly** (`"sonnet"` / `"haiku"` / `"opus"`).
3. **Reviews are spot-checks by default:** read the diff summary + tests, skim hot spots, no line-by-line read. **Full Opus review (via `architect`) only for:** DB schema (1.2), CrawlClient (1.3), ingest closing/dedupe (1.7). (Auth removed: personal single-user site.)
4. **Small related tasks are bundled** into one subagent run.
5. **Stop at each phase end**, as before. Each phase leaves a working product.
6. Stretch work is moved to **Deferred** (bottom). Pull items back only if usage allows.

Dependencies are in `(after …)`. Tasks without mutual dependencies run in parallel.

---

## Scope: United States jobs only (see ARCHITECTURE §0)

Tasks below are adjusted: 1.7 adds the US-eligibility filter; 2.5/2.6 drop Personio, Teamtailor, Recruitee and SmartRecruiters to Deferred (keep Workable + BambooHR); 3.1 filters by US state/metro instead of country; 4.3 uses a US-only gazetteer.

---

## Personal match profile (see ARCHITECTURE §0.2) — adjusts the plan

The product is now a **personal job-matching tool for data analyst / data engineering / commercial-analytics roles at ANY company** (pharma/health industries rank higher but nothing is restricted to them). Changes:
- **Registry scope stays broad (corrected):** US employers across all industries likely to hire data roles (tech, finance, insurance, retail, consulting, health), with the pharma/biotech/pharma-consulting/health-tech/PBM/specialty-pharmacy/healthcare-data companies seeded first and boosted. Phase 2 goal returns to 1,500–2,000 companies. Phase 1 seeds (1.9) become this list. Large pharma often sits on Workday or custom/enterprise career sites, so **All the “other ATS” work is one block, the Workday phase (tasks 2.7a–2.7d, first thing in Phase 2 after the Phase 1 report), scoped by evidence from `data/seeds_rejected.csv` and detection results.**
- **New task 1.7b** `[sonnet/builder]` (after 1.7; **architect review of the migration**): migration `0002_match` (columns in ARCH §0.2 + `companies.industry/is_staffing`), `normalize` v2 extractors (`years_min/max`, `visa_status`, `employment` hardening), `match/` package (`match(job, company, profile)`, profile loader, title-tier matcher using the profile's titles with slash-alternation expansion and seniority/synonym handling, fit score), wired into ingest + `renormalize`, golden tests: ≥60 titles → tier, ≥60 years phrases, ≥50 visa phrases (both exclude and flag, including near-miss phrases that must NOT exclude, e.g. "we do not discriminate based on visa status"), location rank, staffing, 14-day rule.
- **1.9 (seeds)** `[haiku/grunt]` now: compile ~300 US companies on Greenhouse/Lever/Ashby, about a third from the priority industries (the owner's named peers + others) and the rest broad data-employing companies, find their ATS via the detector once available; Axtria excluded; `industry` and `is_staffing` set. **Staffing agencies** (e.g. Randstad, Kforce, Robert Half-style firms) are tagged `is_staffing`, not crawled for matches.
- **Phase 3 changes:** default page = “My matches” (ARCH §0.2 ordering) with a visible “Filtered out (n)” audit tab showing reasons; no alerts or RSS (owner opens the tool daily); a “new since your last visit” highlight instead; drop hidden-gem/high-salary badges (3.2) except a simple “pay ≥ $120K stated” flag; company pages stay minimal.
- **Salary:** target base $120K+ → `pay:below_min` flag only when a stated max < $120K; unstated pay is kept.

---

## Phase 0 — Plan ✅

- ☑ **0.1** `[opus/main]` Create `.claude/agents/{architect,builder,grunt}.md`. — done by: opus
- ☑ **0.2** `[opus/main]` Probe ATS endpoints and robots.txt (ARCHITECTURE §5). — done by: opus
- ☑ **0.3** `[opus/main]` Write `docs/ARCHITECTURE.md`. — done by: opus
- ☑ **0.4** `[opus/main]` Write `docs/PLAN.md`; lean revision for usage. — done by: opus
- ☐ **0.5** You approve, or answer Q1–Q5 in ARCHITECTURE §13.

---

## Phase 1 — Skeleton + Greenhouse/Lever/Ashby → end-to-end slice

**Goal:** ~200 companies crawled, normalized, stored, and shown in a searchable feed.
**Main thread model:** Sonnet.

- ☑ **1.1** `[haiku/grunt]` *(bundles old 1.0 + 1.1)* (a) `docs/ENDPOINTS.md` from ARCH §5 (one section per ATS: URL, method, params, pagination, robots; nothing beyond §5). (b) Scaffold: README setup (`brew install uv postgresql@17`), `uv init` (py3.12), deps from ARCH §1, ruff + pytest + pytest-asyncio, Makefile (`dev|test|lint|db-up|migrate`), `.env.example`, directory tree from ARCH §2 with empty modules. *AC:* `uv run pytest` ok, `uv run ruff check` clean. **Routing check:** the main thread inspects this run's transcript to confirm it ran on Haiku, then logs the result below and reports it to you. — done by: haiku (verified in transcript)
- ☑ **1.2** `[sonnet/builder]` (after 1.1) Config, async DB session, Alembic, **initial migration = Phase 1 tables in ARCH §3**, test-DB fixture. *AC:* migrate up/down/up; test asserts tables + indexes. **Full review (architect/opus).**
- ☑ **1.3** `[sonnet/builder]` (after 1.1) `CrawlClient`: UA, robots (protego + cache), per-host token bucket + overrides, retries/backoff/Retry-After, host circuit, size cap, request accounting, `FixtureTransport` replay. Also: adapter `base.py` (ARCH §4), adapter registry, `daily_job_tool fixtures record` CLI (≤500 KB), and a no-httpx-import test. *(Bundles old 1.4.)* *AC:* respx tests for robots allow/deny/5xx-deny, 429 + Retry-After, retry exhaustion, rate-limit timing (fake clock), host circuit. **Full review (architect/opus).**
- ☑ **1.4** `[haiku/grunt]` (after 1.3) Record fixtures (3 small boards + one 404 token each for Greenhouse, Lever, Ashby; the main thread supplies tokens). Also write the data files: `titles.yaml`, `functions.yaml`, `tech_tags.yaml` (~300 tags with `requires_context`), `blocked_domains.csv`, and the Phase 1 location table. *(Bundles old 1.5 + 1.7a.)* *AC:* fixtures ≤500 KB each; YAML loads. Spot-check 30 data entries.
- ☐ **1.5** `[sonnet/builder]` (after 1.4) **All three Tier 1 adapters in one run:** Greenhouse (verify the pay-transparency detail param), Lever (US/EU region, pagination), Ashby (compensation, workplaceType). Offline tests for each: mapping, empty, 404, malformed record. *(Bundles old 1.6a–c.)*
- ☐ **1.6** `[sonnet/builder]` (after 1.2, 1.4) **Normalizer in one run:** text sanitize, title_norm, seniority, function, employment type, locations v1, remote type + regions, tech tags, salary (structured + regex v1) + annualization + FX (ECB fetch + `fx_rates`). Golden tests: ≥200 titles, ≥100 locations, ≥150 salary strings. *AC:* ≥95% title golden accuracy. *(Bundles old 1.7b–d.)*
- ☐ **1.7** `[sonnet/builder]` (after 1.5, 1.6) Ingest (**with US-only filter per ARCH §0**: non-US-eligible postings stored as id+hash only, `job_id NULL`): posting upsert + hash, detail-on-new hook, normalize → jobs, dedupe (ARCH §7), closing with `complete` + mass-disappearance guard, reopen within 14 days, crawl_runs, `daily_job_tool renormalize`. *AC:* fixture-sequence DB tests (new → unchanged → missed 1 → closed → reopened; suspicious drop not closed). **Full review (architect/opus).**
- ☐ **1.8** `[sonnet/builder]` (after 1.7) Worker (SKIP LOCKED claim, concurrency, interval/priority/jitter, breaker, `crawl --once`, `worker`) **+ registry importer** (`registry import` idempotent, blocked domains enforced; `registry verify`). *(Bundles old 1.9 + 1.10.)* *AC:* two-worker no-double-claim test; breaker test; re-import no-op.
- ☐ **1.9** `[haiku/grunt]` (after 1.8) Compile `data/seeds.csv` (~250 companies on Greenhouse/Lever/Ashby), run `registry verify`, drop failures. *AC:* ≥200 verified rows.
- ☐ **1.10** `[sonnet/builder]` (after 1.7) Web v1: FastAPI + Jinja + HTMX + Tailwind standalone CLI. Feed (keyset pagination), keyword search (FTS + trigram), job detail with apply link, `/bot`, `/api/v1/jobs`. *AC:* route tests; feed < 300 ms server time on 20k synthetic jobs.
- ☐ **1.11** `[opus/main — switch to Opus]` Live run on ~200 companies, 20-job quality sample, **Phase 1 report** that includes how much of your weekly usage Phase 1 took and a projection for the rest. Wait for go-ahead.

---

## Phase 2 — Registry growth + ATS detection + more adapters

**Goal:** 2,000+ companies, Workday support, adapter health dashboard. **Main thread:** Sonnet.

- ☐ **2.1** `[haiku/grunt]` Candidates (`yc-oss` + 2–3 public lists, licenses in `docs/SOURCES.md`) → `data/candidates.csv`, deduped by domain. **Plus** record ~40 homepage/careers fixtures for detector tests. *(Bundles old 2.1 + 2.2.)*
- ☐ **2.2** `[sonnet/builder]` (after 2.1) Detector per ARCH §9 (patterns, slug probing + identity verification, confidence) and `daily_job_tool detect`. *AC:* ≥95% on fixtures, 0 false positives on custom-page fixtures. Spot-check.
- ☐ **2.3** `[haiku/grunt]` (after 2.2) Run the detector over candidates in batches of 200 → `data/detected.csv`. Then record fixtures for 2.5–2.7 using tokens from detected.csv. *(Bundles old 2.4 + 2.7.)*
- ☐ **2.4** `[opus/main — switch to Opus]` Review 50 random detections + all probe-based hits; tune; import accepted rows.
- ☐ **2.5** `[sonnet/builder]` Adapters, run 1: Workable, BambooHR (verify first; stop if no public JSON). (Recruitee and Personio deferred: European-skewed.)
- ☐ **2.6** `[sonnet/builder]` Adapters, run 2: **cancelled** (Teamtailor, SmartRecruiters deferred/dropped under US-only scope). Re-add if US coverage on the 2,000-company set turns out thin.
- ☐ **2.7 — “Workday phase”** (owner decision: the ATS gap found while seeding goes here). First `[haiku/grunt]` **2.7.0**: tally `data/seeds_rejected.csv` + detection output by ATS (count of target companies per unsupported ATS, priority-industry companies weighted ×3) and write `docs/ATS_GAP.md`; the main thread picks the build order from that table. Then `[sonnet/builder]` runs, one per adapter or tightly related pair, each with recorded fixtures and offline tests, spot-checked (Workday gets closer attention to `complete` handling):
  - **2.7a** Workday (tenant/wdN/site parsing, pagination by 20, sitemap-first diff, detail-on-new, 0.5 req/s per tenant).
  - **2.7b** The next-most-common unsupported ATS from `ATS_GAP.md` (expected candidates: iCIMS, Oracle Recruiting Cloud, SuccessFactors, Jobvite, Taleo, Phenom/Avature custom sites; HTML/sitemap + JSON-LD where no public JSON).
  - **2.7c / 2.7d** Further ATSs only while each unlocks ≥ ~10 target companies; stop otherwise.
  - Exit criterion: ≥ 80% of seeded priority-industry companies have a working source (or are documented as custom pages for the Phase 4 generic fallback).
- ☐ **2.8** `[sonnet/builder]` Adaptive scheduling (EWMA), `not_found`×3 → broken + re-detect, **+** `daily_job_tool report` and `/admin` (failing sources, per-adapter success 24h/7d, opened/closed per day, normalizer coverage). *(Bundles old 2.11 + 2.12.)*
- ☐ **2.9** `[opus/main — switch to Opus]` Scale run to 2,000+, **Phase 2 report**. Wait.

---

## Phase 3 — Product features

**Main thread:** Sonnet.

- ☐ **3.1** `[sonnet/builder]` `SearchFilters` model + query builder for all filters, filter UI (HTMX, URL state), indexes, **plus the “new since last visit” marker** (`app_state` migration `0005`). *AC:* DB test per filter; p95 < 300 ms on 150k synthetic jobs.
- ☐ **3.2** `[sonnet/builder]` Nightly `company_daily_stats`, company list + pages with 90-day SVG sparkline **+** badges (high-salary percentile, hidden gem, "Hidden gems" view). `[haiku/grunt]` builds `data/well_known.csv` first. *(Bundles old 3.2 + 3.7.)*
- ~~**3.3** Auth~~ **Removed: personal single-user site, no accounts.** No token needed either: the app listens on 127.0.0.1 only.
- ☐ **3.4** `[sonnet/builder]` Saved searches CRUD (no users table; single owner; the app listens on 127.0.0.1 only, so no token or login at all) (RSS removed along with alerts: the tool is opened daily).
- ~~**3.5** Alerts~~ **Removed: owner uses the tool daily.** Replaced by a tiny “new since your last visit” marker: single-row `app_state(key, value)` table stores `last_visit_at`; the feed highlights jobs with `first_seen_at > last_visit_at` and shows a “N new” count; visiting the page (or a “mark all seen” button) advances it. Folded into task 3.1.
- ☐ **3.6** `[sonnet/builder]` Removal flow (`/remove` form for companies that ask, admin approve on the local `/admin` page, block + hide; keep: it's a compliance rule) **+** `[haiku/grunt]` static pages (about, privacy, `/bot`, removal policy, README).
- ☐ **3.7** `[sonnet/builder]` Local run, no hosting: `make up` starts web (bound to **127.0.0.1 only**) + worker; a `launchd` plist (`make install-agent`) to start them at login and keep the worker running; `make backup` = `pg_dump` to `~/Backups/daily_job_tool/`. Worker must tolerate the laptop sleeping (due-time claims already catch up; no burst: cap catch-up to the normal per-host rate limits).
- ☐ **3.8** `[opus/main — switch to Opus]` **Phase 3 report**. Wait.

---

## Phase 4 — Generic fallback + quality (lean)

**Main thread:** Sonnet.

- ☐ **4.1** `[sonnet/builder]` `jsonld` adapter (careers page + sitemap → JobPosting JSON-LD) **+** `html` link-list heuristic adapter. Spot-check.
- ☐ **4.2** `[haiku/grunt]` Download GeoNames + loader script; draft golden sets (200 salary strings, 200 locations) from crawled data.
- ☐ **4.3** `[sonnet/builder]` US-only gazetteer geocoder (US cities >15k + states, not worldwide GeoNames) + salary/location parser v2 against the golden sets.
- ☐ **4.4** `[opus/main — switch to Opus]` **Phase 4 report**.

---

## Deferred (stretch, cut to save usage)

- Recruitee, Personio, Teamtailor adapters (European-skewed; US-only scope). SmartRecruiters dropped (robots blocks the API).
- (Moved into the Workday phase, 2.7: iCIMS, SuccessFactors, Jobvite and others, as evidence from the seeds requires.)
- Playwright headless fallback (old 4.4)
- LLM extraction with Haiku via API (old 4.5)
- Dedupe tuning with labeled pairs (old 4.8): the exact/explicit-link dedupe from ARCH §7 stays
- Separate Opus design pass for generic extraction (old 4.1)

---

## Delegation log

| Task | Subagent | Model requested | Model observed | Notes |
|---|---|---|---|---|
| 0.1–0.4 | main | opus | opus | — |
| 1.1 | general-purpose (grunt prompt inlined; named agents not loaded because session started in parent dir) | haiku | haiku (36/36 API turns `claude-haiku-5-5`) | Main thread fixed pyproject description + pytest-asyncio warning, task ref in ENDPOINTS.md |
| 1.2 | general-purpose (builder prompt) | sonnet | sonnet (self-reported claude-sonnet-5-5) | Opus review (model self-reported opus): 2 blockers + 5 should-fix, fixed by main thread |
| 1.3 | general-purpose (builder prompt) | sonnet | sonnet | Opus review: 3 blockers (redirects bypass robots, burst after semaphore wait, no blocklist) + 8 should-fix; fixed by a 2nd Sonnet run |
| 1.4 | general-purpose (grunt prompt) | haiku | haiku (self-reported claude-haiku-5-5) | Short of target: 266/300 tech tags, 392/500 cities; no EU Lever fixture found |

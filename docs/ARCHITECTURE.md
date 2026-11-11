# Firsthand — Architecture

> Working name: **Firsthand** (jobs firsthand from employers). Visual identity: plain, dense, fast; one accent color (deep teal `#0F766E`), system font stack, no imagery. Nothing borrowed from careerhound.io besides the idea.

Status: **Draft v1, Phase 0**. Waiting for approval. Owner of this doc: main thread (Opus) / `architect` subagent.

---

## 0. Scope: United States only (owner decision)

Config `US_ONLY=true` (default). Effects:
- **Ingest filter (task 1.7):** after normalization a posting becomes a `jobs` row only if it is *US-eligible*: any location resolves to `US`; **or** it is remote with `remote_regions` containing `US`, `NORTH_AMERICA` or `WORLD`; **or** location is unknown/unparseable (kept, flagged `location_unknown`, so we never silently lose a US job). Everything else is skipped (posting row kept with `job_id NULL` and **no payload**, only id + hash, so closing logic and change detection still work cheaply).
- **Registry:** seed US-HQ companies first. Non-US-HQ companies are still allowed (many hire in the US) but get priority 8 until they show US-eligible jobs.
- **Adapter order:** US-heavy ATSs first (Greenhouse, Lever, Ashby, Workday, Workable). European-skewed ones (Personio, Teamtailor, Recruitee) move to Deferred; SmartRecruiters is dropped (also robots-blocked), which resolves Q5.
- **Normalization:** salary currency is expected to be USD, so FX is a safety net only; geocoding can use a US-only gazetteer (all US cities >15k + states) instead of worldwide GeoNames in Phase 4; filters drop the country selector in favor of US state / metro.
- Not US-only for *crawling*: we can't know a board's countries before fetching it, so boards are still fetched whole.

## 0.2 Personal match profile (owner decision)

This is a personal job-hunt tool for data analyst / data engineering / commercial-analytics roles **at any company or industry**; pharma/health industries get a ranking boost only (`industry_priority`), never a restriction. `data/profile.yaml` (gitignored; template `data/profile.example.yaml`) holds target titles in 3 tiers, hard filters, location/industry priority and the owner's skills. A pure `match(job, company, profile) -> MatchResult` runs at ingest (and on `renormalize`) and stores results on `jobs`:

| Column | Meaning |
|---|---|
| `match_tier` smallint null | 1/2/3 from title match (`target_titles` tiers, plus `generic_data_roles` regexes → tier 2 unless a tier-1 title matches), null = not a target title ("still open to other roles": kept, ranked last) |
| `match_status` text | `include` / `excluded` / `flagged` |
| `match_reasons` text[] | machine-readable, e.g. `visa:no_sponsorship`, `years:min_7`, `employment:contract`, `company:excluded`, `staffing`, `stale`, `pay:below_min` |
| `visa_status` text | `offered` / `not_available` / `not_stated` (regex phrases in profile) |
| `years_min`, `years_max` smallint null | parsed from description ("3+ years", "2-5 years of experience"); null = not stated (kept) |
| `industry_rank` smallint | 0 = priority industry (pharma/biotech, pharma consulting, health tech, PBM/health plan, specialty pharmacy, healthcare data), 1 = everything else |
| `location_rank` smallint | 0 NJ, 1 NYC, 2 US-remote, 3 other US |
| `fit_score` smallint | skills overlap − gap-skill penalty; ranks within a tier; never excludes |

Hard exclusions set `match_status='excluded'` with reasons rather than dropping rows, so a **“Filtered out” view lets the owner audit mistakes** (a wrongly excluded visa phrase is the costly failure). Employment type unknown stays included, flagged. Company columns added: `industry text`, `is_staffing bool`. Default feed = `match_status<>'excluded' AND freshness_at > now()-14d AND closed_at IS NULL`, ordered tier, location_rank, industry_rank, fit_score, freshness. “Still accepting applications” = job still open in the source (closing rules §7). Posted-within-14-days uses `freshness_at` (source `posted_at` when given; otherwise first seen, so a newly added board's undated jobs look new on day one: shown, flagged `date:first_crawl`).

## 0.1 Principles

1. **Employer-sourced only.** Every listing comes from an employer's own careers page or the employer's own ATS board. No LinkedIn, Indeed, Glassdoor, Wellfound, BuiltIn, or any other aggregator, either as a source or for "enrichment".
2. **Public, polite, and identifiable.** Only publicly posted listings. Respect robots.txt (RFC 9309). Use conservative per-host rate limits and an identifiable User-Agent. Never get around logins, CAPTCHAs, or bot walls. If a host blocks us, the source is marked `blocked` and left alone.
3. **Raw first, normalize later.** Store the raw payload per posting. Normalization is a pure, versioned function, so it can be re-run offline.
4. **A missing job is not a closed job until we're sure.** Closing only happens after *complete, successful* fetches.
5. **Boring infra.** One Postgres instance holds data, search, queue, and scheduling. Add new infra only when a number says we need it.

---

## 1. Stack (with the changes I'm proposing)

| Concern | Choice | Notes / change vs. brief |
|---|---|---|
| Language | Python 3.12, managed by **uv** | The machine has system Python 3.9 and Homebrew 3.14. uv pins 3.12 per project. |
| HTTP | `httpx` (async, HTTP/2 off by default) | |
| Parsing | `selectolax` (HTML), `lxml` + `defusedxml` (XML/RSS/sitemaps), `extruct` (JSON-LD, Phase 4) | |
| Models | Pydantic v2 | |
| robots.txt | `protego` | Supports wildcards and Crawl-delay. |
| HTML sanitizing | `nh3` | Job descriptions get rendered, so they have to be sanitized. |
| DB | **Postgres 17**, SQLAlchemy 2 Core (async, asyncpg), Alembic | Use Core, not the ORM, so the SQL stays explicit. |
| Search | Postgres FTS (`tsvector`, weighted) + `pg_trgm` | Move to Meilisearch/Typesense only if p95 search goes over 300 ms at our size or relevance complaints pile up. |
| Queue / scheduler | **Postgres only** (`SELECT … FOR UPDATE SKIP LOCKED` on `sources.next_crawl_at`) | **Change:** no Redis/arq. The crawl unit is a row with a due time, so a separate queue would just duplicate state. |
| API + UI | **FastAPI + Jinja2 + HTMX**, Tailwind via the standalone CLI (no Node) | **Change (needs your OK):** server-rendered instead of Next.js. One deploy, no Node toolchain (Node isn't installed here), SEO-friendly company pages, and plenty for a feed-plus-filters product. The JSON API (`/api/v1`) still exists, so a Next.js frontend can be added later. |
| CLI | `typer` | `firsthand crawl/detect/registry/report/fixtures …` |
| Alerts | none (removed; daily use) | “New since last visit” marker instead. |
| Geocoding | **GeoNames** offline dumps (`cities15000`, `admin1`, `countryInfo`, alt names), CC-BY | No external API calls at runtime. |
| FX | ECB daily reference rates (free XML), cached in `fx_rates` | |
| LLM (**deferred**) | Anthropic API, `claude-haiku-5-5`, results cached by page hash | Last-resort extraction for custom career pages only, with a hard monthly cost cap. Cut from the lean plan along with Playwright and the iCIMS/SuccessFactors/Jobvite adapters (see PLAN "Deferred"). The interfaces still allow them. |
| Local dev | **Homebrew Postgres 17** (Docker isn't installed) + `docker-compose.yml` kept for deploy parity | See open question Q3. |
| Deploy | **Local only (own laptop)**: Homebrew Postgres + `make up`, `launchd` agent, `pg_dump` backups. (Earlier idea, not used: one small VM (e.g. Hetzner CX22, ~US$5/mo, 4 GB RAM) running Postgres + web + worker under Docker Compose + Caddy (TLS). Nightly `pg_dump` to object storage.) | Expect 1–3 GB of data within months. |

---

## 2. Components

```
             ┌────────────────────────────────────────────────────────────┐
             │                        Postgres                            │
             │ companies · sources · postings · jobs · crawl_runs        │
             │ saved_searches · app_state · fx_rates · robots_cache│
             └───▲─────────────▲──────────────▲───────────────▲───────────┘
                 │             │              │               │
 seeds.csv ─► Registry     Crawl worker ──► Ingest ──►   Web (FastAPI)
 yc-oss     (import,      (claims due     (diff,        feed · search · company
 lists       ATS detect)   sources, rate   normalize,    pages · saved searches
                           limit, robots,  dedupe,       admin · removal
                           adapters)       close)        requests
                                                              │
```

Processes in prod: `web` (uvicorn), `worker` (crawl loop), plus `postgres`. (Local laptop run; no alerts process.)

### Repo layout
```
firsthand/
  pyproject.toml  uv.lock  Makefile  .env.example  docker-compose.yml  Dockerfile
  src/firsthand/
    config.py
    db/            schema.py (SQLAlchemy Core tables), session.py, migrations/ (alembic)
    http/          client.py (CrawlClient), ratelimit.py, robots.py
    adapters/      base.py, registry.py, greenhouse.py, lever.py, ashby.py, workable.py,
                   smartrecruiters.py, recruitee.py, teamtailor.py, personio.py, bamboohr.py,
                   workday.py, oracle.py, icims.py, successfactors.py, jobvite.py,
                   generic/ (jsonld.py, sitemap.py, html.py, browser.py, llm.py)
    detect/        patterns.py, detector.py, probe.py
    normalize/     __init__.py (normalize(raw) -> NormalizedJob, NORMALIZER_VERSION),
                   title.py, seniority.py, function.py, location.py, remote.py,
                   salary.py, fx.py, techtags.py, employment.py, text.py
    pipeline/      ingest.py, dedupe.py, closing.py
    scheduler/     worker.py, priority.py, breaker.py
    registry/      importer.py, sources_yc.py, sources_lists.py
    web/           app.py, routes/, templates/, static/, queries.py
    cli.py
  data/            seeds.csv, blocked_domains.csv, tech_tags.yaml, titles.yaml,
                   functions.yaml, geonames/ (downloaded, gitignored)
  tests/           unit/, adapters/, fixtures/<adapter>/<token>/...
  docs/            ARCHITECTURE.md, PLAN.md, ENDPOINTS.md
```

---

## 3. Data model (Postgres)

Key decision: **company ≠ source.** A company can have several boards (e.g. Greenhouse for engineering plus Workday for corporate). The unit of crawling is the **source**. The unit of display is the **job**, which can be backed by one or more **postings**, one per source where it appears.

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS citext;

CREATE TYPE company_status AS ENUM ('active','paused','removed');
CREATE TYPE source_status  AS ENUM ('active','broken','blocked','paused','removed');
CREATE TYPE seniority      AS ENUM ('intern','entry','mid','senior','staff','principal',
                                    'lead','manager','director','vp','exec','unknown');
CREATE TYPE remote_type    AS ENUM ('onsite','hybrid','remote','unknown');
CREATE TYPE emp_type       AS ENUM ('full_time','part_time','contract','temporary',
                                    'internship','apprenticeship','unknown');
CREATE TYPE crawl_outcome  AS ENUM ('ok','partial','http_error','parse_error','timeout',
                                    'robots_disallowed','circuit_open','not_found','suspicious');

-- ── Registry ──────────────────────────────────────────────────────────────
CREATE TABLE companies (
  id              bigserial PRIMARY KEY,
  name            text NOT NULL,
  slug            citext NOT NULL UNIQUE,            -- url slug for company page
  domain          citext UNIQUE,                     -- registrable domain, e.g. stripe.com
  careers_url     text,
  hq_country      char(2),                           -- ISO-3166 alpha-2
  size_bucket     text CHECK (size_bucket IN ('1-10','11-50','51-200','201-1000','1001-5000','5000+')),
  tags            text[] NOT NULL DEFAULT '{}',      -- 'ai','fintech','yc', 'yc:W21' ...
  is_well_known   boolean NOT NULL DEFAULT false,    -- used by the "hidden jobs" heuristic
  seed_source     text,                              -- 'manual','yc-oss','list:remoteintech', 'detector'
  status          company_status NOT NULL DEFAULT 'active',
  logo_url        text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sources (                                -- one crawlable board
  id                    bigserial PRIMARY KEY,
  company_id            bigint NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  adapter               text NOT NULL,               -- 'greenhouse','lever','workday','jsonld',...
  token                 text NOT NULL,               -- board slug / tenant id / URL for generic
  config                jsonb NOT NULL DEFAULT '{}', -- e.g. {"region":"eu"} lever; {"host":..,"site":..} workday
  status                source_status NOT NULL DEFAULT 'active',
  detected_by           text,                        -- 'manual','pattern:iframe','probe', ...
  detection_confidence  real,
  -- scheduling
  priority              smallint NOT NULL DEFAULT 5, -- 1 = highest
  crawl_interval        interval NOT NULL DEFAULT '24 hours',
  next_crawl_at         timestamptz NOT NULL DEFAULT now(),
  locked_until          timestamptz,                 -- lease for SKIP LOCKED claim
  -- health
  last_crawled_at       timestamptz,
  last_success_at       timestamptz,
  last_outcome          crawl_outcome,
  consecutive_failures  int NOT NULL DEFAULT 0,
  circuit_open_until    timestamptz,
  new_postings_ewma     real NOT NULL DEFAULT 0,     -- new postings/day, drives interval
  open_postings         int NOT NULL DEFAULT 0,
  created_at            timestamptz NOT NULL DEFAULT now(),
  UNIQUE (adapter, token)
);
CREATE INDEX sources_due ON sources (priority, next_crawl_at) WHERE status = 'active';

CREATE TABLE blocked_domains (                        -- removal requests + never-crawl list
  domain      citext PRIMARY KEY,
  reason      text NOT NULL,                          -- 'removal_request','aggregator','manual'
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE removal_requests (
  id             bigserial PRIMARY KEY,
  company_domain citext NOT NULL,
  requester_email citext NOT NULL,                    -- must be @company_domain to auto-verify
  message        text,
  verify_token   text NOT NULL,
  verified_at    timestamptz,
  resolved_at    timestamptz,
  resolution     text,                                -- 'removed','rejected'
  created_at     timestamptz NOT NULL DEFAULT now()
);

-- ── Crawl data ────────────────────────────────────────────────────────────
CREATE TABLE postings (                               -- one listing as seen in one source
  id               bigserial PRIMARY KEY,
  source_id        bigint NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  external_id      text NOT NULL,                     -- ATS job id / URL hash for generic
  job_id           bigint,                            -- FK set after dedupe (deferred, see below)
  url              text NOT NULL,                     -- hosted posting page
  apply_url        text,
  payload          jsonb NOT NULL,                    -- latest raw payload (RawJob.payload)
  payload_hash     bytea NOT NULL,                    -- sha256 of canonicalized payload
  detail_fetched_at timestamptz,                      -- for list+detail APIs
  first_seen_at    timestamptz NOT NULL DEFAULT now(),
  last_seen_at     timestamptz NOT NULL DEFAULT now(),
  missed_crawls    smallint NOT NULL DEFAULT 0,       -- consecutive complete crawls without it
  closed_at        timestamptz,
  UNIQUE (source_id, external_id)
);
CREATE INDEX postings_open ON postings (source_id) WHERE closed_at IS NULL;

CREATE TABLE jobs (                                   -- canonical, deduplicated, normalized
  id                   bigserial PRIMARY KEY,
  company_id           bigint NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  primary_posting_id   bigint REFERENCES postings(id),
  dedupe_key           text NOT NULL,                 -- see §6
  title                text NOT NULL,
  title_norm           text NOT NULL,
  seniority            seniority NOT NULL DEFAULT 'unknown',
  function             text NOT NULL DEFAULT 'other', -- controlled vocab, data/functions.yaml
  department_raw       text,
  employment_type      emp_type NOT NULL DEFAULT 'unknown',
  remote_type          remote_type NOT NULL DEFAULT 'unknown',
  remote_regions       text[] NOT NULL DEFAULT '{}',  -- ISO countries or 'EU','EMEA','AMER','APAC','WORLD'
  locations            jsonb NOT NULL DEFAULT '[]',   -- [{raw, city, admin1, country, lat, lon, geonameid}]
  countries            char(2)[] NOT NULL DEFAULT '{}',
  salary_min           numeric, salary_max numeric,
  salary_currency      char(3),
  salary_period        text CHECK (salary_period IN ('hour','day','week','month','year')),
  salary_min_usd       int,  salary_max_usd int,      -- annualized USD
  salary_source        text,                          -- 'structured','parsed', null
  tech_tags            text[] NOT NULL DEFAULT '{}',
  description_html     text,                          -- sanitized
  description_text     text,
  posted_at            timestamptz,                   -- as claimed by source
  first_seen_at        timestamptz NOT NULL,
  last_seen_at         timestamptz NOT NULL,
  freshness_at         timestamptz NOT NULL,          -- LEAST(COALESCE(posted_at, first_seen_at), first_seen_at)
  closed_at            timestamptz,
  apply_url            text NOT NULL,                 -- canonical, employer-owned (§5.9)
  is_hidden_gem        boolean NOT NULL DEFAULT false,
  is_high_salary       boolean NOT NULL DEFAULT false,
  normalizer_version   smallint NOT NULL,
  search_tsv           tsvector GENERATED ALWAYS AS (
                         setweight(to_tsvector('english', coalesce(title,'')), 'A') ||
                         setweight(to_tsvector('english', coalesce(array_to_string(tech_tags,' '),'')), 'B') ||
                         setweight(to_tsvector('english', coalesce(department_raw,'')), 'C') ||
                         setweight(to_tsvector('english', left(coalesce(description_text,''), 20000)), 'D')
                       ) STORED,
  UNIQUE (company_id, dedupe_key)
);
ALTER TABLE postings ADD CONSTRAINT postings_job_fk FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE SET NULL;
CREATE INDEX jobs_feed      ON jobs (freshness_at DESC, id DESC) WHERE closed_at IS NULL;
CREATE INDEX jobs_tsv       ON jobs USING gin (search_tsv);
CREATE INDEX jobs_title_trg ON jobs USING gin (title gin_trgm_ops);
CREATE INDEX jobs_tags      ON jobs USING gin (tech_tags);
CREATE INDEX jobs_countries ON jobs USING gin (countries);
CREATE INDEX jobs_salary    ON jobs (salary_max_usd) WHERE closed_at IS NULL;
CREATE INDEX jobs_company   ON jobs (company_id) WHERE closed_at IS NULL;
-- company name search is done by joining companies with a trigram index on companies.name

CREATE TABLE crawl_runs (
  id            bigserial PRIMARY KEY,
  source_id     bigint NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  adapter       text NOT NULL,
  started_at    timestamptz NOT NULL,
  finished_at   timestamptz,
  outcome       crawl_outcome,
  complete      boolean,                              -- FetchResult.complete
  http_requests int NOT NULL DEFAULT 0,
  http_status   int,
  jobs_seen     int, jobs_new int, jobs_updated int, jobs_closed int,
  error         text,
  duration_ms   int
);
CREATE INDEX crawl_runs_recent ON crawl_runs (started_at DESC);

CREATE TABLE company_daily_stats (                    -- hiring trend, computed nightly
  company_id bigint REFERENCES companies(id) ON DELETE CASCADE,
  day date, open_jobs int, opened int, closed int,
  PRIMARY KEY (company_id, day)
);

CREATE TABLE robots_cache (host text PRIMARY KEY, body text, status int, fetched_at timestamptz NOT NULL);
CREATE TABLE fx_rates (day date, currency char(3), usd_per_unit numeric NOT NULL, PRIMARY KEY (day, currency));
CREATE TABLE llm_extractions (page_hash bytea PRIMARY KEY, model text, result jsonb, cost_usd numeric, created_at timestamptz DEFAULT now());

-- ── Product (Phase 3) — SINGLE-USER: no users, plans, sessions or login ──
CREATE TABLE saved_searches (
  id bigserial PRIMARY KEY,
  name text NOT NULL, query jsonb NOT NULL,           -- serialized SearchFilters (same model as the UI)
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE app_state (key text PRIMARY KEY, value text NOT NULL);   -- e.g. last_visit_at for the "new since last visit" marker
```

Notes:
- `postings.payload` keeps only the **latest** payload, not history, to keep storage bounded. If a version history turns out to be useful, add `posting_versions` later.
- `NORMALIZER_VERSION` bump → `firsthand renormalize` reprocesses every open posting from `payload` without any HTTP.
- Monetization / multi-user is out of scope (personal site). Adding `users` and `plans` tables later would not require changes to the crawl/jobs tables.

---

## 4. Adapter interface

```python
# src/firsthand/adapters/base.py
class SalaryRaw(BaseModel):
    min: float | None = None; max: float | None = None
    currency: str | None = None; period: str | None = None   # as given
    text: str | None = None                                   # free-text comp string if any

class RawJob(BaseModel):
    external_id: str
    title: str
    url: str                          # hosted posting page (employer site or employer's ATS board)
    apply_url: str | None = None
    locations_raw: list[str] = []
    workplace_raw: str | None = None  # structured remote/hybrid hint if the ATS provides it
    department_raw: str | None = None
    employment_type_raw: str | None = None
    description_html: str | None = None
    posted_at: datetime | None = None
    updated_at: datetime | None = None
    salary: SalaryRaw | None = None
    needs_detail: bool = False        # list endpoint lacked description; fetch_detail required
    payload: dict                     # complete original record, JSON-serializable

class FetchResult(BaseModel):
    jobs: list[RawJob]
    complete: bool                    # True only if the whole board was enumerated (pagination finished, no errors)
    board_name: str | None = None     # used by detection to verify identity
    warnings: list[str] = []

class SourceRef(BaseModel):
    id: int | None; adapter: str; token: str; config: dict = {}

class Adapter(ABC):
    name: ClassVar[str]               # 'greenhouse'
    tier: ClassVar[int]               # 1, 2, 3
    hosts: ClassVar[list[str]]        # for rate-limit grouping and docs

    @abstractmethod
    async def fetch_jobs(self, src: SourceRef, http: CrawlClient) -> FetchResult: ...

    async def fetch_detail(self, src: SourceRef, job: RawJob, http: CrawlClient) -> RawJob:
        return job                    # override for list+detail APIs (Workday, SmartRecruiters, BambooHR)

    @classmethod
    def match_url(cls, url: str) -> tuple[str, dict] | None:
        """If url belongs to this ATS, return (token, config). Pure; used by the detector."""
```

Rules for adapters:
- **Never import httpx.** All I/O goes through `CrawlClient` (`get_json`, `get_text`, `post_json`). It enforces robots, the rate limit, the UA, retries, and request accounting, and in tests it replays fixtures.
- Pure mapping only (API → `RawJob`). No normalization inside an adapter.
- Return `complete=False` if any page failed, pagination was capped, or the response looked truncated.
- Detail fetches happen only for postings that are **new or whose list-level hash changed** (the ingest layer decides). This keeps Workday and SmartRecruiters affordable.
- Every adapter has fixtures in `tests/fixtures/<adapter>/<token>/` (≤ 500 KB per board, so choose small boards) recorded with `firsthand fixtures record`, plus offline tests: mapping, pagination, empty board, 404 board, malformed record skipped with a warning.

### CrawlClient
- UA: `FirsthandBot/0.1 (+https://<domain>/bot; <contact email>)` (see Q4).
- robots: fetched per host, cached 24 h in `robots_cache`. A 4xx robots response means allow; 5xx or unreachable means **disallow** for that run (RFC 9309). Honor `Crawl-delay`.
- Rate limit: a token bucket per host, **default 1 req/s, burst 2**. Overrides per ATS API host: greenhouse/lever/ashby 2 req/s, Workday tenants 0.5 req/s. Global concurrency ≤ 16.
- Retries: 3 attempts on timeouts, 429, and 5xx; exponential backoff (2s, 8s, 30s) + jitter; honor `Retry-After` (cap 10 min; longer means fail the run).
- Host circuit: if >50% of the last 20 requests to a host failed, pause that host for 30 minutes. This protects us during ATS outages and keeps us from looking abusive.
- Timeouts: connect 10 s, read 30 s. Response size cap 25 MB.

---

## 5. Source endpoints (probed 2026‑11‑11 from this machine)

| ATS | Endpoint | Probe | robots | Notes |
|---|---|---|---|---|
| Greenhouse | `GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` | ✅ 200 | `/embed/` disallowed only | Single call returns the whole board. Pay ranges via `GET …/jobs/{id}?pay_transparency=true` (verify in task 1.7). Board name: `GET /v1/boards/{token}`. |
| Lever | `GET https://api.lever.co/v0/postings/{site}?mode=json` (EU: `api.eu.lever.co`) | ✅ 200 | allow all | Has `salaryRange`, `workplaceType`. Supports `skip`/`limit` pagination. |
| Ashby | `GET https://api.ashbyhq.com/posting-api/job-board/{name}?includeCompensation=true` | ✅ 200 | none | Has `isRemote`, `workplaceType`, compensation tiers. |
| Workable | `GET https://apply.workable.com/api/v1/widget/accounts/{sub}`; v3 `POST …/api/v3/accounts/{sub}/jobs` (paginated by `token`) | ✅ 200 (valid slug) | allow all | Use the v1 widget for the list. Details via v2/v3 job endpoint (verify). |
| SmartRecruiters | `GET https://api.smartrecruiters.com/v1/companies/{id}/postings` | ✅ 200 | ⛔ **`Disallow: /` for `*`** (only LinkedInBot allowed) | **Not usable under our robots policy.** Fallback: public pages `jobs.smartrecruiters.com/{id}/…` (no valid robots.txt → allowed) via JSON-LD. Decision Q5. |
| Recruitee | `GET https://{co}.recruitee.com/api/offers/` | ✅ 200 | none | Full descriptions, salary fields on some boards. |
| Teamtailor | `GET https://{sub}.teamtailor.com/jobs.rss` (public RSS) | ✅ 200 | `/jobs/internal/` disallowed | The JSON API needs a customer key, so use RSS plus per-job JSON-LD on the job page. Custom domains are common, so detect them via `teamtailor` script/meta. |
| Personio | `GET https://{co}.jobs.personio.de/xml` (also `.jobs.personio.com`) | ✅ 200 | allow all | XML with descriptions; `?language=en`. |
| BambooHR | `GET https://{co}.bamboohr.com/careers/list` (+ `/careers/{id}/detail`) | ⚠️ unverified (test slug redirected) | — | Confirm with a real customer slug in task 2.11. |
| Workday | `POST https://{tenant}.{wdN}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs` body `{"appliedFacets":{},"limit":20,"offset":0,"searchText":""}`; detail `GET …/wday/cxs/{tenant}/{site}{externalPath}` | ✅ 200 | per tenant; career-site path allowed, `/wday/cxs` not disallowed | Paginate by 20. Each tenant site also exposes `/{site}/siteMap.xml`, which is the cheap way to diff before paging. Check robots per tenant at runtime. Big tenants take 100+ requests, so crawl every 24 h at 0.5 req/s. |
| Oracle Recruiting Cloud | `GET https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList&finder=findReqs;siteNumber={site},limit=…,offset=…` | ✅ 200 | none | Detail: `recruitingCEJobRequisitionDetails`. |
| iCIMS | `https://careers-{co}.icims.com/jobs/search?pr={n}&in_iframe=1` (HTML) + job pages with JSON-LD | not probed | per tenant | Tier 2, HTML. Stretch goal. |
| SuccessFactors | Career-site sitemap (`/sitemap.xml`) + JSON-LD on job pages | not probed | per tenant | Stretch goal. |
| Jobvite | `https://jobs.jobvite.com/{co}/jobs` (HTML) + job pages | not probed | per tenant | Stretch goal. |

The full probe log and per-endpoint request/response notes go in `docs/ENDPOINTS.md` (task 1.0).

---

## 6. Normalization rules (`normalize(raw: RawJob, company, source) -> NormalizedJob`)

A pure function with no I/O. FX rates and GeoNames are loaded once and passed in through a `NormContext`.

1. **Text.** Sanitize `description_html` with nh3 (allow p, ul, ol, li, strong, em, a[href], h2–h4, br). `description_text` = text with whitespace collapsed. Decode HTML entities in titles.
2. **Title → `title_norm`.** Lowercase. Strip requisition codes (`(R-12345)`, `#123`, `- Req 4567`), location suffixes that duplicate `locations_raw` (`- Remote, US`, `(London)`), gender markers (`(m/w/d)`, `(f/m/x)`), and emoji. Expand abbreviations from `data/titles.yaml` (`sr`→senior, `jr`→junior, `swe`→software engineer, `eng`→engineer, `mgr`→manager). Collapse whitespace.
3. **Seniority** (first match wins, on `title_norm`): intern/co-op/working student → `intern`; `vp|vice president` → `vp`; `chief|cto|ceo|cfo|head of`* → `exec` (*`head of` → `director` when the company size is ≤200, a judgment call I'm encoding as a rule); `director` → `director`; `principal|distinguished|fellow` → `principal`; `staff` → `staff`; `lead|tech lead` → `lead`; `manager` (excluding "product manager", "program manager", "account manager", "project manager") → `manager`; `senior|sr|iii|level 3` → `senior`; `junior|jr|graduate|new grad|entry|associate (only for eng)|i\b|level 1` → `entry`; else `mid` if the title is a recognized IC role, else `unknown`. Roman/level suffixes come from the table in `titles.yaml`. Golden tests: ≥200 titles.
4. **Function** (controlled vocabulary): engineering, data, ml_ai, design, product, research, security, it, devrel, sales, marketing, customer_success, support, operations, finance, legal, people, recruiting, other. Order: title keywords first (`data scientist` → data, `ml engineer` → ml_ai, `product designer` → design, `product manager` → product), then the ATS department as a hint, then `other`.
5. **Locations.** Split each `locations_raw` on ` | ; / •` and ` or `, and also on `,` when both sides are known places. Each fragment: strip remote words → match `City, Admin1, Country`, `City, ST` (US/CA/AU state codes), `City, Country`, country name or alias, or region tokens (`EMEA`, `Europe`, `EU`, `LATAM`, `APAC`, `North America`, `Worldwide/Anywhere`) against GeoNames (cities15000 + alt names, ranked by population when ambiguous, biased toward `company.hq_country`). Output `{raw, city, admin1, country, lat, lon, geonameid}`. Unknown fragments stay as `{raw}`. `countries` = distinct countries. Phase 1 uses a small built-in table (countries, US/CA states, top 500 cities). Full GeoNames comes in Phase 4.
6. **Remote type.** Structured hint first (Ashby `workplaceType`/`isRemote`, Lever `workplaceType`, Workday `remoteType`, Personio/Recruitee fields). Otherwise use the title/location tokens (`remote`, `hybrid`, `in office|onsite|on-site`), then the first 2 KB of the description for explicit phrases ("this is a fully remote role", "X days a week in office"). `remote_regions`: from location fragments of remote jobs (`Remote - US` → `['US']`, `Remote (EMEA)` → `['EMEA']`, plain `Remote` → `['WORLD']` only if the description doesn't restrict it. Otherwise use the countries from the other locations or `unknown`).
7. **Salary.** (a) Structured fields (Ashby compensation, Lever `salaryRange`, Greenhouse pay ranges, Recruitee/Personio salary) set `salary_source='structured'`. (b) Otherwise parse the description: look in pay-transparency blocks first ("salary range", "base pay", "compensation", "OTE"), with regex for `[$€£₹¥]|USD|EUR|GBP|CAD|INR…` + number (`120k`, `120,000`, `1.2L`/lakh, `€60.000`) + optional range separator (`-`, `–`, `to`) + optional period (`/hr`, `per hour`, `annually`, `per month`, `p.a.`) → `salary_source='parsed'`. Ignore equity, bonus-only, and "up to X% bonus" numbers. Annualize: hour×2080, day×260, week×52, month×12. Convert to USD with the latest `fx_rates`. Sanity window: 10k ≤ annual USD ≤ 2M, and max/min ≤ 4, otherwise drop. Golden tests: ≥150 strings.
8. **Employment type.** Map ATS enums, then title tokens (`contract`, `contractor`, `freelance`, `part-time`, `temporary`, `fixed-term`, `intern`, `apprentice`), defaulting to `full_time` only when the ATS states it. Otherwise `unknown`.
9. **Canonical apply URL.** Order: (1) the URL on the **employer's own domain** if the source is the employer's site, or if the ATS payload links to the employer site (e.g. Greenhouse `absolute_url` on a custom domain with `gh_jid`); (2) the ATS-hosted posting page (`boards.greenhouse.io/x/jobs/1`, `jobs.lever.co/x/uuid`, `jobs.ashbyhq.com/x/uuid`, …), which counts as the employer's own board. Strip tracking params (`utm_*`, `source`, `gh_src`, `lever-source`, `ref`). Never point to anything on the aggregator blocklist.
10. **Tech tags.** Dictionary in `data/tech_tags.yaml` (~300 canonical tags with aliases and optional `requires_context` regex). Word-boundary match on title + description text. Ambiguous tokens (`Go`, `R`, `C`, `Swift`, `Rust`, `Spark`, `Elixir`, `Dart`) count only in tech context (`Golang`, `Go (programming|language|developer)`, or a comma/slash list next to other recognized languages). Cap at 15 tags, ordered by first occurrence.
11. **Posted date / freshness.** `posted_at` comes from the source (`updated_at` is never used as `posted_at`). `freshness_at = LEAST(COALESCE(posted_at, first_seen_at), first_seen_at)`, so a board that "bumps" dates can't float old jobs back to the top. On the very first crawl of a new source, jobs without `posted_at` are hidden from the default "new" feed for that day, so a newly added big board doesn't flood the feed.
12. **Badges.** `is_high_salary`: `salary_max_usd` ≥ the 85th percentile for (function, seniority) across open jobs, recomputed nightly. `is_hidden_gem`: the company is *not* `is_well_known` (a curated list of ~1,500 big brands, `data/well_known.csv`), `size_bucket` ≤ 1000 or unknown, and the source is a custom page or a Tier 1/2 board. This is a heuristic and the UI labels it that way. We **do not** check whether the job is on LinkedIn/Indeed, because that would mean scraping them.

---

## 7. Dedupe strategy

Dedupe runs **only within a company** (cross-company duplicates are not a real case for employer-sourced data, except agency postings, which we don't ingest).

1. **Posting identity:** `(source_id, external_id)`. Stable ATS ids make this trivial. For generic pages, `external_id = sha1(canonical_url_without_query)`.
2. **Job identity across sources of the same company:** `dedupe_key = sha1(title_norm_core | location_key)` where
   - `title_norm_core` = `title_norm` with seniority-neutral noise removed (but seniority words are **kept**: "Senior SWE" ≠ "SWE"),
   - `location_key` = sorted distinct `geonameid`s, or country codes if there's no city, or `remote:<regions>`, or `?` if unknown.
3. **Explicit links beat fuzzy keys.** If a generic/custom posting's URL or apply link contains an ATS id we already know (`gh_jid=`, `lever.co/{site}/{uuid}`, `ashbyhq.com/{x}/{uuid}`, Workday `externalPath`), attach it to that ATS posting's job directly.
4. **Multiple open postings with the same key in the *same* source** (e.g. 5 identical "Software Engineer, Remote US" reqs) are **not** merged. Real duplicate reqs mean more headcount. Their keys get suffixed `#n` by external-id order.
5. **Primary posting** selection: the ATS adapter with structured data > Tier 2 > generic. Ties go to the oldest `first_seen_at`.
6. A job is **closed** when all its postings are closed. `UNIQUE (company_id, dedupe_key)` spans closed jobs too, so a new posting whose key matches a *closed* job always **revives that job** (clears `closed_at`, keeps `first_seen_at`); the 14-day window below applies to postings only. If a posting reappears within 14 days, the posting and job reopen, keeping `first_seen_at`.
7. Fuzzy tuning (Phase 4): sample 200 candidate pairs with trigram title similarity > 0.8 and the same location, then hand-label them to set thresholds.

### Closing rules (pipeline/closing.py)
- After a crawl with `FetchResult.complete=True` **and** outcome `ok`: every open posting of the source *not* in the result gets `missed_crawls += 1`, and is closed when `missed_crawls >= 2`. Seen postings reset to 0.
- **Mass-disappearance guard:** if a complete crawl returns 0 jobs when the source had ≥5 open, or loses >60% of open postings when ≥20 were open, mark the run `suspicious`, don't increment misses, and re-crawl in 2 h. If a second consecutive crawl confirms it, apply the rule normally. This catches ATS migrations and broken boards without hiding thousands of live jobs.
- Partial or failed crawls never touch `missed_crawls`.

---

## 8. Crawl orchestration

- **Claim loop** (`worker.py`): every 5 s, `UPDATE sources SET locked_until = now()+'15 min' WHERE id IN (SELECT id FROM sources WHERE status='active' AND next_crawl_at <= now() AND (circuit_open_until IS NULL OR circuit_open_until < now()) AND (locked_until IS NULL OR locked_until < now()) ORDER BY priority, next_crawl_at LIMIT :n FOR UPDATE SKIP LOCKED) RETURNING …`. Run claimed sources concurrently under the per-host limits.
- **Interval:** `crawl_interval = clamp(24h / (1 + new_postings_ewma), 4h, 24h)`. `new_postings_ewma` is the EWMA (α=0.3) of new postings per day. `next_crawl_at = now() + interval ± 10% jitter`. Every source gets crawled at least daily.
- **Priority:** 1 = new or user-requested, 3 = active boards (EWMA ≥ 1/day), 5 = default, 8 = quiet boards (0 postings for 30 days).
- **Circuit breaker (per source):** `consecutive_failures ≥ 5` → `circuit_open_until = now() + min(2^(failures-5) × 6h, 7d)`. `not_found` 3 times in a row → `status='broken'`, then queue ATS re-detection for the company (Phase 2).
- **Robots disallow** → outcome `robots_disallowed`, `status='blocked'`, and it gets reviewed in the admin report.
- **Capacity check:** 3,000 sources, mostly one request each at about 2 req/s per big ATS host, is about 25 min/day per host. Workday is the expensive one (≈ jobs/20 list calls + detail calls for new jobs only). It runs comfortably on one worker.
- **Reports** (`firsthand report` and `/admin`, behind basic auth): sources by status, the top failing sources, per-adapter success rate (24h/7d) from `crawl_runs`, jobs opened/closed per day, normalizer coverage (% with salary, location resolved, seniority ≠ unknown).

---

## 9. Registry and ATS detection

**Seeding sources** (all public, and stored with `seed_source`):
- `data/seeds.csv` (manual, editable by you): `name,domain,careers_url,adapter,token,hq_country,tags`.
- The YC directory via the community-maintained static dataset `yc-oss/api` (public JSON on GitHub Pages), which gives name, website, batch, tags, team size, and status. We don't hit YC's own Algolia search.
- Public "awesome" lists (e.g. remoteintech/remote-jobs company list). Licenses are checked per list and recorded in `docs/SOURCES.md`.
- No aggregators. `blocked_domains` is pre-seeded with aggregator domains (linkedin.com, indeed.com, glassdoor.com, ziprecruiter.com, wellfound.com, builtin.com, …) so they can never be added as companies or sources.

**Detector** (`detect/detector.py`, given a domain or careers URL → `[DetectionHit(adapter, token, config, confidence, evidence)]`):
1. Candidate pages: the given careers URL, or `https://{domain}`, then the first anchor whose text or href matches `careers|jobs|join us|work with us|open roles|hiring`, then the fallbacks `/careers`, `/jobs`, `careers.{domain}`, `jobs.{domain}`. At most 6 fetches per company, all under robots/rate limits.
2. For each page: the redirect chain (final URL) → `Adapter.match_url`; `<iframe src>`, `<script src>`, `<a href>`, and `<link>` URLs → `match_url`; inline text patterns (`Greenhouse.jobBoard`, `grnhse_app`, `ashby_embed`, `lever-jobs-container`, `teamtailor` meta, `personio`, `myworkdayjobs.com`, `icims.com`, `jobvite`, `successfactors`, `oraclecloud.com/hcmUI/CandidateExperience`).
3. If none hit: **slug probing** for Tier 1 JSON APIs (Greenhouse, Lever, Ashby, Workable, Recruitee) with slugs derived from the name or domain (`acme`, `acmeinc`, `acme-ai`, …, at most 4 slugs × 5 ATSs). A hit is accepted only if **identity verifies**: the board name fuzzy-matches the company name (≥0.85), *or* the postings' URLs or description mention the company domain. Otherwise it's discarded. This prevents "slug `apple` on Lever belongs to someone else."
4. If there's still nothing but the page has `JobPosting` JSON-LD → `adapter='jsonld'` (Phase 4).
5. Confidence: redirect/iframe = 0.95, script/link = 0.9, verified probe = 0.8, unverified = rejected. Below 0.8 → `sources.status='paused'` pending review.

---

## 10. Web product

- Routes: `/` feed · `/jobs/{id}-{slug}` job detail (summary + "Apply on employer site") · `/companies` · `/companies/{slug}` (open roles + 90-day trend sparkline from `company_daily_stats`) · `/search?…` (same as the feed with filters) · `/saved` · `/remove` (removal request) · `/bot` (crawler info page the UA links to) · `/admin` · `/api/v1/jobs|companies`.
- **One `SearchFilters` Pydantic model** is the single source of truth for querystring ↔ SQL ↔ saved-search JSON. Fields: `q, title, company, countries[], remote (any|remote|hybrid|onsite), salary_min_usd, seniority[], function[], tech[], posted_within_days, has_salary, hidden_gems, high_salary`.
- Pagination: keyset on `(freshness_at, id)`.
- Search ranking: when `q` is present, sort by `ts_rank_cd(search_tsv, websearch_to_tsquery(q)) * freshness_decay`. Otherwise sort by freshness.
- **Personal site, single owner: no accounts, sign-up, or login.** Browsing is open. **Runs only on the owner's laptop**: the web app binds to `127.0.0.1`, so no auth or token is needed (never bind `0.0.0.0`). If this is ever hosted publicly, add auth first (a `users`/`plans` layer won't touch the crawl/jobs tables).

### No alerts
The owner uses the tool daily, so there are no email/desktop alerts and no RSS. The feed highlights jobs first seen since `app_state.last_visit_at`.

---

## 11. Compliance checklist (enforced in code, not just policy)

- [x] `CrawlClient` is the only network path for crawling, and it enforces robots, the UA, and rate limits. A test asserts that adapters don't import httpx.
- [x] Aggregator domains are hard-blocked in `blocked_domains` and in `canonical_apply_url`.
- [x] No login flows, no cookies carried between requests, no CAPTCHA handling. A 401/403 or a challenge page (Cloudflare/Akamai markers) becomes outcome `http_error` + `status='blocked'` after 3 occurrences. We don't retry around it.
- [x] Personal data: recruiter names/emails in payloads are **not** extracted into `jobs`. `description_html` can still contain "contact jane@…", which we render as published by the employer, the same as on their page. Phase 4: optionally mask emails in descriptions.
- [x] Removal: `/remove` → email verification from an address on the company domain (or a manual admin approval) → company `status='removed'`, sources `removed`, jobs hidden, domain added to `blocked_domains`. Honored within 24 h, and re-seeding can't re-add the company.
- [x] Every job page links to the employer to apply. We never host applications.

---

## 12. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| ATS terms of service restrict automated use of the public endpoints (e.g. SmartRecruiters' robots, possibly others' ToS) | Legal/ethical exposure, source loss | robots enforced; ToS review per ATS logged in `docs/ENDPOINTS.md` before enabling an adapter; easy per-adapter kill switch (`ADAPTERS_DISABLED`). Not legal advice — worth a lawyer look before public launch. |
| Workday/iCIMS tenants rate-limit or block | Gaps in big-company coverage | 0.5 req/s per tenant, sitemap-first diffing, detail-on-new only, host circuit breaker. |
| ATS migration (company moves Lever → Ashby) | Old source 404s, jobs falsely "closed" | Mass-disappearance guard; `not_found`×3 → re-detection. |
| False-positive ATS detection via slug probing | Wrong company's jobs shown | Identity verification required; confidence < 0.8 paused for review. |
| Salary/location parse errors | Bad filters, trust loss | Structured fields first; sanity windows; golden test sets; coverage metrics in report. |
| Normalization changes break history | Re-crawl cost | Raw payloads stored; `NORMALIZER_VERSION` + offline renormalize. |
| DB growth on a small VM | Disk pressure | Latest payload only; closed jobs' `description_html` purged after 180 days; ~1–3 GB expected at 3k companies. |
| Subagent model routing doesn't work as configured | Cost | Explicit `model` param on every delegation; verified after the first delegation (task 1.1); fallback is manual `/model` switching. |

---

## 13. Open questions (defaults chosen; tell me to change any)

- **Q1 Frontend:** HTMX server-rendered (default) vs. Next.js.
- **Q2 Hosting/budget:** one ~$5–10/mo VM (default) vs. Fly/Render + managed Postgres (~$25–40/mo).
- **Q3 Local DB:** Homebrew Postgres 17 (default) vs. installing OrbStack/Docker.
- **Q4 Identity:** product domain + a bot contact email for the UA  Until then: `FirsthandBot/0.1 (+https://example.invalid/bot)` in dev, never your personal email.
- **Q5 Geography and SmartRecruiters:** worldwide, English-language postings first (default). SmartRecruiters via public pages + JSON-LD only (default) vs. dropping it entirely.

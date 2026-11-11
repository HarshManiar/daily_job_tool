"""Phase 1 schema (ARCHITECTURE.md section 3).

Revision ID: 0001_initial
Revises:
"""

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

UPGRADE = [
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    "CREATE EXTENSION IF NOT EXISTS citext",
    "CREATE TYPE company_status AS ENUM ('active','paused','removed')",
    "CREATE TYPE source_status  AS ENUM ('active','broken','blocked','paused','removed')",
    """CREATE TYPE seniority AS ENUM ('intern','entry','mid','senior','staff','principal',
                                    'lead','manager','director','vp','exec','unknown')""",
    "CREATE TYPE remote_type AS ENUM ('onsite','hybrid','remote','unknown')",
    """CREATE TYPE emp_type AS ENUM ('full_time','part_time','contract','temporary',
                                    'internship','apprenticeship','unknown')""",
    """CREATE TYPE crawl_outcome AS ENUM ('ok','partial','http_error','parse_error','timeout',
                                    'robots_disallowed','circuit_open','not_found','suspicious')""",
    # array_to_string is only STABLE; generated columns need IMMUTABLE.
    """CREATE FUNCTION firsthand_immutable_array_to_string(text[], text) RETURNS text
       LANGUAGE sql IMMUTABLE AS $$ SELECT array_to_string($1, $2) $$""",
    """CREATE TABLE companies (
  id              bigserial PRIMARY KEY,
  name            text NOT NULL,
  slug            citext NOT NULL UNIQUE,
  domain          citext UNIQUE,
  careers_url     text,
  hq_country      char(2),
  size_bucket     text CHECK (size_bucket IN
                    ('1-10','11-50','51-200','201-1000','1001-5000','5000+')),
  tags            text[] NOT NULL DEFAULT '{}',
  is_well_known   boolean NOT NULL DEFAULT false,
  seed_source     text,
  status          company_status NOT NULL DEFAULT 'active',
  logo_url        text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
)""",
    "CREATE INDEX companies_name_trg ON companies USING gin (name gin_trgm_ops)",
    """CREATE TABLE sources (
  id                    bigserial PRIMARY KEY,
  company_id            bigint NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  adapter               text NOT NULL,
  token                 text NOT NULL,
  config                jsonb NOT NULL DEFAULT '{}',
  status                source_status NOT NULL DEFAULT 'active',
  detected_by           text,
  detection_confidence  real,
  priority              smallint NOT NULL DEFAULT 5,
  crawl_interval        interval NOT NULL DEFAULT '24 hours',
  next_crawl_at         timestamptz NOT NULL DEFAULT now(),
  locked_until          timestamptz,
  last_crawled_at       timestamptz,
  last_success_at       timestamptz,
  last_outcome          crawl_outcome,
  consecutive_failures  int NOT NULL DEFAULT 0,
  circuit_open_until    timestamptz,
  new_postings_ewma     real NOT NULL DEFAULT 0,
  open_postings         int NOT NULL DEFAULT 0,
  created_at            timestamptz NOT NULL DEFAULT now(),
  UNIQUE (adapter, token)
)""",
    "CREATE INDEX sources_due ON sources (priority, next_crawl_at) WHERE status = 'active'",
    """CREATE TABLE blocked_domains (
  domain      citext PRIMARY KEY,
  reason      text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
)""",
    """CREATE TABLE removal_requests (
  id             bigserial PRIMARY KEY,
  company_domain citext NOT NULL,
  requester_email citext NOT NULL,
  message        text,
  verify_token   text NOT NULL,
  verified_at    timestamptz,
  resolved_at    timestamptz,
  resolution     text,
  created_at     timestamptz NOT NULL DEFAULT now()
)""",
    """CREATE TABLE postings (
  id               bigserial PRIMARY KEY,
  source_id        bigint NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  external_id      text NOT NULL,
  job_id           bigint,
  url              text NOT NULL,
  apply_url        text,
  payload          jsonb NOT NULL,
  payload_hash     bytea NOT NULL,
  detail_fetched_at timestamptz,
  first_seen_at    timestamptz NOT NULL DEFAULT now(),
  last_seen_at     timestamptz NOT NULL DEFAULT now(),
  missed_crawls    smallint NOT NULL DEFAULT 0,
  closed_at        timestamptz,
  UNIQUE (source_id, external_id)
)""",
    "CREATE INDEX postings_open ON postings (source_id) WHERE closed_at IS NULL",
    "CREATE INDEX postings_job ON postings (job_id) WHERE job_id IS NOT NULL",
    """CREATE TABLE jobs (
  id                   bigserial PRIMARY KEY,
  company_id           bigint NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  primary_posting_id   bigint REFERENCES postings(id) ON DELETE SET NULL,
  dedupe_key           text NOT NULL,
  title                text NOT NULL,
  title_norm           text NOT NULL,
  seniority            seniority NOT NULL DEFAULT 'unknown',
  function             text NOT NULL DEFAULT 'other',
  department_raw       text,
  employment_type      emp_type NOT NULL DEFAULT 'unknown',
  remote_type          remote_type NOT NULL DEFAULT 'unknown',
  remote_regions       text[] NOT NULL DEFAULT '{}',
  locations            jsonb NOT NULL DEFAULT '[]',
  countries            char(2)[] NOT NULL DEFAULT '{}',
  salary_min           numeric, salary_max numeric,
  salary_currency      char(3),
  salary_period        text CHECK (salary_period IN ('hour','day','week','month','year')),
  salary_min_usd       int,  salary_max_usd int,
  salary_source        text,
  tech_tags            text[] NOT NULL DEFAULT '{}',
  description_html     text,
  description_text     text,
  posted_at            timestamptz,
  first_seen_at        timestamptz NOT NULL,
  last_seen_at         timestamptz NOT NULL,
  freshness_at         timestamptz NOT NULL,
  closed_at            timestamptz,
  apply_url            text NOT NULL,
  is_hidden_gem        boolean NOT NULL DEFAULT false,
  is_high_salary       boolean NOT NULL DEFAULT false,
  normalizer_version   smallint NOT NULL,
  search_tsv           tsvector GENERATED ALWAYS AS (
                         setweight(to_tsvector('english'::regconfig, coalesce(title,'')), 'A') ||
                         setweight(to_tsvector('english'::regconfig,
                           coalesce(firsthand_immutable_array_to_string(tech_tags,' '),'')), 'B') ||
                         setweight(to_tsvector('english'::regconfig,
                           coalesce(department_raw,'')), 'C') ||
                         setweight(to_tsvector('english'::regconfig,
                           left(coalesce(description_text,''), 20000)), 'D')
                       ) STORED,
  UNIQUE (company_id, dedupe_key)
)""",
    """ALTER TABLE postings ADD CONSTRAINT postings_job_fk
       FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE SET NULL""",
    "CREATE INDEX jobs_feed      ON jobs (freshness_at DESC, id DESC) WHERE closed_at IS NULL",
    "CREATE INDEX jobs_tsv       ON jobs USING gin (search_tsv)",
    "CREATE INDEX jobs_title_trg ON jobs USING gin (title gin_trgm_ops)",
    "CREATE INDEX jobs_tags      ON jobs USING gin (tech_tags)",
    "CREATE INDEX jobs_countries ON jobs USING gin (countries)",
    "CREATE INDEX jobs_salary    ON jobs (salary_max_usd) WHERE closed_at IS NULL",
    "CREATE INDEX jobs_company   ON jobs (company_id) WHERE closed_at IS NULL",
    """CREATE TABLE crawl_runs (
  id            bigserial PRIMARY KEY,
  source_id     bigint NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  adapter       text NOT NULL,
  started_at    timestamptz NOT NULL,
  finished_at   timestamptz,
  outcome       crawl_outcome,
  complete      boolean,
  http_requests int NOT NULL DEFAULT 0,
  http_status   int,
  jobs_seen     int, jobs_new int, jobs_updated int, jobs_closed int,
  error         text,
  duration_ms   int
)""",
    "CREATE INDEX crawl_runs_recent ON crawl_runs (started_at DESC)",
    "CREATE INDEX crawl_runs_source ON crawl_runs (source_id, started_at DESC)",
    """CREATE TABLE company_daily_stats (
  company_id bigint REFERENCES companies(id) ON DELETE CASCADE,
  day date, open_jobs int, opened int, closed int,
  PRIMARY KEY (company_id, day)
)""",
    """CREATE TABLE robots_cache (
  host text PRIMARY KEY, body text, status int, fetched_at timestamptz NOT NULL
)""",
    """CREATE TABLE fx_rates (
  day date, currency char(3), usd_per_unit numeric NOT NULL, PRIMARY KEY (day, currency)
)""",
]

DOWNGRADE = [
    "ALTER TABLE postings DROP CONSTRAINT postings_job_fk",
    "DROP TABLE fx_rates",
    "DROP TABLE robots_cache",
    "DROP TABLE company_daily_stats",
    "DROP TABLE crawl_runs",
    "DROP TABLE jobs",
    "DROP TABLE postings",
    "DROP TABLE removal_requests",
    "DROP TABLE blocked_domains",
    "DROP TABLE sources",
    "DROP TABLE companies",
    "DROP FUNCTION firsthand_immutable_array_to_string(text[], text)",
    "DROP TYPE crawl_outcome",
    "DROP TYPE emp_type",
    "DROP TYPE remote_type",
    "DROP TYPE seniority",
    "DROP TYPE source_status",
    "DROP TYPE company_status",
]


def upgrade() -> None:
    for stmt in UPGRADE:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWNGRADE:
        op.execute(stmt)

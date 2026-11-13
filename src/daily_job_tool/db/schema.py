"""SQLAlchemy Core table definitions for query building.

The Alembic migration is the source of truth for DDL; keep this in sync by hand.
"""

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Interval,
    LargeBinary,
    MetaData,
    Numeric,
    SmallInteger,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, ENUM, JSONB, REAL, TSVECTOR
from sqlalchemy.types import CHAR

metadata = MetaData()


def _enum(name: str, *values: str) -> ENUM:
    return ENUM(*values, name=name, create_type=False)


company_status = _enum("company_status", "active", "paused", "removed")
source_status = _enum("source_status", "active", "broken", "blocked", "paused", "removed")
seniority = _enum(
    "seniority",
    *("intern entry mid senior staff principal lead manager director vp exec unknown".split()),
)
remote_type = _enum("remote_type", "onsite", "hybrid", "remote", "unknown")
emp_type = _enum(
    "emp_type",
    "full_time", "part_time", "contract", "temporary", "internship", "apprenticeship", "unknown",
)  # fmt: skip
crawl_outcome = _enum(
    "crawl_outcome",
    "ok", "partial", "http_error", "parse_error", "timeout",
    "robots_disallowed", "circuit_open", "not_found", "suspicious",
)  # fmt: skip

_now = func.now()
_empty_arr = text("'{}'")


def _ts(name: str, **kw) -> Column:
    return Column(name, DateTime(timezone=True), **kw)


companies = Table(
    "companies",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("name", Text, nullable=False),
    Column("slug", CITEXT, nullable=False, unique=True),
    Column("domain", CITEXT, unique=True),
    Column("careers_url", Text),
    Column("hq_country", CHAR(2)),
    Column("size_bucket", Text),
    Column("tags", ARRAY(Text), nullable=False, server_default=_empty_arr),
    Column("is_well_known", Boolean, nullable=False, server_default=text("false")),
    Column("seed_source", Text),
    Column("status", company_status, nullable=False, server_default="active"),
    Column("logo_url", Text),
    Column("industry", Text),
    Column("is_staffing", Boolean, nullable=False, server_default=text("false")),
    Column("exclude_reason", Text),  # NULL = not excluded
    _ts("created_at", nullable=False, server_default=_now),
    _ts("updated_at", nullable=False, server_default=_now),
    CheckConstraint("size_bucket IN ('1-10','11-50','51-200','201-1000','1001-5000','5000+')"),
)

sources = Table(
    "sources",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column(
        "company_id", BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    ),
    Column("adapter", Text, nullable=False),
    Column("token", Text, nullable=False),
    Column("config", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("status", source_status, nullable=False, server_default="active"),
    Column("detected_by", Text),
    Column("detection_confidence", REAL),
    Column("priority", SmallInteger, nullable=False, server_default=text("5")),
    Column("crawl_interval", Interval, nullable=False, server_default=text("'24 hours'")),
    _ts("next_crawl_at", nullable=False, server_default=_now),
    _ts("locked_until"),
    _ts("last_crawled_at"),
    _ts("last_success_at"),
    Column("last_outcome", crawl_outcome),
    Column("consecutive_failures", Integer, nullable=False, server_default=text("0")),
    _ts("circuit_open_until"),
    Column("new_postings_ewma", REAL, nullable=False, server_default=text("0")),
    Column("open_postings", Integer, nullable=False, server_default=text("0")),
    _ts("created_at", nullable=False, server_default=_now),
    UniqueConstraint("adapter", "token"),
)

blocked_domains = Table(
    "blocked_domains",
    metadata,
    Column("domain", CITEXT, primary_key=True),
    Column("reason", Text, nullable=False),
    _ts("created_at", nullable=False, server_default=_now),
)

removal_requests = Table(
    "removal_requests",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("company_domain", CITEXT, nullable=False),
    Column("requester_email", CITEXT, nullable=False),
    Column("message", Text),
    Column("verify_token", Text, nullable=False),
    _ts("verified_at"),
    _ts("resolved_at"),
    Column("resolution", Text),
    _ts("created_at", nullable=False, server_default=_now),
)

postings = Table(
    "postings",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("source_id", BigInteger, ForeignKey("sources.id", ondelete="CASCADE"), nullable=False),
    Column("external_id", Text, nullable=False),
    Column(
        "job_id",
        BigInteger,
        ForeignKey("jobs.id", ondelete="SET NULL", name="postings_job_fk", use_alter=True),
    ),
    Column("url", Text, nullable=False),
    Column("apply_url", Text),
    Column("payload", JSONB, nullable=False),
    Column("payload_hash", LargeBinary, nullable=False),
    _ts("detail_fetched_at"),
    _ts("first_seen_at", nullable=False, server_default=_now),
    _ts("last_seen_at", nullable=False, server_default=_now),
    Column("missed_crawls", SmallInteger, nullable=False, server_default=text("0")),
    _ts("closed_at"),
    UniqueConstraint("source_id", "external_id"),
)

jobs = Table(
    "jobs",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column(
        "company_id", BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    ),
    Column("primary_posting_id", BigInteger, ForeignKey("postings.id", ondelete="SET NULL")),
    Column("dedupe_key", Text, nullable=False),
    Column("title", Text, nullable=False),
    Column("title_norm", Text, nullable=False),
    Column("seniority", seniority, nullable=False, server_default="unknown"),
    Column("function", Text, nullable=False, server_default="other"),
    Column("department_raw", Text),
    Column("employment_type", emp_type, nullable=False, server_default="unknown"),
    Column("remote_type", remote_type, nullable=False, server_default="unknown"),
    Column("remote_regions", ARRAY(Text), nullable=False, server_default=_empty_arr),
    Column("locations", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("countries", ARRAY(CHAR(2)), nullable=False, server_default=_empty_arr),
    Column("salary_min", Numeric),
    Column("salary_max", Numeric),
    Column("salary_currency", CHAR(3)),
    Column("salary_period", Text),
    Column("salary_min_usd", Integer),
    Column("salary_max_usd", Integer),
    Column("salary_source", Text),
    Column("tech_tags", ARRAY(Text), nullable=False, server_default=_empty_arr),
    Column("description_html", Text),
    Column("description_text", Text),
    _ts("posted_at"),
    _ts("first_seen_at", nullable=False),
    _ts("last_seen_at", nullable=False),
    _ts("freshness_at", nullable=False),
    _ts("closed_at"),
    Column("apply_url", Text, nullable=False),
    Column("is_hidden_gem", Boolean, nullable=False, server_default=text("false")),
    Column("is_high_salary", Boolean, nullable=False, server_default=text("false")),
    Column("normalizer_version", SmallInteger, nullable=False),
    Column("match_tier", SmallInteger),
    Column("match_status", Text, nullable=False, server_default="include"),
    Column("match_reasons", ARRAY(Text), nullable=False, server_default=_empty_arr),
    Column("visa_status", Text, nullable=False, server_default="not_stated"),
    Column("visa_evidence", Text),
    Column("years_min", SmallInteger),
    Column("years_max", SmallInteger),
    Column("industry_rank", SmallInteger, nullable=False, server_default="1"),
    Column("location_rank", SmallInteger, nullable=False, server_default="3"),
    Column("fit_score", SmallInteger, nullable=False, server_default="0"),
    Column("norm_flags", ARRAY(Text), nullable=False, server_default=_empty_arr),
    # Generated by Postgres (see migration); never insert/update. Expression omitted here.
    # NOTE: placeholder expression; the migration is the real DDL. Never use metadata.create_all().
    Column("search_tsv", TSVECTOR, Computed("''::tsvector", persisted=True)),
    UniqueConstraint("company_id", "dedupe_key"),
    CheckConstraint("salary_period IN ('hour','day','week','month','year')"),
)

crawl_runs = Table(
    "crawl_runs",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("source_id", BigInteger, ForeignKey("sources.id", ondelete="CASCADE"), nullable=False),
    Column("adapter", Text, nullable=False),
    _ts("started_at", nullable=False),
    _ts("finished_at"),
    Column("outcome", crawl_outcome),
    Column("complete", Boolean),
    Column("http_requests", Integer, nullable=False, server_default=text("0")),
    Column("http_status", Integer),
    Column("jobs_seen", Integer),
    Column("jobs_new", Integer),
    Column("jobs_updated", Integer),
    Column("jobs_closed", Integer),
    Column("error", Text),
    Column("duration_ms", Integer),
)

company_daily_stats = Table(
    "company_daily_stats",
    metadata,
    Column(
        "company_id", BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True
    ),
    Column("day", Date, primary_key=True),
    Column("open_jobs", Integer),
    Column("opened", Integer),
    Column("closed", Integer),
)

robots_cache = Table(
    "robots_cache",
    metadata,
    Column("host", Text, primary_key=True),
    Column("body", Text),
    Column("status", Integer),
    _ts("fetched_at", nullable=False),
)

fx_rates = Table(
    "fx_rates",
    metadata,
    Column("day", Date, primary_key=True),
    Column("currency", CHAR(3), primary_key=True),
    Column("usd_per_unit", Numeric, nullable=False),
)

app_state = Table(
    "app_state",
    metadata,
    Column("key", Text, primary_key=True),
    Column("value", Text, nullable=False),
)

job_state = Table(
    "job_state",
    metadata,
    Column("job_id", BigInteger, ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True),
    _ts("viewed_at"),
    _ts("dismissed_at"),
)

saved_searches = Table(
    "saved_searches",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("name", Text, nullable=False),
    Column("query", JSONB, nullable=False),
    _ts("created_at", nullable=False, server_default=_now),
)

"""Feed query construction (the one place), plus detail/health queries.

All SQL is parameterized; user-controlled enums are whitelisted by pydantic Literals.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

PAGE_SIZE = 25
MAX_COMPANIES = 20
PLACE_RANK = {"nj": 0, "nyc": 1, "remote": 2, "other": 3}

FEED_COLS = """
    j.id, j.title, j.company_id, co.name AS company_name, co.slug AS company_slug,
    co.industry AS company_industry, j.locations, j.remote_type, j.posted_at, j.freshness_at,
    j.first_seen_at, j.salary_min_usd, j.salary_max_usd, j.match_tier, j.match_status,
    j.match_reasons, j.visa_status, j.visa_evidence, j.years_min, j.years_max, j.fit_score,
    j.apply_url, j.location_rank, j.industry_rank, j.last_seen_at, j.salary_min, j.salary_max,
    j.salary_period, j.employment_type, {SCORE} AS job_score,
    (SELECT s.viewed_at FROM job_state s WHERE s.job_id = j.id) AS viewed_at,
    (SELECT s.dismissed_at FROM job_state s WHERE s.job_id = j.id) AS dismissed_at
"""
# Fit score out of 10, from columns the match engine already stores: title tier (0-4),
# skills overlap (0-2), years required vs 2-6 (0-2), location priority (0-1), industry (0-1).
SCORE_SQL = """LEAST(10, (
    CASE j.match_tier WHEN 1 THEN 4 WHEN 2 THEN 3 WHEN 3 THEN 2 ELSE 0 END
  + LEAST(2, GREATEST(0, j.fit_score) / 2.0)
  + CASE WHEN j.years_min IS NULL THEN 1 WHEN j.years_min >= 7 THEN 0
         WHEN j.years_min BETWEEN 2 AND 6 THEN 2 ELSE 1 END
  + CASE j.location_rank WHEN 0 THEN 1 WHEN 1 THEN 0.75 WHEN 2 THEN 0.5 ELSE 0 END
  + CASE WHEN j.industry_rank = 0 THEN 1 ELSE 0 END))::numeric(4,1)"""
FEED_COLS = FEED_COLS.replace("{SCORE}", SCORE_SQL)
FEED_FROM = "jobs j JOIN companies co ON co.id = j.company_id"


SENIORITY = (
    "intern entry mid senior staff principal lead manager director vp exec unknown"
).split()
_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9+#.\-_/ ]{0,39}$")
_FUNC_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_LIST_FIELDS = ("seniority", "tags", "companies")


class FeedFilters(BaseModel):
    """Querystring <-> SQL source of truth for the feed and the saved searches.

    Unknown fields are ignored (so old saved searches keep loading). Enums are whitelisted by
    Literals / allowed-value checks; everything reaches SQL as a bind parameter.
    """

    q: str = Field(default="", max_length=200)
    company: str = Field(default="", max_length=100)
    tier: Literal["any", "1", "2", "3", "targets"] = "any"
    place: Literal["any", "nj", "nyc", "remote", "other"] = "any"
    remote: Literal["any", "remote", "hybrid", "onsite"] = "any"
    industry_only: bool = False
    visa_only: bool = False
    min_pay: int | None = Field(default=None, ge=0, le=10_000_000)
    max_pay: int | None = Field(default=None, ge=0, le=10_000_000)
    pay_unknown: Literal["include", "exclude"] = "include"
    hide_no_pay: bool = False
    max_years: int | None = Field(default=None, ge=0, le=60)
    seniority: list[str] = Field(default_factory=list, max_length=12)
    function: str = Field(default="", max_length=32)
    tags: list[str] = Field(default_factory=list, max_length=10)
    emp: Literal["any", "full_time", "full_time_unknown"] = "any"
    clean_only: bool = False
    days: Literal["h6", "h12", "1", "2", "7", "14", "30", "all"] = "14"
    sponsorship: Literal["1", "0"] = "1"  # "1" (default): hide jobs that say no sponsorship
    experience: Literal["any", "0-2", "3-5", "5+"] = "any"
    only_stated_years: bool = False
    verified: bool = False
    companies: list[int] = Field(default_factory=list, max_length=MAX_COMPANIES)
    show_dismissed: bool = False
    posted24: bool = False
    posted48: bool = False
    sort: Literal["best", "score", "newest"] = "best"
    reason: str = Field(default="", max_length=64)  # /filtered only
    offset: int = Field(default=0, ge=0, le=1_000_000)
    limit: int = Field(default=PAGE_SIZE, ge=1, le=200)

    @field_validator("q", "reason", "company", "function", mode="before")
    @classmethod
    def _strip(cls, v: Any) -> Any:
        return v.strip() if isinstance(v, str) else (v or "")

    @field_validator("function")
    @classmethod
    def _func(cls, v: str) -> str:
        v = v.lower()
        if v and not _FUNC_RE.match(v):
            raise ValueError("bad function")
        return v

    @field_validator("min_pay", "max_pay", "max_years", mode="before")
    @classmethod
    def _blank_int(cls, v: Any) -> Any:
        return None if v == "" else v

    @field_validator(
        "industry_only", "visa_only", "hide_no_pay", "clean_only", "posted24", "posted48",
        "only_stated_years", "verified", "show_dismissed",
        mode="before",
    )  # fmt: skip
    @classmethod
    def _bool(cls, v: Any) -> Any:
        return False if v in (None, "") else v

    @field_validator("companies", mode="before")
    @classmethod
    def _co_ids(cls, v: Any) -> Any:
        if v in (None, ""):
            return []
        items = [v] if not isinstance(v, list | tuple) else list(v)
        out: list[int] = []
        for item in items:
            for part in str(item).split(","):
                part = part.strip()
                if part:
                    n = int(part)
                    if n not in out and 0 < n < 2**62:
                        out.append(n)
        return out

    @field_validator("seniority", "tags", mode="before")
    @classmethod
    def _listify(cls, v: Any) -> Any:
        if v in (None, ""):
            return []
        return [v] if isinstance(v, str) else v

    @field_validator("seniority")
    @classmethod
    def _sen(cls, v: list[str]) -> list[str]:
        out = [x.strip().lower() for x in v if x and x.strip()]
        bad = [x for x in out if x not in SENIORITY]
        if bad:
            raise ValueError(f"unknown seniority {bad[0]!r}")
        return sorted(set(out))

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for item in v:  # a text box may carry "python, sql"
            for t in item.split(","):
                t = t.strip().lower()
                if t:
                    if not _TAG_RE.match(t):
                        raise ValueError(f"bad tag {t!r}")
                    if t not in out:
                        out.append(t)
        return out

    @property
    def window_hours(self) -> int | None:
        """24/48 h boxes override the days dropdown; both checked behaves as 48 h."""
        if self.posted48:
            return 48
        if self.posted24:
            return 24
        return {"h6": 6, "h12": 12, "1": 24, "2": 48}.get(self.days)

    @property
    def hide_no_sponsor(self) -> bool:
        return self.sponsorship == "1"

    def non_default(self) -> dict[str, Any]:
        """Only the fields that differ from defaults (JSON-able); no paging, no /filtered reason."""
        d = self.model_dump(mode="json", exclude={"limit", "offset", "reason", "tier"})
        return {k: v for k, v in d.items() if v != type(self).model_fields[k].get_default(
            call_default_factory=True
        )}  # fmt: skip

    @classmethod
    def from_pairs(cls, pairs: list[tuple[str, str]]) -> FeedFilters:
        d: dict[str, Any] = {}
        for k, v in pairs:
            if k in _LIST_FIELDS:
                d.setdefault(k, []).append(v)
            else:
                d[k] = v
        return cls.model_validate(d)

    @classmethod
    def from_qs(cls, qs: str) -> FeedFilters:
        from urllib.parse import parse_qsl

        return cls.from_pairs(parse_qsl(qs, keep_blank_values=True))

    def query_string(self, **over: Any) -> str:
        from urllib.parse import urlencode

        d = self.non_default()
        for k in ("reason",):
            if getattr(self, k):
                d[k] = getattr(self, k)
        if self.offset:
            d["offset"] = self.offset
        d.update(over)
        pairs: list[tuple[str, Any]] = []
        for k, v in d.items():
            if v in (None, "", False, []) or (k == "offset" and v == 0):
                continue
            if isinstance(v, list):
                pairs.extend((k, x) for x in v)
            else:
                pairs.append((k, "1" if v is True else v))
        return urlencode(pairs)


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


BEST_ORDER = (
    "j.match_tier NULLS LAST, j.location_rank, j.industry_rank, j.fit_score DESC, "
    "j.freshness_at DESC, j.id DESC"
)


# Compliance: jobs of companies that asked to be removed are never shown anywhere.
NOT_REMOVED = (
    "NOT EXISTS (SELECT 1 FROM companies rc WHERE rc.id = j.company_id AND rc.status = 'removed')"
)


# Hard-exclusion reasons other than the visa one: a job excluded ONLY for "says no sponsorship"
# is brought back when the sponsorship toggle is off; anything else stays excluded.
_OTHER_EXCLUSION = (
    "NOT EXISTS (SELECT 1 FROM unnest(j.match_reasons) r WHERE r IN "
    "('employment:contract', 'company:excluded', 'company:staffing') "
    "OR r LIKE 'years:min\\_%')"
)
NOT_DISMISSED = (
    "NOT EXISTS (SELECT 1 FROM job_state s WHERE s.job_id = j.id AND s.dismissed_at IS NOT NULL)"
)
IS_DISMISSED = (
    "EXISTS (SELECT 1 FROM job_state s WHERE s.job_id = j.id AND s.dismissed_at IS NOT NULL)"
)


def build_feed(f: FeedFilters, *, excluded: bool = False) -> tuple[str, str, dict[str, Any]]:
    """Return (where_sql, order_sql, params). Only fixed SQL fragments are concatenated.

    Dismissed state is an (anti-)semi-join on job_state, so `FROM jobs j` is enough."""
    where = ["j.closed_at IS NULL", NOT_REMOVED]
    p: dict[str, Any] = {}
    if excluded:
        where.append("j.match_status = 'excluded'")
    elif f.hide_no_sponsor:
        where.append("j.match_status <> 'excluded'")
        where.append("j.visa_status <> 'not_available'")
    else:
        where.append(
            "(j.match_status <> 'excluded' OR "
            f"(j.visa_status = 'not_available' AND {_OTHER_EXCLUSION}))"
        )
    where.append(IS_DISMISSED if f.show_dismissed else NOT_DISMISSED)
    hours = f.window_hours
    if hours is not None:
        where.append("j.freshness_at > now() - make_interval(hours => :hours)")
        p["hours"] = hours
    elif f.days != "all":
        where.append("j.freshness_at > now() - make_interval(days => :days)")
        p["days"] = int(f.days)
    if f.tier in ("1", "2", "3"):
        where.append("j.match_tier = :tier")
        p["tier"] = int(f.tier)
    elif f.tier == "targets":
        where.append("j.match_tier IS NOT NULL")
    if f.place != "any":
        where.append("j.location_rank = :lrank")
        p["lrank"] = PLACE_RANK[f.place]
    if f.remote != "any":
        where.append("j.remote_type = CAST(:remote AS remote_type)")
        p["remote"] = f.remote
    if f.industry_only:
        where.append("j.industry_rank = 0")
    if f.visa_only:
        where.append("j.visa_status = 'offered'")
    if f.companies:
        where.append("j.company_id = ANY(CAST(:cids AS bigint[]))")
        p["cids"] = f.companies
    if f.verified:
        where.append("j.last_seen_at > now() - interval '48 hours'")
    stated = "(j.years_min IS NOT NULL OR j.years_max IS NOT NULL)"
    unk = "" if f.only_stated_years else "j.years_min IS NULL OR "
    if f.experience == "0-2":
        where.append(f"({unk}j.years_min <= 2)")
    elif f.experience == "3-5":
        where.append(f"({unk}(j.years_min BETWEEN 3 AND 4))")
    elif f.experience == "5+":
        where.append(f"({unk}j.years_min >= 5)")
    elif f.only_stated_years:
        where.append(stated)
    if f.company:
        where.append("j.company_id IN (SELECT c.id FROM companies c WHERE c.name ILIKE :co)")
        p["co"] = f"%{_like_escape(f.company)}%"
    if f.min_pay is not None:
        cond = "coalesce(j.salary_max_usd, j.salary_min_usd) >= :min_pay"
        if f.pay_unknown == "include" and not f.hide_no_pay:
            cond = f"({cond} OR (j.salary_max_usd IS NULL AND j.salary_min_usd IS NULL))"
        where.append(cond)
        p["min_pay"] = f.min_pay
    if f.max_pay is not None:
        cond = "coalesce(j.salary_min_usd, j.salary_max_usd) <= :max_pay"
        if f.pay_unknown == "include" and not f.hide_no_pay:
            cond = f"({cond} OR (j.salary_max_usd IS NULL AND j.salary_min_usd IS NULL))"
        where.append(cond)
        p["max_pay"] = f.max_pay
    if f.hide_no_pay:
        where.append("(j.salary_max_usd IS NOT NULL OR j.salary_min_usd IS NOT NULL)")
    if f.max_years is not None:
        where.append("(j.years_min IS NULL OR j.years_min <= :max_years)")
        p["max_years"] = f.max_years
    if f.seniority:
        where.append("j.seniority::text = ANY(CAST(:sen AS text[]))")
        p["sen"] = f.seniority
    if f.function:
        where.append("j.function = :func")
        p["func"] = f.function
    if f.tags:
        where.append("j.tech_tags && CAST(:tags AS text[])")
        p["tags"] = f.tags
    if f.emp == "full_time":
        where.append("j.employment_type = 'full_time'")
    elif f.emp == "full_time_unknown":
        where.append("j.employment_type IN ('full_time', 'unknown')")
    if f.clean_only and not excluded:
        where.append("j.match_status = 'include'")
    if f.reason:
        where.append("j.match_reasons @> ARRAY[:reason]::text[]")
        p["reason"] = f.reason
    if f.sort == "best":
        order = BEST_ORDER
    elif f.sort == "score":
        order = f"{SCORE_SQL} DESC, j.freshness_at DESC, j.id DESC"
    else:
        order = "j.freshness_at DESC, j.id DESC"
    if f.q:
        where.append(
            "(j.search_tsv @@ websearch_to_tsquery('english', :q) "
            "OR j.title ILIKE :qlike ESCAPE '\\')"
        )
        p["q"] = f.q
        p["qlike"] = f"%{_like_escape(f.q)}%"
        order = "ts_rank_cd(j.search_tsv, websearch_to_tsquery('english', :q)) DESC, " + order
    return " AND ".join(where), order, p


async def fetch_feed(
    conn: AsyncConnection, f: FeedFilters, *, excluded: bool = False, with_total: bool = True
) -> tuple[list[dict[str, Any]], bool, int | None, Any]:
    """Return (rows, has_more, total, now)."""
    where, order, p = build_feed(f, excluded=excluded)
    sql = (
        f"SELECT {FEED_COLS}, now() AS _now FROM {FEED_FROM} "
        f"WHERE {where} ORDER BY {order} LIMIT :lim OFFSET :off"
    )
    res = await conn.execute(text(sql), {**p, "lim": f.limit + 1, "off": f.offset})
    rows = [dict(r) for r in res.mappings().all()]
    has_more = len(rows) > f.limit
    rows = rows[: f.limit]
    total = None
    if with_total:
        total = (
            await conn.execute(
                text(f"SELECT count(*) FROM jobs j WHERE {where}"),
                p,
            )
        ).scalar_one()
    now = rows[0]["_now"] if rows else (await conn.execute(text("SELECT now()"))).scalar_one()
    return rows, has_more, total, now


async def posted_counts(
    conn: AsyncConnection, f: FeedFilters, *, excluded: bool = False
) -> dict[str, int]:
    """Counts for the 'Posted last 24/48 hours' boxes: all other filters apply, the days
    dropdown and the two boxes themselves do not."""
    base = f.model_copy(update={"days": "all", "posted24": False, "posted48": False})
    where, _, p = build_feed(base, excluded=excluded)
    row = (
        await conn.execute(
            text(
                "SELECT count(*) FILTER (WHERE j.freshness_at > now() - interval '24 hours')"
                " AS h24,"
                " count(*) FILTER (WHERE j.freshness_at > now() - interval '48 hours') AS h48 "
                f"FROM jobs j WHERE {where}"
            ),
            p,
        )
    ).one()
    return {"h24": row.h24, "h48": row.h48}


async def match_and_new_counts(
    conn: AsyncConnection, f: FeedFilters, since: Any
) -> tuple[int, int]:
    """(matches, matches first seen after `since`); since=None -> 0 new."""
    where, _, p = build_feed(f)
    row = (
        await conn.execute(
            text(
                "SELECT count(*) AS n, count(*) FILTER (WHERE j.first_seen_at > "
                f"CAST(:since AS timestamptz)) AS new FROM jobs j "
                f"WHERE {where}"
            ),
            {**p, "since": since},
        )
    ).one()
    return row.n, row.new


async def company_trend(conn: AsyncConnection, company_id: int, weeks: int = 13) -> list[dict]:
    """Jobs opened (first_seen_at) vs closed (closed_at) per ISO week, last `weeks` weeks."""
    res = await conn.execute(
        text(
            "SELECT w.wk::date AS week,"
            " (SELECT count(*) FROM jobs j WHERE j.company_id = :c AND j.first_seen_at >= w.wk"
            "   AND j.first_seen_at < w.wk + interval '1 week') AS opened,"
            " (SELECT count(*) FROM jobs j WHERE j.company_id = :c AND j.closed_at >= w.wk"
            "   AND j.closed_at < w.wk + interval '1 week') AS closed "
            "FROM generate_series(date_trunc('week', now()) - make_interval(weeks => :w - 1),"
            " date_trunc('week', now()), interval '1 week') AS w(wk) ORDER BY w.wk"
        ),
        {"c": company_id, "w": weeks},
    )
    return [dict(r) for r in res.mappings()]


async def company_tier_counts(conn: AsyncConnection, company_id: int) -> dict[str, int]:
    """Open (non-excluded) roles by match tier; key 'other' = not a target title."""
    res = await conn.execute(
        text(
            "SELECT match_tier, count(*) AS n FROM jobs WHERE company_id = :c "
            "AND closed_at IS NULL AND match_status <> 'excluded' GROUP BY 1"
        ),
        {"c": company_id},
    )
    out = {"1": 0, "2": 0, "3": 0, "other": 0}
    for r in res:
        out[str(r.match_tier) if r.match_tier else "other"] = r.n
    out["open"] = sum(out.values())
    return out


async def function_options(conn: AsyncConnection) -> list[str]:
    res = await conn.execute(
        text("SELECT function FROM jobs WHERE closed_at IS NULL GROUP BY 1 ORDER BY count(*) DESC")
    )
    return [r[0] for r in res if _FUNC_RE.match(r[0] or "")]


async def reason_counts(conn: AsyncConnection, f: FeedFilters) -> list[tuple[str, int]]:
    f2 = f.model_copy(update={"reason": ""})
    where, _, p = build_feed(f2, excluded=True)
    res = await conn.execute(
        text(
            f"SELECT r, count(*) AS n FROM jobs j, "
            f"unnest(j.match_reasons) r WHERE {where} "
            "AND (r IN ('visa:not_available','employment:contract','company:excluded',"
            "'company:staffing') OR r LIKE 'years:min\\_%') GROUP BY r ORDER BY n DESC, r"
        ),
        p,
    )
    return [(r.r, r.n) for r in res]


JOB_DETAIL = """
SELECT j.*, {SCORE} AS job_score,
       (SELECT s.viewed_at FROM job_state s WHERE s.job_id = j.id) AS viewed_at,
       (SELECT s.dismissed_at FROM job_state s WHERE s.job_id = j.id) AS dismissed_at,
       co.name AS company_name, co.slug AS company_slug, co.industry AS company_industry,
       co.domain AS company_domain, p.url AS posting_url, s.adapter AS source_adapter,
       s.token AS source_token
FROM jobs j JOIN companies co ON co.id = j.company_id
LEFT JOIN postings p ON p.id = j.primary_posting_id
LEFT JOIN sources s ON s.id = p.source_id
WHERE j.id = :id AND co.status <> 'removed'
"""
JOB_DETAIL = JOB_DETAIL.replace("{SCORE}", SCORE_SQL)


async def fetch_job(conn: AsyncConnection, job_id: int) -> dict[str, Any] | None:
    row = (await conn.execute(text(JOB_DETAIL), {"id": job_id})).mappings().first()
    if row is None:
        return None
    d = dict(row)
    d["_now"] = (await conn.execute(text("SELECT now()"))).scalar_one()
    return d


FETCH_COMPANY = "SELECT * FROM companies WHERE slug = :s AND status <> 'removed'"


async def fetch_company(conn: AsyncConnection, slug: str) -> dict[str, Any] | None:
    row = (
        (
            await conn.execute(
                text("SELECT * FROM companies WHERE slug = :s AND status <> 'removed'"), {"s": slug}
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def company_jobs(
    conn: AsyncConnection, company_id: int, *, excluded: bool
) -> tuple[list[dict[str, Any]], Any]:
    op = "=" if excluded else "<>"
    sql = (
        f"SELECT {FEED_COLS}, now() AS _now FROM {FEED_FROM} "
        f"WHERE j.company_id = :c AND j.closed_at IS NULL AND co.status <> 'removed' "
        f"AND j.match_status {op} 'excluded' "
        "ORDER BY j.match_tier NULLS LAST, j.location_rank, j.industry_rank, j.fit_score DESC, "
        "j.freshness_at DESC, j.id DESC LIMIT 500"
    )
    rows = [dict(r) for r in (await conn.execute(text(sql), {"c": company_id})).mappings()]
    now = rows[0]["_now"] if rows else (await conn.execute(text("SELECT now()"))).scalar_one()
    return rows, now


async def company_activity(conn: AsyncConnection, company_id: int) -> dict[str, int]:
    r = (
        await conn.execute(
            text(
                "SELECT count(*) FILTER (WHERE first_seen_at > now() - interval '30 days')"
                " AS opened,"
                " count(*) FILTER (WHERE closed_at > now() - interval '30 days') AS closed "
                "FROM jobs WHERE company_id = :c"
            ),
            {"c": company_id},
        )
    ).one()
    return {"opened": r.opened, "closed": r.closed}


async def health_data(conn: AsyncConnection) -> dict[str, Any]:
    async def rows(sql: str) -> list[dict[str, Any]]:
        return [dict(r) for r in (await conn.execute(text(sql))).mappings()]

    d: dict[str, Any] = {}
    d["sources_by_status"] = await rows(
        "SELECT status::text AS status, count(*) AS n FROM sources GROUP BY 1 ORDER BY 2 DESC"
    )
    d["problem_sources"] = await rows(
        "SELECT s.id, s.adapter, s.token, s.status::text AS status, s.consecutive_failures,"
        " s.last_outcome::text AS last_outcome, s.last_crawled_at, co.name AS company,"
        " (SELECT cr.error FROM crawl_runs cr WHERE cr.source_id = s.id AND cr.error IS NOT NULL"
        "  ORDER BY cr.started_at DESC LIMIT 1) AS last_error "
        "FROM sources s JOIN companies co ON co.id = s.company_id "
        "WHERE (s.consecutive_failures > 0 OR s.status IN ('broken','blocked')) "
        "AND co.status <> 'removed' "
        "ORDER BY s.consecutive_failures DESC, s.id LIMIT 200"
    )
    d["held_sources"] = await rows(
        "SELECT s.id, co.name AS company, s.adapter, s.token, s.status::text AS status,"
        " coalesce((SELECT cr.error FROM crawl_runs cr WHERE cr.source_id = s.id"
        "  AND cr.error IS NOT NULL ORDER BY cr.started_at DESC LIMIT 1),"
        "  s.last_outcome::text, 'no reason recorded') AS reason "
        "FROM sources s JOIN companies co ON co.id = s.company_id "
        "WHERE s.status IN ('blocked','paused') AND co.status <> 'removed' "
        "ORDER BY s.status, co.name LIMIT 200"
    )
    d["adapter_success"] = await rows(
        "SELECT adapter, "
        " count(*) FILTER (WHERE started_at > now() - interval '24 hours') AS runs_24h,"
        " count(*) FILTER (WHERE started_at > now() - interval '24 hours'"
        "   AND outcome IN ('ok','partial')) AS ok_24h,"
        " count(*) AS runs_7d, count(*) FILTER (WHERE outcome IN ('ok','partial')) AS ok_7d "
        "FROM crawl_runs WHERE started_at > now() - interval '7 days' GROUP BY adapter "
        "ORDER BY adapter"
    )
    d["per_day"] = await rows(
        "SELECT g.day::date AS day,"
        " (SELECT count(*) FROM jobs WHERE first_seen_at >= g.day"
        "   AND first_seen_at < g.day + interval '1 day') AS added,"
        " (SELECT count(*) FROM jobs WHERE closed_at >= g.day"
        "   AND closed_at < g.day + interval '1 day') AS closed "
        "FROM generate_series(date_trunc('day', now()) - interval '6 days',"
        " date_trunc('day', now()), interval '1 day') AS g(day) ORDER BY g.day DESC"
    )
    d["match_status"] = await rows(
        "SELECT match_status, count(*) AS n FROM jobs WHERE closed_at IS NULL GROUP BY 1 ORDER BY 1"
    )
    misc = (
        await conn.execute(
            text(
                "SELECT (SELECT max(started_at) FROM crawl_runs) AS last_crawl,"
                " (SELECT count(*) FROM jobs WHERE closed_at IS NULL) AS open_jobs"
            )
        )
    ).one()
    d["last_crawl"], d["open_jobs"] = misc.last_crawl, misc.open_jobs
    oldest = (
        (
            await conn.execute(
                text(
                    "SELECT s.id, s.adapter, s.token, s.next_crawl_at FROM sources s "
                    "WHERE s.status = 'active' ORDER BY s.next_crawl_at LIMIT 1"
                )
            )
        )
        .mappings()
        .first()
    )
    d["oldest_due"] = dict(oldest) if oldest else None
    d["now"] = (await conn.execute(text("SELECT now()"))).scalar_one()
    return d


async def search_companies(
    conn: AsyncConnection, q: str, exclude: list[int] | None = None, limit: int = 10
) -> list[dict[str, Any]]:
    """Typeahead: companies with open jobs whose name contains `q` (prefix matches first)."""
    q = q.strip()[:100]
    res = await conn.execute(
        text(
            "SELECT c.id, c.name, c.slug, count(j.id) AS open_jobs FROM companies c "
            "JOIN jobs j ON j.company_id = c.id AND j.closed_at IS NULL "
            "AND j.match_status <> 'excluded' "
            "WHERE c.status <> 'removed' AND c.name ILIKE :like ESCAPE '\\' "
            "AND NOT (c.id = ANY(CAST(:ex AS bigint[]))) "
            "GROUP BY c.id ORDER BY (c.name ILIKE :pre ESCAPE '\\') DESC, count(j.id) DESC, "
            "c.name LIMIT :lim"
        ),
        {
            "like": f"%{_like_escape(q)}%",
            "pre": f"{_like_escape(q)}%",
            "ex": exclude or [],
            "lim": limit,
        },
    )
    return [dict(r) for r in res.mappings()]


async def companies_by_ids(conn: AsyncConnection, ids: list[int]) -> list[dict[str, Any]]:
    if not ids:
        return []
    res = await conn.execute(
        text(
            "SELECT id, name, slug FROM companies WHERE id = ANY(CAST(:ids AS bigint[])) "
            "AND status <> 'removed' ORDER BY name"
        ),
        {"ids": ids},
    )
    return [dict(r) for r in res.mappings()]


async def fetch_card(conn: AsyncConnection, job_id: int) -> dict[str, Any] | None:
    row = (
        (
            await conn.execute(
                text(
                    f"SELECT {FEED_COLS}, now() AS _now FROM {FEED_FROM} "
                    "WHERE j.id = :id AND co.status <> 'removed'"
                ),
                {"id": job_id},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None

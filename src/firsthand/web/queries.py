"""Feed query construction (the one place), plus detail/health queries.

All SQL is parameterized; user-controlled enums are whitelisted by pydantic Literals.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

PAGE_SIZE = 50
PLACE_RANK = {"nj": 0, "nyc": 1, "remote": 2, "other": 3}

FEED_COLS = """
    j.id, j.title, j.company_id, co.name AS company_name, co.slug AS company_slug,
    co.industry AS company_industry, j.locations, j.remote_type, j.posted_at, j.freshness_at,
    j.first_seen_at, j.salary_min_usd, j.salary_max_usd, j.match_tier, j.match_status,
    j.match_reasons, j.visa_status, j.visa_evidence, j.years_min, j.years_max, j.fit_score,
    j.apply_url, j.location_rank, j.industry_rank
"""


class FeedFilters(BaseModel):
    """Querystring <-> SQL source of truth for the feed (Phase 3 extends this)."""

    q: str = Field(default="", max_length=200)
    tier: Literal["any", "1", "2", "3", "targets"] = "any"
    place: Literal["any", "nj", "nyc", "remote", "other"] = "any"
    industry_only: bool = False
    visa_only: bool = False
    min_pay: int | None = Field(default=None, ge=0, le=10_000_000)
    pay_unknown: Literal["include", "exclude"] = "include"
    days: Literal["7", "14", "30", "all"] = "14"
    reason: str = Field(default="", max_length=64)  # /filtered only
    offset: int = Field(default=0, ge=0, le=1_000_000)
    limit: int = Field(default=PAGE_SIZE, ge=1, le=200)

    @field_validator("q", "reason", mode="before")
    @classmethod
    def _strip(cls, v: Any) -> Any:
        return v.strip() if isinstance(v, str) else (v or "")

    @field_validator("min_pay", "industry_only", "visa_only", mode="before")
    @classmethod
    def _blank(cls, v: Any) -> Any:
        return None if v == "" else v

    @field_validator("industry_only", "visa_only", mode="before")
    @classmethod
    def _bool(cls, v: Any) -> Any:
        return False if v is None else v

    def query_string(self, **over: Any) -> str:
        from urllib.parse import urlencode

        d = self.model_dump(exclude={"limit"})
        d.update(over)
        d = {
            k: ("1" if v is True else v)
            for k, v in d.items()
            if v not in (None, "", False) and not (k == "offset" and v == 0)
        }
        defaults = {"tier": "any", "place": "any", "pay_unknown": "include", "days": "14"}
        d = {k: v for k, v in d.items() if defaults.get(k) != v}
        return urlencode(d)


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def build_feed(f: FeedFilters, *, excluded: bool = False) -> tuple[str, str, dict[str, Any]]:
    """Return (where_sql, order_sql, params). Only fixed SQL fragments are concatenated."""
    where = ["j.closed_at IS NULL"]
    p: dict[str, Any] = {}
    where.append("j.match_status = 'excluded'" if excluded else "j.match_status <> 'excluded'")
    if f.days != "all":
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
    if f.industry_only:
        where.append("j.industry_rank = 0")
    if f.visa_only:
        where.append("j.visa_status = 'offered'")
    if f.min_pay is not None:
        cond = "coalesce(j.salary_max_usd, j.salary_min_usd) >= :min_pay"
        if f.pay_unknown == "include":
            cond = f"({cond} OR (j.salary_max_usd IS NULL AND j.salary_min_usd IS NULL))"
        where.append(cond)
        p["min_pay"] = f.min_pay
    if f.reason:
        where.append("j.match_reasons @> ARRAY[:reason]::text[]")
        p["reason"] = f.reason
    order = (
        "j.match_tier NULLS LAST, j.location_rank, j.industry_rank, j.fit_score DESC, "
        "j.freshness_at DESC, j.id DESC"
    )
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
        f"SELECT {FEED_COLS}, now() AS _now FROM jobs j JOIN companies co ON co.id = j.company_id "
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


async def reason_counts(conn: AsyncConnection, f: FeedFilters) -> list[tuple[str, int]]:
    f2 = f.model_copy(update={"reason": ""})
    where, _, p = build_feed(f2, excluded=True)
    res = await conn.execute(
        text(
            f"SELECT r, count(*) AS n FROM jobs j, unnest(j.match_reasons) r WHERE {where} "
            "AND (r IN ('visa:not_available','employment:contract','company:excluded',"
            "'company:staffing') OR r LIKE 'years:min\\_%') GROUP BY r ORDER BY n DESC, r"
        ),
        p,
    )
    return [(r.r, r.n) for r in res]


JOB_DETAIL = """
SELECT j.*, co.name AS company_name, co.slug AS company_slug, co.industry AS company_industry,
       co.domain AS company_domain, p.url AS posting_url, s.adapter AS source_adapter,
       s.token AS source_token
FROM jobs j JOIN companies co ON co.id = j.company_id
LEFT JOIN postings p ON p.id = j.primary_posting_id
LEFT JOIN sources s ON s.id = p.source_id
WHERE j.id = :id
"""


async def fetch_job(conn: AsyncConnection, job_id: int) -> dict[str, Any] | None:
    row = (await conn.execute(text(JOB_DETAIL), {"id": job_id})).mappings().first()
    if row is None:
        return None
    d = dict(row)
    d["_now"] = (await conn.execute(text("SELECT now()"))).scalar_one()
    return d


async def fetch_company(conn: AsyncConnection, slug: str) -> dict[str, Any] | None:
    row = (
        (await conn.execute(text("SELECT * FROM companies WHERE slug = :s"), {"s": slug}))
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def company_jobs(
    conn: AsyncConnection, company_id: int, *, excluded: bool
) -> tuple[list[dict[str, Any]], Any]:
    op = "=" if excluded else "<>"
    sql = (
        f"SELECT {FEED_COLS}, now() AS _now FROM jobs j JOIN companies co ON co.id = j.company_id "
        f"WHERE j.company_id = :c AND j.closed_at IS NULL AND j.match_status {op} 'excluded' "
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
        "WHERE s.consecutive_failures > 0 OR s.status IN ('broken','blocked') "
        "ORDER BY s.consecutive_failures DESC, s.id LIMIT 200"
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

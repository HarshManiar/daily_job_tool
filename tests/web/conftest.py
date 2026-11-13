import itertools
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from daily_job_tool.web.app import create_app

_seq = itertools.count(1)

JOB_DEFAULTS: dict[str, Any] = {
    "title": "Data Analyst",
    "remote_type": "onsite",
    "match_status": "include",
    "match_tier": None,
    "match_reasons": [],
    "location_rank": 3,
    "industry_rank": 1,
    "fit_score": 0,
    "visa_status": "not_stated",
    "visa_evidence": None,
    "years_min": None,
    "years_max": None,
    "salary_min_usd": None,
    "salary_max_usd": None,
    "apply_url": "https://example.com/apply",
    "description_html": "<p>Hello</p>",
    "description_text": "Hello",
    "age_days": 1.0,
    "closed": False,
    "locations": "[]",
    "seniority": "unknown",
    "function": "other",
    "employment_type": "unknown",
    "tech_tags": [],
    "first_seen_age_days": None,  # default: same as age_days
    "closed_age_days": None,  # when set, closed_at = now - that many days
}


class Factory:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def company(self, name: str = "Acme", slug: str | None = None, **kw: Any) -> int:
        slug = slug or f"{name.lower().replace(' ', '-')}-{next(_seq)}"
        async with self.engine.begin() as c:
            return (
                await c.execute(
                    text(
                        "INSERT INTO companies (name, slug, industry) VALUES (:n, :s, :i) "
                        "RETURNING id"
                    ),
                    {"n": name, "s": slug, "i": kw.get("industry")},
                )
            ).scalar_one()

    async def job(self, company_id: int, **kw: Any) -> int:
        p = {**JOB_DEFAULTS, **kw}
        p["key"] = f"k{next(_seq)}"
        p["cid"] = company_id
        p["fs_age"] = (
            p["age_days"] if p["first_seen_age_days"] is None else p["first_seen_age_days"]
        )
        p["closed_age"] = p.pop("closed_age_days")
        p.pop("first_seen_age_days")
        async with self.engine.begin() as c:
            return (
                await c.execute(
                    text(
                        """INSERT INTO jobs (company_id, dedupe_key, title, title_norm,
                        remote_type, match_status, match_tier, match_reasons, location_rank,
                        industry_rank, fit_score, visa_status, visa_evidence, years_min, years_max,
                        salary_min_usd, salary_max_usd, apply_url, description_html,
                        description_text, first_seen_at, last_seen_at, freshness_at, closed_at,
                        normalizer_version, locations, seniority, function, employment_type, tech_tags)
                        VALUES (:cid, :key, :title, lower(:title), :remote_type, :match_status,
                        :match_tier, :match_reasons, :location_rank, :industry_rank, :fit_score,
                        :visa_status, :visa_evidence, :years_min, :years_max, :salary_min_usd,
                        :salary_max_usd, :apply_url, :description_html, :description_text,
                        now() - make_interval(secs => CAST(:fs_age AS float8) * 86400),
                        now() - make_interval(secs => CAST(:age_days AS float8) * 86400),
                        now() - make_interval(secs => CAST(:age_days AS float8) * 86400),
                        CASE WHEN CAST(:closed_age AS float8) IS NOT NULL
                             THEN now() - make_interval(secs => CAST(:closed_age AS float8) * 86400)
                             WHEN :closed THEN now() END,
                        1, CAST(:locations AS jsonb), CAST(:seniority AS seniority), :function,
                        CAST(:employment_type AS emp_type), :tech_tags)
                        RETURNING id"""
                    ),
                    p,
                )
            ).scalar_one()


@pytest_asyncio.fixture
async def db(engine: AsyncEngine) -> AsyncIterator[Factory]:
    async def wipe() -> None:
        async with engine.begin() as c:
            await c.execute(
                text(
                    "TRUNCATE companies, sources, crawl_runs, app_state, saved_searches RESTART IDENTITY CASCADE"
                )
            )

    await wipe()
    yield Factory(engine)
    await wipe()


@pytest_asyncio.fixture
async def client(engine: AsyncEngine, db: Factory) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(engine)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as c:
        yield c

"""Test helpers for ingest: FakeAdapter, seeding and query shortcuts."""

from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from sqlalchemy import text

from daily_job_tool.adapters.base import Adapter, FetchResult, RawJob, SourceRef
from daily_job_tool.normalize import NormContext
from daily_job_tool.pipeline.ingest import ingest_source
from tests.adapters._helpers import json_handler, mock_client

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
CTX = NormContext.load()


def at(day: float) -> datetime:
    return T0 + timedelta(days=day)


def job(
    ext: str, title: str = "Software Engineer", loc: str | None = "New York, NY", **kw
) -> RawJob:
    desc = kw.pop("desc", None)
    payload = {"id": ext, "title": title, "loc": loc, **kw.pop("extra", {})}
    if desc:
        payload["desc"] = desc
    if kw.get("apply_url"):
        payload["apply"] = kw["apply_url"]
    return RawJob(
        external_id=ext,
        title=title,
        url=f"https://example.com/jobs/{ext}",
        locations_raw=[loc] if loc else [],
        description_html=desc or "<p>desc</p>",
        payload=payload,
        **kw,
    )


class FakeAdapter(Adapter):
    name: ClassVar[str] = "fake_t1"
    tier: ClassVar[int] = 1
    hosts: ClassVar[list[str]] = []

    def __init__(self) -> None:
        self.script: list[Any] = []
        self.detail_calls: list[str] = []
        self.make_request = False

    def push(self, jobs: list[RawJob] | Exception, complete: bool = True) -> "FakeAdapter":
        self.script.append(
            jobs if isinstance(jobs, Exception) else FetchResult(jobs=jobs, complete=complete)
        )
        return self

    async def fetch_jobs(self, src: SourceRef, http) -> FetchResult:
        if self.make_request:
            await http.get_json("https://example.com/api")
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    def _map(self, payload: dict[str, Any]) -> RawJob:
        """Rebuild a RawJob from a stored payload (what renormalize / refresh rely on)."""
        loc = payload.get("loc")
        return RawJob(
            external_id=payload["id"],
            title=payload["title"],
            url=f"https://example.com/jobs/{payload['id']}",
            apply_url=payload.get("apply"),
            locations_raw=[loc] if loc else [],
            description_html="<p>detailed</p>"
            if payload.get("detail")
            else payload.get("desc", "<p>desc</p>"),
            payload=payload,
        )

    async def fetch_detail(self, src: SourceRef, job: RawJob, http) -> RawJob:
        self.detail_calls.append(job.external_id)
        return job.model_copy(
            update={
                "needs_detail": False,
                "description_html": "<p>detailed</p>",
                "payload": {**job.payload, "detail": True},
            }
        )


class FakeT3(FakeAdapter):
    name: ClassVar[str] = "fake_t3"
    tier: ClassVar[int] = 3


async def seed(
    engine, *, company: str = "acme", adapter: str = "fake_t1", token: str = "acme"
) -> int:
    async with engine.begin() as conn:
        cid = (
            await conn.execute(text("SELECT id FROM companies WHERE slug = :s"), {"s": company})
        ).scalar_one_or_none()
        if cid is None:
            cid = (
                await conn.execute(
                    text("INSERT INTO companies (name, slug) VALUES (:n, :s) RETURNING id"),
                    {"n": company, "s": company},
                )
            ).scalar_one()
        return (
            await conn.execute(
                text(
                    "INSERT INTO sources (company_id, adapter, token) "
                    "VALUES (:c, :a, :t) RETURNING id"
                ),
                {"c": cid, "a": adapter, "t": token},
            )
        ).scalar_one()


async def crawl(engine, sid: int, adapter: Adapter, day: float, **kw):
    handler = json_handler({})
    async with mock_client(handler) as c:
        return await ingest_source(engine, sid, adapter, c, CTX, now=at(day), **kw)


async def rows(engine, sql: str, **params) -> list[Any]:
    async with engine.connect() as conn:
        return list((await conn.execute(text(sql), params)).all())


async def scalar(engine, sql: str, **params) -> Any:
    async with engine.connect() as conn:
        return (await conn.execute(text(sql), params)).scalar_one()

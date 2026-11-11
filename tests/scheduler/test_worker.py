import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from firsthand.http.errors import NotFound
from firsthand.scheduler import worker as worker_mod
from firsthand.scheduler.breaker import apply_breaker, circuit_pause
from firsthand.scheduler.worker import claim_sources, cleanup_orphan_runs, run_worker
from tests.adapters._helpers import client_for
from tests.pipeline.helpers import CTX, FakeAdapter, job, rows, scalar

_n = 0


async def add_source(
    engine, *, priority=5, due=True, locked=False, circuit=False, status="active",
    company_status="active", staffing=False, exclude=None, adapter="fake_t1",
) -> int:  # fmt: skip
    global _n
    _n += 1
    async with engine.begin() as conn:
        cid = (
            await conn.execute(
                text(
                    "INSERT INTO companies (name, slug, status, is_staffing, exclude_reason) "
                    "VALUES (:n, :sl, CAST(:st AS company_status), :sf, :ex) RETURNING id"
                ),
                {
                    "n": f"co{_n}",
                    "sl": f"co{_n}",
                    "st": company_status,
                    "sf": staffing,
                    "ex": exclude,
                },
            )
        ).scalar_one()
        return (
            await conn.execute(
                text(
                    "INSERT INTO sources (company_id, adapter, token, priority, status, "
                    "next_crawl_at, locked_until, circuit_open_until) VALUES (:c, :a, :t, :p, "
                    "CAST(:st AS source_status), now() + :d, "
                    "CASE WHEN :lk THEN now() + interval '10 min' END, "
                    "CASE WHEN :ci THEN now() + interval '1 hour' END) RETURNING id"
                ),
                {
                    "c": cid, "a": adapter, "t": f"t{_n}", "p": priority, "st": status,
                    "d": timedelta(hours=-1 if due else 1), "lk": locked, "ci": circuit,
                },
            )
        ).scalar_one()  # fmt: skip


async def test_no_double_claim(db_clean):
    ids = {await add_source(db_clean) for _ in range(20)}
    a, b = await asyncio.gather(claim_sources(db_clean, 10), claim_sources(db_clean, 10))
    sa, sb = {c.id for c in a}, {c.id for c in b}
    assert len(sa) == len(sb) == 10 and not sa & sb and sa | sb == ids


async def test_priority_order(db_clean):
    low = await add_source(db_clean, priority=8)
    hi = await add_source(db_clean, priority=1)
    mid = await add_source(db_clean, priority=5)
    got = await claim_sources(db_clean, 10)
    assert [c.id for c in got] == [hi, mid, low]
    assert await claim_sources(db_clean, 10) == []  # now locked


async def test_claim_skips_ineligible(db_clean):
    ok = await add_source(db_clean)
    await add_source(db_clean, locked=True)
    await add_source(db_clean, circuit=True)
    await add_source(db_clean, exclude="current employer")
    await add_source(db_clean, staffing=True)
    await add_source(db_clean, status="broken")
    await add_source(db_clean, status="paused")
    await add_source(db_clean, company_status="paused")
    await add_source(db_clean, due=False)
    assert [c.id for c in await claim_sources(db_clean, 50)] == [ok]


async def test_claim_filters_and_sets_lease(db_clean):
    a = await add_source(db_clean)
    await add_source(db_clean, adapter="fake_t3")
    got = await claim_sources(db_clean, 10, adapter="fake_t1")
    assert [c.id for c in got] == [a]
    assert await scalar(db_clean, "SELECT locked_until > now() FROM sources WHERE id=:i", i=a)


async def test_lock_released_when_ingest_raises(db_clean, monkeypatch):
    await add_source(db_clean)

    async def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(worker_mod, "ingest_source", boom)
    out = await run_worker(
        db_clean, CTX, once=True, client_factory=lambda: client_for(None),
        install_signals=False,
    )  # fmt: skip
    assert [s.outcome for s in out] == ["error"]
    r = (
        await rows(
            db_clean,
            "SELECT locked_until, consecutive_failures, next_crawl_at > now() AS later "
            "FROM sources",
        )
    )[0]
    assert r.locked_until is None and r.consecutive_failures == 1 and r.later


async def test_run_once_end_to_end_and_breaker(db_clean, monkeypatch):
    sid = await add_source(db_clean)
    ad = FakeAdapter()
    ad.push(NotFound("https://x"))
    monkeypatch.setattr(worker_mod.registry, "get_adapter", lambda name: ad)
    out = await run_worker(
        db_clean, CTX, once=True, client_factory=lambda: client_for(None), install_signals=False
    )
    assert [(s.source_id, s.outcome) for s in out] == [(sid, "not_found")]
    r = (
        await rows(
            db_clean,
            "SELECT locked_until, last_outcome::text AS o, status::text AS st FROM sources",
        )
    )[0]
    assert r.locked_until is None and r.o == "not_found" and r.st == "active"


async def test_once_limit(db_clean, monkeypatch):
    for _ in range(4):
        await add_source(db_clean)
    ad = FakeAdapter()
    for _ in range(4):
        ad.push([job("1")])
    monkeypatch.setattr(worker_mod.registry, "get_adapter", lambda name: ad)
    out = await run_worker(
        db_clean, CTX, once=True, limit=2, concurrency=8,
        client_factory=lambda: client_for(None), install_signals=False,
    )  # fmt: skip
    assert len(out) == 2


async def test_orphan_cleanup(db_clean):
    sid = await add_source(db_clean)
    async with db_clean.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO crawl_runs (source_id, adapter, started_at) VALUES "
                "(:s,'x', now() - interval '2 hours'), (:s,'x', now() - interval '5 min')"
            ),
            {"s": sid},
        )
    assert await cleanup_orphan_runs(db_clean) == 1
    r = await rows(db_clean, "SELECT outcome::text AS o, finished_at FROM crawl_runs ORDER BY id")
    assert r[0].o == "timeout" and r[0].finished_at is not None
    assert r[1].o is None and r[1].finished_at is None


def test_circuit_pause_math():
    assert circuit_pause(4) is None
    assert circuit_pause(5) == timedelta(hours=6)
    assert circuit_pause(6) == timedelta(hours=12)
    assert circuit_pause(7) == timedelta(hours=24)
    assert circuit_pause(10) == timedelta(days=7)
    assert circuit_pause(500) == timedelta(days=7)


async def _breaker(engine, sid, failures, outcomes):
    now = datetime(2026, 11, 1, tzinfo=UTC)
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE sources SET consecutive_failures=:f WHERE id=:i"),
            {"f": failures, "i": sid},
        )
        for k, o in enumerate(outcomes):
            await conn.execute(
                text(
                    "INSERT INTO crawl_runs (source_id, adapter, started_at, finished_at, outcome) "
                    "VALUES (:s,'x', :t, :t, CAST(:o AS crawl_outcome))"
                ),
                {"s": sid, "t": now + timedelta(minutes=k), "o": o},
            )
    async with engine.begin() as conn:
        ch = await apply_breaker(conn, sid, now=now)
    r = (
        await rows(
            engine,
            "SELECT status::text AS st, circuit_open_until AS c FROM sources WHERE id=:i",
            i=sid,
        )
    )[0]
    return ch, r, now


async def test_breaker_circuit(db_clean):
    sid = await add_source(db_clean)
    ch, r, now = await _breaker(db_clean, sid, 6, ["http_error"] * 6)
    assert r.c == now + timedelta(hours=12) and r.st == "active"
    sid = await add_source(db_clean)
    ch, r, now = await _breaker(db_clean, sid, 4, ["http_error"] * 4)
    assert r.c is None and ch == {}


async def test_breaker_not_found_and_robots(db_clean):
    sid = await add_source(db_clean)
    _, r, _ = await _breaker(db_clean, sid, 3, ["not_found"] * 3)
    assert r.st == "broken"
    sid = await add_source(db_clean)
    _, r, _ = await _breaker(db_clean, sid, 2, ["not_found", "not_found"])
    assert r.st == "active"
    sid = await add_source(db_clean)
    _, r, _ = await _breaker(db_clean, sid, 3, ["robots_disallowed"] * 3)
    assert r.st == "blocked"
    sid = await add_source(db_clean)
    _, r, _ = await _breaker(db_clean, sid, 3, ["not_found", "ok", "not_found"])
    assert r.st == "active"

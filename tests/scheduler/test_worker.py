import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from daily_job_tool.http.errors import NotFound
from daily_job_tool.scheduler import worker as worker_mod
from daily_job_tool.scheduler.breaker import apply_breaker, circuit_pause
from daily_job_tool.scheduler.worker import claim_sources, cleanup_orphan_runs, run_worker
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


# -- continuous pool, wall-clock cap, shutdown ------------------------------------------


class SlowAdapter(FakeAdapter):
    """Per-token behaviour: 'hang' sleeps forever, 'slow' 0.6 s, others instant."""

    def __init__(self, delays, log):
        super().__init__()
        self.delays, self.log = delays, log

    async def fetch_jobs(self, src, http):
        self.log.append(("start", src.id, asyncio.get_running_loop().time()))
        d = self.delays.get(src.id, 0)
        await (asyncio.sleep(3600) if d == "hang" else asyncio.sleep(d))
        self.log.append(("end", src.id, asyncio.get_running_loop().time()))
        from daily_job_tool.adapters.base import FetchResult

        return FetchResult(jobs=[job("1")], complete=True)


def _patch(monkeypatch, ad):
    monkeypatch.setattr(worker_mod.registry, "get_adapter", lambda name: ad)


async def _run(db, **kw):
    kw.setdefault("once", True)
    return await run_worker(
        db, CTX, client_factory=lambda: client_for(None), install_signals=False, **kw
    )


async def test_hung_source_times_out_and_does_not_block(db_clean, monkeypatch):
    hung = await add_source(db_clean, priority=1)
    fast = [await add_source(db_clean, priority=5) for _ in range(5)]
    log: list = []
    _patch(monkeypatch, SlowAdapter({hung: "hang"}, log))
    out = await _run(db_clean, concurrency=2, source_timeout_seconds=1.0)
    by = {s.source_id: s for s in out}
    assert by[hung].outcome == "timeout" and by[hung].error == "source exceeded wall-clock cap"
    assert all(by[f].outcome == "ok" for f in fast)
    ends = {sid: t for k, sid, t in log if k == "end"}
    starts = {sid: t for k, sid, t in log if k == "start"}
    assert max(ends.values()) - starts[hung] < 0.9  # fast ones finished before the cap fired
    r = (
        await rows(
            db_clean,
            "SELECT locked_until, consecutive_failures AS f, last_outcome::text AS o, "
            "next_crawl_at > now() AS later FROM sources WHERE id=:i", i=hung,
        )
    )[0]  # fmt: skip
    assert r.locked_until is None and r.f == 1 and r.o == "timeout" and r.later
    run = (
        await rows(
            db_clean,
            "SELECT outcome::text AS o, error, finished_at FROM crawl_runs WHERE source_id=:i",
            i=hung,
        )
    )[0]
    assert run.o == "timeout" and run.error == "source exceeded wall-clock cap"
    assert run.finished_at is not None


async def test_cancel_mid_ingest_leaves_no_partial_state(db_clean, monkeypatch):
    class HangAfterJobs(FakeAdapter):
        async def fetch_jobs(self, src, http):
            await asyncio.sleep(3600)

    sid = await add_source(db_clean)
    _patch(monkeypatch, HangAfterJobs())
    await _run(db_clean, source_timeout_seconds=0.3)
    assert await scalar(db_clean, "SELECT count(*) FROM postings") == 0
    assert await scalar(db_clean, "SELECT count(*) FROM crawl_runs WHERE finished_at IS NULL") == 0
    assert await scalar(db_clean, "SELECT locked_until IS NULL FROM sources WHERE id=:i", i=sid)


async def test_cancel_inside_open_transaction_rolls_back(db_clean, monkeypatch):
    sid = await add_source(db_clean)

    async def fake_ingest(engine, source_id, *a, **k):
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE sources SET consecutive_failures = 99"))
            await asyncio.sleep(3600)

    monkeypatch.setattr(worker_mod, "ingest_source", fake_ingest)
    await _run(db_clean, source_timeout_seconds=0.3)
    r = (await rows(db_clean, "SELECT consecutive_failures AS f FROM sources WHERE id=:i", i=sid))[
        0
    ]
    assert r.f == 1  # the 99 was rolled back; only the timeout increment remains


async def test_pool_refills_as_slots_free(db_clean, monkeypatch):
    a = await add_source(db_clean, priority=1)  # slow
    b = await add_source(db_clean, priority=2)  # fast
    c = await add_source(db_clean, priority=3)
    d = await add_source(db_clean, priority=4)
    log: list = []
    _patch(monkeypatch, SlowAdapter({a: 1.5, b: 0.1, c: 0.1, d: 0.1}, log))
    out = await _run(db_clean, concurrency=2, poll_seconds=30)
    assert len(out) == 4
    starts = {sid: t for k, sid, t in log if k == "start"}
    ends = {sid: t for k, sid, t in log if k == "end"}
    # c starts right after b ends (not after the whole first batch), d after c
    assert starts[c] - ends[b] < 0.5 and starts[d] - ends[c] < 0.5
    assert starts[d] < ends[a]


async def test_shutdown_grace_cancels_in_flight(db_clean, monkeypatch):
    hung = await add_source(db_clean, priority=1)
    quick = await add_source(db_clean, priority=2)
    later = await add_source(db_clean, priority=3)
    log: list = []
    _patch(monkeypatch, SlowAdapter({hung: "hang", quick: 0.1}, log))
    stop = asyncio.Event()
    task = asyncio.create_task(
        _run(
            db_clean,
            concurrency=1,
            stop=stop,
            shutdown_grace_seconds=0.5,
            source_timeout_seconds=60,
        )
    )
    await asyncio.sleep(0.5)
    t0 = asyncio.get_running_loop().time()
    stop.set()
    out = await asyncio.wait_for(task, 10)
    assert asyncio.get_running_loop().time() - t0 >= 0.45  # waited for the grace period
    by = {s.source_id: s for s in out}
    assert by[hung].outcome == "timeout" and "cancelled" in by[hung].error
    assert quick not in by and later not in by  # nothing claimed after stop
    assert {c.id for c in await claim_sources(db_clean, 10)} == {quick, later}
    run = (
        await rows(
            db_clean, "SELECT outcome::text AS o, error FROM crawl_runs WHERE source_id=:i", i=hung
        )
    )[0]
    assert run.o == "timeout" and "cancelled" in run.error
    assert await scalar(db_clean, "SELECT locked_until IS NULL FROM sources WHERE id=:i", i=hung)


async def test_force_stop_cancels_immediately(db_clean, monkeypatch):
    hung = await add_source(db_clean)
    _patch(monkeypatch, SlowAdapter({hung: "hang"}, []))
    stop, force = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(
        _run(db_clean, stop=stop, force_stop=force, shutdown_grace_seconds=60)
    )
    await asyncio.sleep(0.5)
    stop.set()
    force.set()
    out = await asyncio.wait_for(task, 5)
    assert [s.outcome for s in out] == ["timeout"]
    assert await scalar(db_clean, "SELECT locked_until IS NULL FROM sources")


async def test_orphan_cleanup_closes_unfinished_run_at_start(db_clean, monkeypatch):
    sid = await add_source(db_clean, due=False)
    async with db_clean.begin() as conn:
        await conn.execute(
            text("INSERT INTO crawl_runs (source_id, adapter, started_at) "
                 "VALUES (:s,'x', now() - interval '3 hours')"), {"s": sid},
        )  # fmt: skip
    await _run(db_clean)
    r = (await rows(db_clean, "SELECT outcome::text AS o, finished_at FROM crawl_runs"))[0]
    assert r.o == "timeout" and r.finished_at is not None

import json
import os
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import text

from daily_job_tool.web.app import create_app

OK = {"Origin": "http://127.0.0.1:8000"}
CSV = (
    "name,industry,priority,careers_url,reason,source_note\n"
    "Zeta Pharm,pharma,1,https://zeta.com/careers,robots.txt forbids crawling,guessed path (x)\n"
    "Alpha Data,healthcare data,1,https://www.alpha.io/jobs,bot protection blocks automated visitors,\n"
    "Evil Co,retail,2,javascript:alert(1),robots.txt forbids crawling,\n"
    "Mid Co,retail,2,ftp://mid.com/jobs,robots.txt forbids crawling,\n"
)


def _pidfile(pid, started):
    return json.dumps({"pid": pid, "started_at": started})


class FakeSpawner:
    def __init__(self, pid=None):
        self.calls = []
        self.pid = pid or os.getpid()

    def __call__(self, argv, log_path):
        self.calls.append((argv, log_path))
        return self.pid


@pytest.fixture
def paths(tmp_path):
    csv = tmp_path / "manual.csv"
    csv.write_text(CSV)
    return {
        "pid_path": tmp_path / "refresh.pid",
        "heartbeat_path": tmp_path / "hb.json",
        "refresh_log_path": tmp_path / "refresh.log",
        "manual_csv": csv,
    }


@pytest.fixture
async def mk(engine, db, paths):
    clients = []

    async def make(spawner=None, **kw):
        app = create_app(engine, spawner=spawner or FakeSpawner(), **{**paths, **kw})
        c = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        )
        clients.append(c)
        return c

    yield make
    for c in clients:
        await c.aclose()


# ---- /manual ----


async def test_manual_page_groups_and_filters_urls(mk):
    c = await mk()
    r = await c.get("/manual")
    assert r.status_code == 200
    h = r.text
    assert h.index("Alpha Data") < h.index("Zeta Pharm") < h.index("Evil Co")
    assert 'href="https://zeta.com/careers"' in h
    assert 'rel="noopener noreferrer"' in h and 'target="_blank"' in h
    assert "javascript:" not in h and "ftp://" not in h
    assert h.count("guessed link") == 1
    assert "no link" in h
    r = await c.get("/manual", params={"q": "alpha"})
    assert "Alpha Data" in r.text and "Zeta Pharm" not in r.text


async def test_manual_toggle_persists_and_shows_age(mk, db, engine):
    c = await mk()
    key = "manual_checked:zeta.com"
    assert (await c.post("/manual/check", data={"key": key})).status_code == 403
    r = await c.post("/manual/check", data={"key": key}, headers={**OK, "HX-Request": "true"})
    assert r.status_code == 200 and "checked today" in r.text
    async with engine.connect() as conn:
        v = (
            await conn.execute(text("SELECT value FROM app_state WHERE key=:k"), {"k": key})
        ).scalar()
    assert v == datetime.now().astimezone().date().isoformat()
    # age text for an older date
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE app_state SET value='2000-01-01' WHERE key=:k"), {"k": key})
    assert (
        "checked " in (await c.get("/manual")).text and "days ago" in (await c.get("/manual")).text
    )
    # toggling again clears it
    await c.post("/manual/check", data={"key": key}, headers={**OK, "HX-Request": "true"})
    assert "days ago" not in (await c.get("/manual")).text
    assert (
        await c.post("/manual/check", data={"key": "manual_checked:nope"}, headers=OK)
    ).status_code == 404


# ---- static pages ----


@pytest.mark.parametrize(
    ("path", "needle"),
    [
        ("/about", "robots.txt"),
        ("/about", "publicly posted"),
        ("/privacy", "No accounts"),
        ("/privacy", "no requests to any third party"),
        ("/remove", "Request removal"),
        ("/bot", "/remove"),
    ],
)
async def test_static_pages(mk, path, needle):
    c = await mk()
    r = await c.get(path)
    assert r.status_code == 200 and needle in r.text
    foot = (await c.get("/")).text
    assert 'href="/about"' in foot and 'href="/privacy"' in foot and 'href="/remove"' in foot


async def test_no_external_assets_or_inline_code(mk):
    c = await mk()
    for p in ("/", "/manual", "/remove", "/about", "/privacy", "/health"):
        h = (await c.get(p)).text
        assert "<style" not in h and " onclick=" not in h and "<script>" not in h
        assert "https://" not in h.replace("https://zeta.com", "").replace(
            "https://www.alpha.io", ""
        )


# ---- removal flow ----


async def _seed_company(db, engine, name="Gone Inc", domain="gone.com"):
    cid = await db.company(name)
    async with engine.begin() as c:
        await c.execute(text("UPDATE companies SET domain=:d WHERE id=:i"), {"d": domain, "i": cid})
        sid = (
            await c.execute(
                text(
                    "INSERT INTO sources (company_id, adapter, token) VALUES (:c,'fake_t1','gone') RETURNING id"
                ),
                {"c": cid},
            )
        ).scalar_one()
        await c.execute(
            text(
                "INSERT INTO postings (source_id, external_id, url, payload, payload_hash) "
                "VALUES (:s,'1','https://gone.com/1','{}',decode('00','hex'))"
            ),
            {"s": sid},
        )
    await db.job(cid, title="Gone Job")
    return cid


async def _clean(engine):
    async with engine.begin() as c:
        await c.execute(text("TRUNCATE blocked_domains, removal_requests RESTART IDENTITY"))


async def test_remove_company_effects_idempotent_and_hidden(mk, db, engine):
    from daily_job_tool.registry.removal import CompanyNotFound, remove_company

    await _clean(engine)
    cid = await _seed_company(db, engine)
    keep = await db.company("Keeper")
    await db.job(keep, title="Keeper Job")
    c = await mk()
    assert "Gone Job" in (await c.get("/")).text
    r1 = await remove_company(engine, "https://www.gone.com/careers", "asked nicely", "hr@gone.com")
    assert (r1.sources_removed, r1.postings_closed, r1.jobs_closed) == (1, 1, 1)
    r2 = await remove_company(engine, "Gone Inc", "again")
    assert r2.already_removed and r2.jobs_closed == 0
    async with engine.connect() as conn:
        co = (
            await conn.execute(
                text("SELECT status::text s, exclude_reason e FROM companies WHERE id=:i"),
                {"i": cid},
            )
        ).one()
        assert co.s == "removed" and co.e == "removal request: asked nicely"
        assert (
            await conn.execute(
                text("SELECT status::text FROM sources WHERE company_id=:i"), {"i": cid}
            )
        ).scalar() == "removed"
        assert (
            await conn.execute(
                text("SELECT count(*) FROM jobs WHERE company_id=:i AND closed_at IS NULL"),
                {"i": cid},
            )
        ).scalar() == 0
        assert (
            await conn.execute(text("SELECT count(*) FROM postings WHERE closed_at IS NULL"))
        ).scalar() == 0
        bd = (await conn.execute(text("SELECT domain::text, reason FROM blocked_domains"))).all()
        assert [tuple(x) for x in bd] == [("gone.com", "removal_request")]
        rr = (
            await conn.execute(
                text(
                    "SELECT requester_email::text, resolution, verified_at IS NOT NULL v, resolved_at IS NOT NULL r FROM removal_requests"
                )
            )
        ).all()
        assert len(rr) == 1 and rr[0].resolution == "removed" and rr[0].v and rr[0].r
    with pytest.raises(CompanyNotFound):
        await remove_company(engine, "nobody.example", "x")
    # never visible anywhere, even if the jobs were somehow reopened
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE jobs SET closed_at=NULL WHERE company_id=:i"), {"i": cid})
    for p in ("/", "/filtered", "/api/v1/jobs"):
        assert "Gone Job" not in (await c.get(p)).text
    jid = await _jid(engine, cid)
    assert (await c.get(f"/jobs/{jid}")).status_code == 404
    slug = await _slug(engine, cid)
    assert (await c.get(f"/companies/{slug}")).status_code == 404
    assert "Keeper Job" in (await c.get("/")).text
    await _clean(engine)


async def _jid(engine, cid):
    async with engine.connect() as conn:
        return (
            await conn.execute(text("SELECT id FROM jobs WHERE company_id=:i"), {"i": cid})
        ).scalar()


async def _slug(engine, cid):
    async with engine.connect() as conn:
        return (
            await conn.execute(text("SELECT slug::text FROM companies WHERE id=:i"), {"i": cid})
        ).scalar()


async def test_remove_web_flow(mk, db, engine):
    await _clean(engine)
    await _seed_company(db, engine, "Web Gone", "webgone.com")
    c = await mk()
    r = await c.get("/remove", params={"target": "webgone.com"})
    assert "Web Gone" in r.text and "Open jobs" in r.text and 'action="/remove/confirm"' in r.text
    assert "No company matches" in (await c.get("/remove", params={"target": "zzz.example"})).text
    assert (await c.post("/remove/confirm", data={"target": "webgone.com"})).status_code == 403
    r = await c.post("/remove/confirm", data={"target": "webgone.com", "reason": "r"}, headers=OK)
    assert (
        r.status_code == 200
        and "has been removed" in r.text
        and "webgone.com is now blocked" in r.text
    )
    assert (
        await c.post("/remove/confirm", data={"target": "nope.example"}, headers=OK)
    ).status_code == 404
    await _clean(engine)


async def test_importer_refuses_removed_company(db, engine, tmp_path):
    from daily_job_tool.registry.importer import import_seeds
    from daily_job_tool.registry.removal import remove_company

    await _clean(engine)
    hdr = "name,domain,careers_url,adapter,token,hq_country,size_bucket,tags,industry,is_staffing,exclude_reason,config\n"
    p = tmp_path / "s.csv"
    p.write_text(hdr + "Reseed Co,reseed.com,,,,US,,,pharma,,,\nNoDomain Co,,,,,US,,,pharma,,,\n")
    rep = await import_seeds(engine, p)
    assert rep.companies_new == 2
    await remove_company(engine, "reseed.com", "r")
    await remove_company(engine, "NoDomain Co", "r")
    rep = await import_seeds(engine, p)
    assert rep.companies_new == 0 and rep.companies_updated == 0 and len(rep.rejected) == 2
    async with engine.connect() as conn:
        st = (
            (
                await conn.execute(
                    text(
                        "SELECT status::text FROM companies WHERE name IN ('Reseed Co','NoDomain Co')"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert st == ["removed", "removed"]
    await _clean(engine)


# ---- refresh ----


async def test_refresh_spawns_once_then_running(mk, paths):
    sp = FakeSpawner()
    c = await mk(sp)
    h = {**OK, "HX-Request": "true"}
    r = await c.post("/refresh", headers=h)
    assert r.status_code == 200 and "Refreshing" in r.text and 'hx-trigger="every 5s"' in r.text
    assert len(sp.calls) == 1
    argv, log = sp.calls[0]
    assert (
        argv[-4:-2] == ["crawl", "--once"]
        and argv[-2] == "--stale-hours"
        and log == paths["refresh_log_path"]
    )
    r = await c.post("/refresh", headers=h)
    assert len(sp.calls) == 1 and "already running" in r.text
    st = await c.get("/refresh/status?poll=1")
    assert "0 sources done, 0 in flight" in st.text


async def test_refresh_stale_pid_replaced(mk, paths):
    paths["pid_path"].write_text(_pidfile(2147483646, datetime.now(UTC).isoformat()))
    sp = FakeSpawner()
    c = await mk(sp)
    await c.post("/refresh", headers={**OK, "HX-Request": "true"})
    assert len(sp.calls) == 1
    assert str(os.getpid()) in paths["pid_path"].read_text()


async def test_refresh_old_pidfile_with_live_pid_is_stale(mk, paths):
    paths["pid_path"].write_text(_pidfile(os.getpid(), "2000-01-01T00:00:00+00:00"))
    sp = FakeSpawner()
    c = await mk(sp)
    await c.post("/refresh", headers={**OK, "HX-Request": "true"})
    assert len(sp.calls) == 1


async def test_refresh_worker_alive_does_not_spawn(mk, paths):
    from daily_job_tool.ops.heartbeat import write_heartbeat

    write_heartbeat(
        paths["heartbeat_path"],
        {"pid": 1, "in_flight": 3, "updated_at": datetime.now(UTC).isoformat()},
    )
    sp = FakeSpawner()
    c = await mk(sp)
    r = await c.post("/refresh", headers={**OK, "HX-Request": "true"})
    assert sp.calls == []
    assert "worker is running; it refreshes automatically" in r.text.lower()
    assert "3 in flight" in r.text


async def test_refresh_status_idle_done_and_last_refreshed(mk, db, engine, paths):
    c = await mk()
    assert "Last refreshed never" in (await c.get("/refresh/status")).text
    async with engine.begin() as conn:
        cid = await db.company("RC")
        sid = (
            await conn.execute(
                text(
                    "INSERT INTO sources (company_id, adapter, token) VALUES (:c,'fake_t1','rc') RETURNING id"
                ),
                {"c": cid},
            )
        ).scalar_one()
        await conn.execute(
            text(
                "INSERT INTO crawl_runs (source_id, adapter, started_at, finished_at, outcome, jobs_new) VALUES (:s,'fake_t1', now(), now(), 'ok', 4)"
            ),
            {"s": sid},
        )
    c = await mk()
    assert "Last refreshed" in (await c.get("/refresh/status")).text
    # a finished refresh (dead pid) reports Done on the polling request only
    paths["pid_path"].write_text(
        _pidfile(2147483646, datetime.now(UTC).replace(microsecond=0).isoformat())
    )
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE crawl_runs SET started_at = now() + interval '1 second'"))
    done = (await c.get("/refresh/status?poll=1")).text
    assert "Done: 4 new jobs" in done and "every 5s" not in done
    assert "Done:" not in (await c.get("/refresh/status")).text
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE crawl_runs"))


async def test_default_spawner_dryrun(monkeypatch):
    from daily_job_tool.web import refresh

    monkeypatch.setenv("DAILY_JOB_TOOL_REFRESH_DRYRUN", "1")
    assert refresh.default_spawner() is refresh.dryrun_spawner
    assert refresh.dryrun_spawner(["x"], refresh.Path("/nonexistent/l")) == 0
    monkeypatch.delenv("DAILY_JOB_TOOL_REFRESH_DRYRUN")
    assert refresh.default_spawner() is refresh.real_spawner


async def test_refresh_dryrun_writes_no_pidfile(mk, paths):
    from daily_job_tool.web.refresh import dryrun_spawner

    c = await mk(dryrun_spawner)
    r = await c.post("/refresh", headers={**OK, "HX-Request": "true"})
    assert "Dry run" in r.text and not paths["pid_path"].exists()


# ---- health, origin ----


async def test_health_sections(mk, db, engine):
    cid = await db.company("Held Co")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO sources (company_id, adapter, token, status) VALUES (:c,'fake_t1','h','blocked')"
            ),
            {"c": cid},
        )
    c = await mk()
    h = (await c.get("/health")).text
    for s in (
        "Worker and backups",
        "Last backup",
        "Blocked or paused sources",
        "Held Co",
        'href="/manual"',
        "not running",
    ):
        assert s in h


@pytest.mark.parametrize(
    ("path", "data"),
    [("/refresh", None), ("/manual/check", {"key": "manual_checked:zeta.com"}),
     ("/remove/confirm", {"target": "x"})],
)  # fmt: skip
@pytest.mark.parametrize("origin", [None, "http://evil.example", "http://127.0.0.1:9999"])
async def test_new_write_routes_reject_foreign_origin(mk, path, data, origin):
    sp = FakeSpawner()
    c = await mk(sp)
    r = await c.post(path, data=data, headers={"Origin": origin} if origin else {})
    assert r.status_code == 403 and sp.calls == []

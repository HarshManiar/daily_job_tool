"""'New since your last visit': pure rule with injected clock, then through the app."""

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import text

from daily_job_tool.web import visits
from daily_job_tool.web.app import create_app
from tests.web.test_pages import titles

T0 = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
H = {"Origin": "http://127.0.0.1:8000"}


def iso(d: datetime) -> str:
    return d.isoformat()


def test_effective_since_rules():
    es = visits.effective_since
    # first ever visit: nothing is "new"
    assert es({}, T0) == (None, True)
    # same-session reload: baseline unchanged
    st = {visits.K_VISIT: iso(T0), visits.K_ACTIVITY: iso(T0 + timedelta(hours=3))}
    assert es(st, T0 + timedelta(hours=5)) == (T0, False)
    # exactly 12 h later is still the same session; just over rolls to last activity
    assert es(st, T0 + timedelta(hours=15)) == (T0, False)
    since, rolls = es(st, T0 + timedelta(hours=15, minutes=1))
    assert rolls and since == T0 + timedelta(hours=3)
    # garbage values are treated as absent
    assert es({visits.K_ACTIVITY: "nope"}, T0) == (None, True)


async def test_touch_and_mark_seen_persist(engine, db):
    async def state():
        async with engine.connect() as c:
            return await visits.read_state(c)

    async def do(fn, now):
        async with engine.begin() as c:
            await fn(c, now)

    await do(visits.touch, T0)  # first visit
    s = await state()
    assert s[visits.K_VISIT] == iso(T0) and s[visits.K_ACTIVITY] == iso(T0)
    await do(visits.touch, T0 + timedelta(hours=2))  # reload same day
    s = await state()
    assert s[visits.K_VISIT] == iso(T0) and s[visits.K_ACTIVITY] == iso(T0 + timedelta(hours=2))
    day2 = T0 + timedelta(days=1)
    await do(visits.touch, day2)  # next day: baseline = last activity of day 1
    s = await state()
    assert s[visits.K_VISIT] == iso(T0 + timedelta(hours=2)) and s[visits.K_ACTIVITY] == iso(day2)
    await do(visits.mark_all_seen, day2 + timedelta(hours=1))
    s = await state()
    assert s[visits.K_VISIT] == iso(day2 + timedelta(hours=1))


async def test_app_flow_with_clock(engine, db):
    # Real "now" drives first_seen_at in the DB; the injected clock only drives the rule.
    real = datetime.now(UTC)
    clock = [real]
    app = create_app(engine, clock=lambda: clock[0])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
    ) as cl:
        c = await db.company("Vis")
        await db.job(c, title="old-job", age_days=3)

        r = await cl.get("/")  # first visit: no banner, no badges
        assert "new since" not in r.text and "chip new" not in r.text
        assert (await cl.post("/visit/touch", headers=H)).status_code == 204

        await db.job(c, title="fresh-job", age_days=0.0001)  # appears after the baseline
        clock[0] = real + timedelta(minutes=30)
        for _ in range(2):  # reload twice: markers persist, nothing advances on GET
            r = await cl.get("/")
            assert "<strong>1</strong> new since" in r.text
            assert r.text.count("chip new") == 1
            await cl.post("/visit/touch", headers=H)

        # next day: previous activity is the baseline; fresh-job predates it -> not new
        clock[0] = real + timedelta(days=1)
        r = await cl.get("/")
        assert "chip new" not in r.text
        await db.job(c, title="day2-job", age_days=0.0001)
        # day2-job's first_seen is "real now"; baseline (activity at real+30m) is later than it
        # on this timeline, so shift baseline via mark-all-seen semantics instead:
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE jobs SET first_seen_at = now() + interval '1 hour' "
                     "WHERE title = 'day2-job'")
            )  # fmt: skip
        clock[0] = real + timedelta(days=1)
        r = await cl.get("/")
        assert "<strong>1</strong> new since" in r.text and "day2-job" in r.text

        seen = await cl.post("/visit/seen", data={"next": "/?tier=any"}, headers=H)
        assert seen.status_code == 303 and seen.headers["location"] == "/?tier=any"
        r = await cl.get("/")
        assert "chip new" not in r.text and "Nothing new since" in r.text
        assert titles(r.text)  # feed still renders


async def test_seen_redirect_is_local_only(client, db):
    for nxt in ("//evil.example", "https://evil.example", "/\\evil.example"):
        r = await client.post("/visit/seen", data={"next": nxt}, headers=H)
        assert r.status_code == 303 and r.headers["location"] == "/"

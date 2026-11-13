"""'Posted last 24/48 hours' boxes: windows, override of days, counts, count cache."""

from daily_job_tool.web.cache import TTLCache
from tests.web.test_pages import titles


async def test_24_48_windows_and_override(client, db):
    c = await db.company("Fresh")
    await db.job(c, title="h3", age_days=3 / 24)
    await db.job(c, title="h30", age_days=30 / 24)
    await db.job(c, title="h60", age_days=60 / 24)
    await db.job(c, title="d10", age_days=10)

    async def t(qs):
        return set(titles((await client.get("/?" + qs)).text))

    assert await t("") == {"h3", "h30", "h60", "d10"}
    assert await t("posted24=1") == {"h3"}
    assert await t("posted48=1") == {"h3", "h30"}
    assert await t("posted24=1&posted48=1") == {"h3", "h30"}  # both = 48 h
    assert await t("posted24=1&days=30") == {"h3"}  # overrides days
    assert await t("posted48=1&days=7") == {"h3", "h30"}
    assert await t("days=7") == {"h3", "h30", "h60"}
    assert (await client.get("/api/v1/jobs?posted24=1")).json()["total"] == 1


async def test_counts_respect_other_filters_and_ignore_days(client, db):
    c = await db.company("Cnt")
    await db.job(c, title="a", age_days=0.1, remote_type="remote")
    await db.job(c, title="b", age_days=1.5, remote_type="remote")
    await db.job(c, title="c", age_days=0.1, remote_type="onsite")
    await db.job(c, title="d", age_days=0.1, remote_type="remote", closed=True)
    r = await client.get("/")
    assert 'id="cnt24"' in r.text
    assert ">2</span>" in r.text.split('id="cnt24"')[1][:80]
    assert ">3</span>" in r.text.split('id="cnt48"')[1][:80]
    r = await client.get("/?remote=remote&days=7&posted24=1")
    assert ">1</span>" in r.text.split('id="cnt24"')[1][:80]
    assert ">2</span>" in r.text.split('id="cnt48"')[1][:80]


async def test_counts_refreshed_out_of_band_on_htmx_swap(client, db):
    c = await db.company("Oob")
    await db.job(c, title="x", age_days=0.1)
    r = await client.get("/?posted24=1", headers={"HX-Request": "true", "HX-Target": "browse"})
    assert 'id="cnt24"' in r.text and "<html" not in r.text and 'id="browse"' in r.text


async def test_count_cache_ttl_with_fake_clock():
    now = [0.0]
    cache = TTLCache(30, clock=lambda: now[0])
    calls = []

    async def make():
        calls.append(1)
        return len(calls)

    assert await cache.get_or_set("k", make) == 1
    now[0] = 29
    assert await cache.get_or_set("k", make) == 1
    now[0] = 31
    assert await cache.get_or_set("k", make) == 2


async def test_count_cache_used_by_app(client, db):
    c = await db.company("Cache")
    await db.job(c, title="x", age_days=0.1)
    assert ">1</span>" in (await client.get("/")).text.split('id="cnt24"')[1][:80]
    await db.job(c, title="y", age_days=0.1)
    # still cached (30 s): stale count is acceptable by design
    assert ">1</span>" in (await client.get("/")).text.split('id="cnt24"')[1][:80]

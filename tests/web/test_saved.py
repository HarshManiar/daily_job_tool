import json

from sqlalchemy import text

from daily_job_tool.web import saved
from tests.web.test_pages import titles

H = {"Origin": "http://127.0.0.1:8000"}


async def _rows(db):
    async with db.engine.connect() as c:
        return [
            dict(r)
            for r in (await c.execute(text("SELECT * FROM saved_searches ORDER BY id"))).mappings()
        ]


async def test_crud_flow_and_counts(client, db):
    c = await db.company("Sv")
    await db.job(c, title="Remote one", remote_type="remote", age_days=0.5)
    await db.job(c, title="Remote two", remote_type="remote", age_days=3)
    await db.job(c, title="Onsite", remote_type="onsite")

    r = await client.post("/saved", data={"name": "  My  remote ", "qs": "remote=remote&tags=sql"},
                          headers=H)  # fmt: skip
    assert r.status_code == 303 and r.headers["location"] == "/?remote=remote&tags=sql"
    (row,) = await _rows(db)
    assert row["name"] == "My remote" and row["query"] == {"remote": "remote", "tags": ["sql"]}

    # no tag filter match: use a saved search without tags for count checks
    await client.post("/saved", data={"name": "Remote", "qs": "remote=remote"}, headers=H)
    page = (await client.get("/")).text
    assert "Saved searches" in page and 'href="/?remote=remote"' in page
    assert 'title="matches">2<' in page  # live count in the Jobs popover
    manage = (await client.get("/saved")).text
    assert "2 matches" in manage

    sid = (await _rows(db))[1]["id"]
    r = await client.post(f"/saved/{sid}/rename", data={"name": "Renamed"}, headers=H)
    assert r.status_code == 303
    assert "Renamed" in (await client.get("/")).text
    assert (
        await client.post("/saved/999/rename", data={"name": "x"}, headers=H)
    ).status_code == 404

    assert (await client.post(f"/saved/{sid}/delete", headers=H)).status_code == 303
    assert len(await _rows(db)) == 1
    assert (
        await client.delete(f"/saved/{(await _rows(db))[0]['id']}", headers=H)
    ).status_code == 303
    assert await _rows(db) == []
    assert (await client.post("/saved/1/delete", headers=H)).status_code == 404
    assert titles((await client.get("/?remote=remote")).text) == ["Remote one", "Remote two"]


async def test_new_count_per_saved_search(client, db):
    c = await db.company("SvNew")
    await db.job(c, title="n1", remote_type="remote", age_days=0.01)
    async with db.engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO saved_searches (name, query) VALUES ('s', '{\"remote\": \"remote\"}')"
            )
        )
        await conn.execute(
            text("INSERT INTO app_state VALUES ('last_visit_at', now() - interval '1 day'),"
                 " ('last_activity_at', now() - interval '1 minute')")
        )  # fmt: skip
    page = (await client.get("/")).text
    assert "1 match" in page and "1 new" in page


async def test_create_validation(client, db):
    assert (
        await client.post("/saved", data={"name": "  ", "qs": ""}, headers=H)
    ).status_code == 422
    assert (
        await client.post("/saved", data={"name": "x", "qs": "tier=bogus"}, headers=H)
    ).status_code == 422
    assert (await client.post("/saved", data={"name": "x" * 500}, headers=H)).status_code == 422
    # long names are trimmed to 80 chars; paging/reason are never stored
    r = await client.post(
        "/saved", data={"name": "y" * 79, "qs": "offset=50&reason=a&tier=1"}, headers=H
    )
    assert r.status_code == 303
    assert (await _rows(db))[0]["query"] == {}  # tier is no longer a filter: never stored


async def test_invalid_and_unknown_stored_queries(client, db):
    async with db.engine.begin() as conn:
        for name, q in [
            ("future", {"tier": "1", "some_new_field": [1, 2]}),
            ("bad-enum", {"tier": "bogus"}),
            ("bad-type", ["not", "an", "object"]),
            ("bad-tag", {"tags": ["Dro;p"]}),
        ]:
            await conn.execute(
                text("INSERT INTO saved_searches (name, query) VALUES (:n, CAST(:q AS jsonb))"),
                {"n": name, "q": json.dumps(q)},
            )
    r = await client.get("/")
    assert r.status_code == 200
    assert 'href="/"' in r.text  # tier and unknown fields ignored: "future" still loads
    assert r.text.count("skipped:") == 2  # invalid ones skipped with a message
    manage = (await client.get("/saved")).text
    assert manage.count("skipped: tier is no longer a filter") == 2  # once per old query
    assert manage.count("skipped:") == 4  # 2 invalid + 2 tier notes


def test_parse_query_units():
    f, err, note = saved.parse_query_note('{"tier": "2", "targets_only": true, "zzz": 1}')
    assert f is not None and f.tier == "any" and err == ""
    assert note == "skipped: tier is no longer a filter"
    assert saved.parse_query_note({"remote": "remote"})[2] == ""
    f, err = saved.parse_query('{"zzz": 1}')
    assert f is not None and err == ""
    assert saved.parse_query("{not json")[0] is None
    assert saved.parse_query('"str"')[0] is None
    f, err = saved.parse_query({"min_pay": -5})
    assert f is None and "min_pay" in err
    f, _ = saved.parse_query({"offset": 40, "reason": "x"})
    assert f.offset == 0 and f.reason == ""


async def test_limit_on_saved_searches(client, db):
    async with db.engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO saved_searches (name, query) SELECT 's' || g, '{}' "
                 "FROM generate_series(1, :n) g"), {"n": saved.MAX_SAVED}
        )  # fmt: skip
    assert (await client.post("/saved", data={"name": "one more"}, headers=H)).status_code == 409

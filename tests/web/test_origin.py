import pytest

POSTS = [
    ("POST", "/visit/touch", None),
    ("POST", "/visit/seen", {"next": "/"}),
    ("POST", "/saved", {"name": "x", "qs": ""}),
    ("POST", "/saved/1/rename", {"name": "x"}),
    ("POST", "/saved/1/delete", None),
    ("DELETE", "/saved/1", None),
    ("POST", "/", None),  # even routes that do not exist: rejected before routing
]


@pytest.mark.parametrize("origin", [None, "http://evil.example", "http://127.0.0.1:9999",
                                    "null", "http://127.0.0.1.evil.example:8000",
                                    "ftp://127.0.0.1:8000"])  # fmt: skip
@pytest.mark.parametrize(("method", "path", "data"), POSTS)
async def test_foreign_or_missing_origin_403(client, db, method, path, data, origin):
    headers = {"Origin": origin} if origin else {}
    r = await client.request(method, path, data=data, headers=headers)
    assert r.status_code == 403


@pytest.mark.parametrize("hdr", [
    {"Origin": "http://127.0.0.1:8000"},
    {"Origin": "http://localhost:8000"},
    {"Origin": "http://[::1]:8000"},
    {"Referer": "http://127.0.0.1:8000/?tier=1"},
])  # fmt: skip
async def test_loopback_origin_or_referer_allowed(client, db, hdr):
    r = await client.post("/visit/touch", headers=hdr)
    assert r.status_code == 204


async def test_origin_wins_over_good_referer(client, db):
    r = await client.post("/visit/touch", headers={"Origin": "http://evil.example",
                          "Referer": "http://127.0.0.1:8000/"})  # fmt: skip
    assert r.status_code == 403


async def test_gets_need_no_origin_and_no_cookies(client, db):
    r = await client.get("/")
    assert r.status_code == 200 and "set-cookie" not in r.headers
    assert "default-src 'self'" in r.headers["content-security-policy"]
    # forbidden host header is still rejected first
    r = await client.get("/", headers={"Host": "evil.example"})
    assert r.status_code == 400


async def test_rw_engine_is_separate_and_ro_conn_cannot_write(client, db):
    app = client._transport.app
    assert app.state.rw_engine is not None
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from daily_job_tool.web.deps import get_conn

    class Req:
        pass

    req = Req()
    req.app = app
    gen = get_conn(req)
    conn = await gen.__anext__()
    with pytest.raises(DBAPIError):
        await conn.execute(text("INSERT INTO app_state VALUES ('x', 'y')"))
    await gen.aclose()

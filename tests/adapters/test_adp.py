import json
from datetime import UTC, datetime

import pytest

from daily_job_tool.adapters.adp import AdpAdapter
from daily_job_tool.http.errors import NotFound
from daily_job_tool.pipeline.ingest import payload_hash
from tests.adapters._helpers import fixture_client, fixture_json, mock_client, src

A = AdpAdapter()
CYTEK = "a21467f2-5c74-46bf-bd5c-61cdb598d8b3"
KIND = "4b9cd2ac-91a0-403a-8081-1c5a825120fc"


@pytest.mark.parametrize(("token", "n"), [(CYTEK, 14), (KIND, 9)])
async def test_fixture_boards(token, n):
    async with fixture_client("adp", token) as c:
        res = await A.fetch_jobs(src("adp", token), c)
    assert res.complete and len(res.jobs) == n
    for j in res.jobs:
        assert j.needs_detail and j.title and token in j.url and j.posted_at
        assert json.loads(json.dumps(j.payload)) == j.payload
        assert "CurrentServerDateTime" not in json.dumps(j.payload)
        assert "numberFields" not in json.dumps(j.payload)


async def test_list_pay_and_dates():
    async with fixture_client("adp", CYTEK) as c:
        res = await A.fetch_jobs(src("adp", CYTEK), c)
    j = next(x for x in res.jobs if x.external_id == "9205143384348_34")
    assert (j.salary.min, j.salary.max, j.salary.currency, j.salary.period) == (
        25,
        40,
        "USD",
        "hour",
    )
    assert j.posted_at == datetime(2026, 11, 10, 0, 8, tzinfo=UTC)
    assert j.locations_raw == ["Fremont, CA, US"]
    assert j.url.endswith("&jobId=585125")
    k = next(x for x in res.jobs if x.external_id == "9205103385153_2")
    assert k.salary.period == "year" and k.employment_type_raw == "Full Time"


async def test_detail_and_round_trip():
    s = src("adp", CYTEK)
    async with fixture_client("adp", CYTEK) as c:
        res = await A.fetch_jobs(s, c)
        job = next(x for x in res.jobs if x.external_id == "9205143384348_34")
        d = await A.fetch_detail(s, job, c)
    assert d.external_id == job.external_id and not d.needs_detail
    assert d.description_html and "Cytek" in d.description_html
    assert d.salary == job.salary and A._map(d.payload) == d


async def test_detail_empty_shell_is_not_found():
    s = src("adp", CYTEK)
    async with fixture_client("adp", CYTEK) as c:
        job = (
            (await A.fetch_jobs(s, c)).jobs[0].model_copy(update={"external_id": "0000000000000_1"})
        )
        with pytest.raises(NotFound):
            await A.fetch_detail(s, job, c)


async def test_stable_across_days():
    async with fixture_client("adp", CYTEK) as c:
        a = await A.fetch_jobs(src("adp", CYTEK), c)
    raw = fixture_json("adp", CYTEK)
    for r in raw["jobRequisitions"]:  # simulate the next day's volatile values
        for f in r["customFieldGroup"]["dateFields"]:
            if f["nameCode"]["codeValue"] == "CurrentServerDateTime":
                f["dateValue"] = "2026-11-13T09:00Z"
        for f in r["customFieldGroup"]["numberFields"]:
            f["numberValue"] = 99.0
    import httpx

    def h(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, json=raw)

    async with mock_client(h) as c:
        b = await A.fetch_jobs(src("adp", CYTEK), c)
    assert [payload_hash(j.payload) for j in a.jobs] == [payload_hash(j.payload) for j in b.jobs]


async def test_pagination_and_malformed():
    import httpx

    seen = []

    def rec(i):
        return {"itemID": f"{i}_1", "requisitionTitle": f"T{i}"}

    def h(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        skip = int(req.url.params["$skip"])
        seen.append(skip)
        items = [rec(i) for i in range(skip, min(skip + 20, 25))]
        if skip == 0:
            items.append({"itemID": "bad"})
        return httpx.Response(200, json={"jobRequisitions": items, "meta": {"totalNumber": 25}})

    async with mock_client(h) as c:
        res = await A.fetch_jobs(src("adp", "abc"), c)
    assert seen[:2] == [0, 21] and len(res.jobs) == 24 and res.complete
    assert len(res.warnings) == 1


async def test_empty_and_incomplete():
    import httpx

    def h(total):
        def f(req):
            if req.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(200, json={"jobRequisitions": [], "meta": {"totalNumber": total}})

        return f

    async with mock_client(h(0)) as c:
        res = await A.fetch_jobs(src("adp", "abc"), c)
    assert res.jobs == [] and res.complete
    async with mock_client(h(5)) as c:
        res = await A.fetch_jobs(src("adp", "abc"), c)
    assert not res.complete


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            f"https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid={CYTEK}&ccId=19000101_000001&lang=en_US",
            (CYTEK, {}),
        ),
        (f"https://recruiting.adp.com/x?cid={KIND}&ccId=123", (KIND, {"ccId": "123"})),
    ],
)
def test_match_url(url, expected):
    assert A.match_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://workforcenow.adp.com/x",
        "https://workforcenow.adp.com/x?cid=nope",
        f"https://example.com/?cid={CYTEK}",
    ],
)
def test_match_url_none(url):
    assert A.match_url(url) is None

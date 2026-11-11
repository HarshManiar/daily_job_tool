import pytest
from pydantic import ValidationError

from firsthand.adapters import registry
from firsthand.adapters.base import Adapter, FetchResult, RawJob, SalaryRaw, SourceRef


def test_rawjob_defaults_and_required():
    j = RawJob(external_id="1", title="Eng", url="https://x/1", payload={"a": 1})
    assert j.locations_raw == [] and j.needs_detail is False and j.salary is None
    j.locations_raw.append("NYC")
    assert RawJob(external_id="2", title="t", url="u", payload={}).locations_raw == []
    with pytest.raises(ValidationError):
        RawJob(external_id="1", title="Eng", url="https://x/1")  # type: ignore[call-arg]


def test_fetchresult_and_salary():
    r = FetchResult(jobs=[], complete=True)
    assert r.warnings == [] and r.board_name is None
    assert SalaryRaw(min=1, max=2, currency="USD").text is None
    with pytest.raises(ValidationError):
        FetchResult(jobs=[])  # type: ignore[call-arg]


def test_sourceref_defaults():
    s = SourceRef(adapter="greenhouse", token="warp")
    assert s.id is None and s.config == {}


class Dummy(Adapter):
    name = "dummy_test"
    tier = 1
    hosts = ["dummy.test"]

    async def fetch_jobs(self, src, http):
        return FetchResult(jobs=[], complete=True)


async def test_adapter_defaults():
    d = Dummy()
    job = RawJob(external_id="1", title="t", url="u", payload={})
    assert await d.fetch_detail(SourceRef(adapter="d", token="t"), job, None) is job  # type: ignore[arg-type]
    assert Dummy.match_url("https://x") is None
    with pytest.raises(TypeError):
        Adapter()  # type: ignore[abstract]


def test_registry_register_get():
    try:
        assert registry.register(Dummy) is Dummy
        assert isinstance(registry.get_adapter("dummy_test"), Dummy)
        assert any(isinstance(a, Dummy) for a in registry.all_adapters())
        with pytest.raises(KeyError):
            registry.get_adapter("nope")
    finally:
        registry._REGISTRY.pop("dummy_test", None)

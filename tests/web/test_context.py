import pytest

from daily_job_tool.match.profile import Profile, load_profile
from daily_job_tool.web.routes import context as ctx_route

PHARMA = "tests/fixtures/profile.pharma.yaml"


@pytest.fixture
def use_profile(monkeypatch, tmp_path):
    def apply(profile: Profile, path):
        monkeypatch.setattr(ctx_route, "get_profile", lambda: profile)
        monkeypatch.setattr(ctx_route, "profile_path", lambda: path)

    return apply


async def test_context_renders_sections(client, use_profile):
    from pathlib import Path

    use_profile(load_profile(PHARMA), Path("data/profile.yaml"))
    r = await client.get("/context")
    assert r.status_code == 200
    h = r.text
    for s in ("Target titles", "Hard filters", "Visa", "Location priority", "Industry priority",
              "Skills", "Scoring weights", "Tier 1", "<details", "make rescore"):  # fmt: skip
        assert s in h, s
    assert "New Jersey" in h and "data/profile.yaml" in h
    assert "example profile" not in h
    assert 'href="/context"' in h and 'aria-current="page"' in h
    assert "<script" not in h.split("<main")[1]


async def test_context_example_warning_and_escaping(client, use_profile):
    from pathlib import Path

    p = Profile.model_validate({"target_titles": {"tier1": ["<script>alert(1)</script>"]}})
    use_profile(p, Path("data/profile.example.yaml"))
    h = (await client.get("/context")).text
    assert "Using the example profile" in h
    assert "<script>alert(1)" not in h and "&lt;script&gt;" in h


async def test_context_without_profile(client, use_profile):
    use_profile(Profile(), None)
    r = await client.get("/context")
    assert r.status_code == 200 and "No profile file found" in r.text

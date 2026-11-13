"""Profile-driven knobs: defaults equal the old hardcoded values (owner behaviour unchanged)."""

import pytest
from pydantic import ValidationError

from daily_job_tool.match.engine import location_rank, match
from daily_job_tool.match.fit import DEFAULT_SKILL_ALIASES, fit_score, skill_rx
from daily_job_tool.match.profile import (
    DEFAULT_BLOCKED_LEVELS,
    Profile,
    load_profile,
)
from daily_job_tool.match.titles import classify_title
from daily_job_tool.normalize.models import CompanyInfo, NormalizedJob

OWNER_LOCS = [
    {"name": "nj", "states": ["NJ"]},
    {"name": "nyc", "cities": ["New York", "Brooklyn", "Jersey City"]},
    {"name": "us_remote", "remote_regions": ["US", "NA", "AMER", "WORLD"]},
]
OWNER = Profile.model_validate({"location_priority": OWNER_LOCS})


def old_location_rank(job: NormalizedJob, profile: Profile) -> int:
    """The pre-refactor implementation, kept verbatim as the reference."""
    from daily_job_tool.normalize.text import fold

    nj, nyc, rem = profile.location("nj"), profile.location("nyc"), profile.location("us_remote")
    states = {s.upper() for s in (nj.states if nj else ["NJ"])}
    cities = {fold(c) for c in (nyc.cities if nyc else [])}
    regions = {r.upper() for r in (rem.remote_regions if rem else [])} | {"NORTH_AMERICA"}
    locs = [loc for loc in job.locations if loc.get("country") == "US"]
    if any((loc.get("admin1") or "").upper() in states for loc in locs):
        return 0
    if any(fold(loc.get("city") or "") in cities for loc in locs):
        return 1
    if job.remote_type == "remote" and regions & {r.upper() for r in job.remote_regions}:
        return 2
    return 3


def J(locs, remote_type="onsite", regions=()):
    return NormalizedJob(
        title="Data Analyst", title_norm="data analyst", locations=locs,
        remote_type=remote_type, remote_regions=list(regions),
    )  # fmt: skip


SAMPLES = [
    J([{"city": "Edison", "admin1": "NJ", "country": "US"}]),
    J([{"city": "New York", "admin1": "NY", "country": "US"}]),
    J([{"city": "Brooklyn", "admin1": "NY", "country": "US"}]),
    J([{"city": "Austin", "admin1": "TX", "country": "US"}]),
    J([{"city": "Austin", "admin1": "TX", "country": "US"}], "remote", ["US"]),
    J([], "remote", ["NORTH_AMERICA"]),
    J([], "remote", ["EU"]),
    J([{"city": "Newark", "admin1": "nj", "country": "US"}], "remote", ["US"]),
    J([{"city": "Paris", "admin1": "", "country": "FR"}]),
    J([{"city": "Paris", "admin1": "NJ", "country": "FR"}]),
    J([]),
]


@pytest.mark.parametrize("job", SAMPLES)
def test_location_rank_matches_old_for_owner_profile(job):
    assert location_rank(job, OWNER) == old_location_rank(job, OWNER)


def test_location_rank_is_index_and_other_is_len():
    p = Profile.model_validate({"location_priority": [{"name": "tx", "states": ["TX"]}]})
    assert location_rank(SAMPLES[3], p) == 0
    assert location_rank(SAMPLES[0], p) == 1


def test_empty_location_priority_is_neutral():
    p = Profile()
    assert location_rank(SAMPLES[0], p) == 0  # len([]) == 0: nothing outranks anything


def test_location_labels():
    p = Profile.model_validate(
        {"location_priority": [*OWNER_LOCS, {"name": "bay_area", "label": "Bay Area"}]}
    )
    assert [e.display_label for e in p.location_priority] == [
        "New Jersey", "NYC area", "US remote", "Bay Area",
    ]  # fmt: skip
    assert (
        Profile.model_validate({"location_priority": [{"name": "pnw_x"}]})
        .location_priority[0]
        .display_label
        == "Pnw X"
    )


def test_scoring_defaults_and_validation():
    s = Profile().scoring
    assert (
        s.tier_points == [4, 3, 2] and s.skills_max == 2 and s.location_points == [1, 0.75, 0.5, 0]
    )
    assert (s.years_in_range, s.years_unknown_or_low, s.years_over_max, s.industry) == (2, 1, 0, 1)
    with pytest.raises(ValidationError):
        Profile.model_validate({"scoring": {"tier_points": [99]}})
    with pytest.raises(ValidationError):
        Profile.model_validate({"scoring": {"industry": "1; DROP TABLE jobs"}})


def test_skill_aliases_default_and_override():
    assert skill_rx("power bi").search("we use PowerBI daily")
    assert not skill_rx("power bi", {"power bi": "pbi"}).search("we use PowerBI daily")
    assert skill_rx("power bi", {"power bi": "pbi"}).search("we use PBI daily")
    p = Profile.model_validate(
        {"skill_aliases": {"Rust": "rust(?:lang)?"}, "profile": {"strong_skills": ["rust"]}}
    )
    assert fit_score("experience with Rustlang", p) == 1
    assert "dbt" in DEFAULT_SKILL_ALIASES
    with pytest.raises(ValidationError):
        Profile.model_validate({"skill_aliases": {"x": "("}})


def test_blocked_levels_default_and_override():
    assert set(Profile().target_titles.blocked_levels) == set(DEFAULT_BLOCKED_LEVELS)
    base = {"target_titles": {"tier1": ["Data Analyst"]}}
    assert classify_title("manager data analyst", Profile.model_validate(base)).tier is None
    free = {"target_titles": {"tier1": ["Data Analyst"], "blocked_levels": []}}
    assert classify_title("manager data analyst", Profile.model_validate(free)).tier == 1


def test_detail_regex_default_and_override():
    from daily_job_tool.adapters.workday import DEFAULT_DETAIL_TITLE_REGEX

    assert Profile().crawl.detail_title_regex == DEFAULT_DETAIL_TITLE_REGEX
    assert (
        Profile.model_validate({"crawl": {"detail_title_regex": "nurse"}}).crawl.detail_title_regex
        == "nurse"
    )
    with pytest.raises(ValidationError):
        Profile.model_validate({"crawl": {"detail_title_regex": "("}})


def test_adapters_use_profile_regex(monkeypatch):
    from daily_job_tool.adapters import workday
    from daily_job_tool.match import profile as pm

    monkeypatch.setattr(
        pm, "get_profile", lambda: Profile.model_validate({"crawl": {"detail_title_regex": "zzz"}})
    )
    assert workday.default_detail_title_regex() == "zzz"

    def boom():
        raise RuntimeError

    monkeypatch.setattr(pm, "get_profile", boom)
    assert workday.default_detail_title_regex() == workday.DEFAULT_DETAIL_TITLE_REGEX


def test_visa_default_is_off():
    p = Profile.model_validate(
        {"hard_filters": {"visa": {"exclude_if_phrases": ["no sponsorship"]}}}
    )
    assert p.hard_filters.visa.status == "none" and not p.visa_filters_on
    assert p.visa_phrases().exclude == ()
    j = NormalizedJob(
        title="Data Analyst", title_norm="data analyst", employment_type="full_time",
        visa_status="not_available",
    )  # fmt: skip
    assert match(j, CompanyInfo(name="A"), p).status != "excluded"
    on = load_profile("tests/fixtures/profile.pharma.yaml")
    assert (
        on.visa_filters_on and "visa:not_available" in match(j, CompanyInfo(name="A"), on).reasons
    )

from datetime import UTC, datetime

import pytest

from daily_job_tool.match.engine import match
from daily_job_tool.match.profile import Profile
from daily_job_tool.normalize.models import CompanyInfo, NormalizedJob

NOW = datetime(2026, 11, 1, tzinfo=UTC)
GOOD_DESC = "SQL Python PySpark Databricks"


def J(**kw) -> NormalizedJob:
    base = dict(
        title="Data Analyst", title_norm="data analyst", employment_type="full_time",
        posted_at=NOW, description_text=GOOD_DESC,
        locations=[{"city": "Edison", "admin1": "NJ", "country": "US"}],
    )  # fmt: skip
    base.update(kw)
    return NormalizedJob(**base)


def C(**kw) -> CompanyInfo:
    return CompanyInfo(name=kw.pop("name", "Acme"), **kw)


def test_clean_include(profile):
    r = match(J(), C(), profile)
    assert (r.tier, r.status, r.reasons) == (2, "include", [])
    assert r.location_rank == 0 and r.industry_rank == 1 and r.fit_score == 4


@pytest.mark.parametrize(
    "kw,reason",
    [
        ({"employment_type": "unknown"}, "employment:unknown"),
        ({"salary_max_usd": 90000}, "pay:below_min"),
        ({"posted_at": None}, "date:undated"),
        ({"norm_flags": ["visa:export_control"]}, "visa:export_control"),
        ({"norm_flags": ["visa:opt_only"]}, "visa:opt_only"),
        ({"norm_flags": ["employment:conflict"]}, "employment:conflict"),
        ({"years_min": 9, "norm_flags": ["years:nonreq", "years:maybe_7+"]}, "years:maybe_7+"),
    ],
)
def test_attention_flags(profile, kw, reason):
    r = match(J(**kw), C(), profile)
    assert r.status == "flagged" and r.reasons == [reason]


@pytest.mark.parametrize(
    "kw,reason",
    [
        ({"visa_status": "offered"}, "visa:offered"),
        ({"title_norm": "brand manager"}, "title:not_target"),
        ({"title_norm": "staff data engineer"}, "title:level_out_of_range"),
    ],
)
def test_informational_reasons_stay_include(profile, kw, reason):
    r = match(J(**kw), C(), profile)
    assert r.status == "include" and r.reasons == [reason]


def test_years_required_pool_excludes_nonrequired_does_not(profile):
    assert match(J(years_min=8), C(), profile).status == "excluded"
    r = match(J(years_min=8, norm_flags=["years:nonreq"]), C(), profile)
    assert r.status == "flagged" and "years:min_7+" not in r.reasons


def test_pay_ok_and_unknown(profile):
    assert match(J(salary_max_usd=150000), C(), profile).status == "include"
    assert match(J(salary_max_usd=None), C(), profile).status == "include"


def test_tier3_needs_fit(profile):
    weak = match(J(title_norm="product analyst", description_text="SQL"), C(), profile)
    assert weak.tier is None and weak.reasons == ["title:tier3_weak_fit"]
    strong = match(J(title_norm="product analyst"), C(), profile)
    assert strong.tier == 3 and strong.status == "include"


def test_excluded_wins_over_flags(profile):
    r = match(J(employment_type="contract", visa_status="offered", posted_at=None), C(), profile)
    assert r.status == "excluded"
    assert r.reasons[0] == "employment:contract" and "visa:offered" in r.reasons


def test_industry_rank(profile):
    for ind in ("Pharma/Biotech", "health tech", "HEALTHCARE DATA", "pbm/health plan"):
        assert match(J(), C(industry=ind), profile).industry_rank == 0
    assert match(J(), C(industry="fintech"), profile).industry_rank == 1
    assert match(J(), C(), profile).industry_rank == 1


def loc(city=None, admin1=None, country="US"):
    return {"city": city, "admin1": admin1, "country": country}


@pytest.mark.parametrize(
    "kw,rank",
    [
        ({"locations": [loc("Edison", "NJ")]}, 0),
        ({"locations": [loc("Jersey City", "NJ")]}, 0),
        ({"locations": [loc("Brooklyn", "NY")]}, 1),
        ({"locations": [loc("New York", "NY")]}, 1),
        ({"locations": [loc("Austin", "TX")]}, 3),
        ({"locations": [loc(None, None)], "remote_type": "remote", "remote_regions": ["US"]}, 2),
        ({"locations": [], "remote_type": "remote", "remote_regions": ["WORLD"]}, 2),
        (
            {"locations": [loc("Austin", "TX")], "remote_type": "remote", "remote_regions": ["US"]},
            2,
        ),
        (
            {"locations": [loc("Newark", "NJ")], "remote_type": "remote", "remote_regions": ["US"]},
            0,
        ),
        ({"locations": [loc("Austin", "TX"), loc("Edison", "NJ")]}, 0),
        (
            {
                "locations": [loc("Toronto", "ON", "CA")],
                "remote_type": "remote",
                "remote_regions": ["EU"],
            },
            3,
        ),
    ],
)
def test_location_rank(profile, kw, rank):
    assert match(J(**kw), C(), profile).location_rank == rank


def test_empty_profile_is_permissive():
    r = match(J(), C(), Profile())
    assert r.status in {"include", "flagged"} and r.tier is None

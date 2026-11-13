"""Location filter driven by the profile; SCORE_SQL equals the old hardcoded SQL by default."""

import itertools

import pytest
from sqlalchemy import text

from daily_job_tool.match.profile import Profile, load_profile
from daily_job_tool.web import format as fmt
from daily_job_tool.web import queries
from daily_job_tool.web.queries import FeedFilters, build_score_sql

OLD_SCORE = """LEAST(10, (
    CASE j.match_tier WHEN 1 THEN 4 WHEN 2 THEN 3 WHEN 3 THEN 2 ELSE 0 END
  + LEAST(2, GREATEST(0, j.fit_score) / 2.0)
  + CASE WHEN j.years_min IS NULL THEN 1 WHEN j.years_min >= 7 THEN 0
         WHEN j.years_min BETWEEN 2 AND 6 THEN 2 ELSE 1 END
  + CASE j.location_rank WHEN 0 THEN 1 WHEN 1 THEN 0.75 WHEN 2 THEN 0.5 ELSE 0 END
  + CASE WHEN j.industry_rank = 0 THEN 1 ELSE 0 END))::numeric(4,1)"""
PHARMA = load_profile("tests/fixtures/profile.pharma.yaml")


async def test_default_score_sql_equals_old(engine):
    rows = ",".join(
        f"({t if t else 'NULL'}::int, {f}, {y if y is not None else 'NULL'}::int, {r}, {i})"
        for t, f, y, r, i in itertools.product(
            [None, 1, 2, 3], [-5, 0, 1, 3, 4, 20], [None, 0, 1, 2, 6, 7, 12], [0, 1, 2, 3], [0, 1]
        )
    )
    sql = (
        "SELECT count(*), count(*) FILTER (WHERE {old} IS DISTINCT FROM {new}) FROM "
        f"(VALUES {rows}) j(match_tier, fit_score, years_min, location_rank, industry_rank)"
    ).format(old=OLD_SCORE, new=build_score_sql(PHARMA))
    async with engine.connect() as c:
        total, bad = (await c.execute(text(sql))).one()
    assert total == 4 * 6 * 7 * 4 * 2 and bad == 0


def test_score_sql_follows_profile():
    p = Profile.model_validate(
        {
            "hard_filters": {"years_required": {"min": 3, "max": 8}},
            "location_priority": [{"name": "tx", "states": ["TX"]}],
            "scoring": {"tier_points": [5, 1, 0], "location_points": [2, 0.25]},
        }
    )
    s = build_score_sql(p)
    assert "BETWEEN 3 AND 8" in s and ">= 9" in s
    assert "WHEN 1 THEN 5.0000 WHEN 2 THEN 1.0000" in s
    assert "WHEN 0 THEN 2.0000 WHEN 1 THEN 0.2500" in s


def test_place_options_owner_and_custom():
    assert [v for v, _ in queries.place_options(PHARMA)] == ["any", "nj", "nyc", "remote", "other"]
    assert [label for _, label in queries.place_options(PHARMA)][1:4] == [
        "New Jersey",
        "NYC area",
        "US remote",
    ]
    p = Profile.model_validate(
        {"location_priority": [{"name": "tx", "label": "Texas", "states": ["TX"]}]}
    )
    assert queries.place_options(p) == [
        ("any", "Anywhere in US"),
        ("tx", "Texas"),
        ("other", "Other US"),
    ]
    assert [
        queries.place_rank(v, PHARMA) for v in ("nj", "nyc", "remote", "us_remote", "other")
    ] == [0, 1, 2, 2, 3]
    assert queries.place_rank("other", p) == 1


def test_place_filter_values(monkeypatch):
    monkeypatch.setattr(queries, "get_profile", lambda: PHARMA)
    assert FeedFilters.from_qs("place=remote").place == "remote"
    assert FeedFilters.from_qs("place=us_remote").place == "remote"
    assert FeedFilters.from_qs("place=nj").place == "nj"
    with pytest.raises(ValueError):
        FeedFilters.from_qs("place=mars")


def test_location_rank_text(monkeypatch):
    monkeypatch.setattr("daily_job_tool.match.profile.get_profile", lambda: PHARMA)
    assert [fmt.location_rank_text(i) for i in range(5)] == [
        "New Jersey", "NYC area", "US remote", "Other US", None,
    ]  # fmt: skip
    assert (
        fmt.ranking_text()
        == "New Jersey, then NYC area, then US remote, then anywhere else in the US"
    )


async def test_custom_profile_labels_render(client, db, monkeypatch):
    p = Profile.model_validate(
        {"location_priority": [{"name": "tx", "label": "Texas", "states": ["TX"]}]}
    )
    monkeypatch.setattr(queries, "get_profile", lambda: p)
    monkeypatch.setattr("daily_job_tool.match.profile.get_profile", lambda: p)
    c = await db.company()
    await db.job(c, location_rank=0)
    await db.job(c, location_rank=1, title="Other")
    page = (await client.get("/?place=tx")).text
    assert "<span>Texas</span>" in page and 'value="tx"' in page and 'value="nj"' not in page
    assert "Then closer locations: Texas, then anywhere else in the US" in page
    assert (await client.get("/?place=nj")).status_code in (400, 422)

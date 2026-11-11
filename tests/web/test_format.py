from datetime import UTC, datetime, timedelta

from daily_job_tool.web import format as f

NOW = datetime(2026, 11, 11, 12, tzinfo=UTC)


def test_money_and_pay_range():
    assert f.money_compact(120000) == "$120K"
    assert f.money_compact(95500) == "$95.5K"
    assert f.money_compact(1_250_000) == "$1.25M"
    assert f.pay_range(120000, 150000) == "$120–150K"
    assert f.pay_range(120000, 120000) == "$120K"
    assert f.pay_range(120000, None) == "$120K+"
    assert f.pay_range(None, None) == ""


def test_relative_date():
    assert f.relative_date(NOW - timedelta(hours=3), NOW) == "today"
    assert f.relative_date(NOW - timedelta(days=1, hours=1), NOW) == "yesterday"
    assert f.relative_date(NOW - timedelta(days=5), NOW) == "5d ago"
    assert f.relative_date(NOW - timedelta(days=21), NOW) == "3w ago"
    assert f.posted_text(None, NOW - timedelta(days=2), NOW) == "first seen 2d ago"
    assert f.posted_text(NOW - timedelta(days=2), NOW, NOW) == "2d ago"


def test_years_chip():
    assert f.years_chip(3, 5) == "3–5 yrs"
    assert f.years_chip(5, None) == "5+ yrs"
    assert f.years_chip(None, None) == ""


def test_location_text():
    locs = [
        {"raw": "New Jersey", "city": None, "admin1": "NJ"},
        {"raw": "Newark", "city": "Newark", "admin1": "NJ"},
    ]
    assert f.location_text(locs, "onsite") == "Newark, NJ"
    assert f.location_text([], "remote") == "Remote"
    assert f.location_text([], "onsite") == "Location not stated"
    assert f.location_text(locs, "remote") == "Remote (Newark, NJ)"


def test_reasons_and_chips():
    assert f.reason_text("visa:not_available") == "No visa sponsorship"
    assert f.reason_text("years:min_7+") == "Requires 7+ years"
    assert f.reason_text("employment:contract") == "Contract role"
    assert f.reason_text("company:excluded") == "Excluded employer"
    assert f.reason_text("company:staffing") == "Staffing agency"
    assert f.warn_chips(["employment:unknown", "title:not_target", "pay:below_min"]) == [
        "type not stated",
        "pay < $120K",
    ]
    assert f.exclusion_reasons(["years:min_7+", "pay:below_min"]) == ["years:min_7+"]


def test_safe_http_url():
    assert f.safe_http_url("https://a.com/x") == "https://a.com/x"
    assert f.safe_http_url("javascript:alert(1)") is None
    assert f.safe_http_url("data:text/html,x") is None
    assert f.safe_http_url("//evil.com") is None
    assert f.safe_http_url(None) is None

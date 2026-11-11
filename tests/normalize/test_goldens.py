import pytest

from firsthand.adapters.base import SalaryRaw
from firsthand.normalize import CompanyInfo, NormContext
from firsthand.normalize.employment import infer_employment_type
from firsthand.normalize.function import classify_function
from firsthand.normalize.location import parse_locations
from firsthand.normalize.remote import derive_remote_regions, infer_remote_type
from firsthand.normalize.salary import extract_salary, parse_salary_text
from firsthand.normalize.seniority import infer_seniority
from firsthand.normalize.techtags import extract_tech_tags
from firsthand.normalize.title import normalize_title
from tests.normalize.golden_locations import LOCATIONS
from tests.normalize.golden_remote import REMOTE
from tests.normalize.golden_salary import SALARY_TEXT, SALARY_USD
from tests.normalize.golden_techtags import TECH
from tests.normalize.golden_titles import TITLES


def test_golden_set_sizes():
    assert len(TITLES) >= 200
    assert len(LOCATIONS) >= 100
    assert len(SALARY_TEXT) >= 150
    assert len(REMOTE) >= 40
    assert len(TECH) >= 40


# --------------------------------------------------------------------------- titles
def run_title(case: tuple, ctx: NormContext) -> tuple:
    title, _norm, _sen, _func, *rest = case
    opts = rest[0] if rest else {}
    norm = normalize_title(title, ctx, opts.get("locs", []))
    func = classify_function(norm, opts.get("dept"), ctx)
    sen = infer_seniority(norm, func, opts.get("size"), ctx)
    return norm, sen, func


def title_ok(case: tuple, got: tuple) -> bool:
    _t, norm, sen, func, *_ = case
    return got[0] == norm and got[1] == sen and (func is None or got[2] == func)


def test_title_accuracy_at_least_95_percent(ctx):
    bad = [(c[0], c[1:4], run_title(c, ctx)) for c in TITLES if not title_ok(c, run_title(c, ctx))]
    acc = 1 - len(bad) / len(TITLES)
    assert acc >= 0.95, f"title accuracy {acc:.1%}: {bad}"


@pytest.mark.parametrize(
    "case", TITLES, ids=lambda c: f"{c[0]}|{c[4].get('size', '') if len(c) > 4 else ''}"
)
def test_title_golden(case, ctx):
    got = run_title(case, ctx)
    exp = (case[1], case[2], case[3] if case[3] is not None else got[2])
    assert got == exp


# ------------------------------------------------------------------------- locations
@pytest.mark.parametrize(
    "case", LOCATIONS, ids=lambda c: c[0] if len(c) < 3 else f"{c[0]}@{c[2]['hq']}"
)
def test_location_golden(case, ctx):
    raw, expected, *rest = case
    hq = rest[0]["hq"] if rest else None
    res = parse_locations([raw], ctx, hq)
    got = [(x["city"], x["admin1"], x["country"]) for x in res.locations]
    assert got == expected
    assert res.countries == list(dict.fromkeys(c for _, _, c in expected if c))
    for x in res.locations:
        assert set(x) == {"raw", "city", "admin1", "country", "lat", "lon", "geonameid"}
        assert x["lat"] is None and x["lon"] is None and x["geonameid"] is None


def test_unknown_fragment_keeps_raw(ctx):
    res = parse_locations(["Narnia"], ctx)
    assert res.locations[0]["raw"] == "Narnia"
    assert res.countries == []


def test_ambiguity_biased_to_hq(ctx):
    assert parse_locations(["London"], ctx, "CA").countries == ["CA"]
    assert parse_locations(["London"], ctx, "US").countries == ["GB"]
    assert parse_locations(["Birmingham"], ctx, "US").locations[0]["admin1"] == "AL"
    assert parse_locations(["Birmingham"], ctx, "GB").locations[0]["admin1"] is None


# ---------------------------------------------------------------------------- salary
def as_tuple(r):
    return None if r is None else (r.min, r.max, r.currency, r.period)


@pytest.mark.parametrize("case", SALARY_TEXT, ids=lambda c: c[0][:50] or "empty")
def test_salary_text_golden(case, ctx):
    text, expected = case
    r = parse_salary_text(text, ctx)
    assert as_tuple(r) == expected
    if r:
        assert r.source == "parsed"


@pytest.mark.parametrize("case", SALARY_USD, ids=lambda c: c[0][:50])
def test_salary_usd_golden(case, ctx):
    text, (lo, hi) = case
    r = parse_salary_text(text, ctx)
    assert r is not None
    assert (r.min_usd, r.max_usd) == (lo, hi)


def test_structured_salary_sets_source(ctx):
    sal = SalaryRaw(min=120000, max=150000, currency="USD", period="YEAR")
    r = extract_salary(sal, "Salary $1 - $2", ctx)
    assert (r.source, r.min, r.max, r.currency, r.period) == (
        "structured",
        120000,
        150000,
        "USD",
        "year",
    )
    assert (r.min_usd, r.max_usd) == (120000, 150000)


@pytest.mark.parametrize(
    "sal, expected",
    [
        (SalaryRaw(min=50, max=60, currency="USD", period="hour"), ("hour", 104000, 124800)),
        (SalaryRaw(min=50, max=60, currency="usd", period="Hourly"), ("hour", 104000, 124800)),
        (
            SalaryRaw(min=8000, max=10000, currency="EUR", period="monthly"),
            ("month", 105600, 132000),
        ),
        (SalaryRaw(min=2000, max=2500, currency="USD", period="1-WEEK"), ("week", 104000, 130000)),
        (SalaryRaw(min=400, max=500, currency="USD", period="day"), ("day", 104000, 130000)),
        (SalaryRaw(min=60000, max=80000, currency="GBP", period="annual"), ("year", 75000, 100000)),
        (
            SalaryRaw(min=60000, max=80000, currency="GBP", period="PER_YEAR"),
            ("year", 75000, 100000),
        ),
        (SalaryRaw(min=100000, max=None, currency="USD", period="year"), ("year", 100000, None)),
        (SalaryRaw(min=None, max=100000, currency="USD", period="year"), ("year", None, 100000)),
        (SalaryRaw(min=120000, max=150000, currency="USD", period=None), ("year", 120000, 150000)),
        (SalaryRaw(min=50, max=60, currency="USD", period=None), ("hour", 104000, 124800)),
    ],
)
def test_structured_periods(ctx, sal, expected):
    r = extract_salary(sal, None, ctx)
    assert r is not None and r.source == "structured"
    assert (r.period, r.min_usd, r.max_usd) == expected


def test_structured_invalid_falls_back_to_description(ctx):
    sal = SalaryRaw(min=5, max=6, currency="USD", period="year")  # nonsense
    r = extract_salary(sal, "Pay range: $110,000 - $130,000", ctx)
    assert r is not None and r.source == "parsed" and r.min == 110000


def test_structured_free_text_only(ctx):
    r = extract_salary(SalaryRaw(text="$100k - $120k"), None, ctx)
    assert r is not None and (r.min, r.max, r.source) == (100000, 120000, "parsed")


def test_structured_unknown_currency_keeps_native_values(ctx):
    r = extract_salary(SalaryRaw(min=100000, max=120000, currency="XXX", period="year"), None, ctx)
    assert r is not None and r.min_usd is None and r.min == 100000


def test_no_salary(ctx):
    assert extract_salary(None, "Nothing here", ctx) is None
    assert extract_salary(SalaryRaw(), None, ctx) is None


# ------------------------------------------------------------------------ remote/tech
def run_remote(case: tuple, ctx: NormContext, hq=None):
    wp, title, locs, desc, _t, _r = case
    lr = parse_locations(locs, ctx, hq)
    rt = infer_remote_type(wp, title, locs, desc, lr.fragments)
    return rt, derive_remote_regions(rt, lr.fragments, desc)


@pytest.mark.parametrize("case", REMOTE, ids=lambda c: f"{c[0]}|{c[1]}|{c[2]}|{(c[3] or '')[:30]}")
def test_remote_golden(case, ctx):
    rt, regions = run_remote(case, ctx)
    assert rt == case[4]
    if case[5] is not None:
        assert regions == case[5]


@pytest.mark.parametrize("case", TECH, ids=lambda c: f"{c[0]}|{(c[1] or '')[:40]}")
def test_tech_golden(case, ctx):
    title, desc, expected = case
    assert extract_tech_tags(title, desc, ctx) == expected


def test_tech_cap_15(ctx):
    desc = ", ".join(
        "python java kotlin typescript javascript docker kubernetes terraform aws gcp azure "
        "postgresql mysql redis kafka react django flask".split()
    )
    tags = extract_tech_tags("Engineer", desc, ctx)
    assert len(tags) == 15 and tags[0] == "python"


# ------------------------------------------------------------------------ employment
@pytest.mark.parametrize(
    "raw, title, expected",
    [
        ("Full-time", "Engineer", "full_time"),
        ("FullTime", "Engineer", "full_time"),
        ("Part-time", "Engineer", "part_time"),
        ("PartTime", "Engineer", "part_time"),
        ("Contract", "Engineer", "contract"),
        ("Contractor", "Engineer", "contract"),
        ("Intern", "Engineer", "internship"),
        ("Internship", "Engineer", "internship"),
        ("Temporary", "Engineer", "temporary"),
        ("Fixed term", "Engineer", "temporary"),
        ("Apprenticeship", "Engineer", "apprenticeship"),
        ("Permanent", "Engineer", "full_time"),
        (None, "Engineer", "unknown"),
        ("", "Engineer", "unknown"),
        ("Weird", "Engineer", "unknown"),
        (None, "Software Engineer Intern", "internship"),
        (None, "Contract Software Engineer", "contract"),
        (None, "Freelance Designer", "contract"),
        (None, "Part-Time Barista", "part_time"),
        (None, "Temporary Warehouse Worker", "temporary"),
        (None, "Fixed-Term Marketing Assistant", "temporary"),
        (None, "Apprentice Electrician", "apprenticeship"),
        (None, "Contract Manager", "unknown"),
        (None, "Internal Tools Engineer", "unknown"),
        ("Full-time", "Software Engineer Intern", "internship"),
        ("Full-time", "Engineer", "full_time"),
        ("Contract", "Software Engineer Intern", "contract"),
    ],
)
def test_employment(raw, title, expected):
    assert infer_employment_type(raw, title) == expected


def test_company_info_defaults():
    c = CompanyInfo(name="X")
    assert c.domain is None and c.size_bucket is None

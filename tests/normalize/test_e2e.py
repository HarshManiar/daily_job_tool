from datetime import UTC, datetime

from daily_job_tool.adapters.base import RawJob, SalaryRaw
from daily_job_tool.normalize import (
    NORMALIZER_VERSION,
    CompanyInfo,
    NormalizedJob,
    SourceInfo,
    normalize,
)

GH_DESC = (
    "&lt;p&gt;&lt;strong&gt;About the role&lt;/strong&gt;&lt;/p&gt;"
    "&lt;p&gt;You will build services in Python and Golang on AWS with PostgreSQL.&lt;/p&gt;"
    "&lt;h3&gt;Pay Transparency&lt;/h3&gt;"
    "&lt;p&gt;The base salary range is $160,000 - $190,000 USD per year.&lt;/p&gt;"
    "&lt;script&gt;alert(1)&lt;/script&gt;"
)


def test_greenhouse_like(ctx):
    raw = RawJob(
        external_id="4012345",
        title="Senior Software Engineer, Platform (Remote - US) (R-1234)",
        url="https://boards.greenhouse.io/acme/jobs/4012345?gh_src=abc123",
        locations_raw=["Remote - US", "New York, NY"],
        department_raw="Engineering",
        description_html=GH_DESC,
        posted_at=datetime(2026, 11, 1, tzinfo=UTC),
        payload={"id": 4012345},
    )
    company = CompanyInfo(name="Acme", domain="acme.com", hq_country="US", size_bucket="51-200")
    job = normalize(raw, company, SourceInfo(adapter="greenhouse", token="acme"), ctx)
    assert job == NormalizedJob(
        title="Senior Software Engineer, Platform (Remote - US) (R-1234)",
        title_norm="senior software engineer, platform",
        seniority="senior",
        function="engineering",
        department_raw="Engineering",
        employment_type="unknown",
        remote_type="remote",
        remote_regions=["US"],
        locations=[
            {
                "raw": "Remote - US",
                "city": None,
                "admin1": None,
                "country": "US",
                "lat": None,
                "lon": None,
                "geonameid": None,
            },
            {
                "raw": "New York, NY",
                "city": "New York",
                "admin1": "NY",
                "country": "US",
                "lat": None,
                "lon": None,
                "geonameid": None,
            },
        ],
        countries=["US"],
        salary_min=160000,
        salary_max=190000,
        salary_currency="USD",
        salary_period="year",
        salary_min_usd=160000,
        salary_max_usd=190000,
        salary_source="parsed",
        tech_tags=["python", "go", "aws", "postgresql"],
        description_html=(
            "<p><strong>About the role</strong></p>"
            "<p>You will build services in Python and Golang on AWS with PostgreSQL.</p>"
            "<h3>Pay Transparency</h3>"
            "<p>The base salary range is $160,000 - $190,000 USD per year.</p>"
        ),
        description_text=(
            "About the role You will build services in Python and Golang on AWS with "
            "PostgreSQL. Pay Transparency The base salary range is $160,000 - $190,000 USD "
            "per year."
        ),
        posted_at=datetime(2026, 11, 1, tzinfo=UTC),
        apply_url="https://boards.greenhouse.io/acme/jobs/4012345",
        normalizer_version=NORMALIZER_VERSION,
    )


def test_ashby_like(ctx):
    raw = RawJob(
        external_id="b1c2",
        title="Product Designer (m/f/d) - London",
        url="https://jobs.ashbyhq.com/acme/b1c2",
        apply_url="https://jobs.ashbyhq.com/acme/b1c2/application?utm_source=linkedin",
        locations_raw=["London, UK"],
        workplace_raw="Hybrid",
        department_raw="Design",
        employment_type_raw="FullTime",
        description_html=(
            '<p>Join us. <a href="https://acme.com/careers" style="x">Careers</a> '
            '<a href="javascript:alert(1)">bad</a></p><ul><li>React</li><li>TypeScript</li></ul>'
            '<div onclick="x()">Div text</div>'
        ),
        salary=SalaryRaw(min=70000, max=90000, currency="GBP", period="year"),
        payload={},
    )
    company = CompanyInfo(name="Acme", domain="acme.com", hq_country="GB", size_bucket="201-1000")
    job = normalize(raw, company, SourceInfo(adapter="ashby", token="acme"), ctx)
    assert job.title == "Product Designer (m/f/d) - London"
    assert (job.title_norm, job.seniority, job.function) == ("product designer", "mid", "design")
    assert job.employment_type == "full_time"
    assert job.remote_type == "hybrid" and job.remote_regions == []
    assert [(x["city"], x["country"]) for x in job.locations] == [("London", "GB")]
    assert job.countries == ["GB"]
    assert (job.salary_min, job.salary_max, job.salary_currency, job.salary_period) == (
        70000,
        90000,
        "GBP",
        "year",
    )
    assert (job.salary_min_usd, job.salary_max_usd, job.salary_source) == (
        87500,
        112500,
        "structured",
    )
    assert job.tech_tags == ["react", "typescript"]
    assert job.description_text == "Join us. Careers bad React TypeScript Div text"
    html = job.description_html or ""
    assert "javascript" not in html and "onclick" not in html and "style" not in html
    assert '<a href="https://acme.com/careers"' in html and "<li>React</li>" in html
    assert job.apply_url == "https://jobs.ashbyhq.com/acme/b1c2/application"
    assert job.posted_at is None


def test_minimal_job_does_not_crash(ctx):
    raw = RawJob(external_id="1", title="Engineer", url="https://x.com/1", payload={})
    job = normalize(raw, CompanyInfo(name="X"), SourceInfo(adapter="generic", token="x"), ctx)
    assert job.title_norm == "engineer" and job.seniority == "mid"
    assert job.locations == [] and job.countries == [] and job.remote_type == "unknown"
    assert job.salary_source is None and job.tech_tags == []
    assert job.description_html is None and job.description_text is None
    assert job.apply_url == "https://x.com/1"


def test_aggregator_only_url_gives_no_apply_url(ctx):
    raw = RawJob(
        external_id="1", title="Engineer", url="https://www.linkedin.com/jobs/view/1", payload={}
    )
    job = normalize(raw, CompanyInfo(name="X"), SourceInfo(adapter="g", token="x"), ctx)
    assert job.apply_url is None


def test_normalize_is_deterministic_and_context_reusable(ctx):
    raw = RawJob(
        external_id="1",
        title="Staff Engineer",
        url="https://x.com/1",
        locations_raw=["Berlin"],
        payload={},
    )
    c, s = CompanyInfo(name="X"), SourceInfo(adapter="g", token="x")
    assert normalize(raw, c, s, ctx) == normalize(raw, c, s, ctx)

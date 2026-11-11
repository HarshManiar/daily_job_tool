import pytest

from daily_job_tool.detect.patterns import match_text, match_url

POSITIVE = [
    ("https://boards.greenhouse.io/acme", ("greenhouse", "acme")),
    ("https://job-boards.greenhouse.io/acme/jobs/123", ("greenhouse", "acme")),
    ("https://boards.greenhouse.io/embed/job_board?for=acme", ("greenhouse", "acme")),
    ("https://boards-api.greenhouse.io/v1/boards/acme/jobs", ("greenhouse", "acme")),
    ("https://jobs.lever.co/acme", ("lever", "acme")),
    ("https://jobs.eu.lever.co/acme/abc", ("lever", "acme")),
    ("https://api.lever.co/v0/postings/acme?mode=json", ("lever", "acme")),
    ("https://jobs.ashbyhq.com/acme", ("ashby", "acme")),
    ("https://jobs.ashbyhq.com/acme/embed?x=1", ("ashby", "acme")),
    ("https://apply.workable.com/acme/", ("workable", "acme")),
    ("https://acme.workable.com/jobs", ("workable", "acme")),
    ("https://jobs.smartrecruiters.com/AcmeInc", ("smartrecruiters", "AcmeInc")),
    ("https://careers.smartrecruiters.com/AcmeInc", ("smartrecruiters", "AcmeInc")),
    ("https://acme.recruitee.com/o/engineer", ("recruitee", "acme")),
    ("https://acme.teamtailor.com/jobs", ("teamtailor", "acme")),
    ("https://acme.jobs.personio.de/", ("personio", "acme")),
    ("https://acme.jobs.personio.com/job/1", ("personio", "acme")),
    ("https://acme.bamboohr.com/careers", ("bamboohr", "acme")),
    ("https://acme.wd5.myworkdayjobs.com/en-US/External", ("workday", "acme")),
    ("https://acme.wd1.myworkdayjobs.com/AcmeCareers/job/x", ("workday", "acme")),
    ("https://acme.wd12.myworkdayjobs.com/", ("workday", "acme")),
    ("https://wd3.myworkdaysite.com/recruiting/acme/Careers", ("workday", "acme")),
    ("https://careers-acme.icims.com/jobs/search", ("icims", "acme")),
    ("https://acme.icims.com/jobs", ("icims", "acme")),
    ("https://career5.successfactors.com/career?company=acme", ("successfactors", "acme")),
    ("https://career10.successfactors.eu/career?company=AcmeEU", ("successfactors", "AcmeEU")),
    ("https://rmkcdn.successfactors.com/x/y.css", ("successfactors", None)),
    ("https://jobs.sap.com/search", ("successfactors", "sap")),
    (
        "https://eiqg.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX",
        ("oracle", "eiqg"),
    ),
    ("https://acme.fa.em3.oraclecloud.com/", ("oracle", "acme")),
    ("https://acme.taleo.net/careersection/ex/jobsearch.ftl", ("taleo", "acme")),
    ("https://jobs.jobvite.com/acme", ("jobvite", "acme")),
    (
        "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=abc-1",
        ("adp", "abc-1"),
    ),
    ("https://recruiting.adp.com/srccar/public/RTI.home?c=1234", ("adp", "1234")),
    ("https://recruiting.ultipro.com/ACM1000/JobBoard/x", ("ultipro", "ACM1000")),
    ("https://recruiting2.ultipro.com/ACM1000/JobBoard/x", ("ultipro", "ACM1000")),
    ("https://recruiting.ukg.com/ACM/JobBoard", ("ultipro", "ACM")),
    (
        "https://recruiting.paylocity.com/recruiting/jobs/All/11111111-2222-3333-4444-555555555555/Acme",
        ("paylocity", "11111111-2222-3333-4444-555555555555"),
    ),
    ("https://www.paycomonline.net/v4/ats/web.php/jobs?clientkey=ABCDEF", ("paycom", "ABCDEF")),
    ("https://acme.phenompeople.com/us/en", ("phenom", None)),
    ("https://cdn.phenompeople.com/CareerConnectResources/x.js", ("phenom", None)),
    ("https://acme.avature.net/careers", ("avature", "acme")),
    ("https://acme.eightfold.ai/careers", ("eightfold", "acme")),
    (
        "https://sjobs.brassring.com/TGnewUI/Search/Home/Home?partnerid=25&siteid=5",
        ("brassring", "25"),
    ),
    ("https://acme.csod.com/ux/ats/careersite/1/home", ("cornerstone", "acme")),
    ("https://jobs.dayforcehcm.com/en-US/acme/CANDIDATEPORTAL", ("dayforce", "acme")),
    ("https://acme.breezy.hr/p/abc", ("breezy", "acme")),
    ("https://acme.applytojob.com/apply", ("applytojob", "acme")),
    ("https://ats.rippling.com/acme/jobs", ("rippling-ats", "acme")),
    ("https://jobs.gem.com/acme", ("gem", "acme")),
    ("https://www.comeet.com/jobs/acme/12.345", ("comeet", "acme")),
    ("https://acme.pinpointhq.com/", ("pinpoint", "acme")),
    ("https://www.hirebridge.com/jobseekers/x.asp?cid=999", ("hirebridge", "999")),
    ("https://www.linkedin.com/company/acme/jobs", ("linkedin-only", "acme")),
    ("HTTPS://JOBS.LEVER.CO/acme", ("lever", "acme")),
]

NEGATIVE = [
    "https://abc123@vs-errors.eightfold.ai/10",
    "https://boards-api.greenhouse.io/v1/boards/${company}/jobs",
    "https://www.acme.com/careers",
    "https://greenhouse.io/",
    "https://www.greenhouse.io/blog",
    "https://boards.greenhouse.io/",
    "https://www.lever.co/pricing",
    "https://jobs.lever.co/",
    "https://www.workable.com/",
    "https://apply.workable.com/",
    "https://www.linkedin.com/company/acme",
    "https://www.linkedin.com/company/acme/about",
    "https://myworkdayjobs.com.evil.example/x",
    "https://www.icims.com/",
    "https://www.ukg.com/",
    "https://example.com/greenhouse.io/acme",
    "mailto:jobs@acme.com",
    "javascript:void(0)",
    "",
    "not a url",
    "https://www.teamtailor.com/",
    "https://www.bamboohr.com/",
]


@pytest.mark.parametrize(("url", "expected"), POSITIVE)
def test_positive(url, expected):
    hit = match_url(url)
    assert hit is not None, url
    assert (hit[0], hit[1]) == expected


@pytest.mark.parametrize("url", NEGATIVE)
def test_negative(url):
    assert match_url(url) is None


def test_workday_config():
    ats, tenant, cfg = match_url("https://acme.wd5.myworkdayjobs.com/en-US/External/job/x")
    assert (ats, tenant, cfg) == ("workday", "acme", {"wd": "wd5", "site": "External"})


def test_text_signatures():
    assert match_text("window.Greenhouse.jobBoard.init()")[0] == "greenhouse"
    assert match_text("<div class='lever-jobs-container'>")[0] == "lever"
    assert match_text("just some words") is None


def test_case_count():
    assert len(POSITIVE) + len(NEGATIVE) >= 60

import pytest
import yaml

from daily_job_tool.normalize import NORMALIZER_VERSION
from daily_job_tool.normalize.employment import infer_employment_detail, infer_employment_type
from daily_job_tool.normalize.visa import VisaPhrases, classify_visa
from daily_job_tool.normalize.years import extract_years, extract_years_detail
from tests.normalize.golden_employment import CONFLICTS, EMPLOYMENT, NO_CONFLICT
from tests.normalize.golden_visa import FLAGS, VISA
from tests.normalize.golden_years import POOLS, YEARS

with open("data/profile.example.yaml", encoding="utf-8") as _f:
    _v = yaml.safe_load(_f)["hard_filters"]["visa"]
PHRASES = VisaPhrases(tuple(_v["exclude_if_phrases"]), tuple(_v["flag_if_phrases"]))


def test_v2_sizes():
    assert NORMALIZER_VERSION == 2
    assert len(YEARS) >= 60 and len(VISA) >= 50 and len(EMPLOYMENT) >= 20


@pytest.mark.parametrize("text,expected", YEARS)
def test_years(text, expected):
    assert extract_years(text) == expected


@pytest.mark.parametrize("text,expected", VISA)
def test_visa(text, expected):
    got = classify_visa(text, PHRASES)
    assert got == expected
    if expected != "not_stated":
        assert got.evidence


@pytest.mark.parametrize("raw,title,desc,expected", EMPLOYMENT)
def test_employment_v2(raw, title, desc, expected):
    assert infer_employment_type(raw, title, desc) == expected


@pytest.mark.parametrize("raw,title,desc", CONFLICTS)
def test_employment_conflict_flagged(raw, title, desc):
    t, flags = infer_employment_detail(raw, title, desc)
    assert t == "full_time" and "employment:conflict" in flags


@pytest.mark.parametrize("raw,title,desc", NO_CONFLICT)
def test_employment_no_conflict(raw, title, desc):
    assert "employment:conflict" not in infer_employment_detail(raw, title, desc)[1]


@pytest.mark.parametrize("text,status,flag", FLAGS)
def test_visa_flags(text, status, flag):
    got = classify_visa(text, PHRASES)
    assert got == status
    assert (flag in got.flags) if flag else not got.flags
    if flag == "visa:export_control":
        assert got.evidence


@pytest.mark.parametrize("text,pool,flags", POOLS)
def test_years_pool(text, pool, flags):
    r = extract_years_detail(text)
    assert (r.pool, r.flags) == (pool, flags)

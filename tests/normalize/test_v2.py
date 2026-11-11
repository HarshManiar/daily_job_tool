import pytest
import yaml

from firsthand.normalize import NORMALIZER_VERSION
from firsthand.normalize.employment import infer_employment_type
from firsthand.normalize.visa import VisaPhrases, classify_visa
from firsthand.normalize.years import extract_years
from tests.normalize.golden_employment import EMPLOYMENT
from tests.normalize.golden_visa import VISA
from tests.normalize.golden_years import YEARS

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

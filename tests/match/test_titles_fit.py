import pytest

from firsthand.match.fit import fit_score
from firsthand.match.titles import classify_title
from tests.match.golden_titles import T


def test_golden_size():
    assert len(T) >= 60


@pytest.mark.parametrize("title,tier,reason", T)
def test_title(profile, title, tier, reason):
    m = classify_title(title, profile)
    assert (m.tier, m.reason) == (tier, reason)


FIT = [
    ("", 0),
    ("We use SQL and Python daily.", 2),
    ("SQL, Python, PySpark, Databricks, Power BI, AWS", 6),
    ("powerbi and power bi count once", 1),
    ("Experience with IQVIA, XPO, LAAD, DDD data and 340B", 5),
    ("EDI 867 and EDI 852 feeds; pharmacy claims", 3),
    ("python python python", 1),
    ("MySQL and PostgreSQL", 0),
    ("Requirements\nSQL\nTableau\nSnowflake", 1 - 2),
    ("Requirements\nSQL\nPreferred Qualifications\nTableau, Snowflake", 1),
    ("SQL. Tableau is a plus.", 1),
    ("SQL. Experience with dbt, Airflow, GCP and Azure.", 1 - 4),
    ("About us\nWe love Tableau.\nRequirements\nSQL", 1),
    ("Generative AI and LLMs, text-to-sql, forecasting, cohort analysis, ANOVA", 7),
    ("Git, GitHub", 1),
    ("A/B testing and Salesforce required\nSQL", 1 - 2),
]


@pytest.mark.parametrize("text,expected", FIT)
def test_fit(profile, text, expected):
    assert fit_score(text, profile) == expected


def test_fit_clamped(profile):
    assert fit_score("Tableau Snowflake dbt Airflow GCP Azure Veeva Salesforce", profile) == -5
    assert fit_score("sql " * 3, profile) == 1

"""Normalizer (ARCHITECTURE.md section 6): a pure function from RawJob to NormalizedJob."""

from daily_job_tool.adapters.base import RawJob
from daily_job_tool.normalize.context import NormContext
from daily_job_tool.normalize.employment import infer_employment_detail
from daily_job_tool.normalize.function import classify_function
from daily_job_tool.normalize.location import parse_locations
from daily_job_tool.normalize.models import (
    NORMALIZER_VERSION,
    CompanyInfo,
    NormalizedJob,
    SourceInfo,
)
from daily_job_tool.normalize.remote import derive_remote_regions, infer_remote_type
from daily_job_tool.normalize.salary import extract_salary
from daily_job_tool.normalize.seniority import infer_seniority
from daily_job_tool.normalize.techtags import extract_tech_tags
from daily_job_tool.normalize.text import clean_title, html_to_lines, html_to_text, sanitize_html
from daily_job_tool.normalize.title import normalize_title
from daily_job_tool.normalize.url import canonical_apply_url
from daily_job_tool.normalize.visa import classify_visa
from daily_job_tool.normalize.years import extract_years_detail

__all__ = [
    "NORMALIZER_VERSION",
    "CompanyInfo",
    "NormContext",
    "NormalizedJob",
    "SourceInfo",
    "normalize",
]


def text_signals(
    title: str,
    employment_type_raw: str | None,
    desc_lines: str | None,
    desc_text: str | None,
    visa_phrases,
) -> dict:
    """The description-derived columns (visa, years, employment, norm_flags). Pure; shared by
    `normalize` and by `match rescore` (which recomputes them from stored descriptions)."""
    yr = extract_years_detail(desc_lines)
    emp, emp_flags = infer_employment_detail(employment_type_raw, title, desc_text)
    visa = classify_visa(desc_lines, visa_phrases)
    return {
        "employment_type": emp,
        "years_min": yr.min,
        "years_max": yr.max,
        "visa_status": str(visa),
        "visa_evidence": visa.evidence,
        "norm_flags": [
            *visa.flags,
            *emp_flags,
            *yr.flags,
            *(["years:nonreq"] if yr.pool in ("N", "P") else []),
        ],
    }


def normalize(
    raw: RawJob, company: CompanyInfo, source: SourceInfo, ctx: NormContext
) -> NormalizedJob:
    title = clean_title(raw.title)
    title_norm = normalize_title(title, ctx, raw.locations_raw)
    function = classify_function(title_norm, raw.department_raw, ctx)
    seniority = infer_seniority(title_norm, function, company.size_bucket, ctx)

    desc_html = sanitize_html(raw.description_html)
    desc_text = html_to_text(raw.description_html)
    desc_lines = html_to_lines(raw.description_html) or desc_text

    locs = parse_locations(raw.locations_raw, ctx, company.hq_country)
    remote_type = infer_remote_type(
        raw.workplace_raw, title, raw.locations_raw, desc_text, locs.fragments
    )
    regions = derive_remote_regions(remote_type, locs.fragments, desc_text)

    sal = extract_salary(raw.salary, desc_text, ctx)
    sig = text_signals(title, raw.employment_type_raw, desc_lines, desc_text, ctx.visa_phrases)

    return NormalizedJob(
        title=title,
        title_norm=title_norm,
        seniority=seniority,
        function=function,
        department_raw=raw.department_raw,
        remote_type=remote_type,
        remote_regions=regions,
        locations=locs.locations,
        countries=locs.countries,
        salary_min=sal.min if sal else None,
        salary_max=sal.max if sal else None,
        salary_currency=sal.currency if sal else None,
        salary_period=sal.period if sal else None,
        salary_min_usd=sal.min_usd if sal else None,
        salary_max_usd=sal.max_usd if sal else None,
        salary_source=sal.source if sal else None,
        tech_tags=extract_tech_tags(title, desc_text, ctx),
        description_html=desc_html,
        description_text=desc_text,
        posted_at=raw.posted_at,
        apply_url=canonical_apply_url([raw.apply_url, raw.url], company.domain),
        **sig,
        normalizer_version=NORMALIZER_VERSION,
    )

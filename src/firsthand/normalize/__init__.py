"""Normalizer (ARCHITECTURE.md section 6): a pure function from RawJob to NormalizedJob."""

from firsthand.adapters.base import RawJob
from firsthand.normalize.context import NormContext
from firsthand.normalize.employment import infer_employment_type
from firsthand.normalize.function import classify_function
from firsthand.normalize.location import parse_locations
from firsthand.normalize.models import (
    NORMALIZER_VERSION,
    CompanyInfo,
    NormalizedJob,
    SourceInfo,
)
from firsthand.normalize.remote import derive_remote_regions, infer_remote_type
from firsthand.normalize.salary import extract_salary
from firsthand.normalize.seniority import infer_seniority
from firsthand.normalize.techtags import extract_tech_tags
from firsthand.normalize.text import clean_title, html_to_text, sanitize_html
from firsthand.normalize.title import normalize_title
from firsthand.normalize.url import canonical_apply_url

__all__ = [
    "NORMALIZER_VERSION",
    "CompanyInfo",
    "NormContext",
    "NormalizedJob",
    "SourceInfo",
    "normalize",
]


def normalize(
    raw: RawJob, company: CompanyInfo, source: SourceInfo, ctx: NormContext
) -> NormalizedJob:
    title = clean_title(raw.title)
    title_norm = normalize_title(title, ctx, raw.locations_raw)
    function = classify_function(title_norm, raw.department_raw, ctx)
    seniority = infer_seniority(title_norm, function, company.size_bucket, ctx)

    desc_html = sanitize_html(raw.description_html)
    desc_text = html_to_text(raw.description_html)

    locs = parse_locations(raw.locations_raw, ctx, company.hq_country)
    remote_type = infer_remote_type(
        raw.workplace_raw, title, raw.locations_raw, desc_text, locs.fragments
    )
    regions = derive_remote_regions(remote_type, locs.fragments, desc_text)

    sal = extract_salary(raw.salary, desc_text, ctx)

    return NormalizedJob(
        title=title,
        title_norm=title_norm,
        seniority=seniority,
        function=function,
        department_raw=raw.department_raw,
        employment_type=infer_employment_type(raw.employment_type_raw, title),
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
        normalizer_version=NORMALIZER_VERSION,
    )

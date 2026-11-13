"""match(job, company, profile) -> MatchResult (ARCHITECTURE.md section 0.2). Pure."""

import re
from dataclasses import dataclass, field

from daily_job_tool.match.fit import fit_score
from daily_job_tool.match.profile import Profile
from daily_job_tool.match.titles import classify_title
from daily_job_tool.normalize.models import CompanyInfo, NormalizedJob
from daily_job_tool.normalize.text import fold, html_to_lines

CONTRACT_LIKE = {"contract", "temporary"}


@dataclass
class MatchResult:
    tier: int | None
    status: str
    reasons: list[str] = field(default_factory=list)
    industry_rank: int = 1
    location_rank: int = 3
    fit_score: int = 0


def _co_key(name: str) -> str:
    s = fold(name)
    s = re.sub(r"\b(?:inc|llc|corp|corporation|ltd|co|company|plc)\b", " ", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def location_rank(job: NormalizedJob, profile: Profile) -> int:
    """Index of the first matching `location_priority` entry; no match = len(list)."""
    locs = [loc for loc in job.locations if loc.get("country") == "US"]
    job_regions = {r.upper() for r in job.remote_regions}
    for i, entry in enumerate(profile.location_priority):
        states = {s.upper() for s in entry.states}
        cities = {fold(c) for c in entry.cities}
        regions = {r.upper() for r in entry.remote_regions}
        if regions:
            regions.add("NORTH_AMERICA")
        if states and any((loc.get("admin1") or "").upper() in states for loc in locs):
            return i
        if cities and any(fold(loc.get("city") or "") in cities for loc in locs):
            return i
        if regions and job.remote_type == "remote" and regions & job_regions:
            return i
    return len(profile.location_priority)


def match(job: NormalizedJob, company: CompanyInfo, profile: Profile) -> MatchResult:
    hf = profile.hard_filters
    excl: list[str] = []
    flags: list[str] = []  # attention flags: make the status 'flagged'
    info: list[str] = []  # informational reasons: never change the status
    nf = set(job.norm_flags)

    if job.employment_type in CONTRACT_LIKE:
        excl.append("employment:contract")
    if profile.visa_filters_on and job.visa_status == "not_available":
        excl.append("visa:not_available")
    thr = hf.years_required.max + 1
    over = job.years_min is not None and job.years_min >= thr
    if over and "years:nonreq" not in nf:
        excl.append(f"years:min_{thr}+")
    elif over:  # the number comes from neutral/preferred text: flag, never exclude
        flags.append(f"years:maybe_{thr}+")
    ck = _co_key(company.name)
    if company.exclude_reason or any(_co_key(c) == ck for c in hf.exclude_companies if c):
        excl.append("company:excluded")
    if company.is_staffing and hf.exclude_staffing_reposts:
        excl.append("company:staffing")

    if profile.visa_filters_on and job.visa_status == "offered":
        info.append("visa:offered")
    for f in ("visa:export_control", "visa:opt_only", "employment:conflict"):
        if f in nf:
            flags.append(f)
    if job.employment_type == "unknown":
        info.append("employment:unknown")  # Greenhouse/Ashby rarely state it: chip only
    mn = profile.profile.min_base_usd
    if mn and job.salary_max_usd is not None and job.salary_max_usd < mn:
        flags.append("pay:below_min")
    if job.posted_at is None:
        flags.append("date:undated")
    if job.years_max is not None and job.years_max < hf.years_required.min:
        flags.append("years:below_range")

    lines = html_to_lines(job.description_html) if job.description_html else None
    text = lines or job.description_text
    fit = fit_score(text, profile)
    tm = classify_title(job.title_norm, profile)
    tier, treason = tm.tier, tm.reason
    if tier == 3 and fit < 3:
        tier, treason = None, "title:tier3_weak_fit"
    if tier is None:
        info.append(treason or "title:not_target")

    ind = {i.lower() for i in profile.industry_priority}
    return MatchResult(
        tier=tier,
        status="excluded" if excl else "flagged" if flags else "include",
        reasons=[*excl, *flags, *info],
        industry_rank=0 if (company.industry or "").lower() in ind else 1,
        location_rank=location_rank(job, profile),
        fit_score=fit,
    )

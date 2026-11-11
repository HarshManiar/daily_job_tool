"""match(job, company, profile) -> MatchResult (ARCHITECTURE.md section 0.2). Pure."""

import re
from dataclasses import dataclass, field

from daily_job_tool.match.fit import fit_score
from daily_job_tool.match.profile import Profile
from daily_job_tool.match.titles import classify_title
from daily_job_tool.normalize.models import CompanyInfo, NormalizedJob
from daily_job_tool.normalize.text import fold, html_to_lines

CONTRACT_LIKE = {"contract", "temporary"}
US_REMOTE = {"US", "NA", "NORTH_AMERICA", "AMER", "WORLD"}


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
    nj = profile.location("nj")
    nyc = profile.location("nyc")
    rem = profile.location("us_remote")
    states = {s.upper() for s in (nj.states if nj else ["NJ"])}
    cities = {fold(c) for c in (nyc.cities if nyc else [])}
    regions = {r.upper() for r in (rem.remote_regions if rem else US_REMOTE)} | {"NORTH_AMERICA"}
    locs = [loc for loc in job.locations if loc.get("country") == "US"]
    if any((loc.get("admin1") or "").upper() in states for loc in locs):
        return 0
    if any(fold(loc.get("city") or "") in cities for loc in locs):
        return 1
    if job.remote_type == "remote" and regions & {r.upper() for r in job.remote_regions}:
        return 2
    return 3


def match(job: NormalizedJob, company: CompanyInfo, profile: Profile) -> MatchResult:
    hf = profile.hard_filters
    excl: list[str] = []
    flags: list[str] = []  # attention flags: make the status 'flagged'
    info: list[str] = []  # informational reasons: never change the status
    nf = set(job.norm_flags)

    if job.employment_type in CONTRACT_LIKE:
        excl.append("employment:contract")
    if job.visa_status == "not_available":
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

    if job.visa_status == "offered":
        info.append("visa:offered")
    for f in ("visa:export_control", "visa:opt_only", "employment:conflict"):
        if f in nf:
            flags.append(f)
    if job.employment_type == "unknown":
        flags.append("employment:unknown")
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

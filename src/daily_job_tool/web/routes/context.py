"""Read-only view of the active match profile (what the ranking is based on)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from daily_job_tool.match.profile import get_profile, profile_is_example, profile_path
from daily_job_tool.web.deps import templates

router = APIRouter()


@router.get("/context")
async def context(request: Request):
    profile = get_profile()
    path = profile_path()
    ctx = {
        "page": "context",
        "p": profile,
        "path": str(path) if path else None,
        "is_example": profile_is_example(path),
        "no_profile": path is None or not path.exists(),
        "score_rows": _score_rows(profile),
    }
    return templates.TemplateResponse(request, "context.html", ctx)


def _score_rows(profile) -> list[tuple[str, str]]:
    sc, yr = profile.scoring, profile.hard_filters.years_required
    loc = [e.display_label for e in profile.location_priority] + ["Other US"]
    pts = (list(sc.location_points) + [0.0] * len(loc))[: len(loc)]
    g = lambda x: f"{x:g}"  # noqa: E731
    return [
        ("Title tier 1 / 2 / 3", " / ".join(g(x) for x in sc.tier_points) + " points"),
        ("Skills overlap", f"up to {g(sc.skills_max)} points (1 per 2 matched skills)"),
        (
            f"Years required ({yr.min}-{yr.max})",
            f"{g(sc.years_in_range)} in range, {g(sc.years_unknown_or_low)} unknown or below, "
            f"{g(sc.years_over_max)} above {yr.max}",
        ),
        ("Location", ", ".join(f"{n}: {g(x)}" for n, x in zip(loc, pts, strict=True))),
        ("Priority industry", f"{g(sc.industry)} point"),
        ("Total", "capped at 10"),
    ]

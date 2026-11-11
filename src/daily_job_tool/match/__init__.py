"""Personal match engine (ARCHITECTURE.md section 0.2)."""

from daily_job_tool.match.engine import MatchResult, match
from daily_job_tool.match.profile import Profile, get_profile, load_profile

__all__ = ["MatchResult", "Profile", "get_profile", "load_profile", "match"]

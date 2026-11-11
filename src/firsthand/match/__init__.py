"""Personal match engine (ARCHITECTURE.md section 0.2)."""

from firsthand.match.engine import MatchResult, match
from firsthand.match.profile import Profile, get_profile, load_profile

__all__ = ["MatchResult", "Profile", "get_profile", "load_profile", "match"]

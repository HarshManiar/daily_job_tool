"""Aggregator blocklist: these domains are never fetched, not even via redirects."""

from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

BLOCKLIST_PATH = Path(__file__).resolve().parents[3] / "data" / "blocked_domains.csv"

BUILTIN_BLOCKED: frozenset[str] = frozenset(
    {
        "linkedin.com",
        "indeed.com",
        "glassdoor.com",
        "ziprecruiter.com",
        "wellfound.com",
        "angel.co",
        "builtin.com",
        "monster.com",
        "dice.com",
        "simplyhired.com",
        "careerbuilder.com",
        "joinhandshake.com",
        "remoteok.com",
        "weworkremotely.com",
        "flexjobs.com",
        "jooble.com",
        "adzuna.com",
        "talent.com",
    }
)


def load_blocked_domains(path: Path = BLOCKLIST_PATH) -> frozenset[str]:
    """First CSV column of data/blocked_domains.csv; built-in list if the file is absent/empty."""
    if not path.exists():
        return BUILTIN_BLOCKED
    domains: set[str] = set()
    for line in path.read_text().splitlines():
        cell = line.split(",", 1)[0].strip().strip('"').lower().lstrip("*.")
        if not cell or cell.startswith("#") or cell in ("domain", "domains"):
            continue
        domains.add(cell)
    return frozenset(domains) or BUILTIN_BLOCKED


@lru_cache(maxsize=1)
def _default_domains() -> frozenset[str]:
    return load_blocked_domains()


def is_blocked_host(host: str, domains: frozenset[str] | None = None) -> bool:
    """True if host equals a blocked domain or is a subdomain of one (suffix on a dot boundary)."""
    host = host.lower().rstrip(".").split(":", 1)[0]
    blocked = _default_domains() if domains is None else domains
    parts = host.split(".")
    return any(".".join(parts[i:]) in blocked for i in range(len(parts)))


BlockedCheck = Callable[[str], bool]

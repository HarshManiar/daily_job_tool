"""Canonical apply URL (rule 9)."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from daily_job_tool.http.blocklist import is_blocked_host

_TRACKING_EXACT = {
    "source",
    "gh_src",
    "ref",
    "ref_src",
    "referrer",
    "src",
    "gclid",
    "fbclid",
    "msclkid",
    "lever-origin",
    "lever-via",
    "trk",
    "trackingid",
    "mc_cid",
    "mc_eid",
}
_TRACKING_PREFIX = ("utm_", "lever-source", "mkt_")


def strip_tracking(url: str) -> str:
    parts = urlsplit(url.strip())
    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_EXACT and not k.lower().startswith(_TRACKING_PREFIX)
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment))


def _ok(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    if is_blocked_host(parts.hostname):
        return None
    return strip_tracking(url)


def _on_domain(url: str, domain: str | None) -> bool:
    if not domain:
        return False
    host = (urlsplit(url).hostname or "").lower()
    d = domain.lower().lstrip(".")
    return host == d or host.endswith("." + d)


def canonical_apply_url(candidates: list[str | None], company_domain: str | None) -> str | None:
    """Employer-domain URL first, else the first non-blocked candidate; None if all blocked."""
    cleaned = [u for u in (_ok(c) for c in candidates) if u]
    for u in cleaned:
        if _on_domain(u, company_domain):
            return u
    return cleaned[0] if cleaned else None

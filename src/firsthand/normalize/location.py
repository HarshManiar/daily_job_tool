"""Locations v1 (rule 5): built-in table from data/locations.yaml, no GeoNames yet."""

import re
from dataclasses import dataclass
from typing import Any

from firsthand.normalize.context import NormContext
from firsthand.normalize.text import fold

# Ambiguous city names: preferred country when company.hq_country gives no bias (population).
_PREFERRED_COUNTRY = {
    "london": "GB",
    "birmingham": "GB",
    "manchester": "GB",
    "cambridge": "GB",
    "newcastle": "GB",
    "portland": "US",
    "san jose": "US",
    "victoria": "CA",
    "hamilton": "CA",
    "washington": "US",
}
_REMOTE_RE = re.compile(
    r"\b(?:fully[ -]|100%[ -]|totally[ -])?"
    r"(?:remote(?:ly)?(?:[ -](?:first|only|friendly|work(?:er)?s?|position|role|job))?|"
    r"work(?:ing)? from home|wfh|home[ -]?based|telecommut\w*)\b"
    r"(?:\s+(?:in|within|from|across|throughout|for)\b)?",
    re.I,
)
_MODE_RE = re.compile(r"\b(?:hybrid|on[- ]?site|in[- ]office|in[- ]person|office[- ]based)\b", re.I)
_NOISE_RE = re.compile(
    r"\b(?:based|only|eligible|candidates?|head ?quarters|hq|metro(?:politan)? area)\b$"
    r"|-based\b|\boffice\b",
    re.I,
)
_SPLIT_CHARS = set("|;•·/")
_IGNORED = {"n/a", "tbd", "tba", "none", "-", "unknown", ""}
_OR = re.compile(r"\s+or\s+", re.I)
_SOFT_SPLIT = re.compile(r",|\s&\s|\s+and\s+|\s+\+\s+", re.I)


@dataclass
class Res:
    kind: str  # place | region | world | unknown
    city: str | None = None
    admin1: str | None = None
    country: str | None = None
    label: str | None = None  # region label


@dataclass
class Frag:
    raw: str
    res: Res | None  # None: a pure "Remote" fragment
    remote: bool  # the source string contained a remote word


@dataclass
class LocationResult:
    locations: list[dict[str, Any]]
    countries: list[str]
    fragments: list[Frag]


def _split_or(p: str) -> list[str]:
    """Split on ' or ' occurring outside parentheses."""
    out: list[str] = []
    last = 0
    for m in _OR.finditer(p):
        depth = p.count("(", 0, m.start()) - p.count(")", 0, m.start())
        if depth <= 0:
            out.append(p[last : m.start()])
            last = m.end()
    out.append(p[last:])
    return out


def _split_top(s: str) -> list[str]:
    """Split on | ; bullet / and ' or ', ignoring separators inside parentheses."""
    parts: list[str] = []
    depth, cur = 0, []
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if depth == 0 and ch in _SPLIT_CHARS:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    out: list[str] = []
    for p in parts:
        out.extend(_split_or(p))
    return [p.strip() for p in out if p.strip()]


def _title_case(name: str) -> str:
    return re.sub(r"(^|[\s\-])([a-z])", lambda m: m.group(1) + m.group(2).upper(), name)


def _clean(raw: str) -> tuple[str, bool]:
    """Strip remote/work-mode words; return (cleaned text, had_remote_word)."""
    remote = bool(_REMOTE_RE.search(raw))
    t = _REMOTE_RE.sub(" ", raw)
    t = _MODE_RE.sub(" ", t)
    t = re.sub(r"\(\s*[,\-–—]*\s*\)", " ", t)
    t = re.sub(r"[()]", ",", t)
    t = re.sub(r"\s+[-–—:]\s+", ", ", t)
    t = _NOISE_RE.sub(" ", t)
    t = re.sub(r"^\s*(?:the|in|within|from)\s+", "", t.strip(), flags=re.I)
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"(?:\s*,\s*)+", ", ", t)
    return t.strip(" ,-–—:;"), remote


class _Resolver:
    def __init__(self, ctx: NormContext, hq: str | None):
        self.d = ctx.data
        self.hq = hq

    # -- helpers --------------------------------------------------------------------------
    def _rank(self, name: str, cands: list[dict[str, Any]]) -> dict[str, Any]:
        if self.hq:
            for c in cands:
                if c["country"] == self.hq:
                    return c
        pref = _PREFERRED_COUNTRY.get(name)
        if pref:
            for c in cands:
                if c["country"] == pref:
                    return c
        return cands[0]

    def _city_res(self, c: dict[str, Any]) -> Res:
        return Res("place", _title_case(c["name"]), c["admin1"], c["country"])

    def _state_country(self, code: str) -> str | None:
        in_us, in_ca, in_au = (
            code in self.d.us_states,
            code in self.d.ca_provinces,
            code in (self.d.au_states),
        )
        opts = [c for c, ok in (("US", in_us), ("CA", in_ca), ("AU", in_au)) if ok]
        if not opts:
            return None
        if self.hq in opts:
            return self.hq
        return opts[0]

    def _state_name(self, f: str) -> Res | None:
        hit = self.d.state_name_idx.get(f)
        if hit:
            return Res("place", None, hit[0], hit[1])
        return None

    # -- single-token lookups ------------------------------------------------------------
    def lookup(self, orig: str) -> Res | None:
        f = fold(orig)
        if not f:
            return None
        cands = self.d.city_idx.get(f)
        if cands:
            return self._city_res(self._rank(f, cands))
        if f in self.d.country_idx:
            return Res("place", None, None, self.d.country_idx[f])
        reg = self.d.region_idx.get(f)
        if reg:
            return Res(reg[0], label=reg[1])
        st = self._state_name(f)
        if st:
            return st
        o = orig.strip()
        if len(o) == 2 and o.isupper() and o in self.d.country_codes:
            return Res("place", None, None, o)
        if f.startswith("greater ") or f.endswith(" metro"):
            return self.lookup(f.removeprefix("greater ").removesuffix(" metro"))
        return None

    def _is_country(self, orig: str) -> str | None:
        f = fold(orig)
        if f in self.d.country_idx:
            return self.d.country_idx[f]
        o = orig.strip()
        if len(o) == 2 and o.isupper() and o in self.d.country_codes:
            return o
        return None

    def _state(self, orig: str) -> tuple[str, str] | None:
        """(admin1 code, country) if `orig` is a state/province code (uppercase) or name."""
        o = orig.strip()
        if 2 <= len(o) <= 3 and o.isupper():
            country = self._state_country(o)
            if country:
                return o, country
        return self.d.state_name_idx.get(fold(o))

    def _find_city(
        self, name: str, country: str | None = None, admin1: str | None = None
    ) -> dict[str, Any] | None:
        cands = self.d.city_idx.get(fold(name))
        if not cands:
            return None
        pool = cands
        if admin1:
            pool = [c for c in pool if c["admin1"] == admin1] or []
        if country and pool:
            pool = [c for c in pool if c["country"] == country] or []
        return self._rank(fold(name), pool) if pool else None

    # -- multi-part ----------------------------------------------------------------------
    def structured(self, parts: list[str]) -> Res | None:
        first, last = parts[0], parts[-1]
        mid = parts[1:-1]
        # last part is a state/province (code or name): "City, ST"
        st = self._state(last)
        country_last = self._is_country(last)
        # Uppercase 2-letter code that is both a state and a country: prefer the city table.
        if st and len(last.strip()) == 2 and country_last:
            c = self._find_city(first, admin1=st[0]) or self._find_city(first, country=country_last)
            if c:
                return self._city_res(c)
            if self.hq == country_last:
                return Res("place", None, None, country_last)
            return Res("place", None, st[0], st[1])
        if country_last:
            hint = None
            if mid:
                s = self._state(mid[-1])
                hint = s[0] if s and s[1] == country_last else None
            c = self._find_city(first, country=country_last, admin1=hint) or self._find_city(
                first, country=country_last
            )
            if c:
                return self._city_res(c)
            fr = self.lookup(first)
            if fr and (fr.kind != "place" or fr.city is None and fr.admin1 is None) and not mid:
                return None  # "Germany, France": both sides known, split instead
            s = self._state(first) if not mid else None
            if s and s[1] == country_last:
                return Res("place", None, s[0], country_last)
            return Res("place", None, hint, country_last)
        if st:
            c = self._find_city(first, admin1=st[0])
            if c:
                return self._city_res(c)
            fr = self.lookup(first)
            if fr and fr.kind != "unknown" and fr.city is None and fr.admin1 is None:
                return None
            if self.d.city_idx.get(fold(first)) and len(parts) == 2:
                return None
            return Res("place", None, st[0], st[1])
        return None

    def resolve(self, orig: str) -> list[Res]:
        if not orig:
            return []
        r = self.lookup(orig)
        if r:
            return [r]
        parts = [p.strip() for p in orig.split(",") if p.strip()]
        if len(parts) >= 2:
            r = self.structured(parts)
            if r:
                return [r]
        subs = [s.strip() for s in _SOFT_SPLIT.split(orig) if s.strip()]
        if len(subs) >= 2:
            rs = [self.lookup(s) for s in subs]
            if all(rs):
                return [r for r in rs if r]
        return [Res("unknown")]


def _loc_dict(raw: str, r: Res) -> dict[str, Any]:
    return {
        "raw": raw,
        "city": r.city,
        "admin1": r.admin1,
        "country": r.country,
        "lat": None,
        "lon": None,
        "geonameid": None,
    }


def parse_locations(
    locations_raw: list[str], ctx: NormContext, hq_country: str | None = None
) -> LocationResult:
    rv = _Resolver(ctx, hq_country)
    locations: list[dict[str, Any]] = []
    countries: list[str] = []
    frags: list[Frag] = []
    seen: set[tuple[Any, ...]] = set()
    for raw_str in locations_raw:
        if fold(raw_str) in _IGNORED:
            continue
        for piece in _split_top(raw_str):
            cleaned, remote = _clean(piece)
            remote = remote or bool(_REMOTE_RE.search(raw_str))
            if not cleaned:
                frags.append(Frag(piece, None, remote))
                continue
            multi = [p for p in _OR.split(cleaned) if p.strip()]
            for sub in multi:
                resolved = rv.resolve(sub)
                single = len(resolved) == 1
                subs = [sub] if single else [s.strip() for s in _SOFT_SPLIT.split(sub) if s.strip()]
                for text, r in zip(subs, resolved, strict=False):
                    raw = piece if single else text
                    frags.append(Frag(raw, r, remote))
                    key = (r.kind, r.city, r.admin1, r.country, r.label, raw.lower())
                    if key in seen:
                        continue
                    seen.add(key)
                    locations.append(_loc_dict(raw, r))
                    if r.kind == "place" and r.country and r.country not in countries:
                        countries.append(r.country)
    return LocationResult(locations, countries, frags)

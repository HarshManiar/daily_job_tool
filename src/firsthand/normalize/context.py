"""NormContext: data files and FX rates, loaded once and passed into the pure normalizer."""

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from firsthand.normalize.fx import FALLBACK_USD_PER_UNIT
from firsthand.normalize.text import fold

DATA_DIR = Path(__file__).resolve().parents[3] / "data"

# Country spellings that are only safe in a location context (not in the YAML aliases).
_EXTRA_COUNTRY_ALIASES = {"us": "US", "usa": "US", "uk": "GB", "america": "US"}

_REGION_LABELS = {
    "emea": "EMEA",
    "eu": "EU",
    "europe": "EUROPE",
    "latam": "LATAM",
    "apac": "APAC",
    "north america": "NA",
    "americas": "AMER",
    "middle east": "MENA",
    "africa": "AFRICA",
}


@dataclass(frozen=True)
class NormData:
    abbreviations: dict[str, str]
    strip_patterns: list[re.Pattern[str]]
    seniority: list[tuple[str, list[re.Pattern[str]]]]
    level_suffixes: dict[str, str]
    functions: list[dict[str, Any]]
    # locations
    country_idx: dict[str, str]  # folded name/alias -> ISO
    country_codes: frozenset[str]
    region_idx: dict[str, tuple[str, str]]  # folded -> (kind, label)
    us_states: dict[str, str]  # code -> folded name
    ca_provinces: dict[str, str]
    au_states: dict[str, str]
    state_name_idx: dict[str, tuple[str, str]]  # folded name -> (admin1 code, country)
    city_idx: dict[str, list[dict[str, Any]]]  # folded name/alias -> candidate cities
    # tech tags
    tech: list[dict[str, Any]] = field(default_factory=list)


def _load_yaml(name: str) -> Any:
    return yaml.safe_load((DATA_DIR / name).read_text(encoding="utf-8"))


def _build() -> NormData:
    titles = _load_yaml("titles.yaml")
    funcs = _load_yaml("functions.yaml")
    loc = _load_yaml("locations.yaml")
    tags = _load_yaml("tech_tags.yaml")

    country_idx: dict[str, str] = {}
    for c in loc["countries"]:
        country_idx[fold(c["name"])] = c["code"]
        for a in c.get("aliases", []):
            country_idx[fold(a)] = c["code"]
    country_idx.update(_EXTRA_COUNTRY_ALIASES)
    codes = frozenset(c["code"] for c in loc["countries"])

    region_idx: dict[str, tuple[str, str]] = {}
    for key, spec in loc["regions"].items():
        if spec["kind"] == "world":
            region_idx[fold(key)] = ("world", "WORLD")
        else:
            region_idx[fold(key)] = ("region", _REGION_LABELS.get(key, key.upper()))

    us = {k: fold(v) for k, v in loc["us_states"].items()}
    ca = {k: fold(v) for k, v in loc["ca_provinces"].items()}
    au = {k: fold(v) for k, v in loc["au_states"].items()}
    state_names: dict[str, tuple[str, str]] = {}
    for table, country in ((au, "AU"), (ca, "CA"), (us, "US")):  # US wins collisions
        for code, name in table.items():
            state_names[name] = (code, country)

    city_idx: dict[str, list[dict[str, Any]]] = {}
    for c in loc["cities"]:
        entry = {"name": c["name"], "admin1": c.get("admin1"), "country": c["country"]}
        for key in {fold(c["name"]), *(fold(a) for a in c.get("aliases", []))}:
            city_idx.setdefault(key, []).append(entry)

    tech = []
    for t in tags:
        ctx_re = t.get("requires_context")
        tech.append(
            {
                "tag": t["tag"],
                "aliases": [a.lower() for a in t["aliases"]],
                "context": re.compile(ctx_re) if ctx_re else None,
            }
        )

    return NormData(
        abbreviations=titles["abbreviations"],
        strip_patterns=[re.compile(p) for p in titles["strip_patterns"]],
        seniority=[
            (s["level"], [re.compile(p) for p in s["patterns"]]) for s in titles["seniority"]
        ],
        level_suffixes={str(k): v for k, v in titles["level_suffixes"].items()},
        functions=funcs,
        country_idx=country_idx,
        country_codes=codes,
        region_idx=region_idx,
        us_states=us,
        ca_provinces=ca,
        au_states=au,
        state_name_idx=state_names,
        city_idx=city_idx,
        tech=tech,
    )


@lru_cache(maxsize=1)
def load_data() -> NormData:
    return _build()


@dataclass(frozen=True)
class NormContext:
    data: NormData
    fx_rates: dict[str, float]  # USD per 1 unit of currency
    fx_is_fallback: bool = False

    @classmethod
    def load(cls, fx_rates: dict[str, float] | None = None) -> "NormContext":
        """Load data files once (cached). Without `fx_rates` the dev-only fallback table is used."""
        if fx_rates:
            return cls(load_data(), dict(fx_rates), False)
        return cls(load_data(), dict(FALLBACK_USD_PER_UNIT), True)

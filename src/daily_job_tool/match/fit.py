"""Skill overlap score (never excludes)."""

import re
from functools import lru_cache

from daily_job_tool.match.profile import Profile
from daily_job_tool.normalize.sections import IGNORE, segments

DEFAULT_SKILL_ALIASES = {
    "power bi": r"power[- ]?bi",
    "pyspark": r"py-?spark",
    "aws": r"aws|amazon web services",
    "llm": r"llms?",
    "text-to-sql": r"text[- ]to[- ]sql",
    "genai": r"gen[- ]?ai|generative ai",
    "forecasting": r"forecast(?:s|ing)?",
    "cohort": r"cohorts?",
    "decision tree": r"decision trees?",
    "git": r"git|github|gitlab",
    "px/rx claims": r"(?:px|rx|pharmacy|medical)[/ ]*(?:(?:px|rx)[ ])?claims|claims data|claims",
    "sp data": r"sp data|specialty pharmacy data",
    "edi 867": r"edi[ -]?867|867 (?:data|file)s?",
    "edi 852": r"edi[ -]?852|852 (?:data|file)s?",
    "a/b testing": r"a/b test(?:s|ing)?|ab testing|split testing",
    "gcp": r"gcp|google cloud",
    "azure": r"azure",
    "dbt": r"dbt",
    "veeva": r"veeva",
    "salesforce": r"salesforce",
    "340b": r"340b",
}


@lru_cache(maxsize=512)
def _compile_skill(skill: str, body: str | None) -> re.Pattern[str]:
    body = body if body is not None else re.escape(skill)
    return re.compile(rf"(?<![\w])(?:{body})(?![\w])", re.I)


def skill_rx(skill: str, aliases: dict[str, str] | None = None) -> re.Pattern[str]:
    """Word-bounded regex for a skill; `aliases` (profile) override the built-in aliases."""
    k = skill.lower()
    body = (aliases or {}).get(k, DEFAULT_SKILL_ALIASES.get(k))
    return _compile_skill(k, body)


def fit_score(text: str | None, profile: Profile) -> int:
    if not text:
        return 0
    p = profile.profile
    al = profile.skill_aliases
    strong = list(dict.fromkeys([*p.strong_skills, *p.treat_as_match, *p.data]))
    score = sum(1 for s in strong if skill_rx(s, al).search(text))
    required = " \n".join(s.text for s in segments(text) if not s.preferred and s.kind != IGNORE)
    gaps = sum(1 for g in dict.fromkeys(p.gap_skills) if skill_rx(g, al).search(required))
    return max(-5, min(20, score - gaps))

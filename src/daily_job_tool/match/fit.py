"""Skill overlap score (never excludes)."""

import re
from functools import lru_cache

from daily_job_tool.match.profile import Profile
from daily_job_tool.normalize.sections import IGNORE, segments

_ALIASES = {
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


@lru_cache(maxsize=256)
def skill_rx(skill: str) -> re.Pattern[str]:
    body = _ALIASES.get(skill.lower(), re.escape(skill.lower()))
    return re.compile(rf"(?<![\w]){'(?:' + body + ')'}(?![\w])", re.I)


def fit_score(text: str | None, profile: Profile) -> int:
    if not text:
        return 0
    p = profile.profile
    strong = list(dict.fromkeys([*p.strong_skills, *p.treat_as_match, *p.data]))
    score = sum(1 for s in strong if skill_rx(s).search(text))
    required = " \n".join(s.text for s in segments(text) if not s.preferred and s.kind != IGNORE)
    gaps = sum(1 for g in dict.fromkeys(p.gap_skills) if skill_rx(g).search(required))
    return max(-5, min(20, score - gaps))

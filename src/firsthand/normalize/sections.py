"""Sentence and section segmentation for job descriptions (used by years, visa and fit).

Works on line-preserving text (see `text.html_to_lines`) but degrades gracefully on flat text:
headings such as "Requirements" or "Preferred Qualifications" are detected inline.
"""

import re
from dataclasses import dataclass

REQUIRED = "required"
PREFERRED = "preferred"
IGNORE = "ignore"  # company boilerplate (about us, benefits ...)
NEUTRAL = "neutral"

_HEADINGS: list[tuple[str, str]] = [
    (
        PREFERRED,
        r"(?:preferred|desired|desirable|nice[- ]to[- ]haves?|bonus(?: points)?|good to have|"
        r"extra credit|what (?:would|will) (?:make you )?stand out|"
        r"what (?:sets|set) you apart)"
        r"(?:\s+(?:qualifications?|skills?|experience|requirements?|points|attributes))?",
    ),
    (
        REQUIRED,
        r"(?:(?:minimum|basic|required|key|core|mandatory|essential)\s+"
        r"(?:qualifications?|requirements?|skills?|experience)|"
        r"requirements?|qualifications?|must[- ]haves?|"
        r"what you(?:['’]ll)?\s+(?:bring|need|have)|what we(?:['’]re)? (?:are )?looking for|"
        r"who you are|about you|you have|your background|"
        r"skills (?:and|&) (?:experience|qualifications)|experience (?:and|&) skills|"
        r"the ideal candidate|to be successful)",
    ),
    (
        IGNORE,
        r"(?:about us|about the company|who we are|our story|our mission|company overview|"
        r"benefits(?: (?:and|&) perks)?|perks(?: (?:and|&) benefits)?|what we offer|"
        r"compensation(?: (?:and|&) benefits)?|equal opportunity(?: employer)?|why join us|"
        r"why work (?:with|at|for) us)",
    ),
    (
        NEUTRAL,
        r"(?:responsibilities|what you(?:['’]ll)? do|about the (?:role|job|position|team)|"
        r"the role|job description|overview|role summary|position summary|key responsibilities|"
        r"the opportunity|in this role)",
    ),
]
_HEAD_RE = re.compile("|".join(f"(?P<k{i}>{p})" for i, (_, p) in enumerate(_HEADINGS)), re.I)
_HEAD_TAIL = re.compile(r"\s*(?::|\n|$|[A-Z0-9•*\-–])")

_ABBREV_LB = (
    r"(?<!\bSr\.)(?<!\bJr\.)(?<!\bInc\.)(?<!\bLtd\.)(?<!\bvs\.)(?<!\bMr\.)(?<!\bMs\.)"
    r"(?<!\bDr\.)(?<!\bCo\.)(?<!\bCorp\.)(?<!\bPh\.D\.)(?<!\be\.g\.)(?<!\bi\.e\.)"
)
_SENT_SPLIT = re.compile(
    r"\n+|\s*[•·▪●]\s*|;\s+|(?<=[!?])\s+|(?<=\.)(?<!\b[A-Za-z]\.)"
    + _ABBREV_LB
    + r"\s+(?=[A-Z0-9(\"“])"
)
PREFERRED_CUE = re.compile(
    r"\bprefer(?:red|ably|ence)?\b|\bnice[- ]to[- ]have\b|\ba plus\b|\ban? (?:big |huge )?(?:bonus|plus)\b|"
    r"\bbonus\b|\bideally\b|\bdesired?\b|\bdesirable\b|\bis plus\b|\bbig plus\b|"
    r"\bnot required\b|\bnot necessary\b|\boptional\b|\bwould be (?:a )?(?:great|nice|helpful)\b|"
    r"\bhelpful\b|\bwill be considered an? (?:asset|advantage)\b|\ban? (?:asset|advantage)\b",
    re.I,
)


@dataclass(frozen=True)
class Segment:
    text: str
    kind: str  # required | preferred | ignore | neutral (section state, before inline cues)
    preferred: bool  # section is preferred OR segment carries an inline preferred cue


def _heading_marks(text: str) -> list[tuple[int, str]]:
    marks: list[tuple[int, str]] = []
    for m in _HEAD_RE.finditer(text):
        word = m.group(0)
        end = m.end()
        tail_ok = _HEAD_TAIL.match(text, end)
        colon = text[end:].lstrip(" ").startswith(":")
        starts_cap = word[0].isupper()
        if not (tail_ok and (starts_cap or colon)):
            continue
        # heading must open its sentence/line (or follow a bullet/colon), not sit mid-clause
        before = text[: m.start()].rstrip(" ")
        if before and before[-1] not in '\n.!?:;•·*-–—)"”' and not (word.isupper()):
            # flat text: allow when the heading phrase is Title Case ("Preferred Qualifications")
            words = word.split()
            if not all(
                w[0].isupper()
                for w in words
                if w.isalpha()
                and w.lower() not in {"and", "to", "of", "the", "you", "we", "are", "for", "a"}
            ):
                continue
        kind = next(_HEADINGS[i][0] for i in range(len(_HEADINGS)) if m.group(f"k{i}"))
        marks.append((m.start(), kind))
    return marks


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text) if s and s.strip()]


def segments(text: str) -> list[Segment]:
    """Split into sentence-level segments tagged with their section state."""
    if not text:
        return []
    marks = _heading_marks(text)
    cuts = {0, len(text)} | {p for p, _ in marks}
    for m in _SENT_SPLIT.finditer(text):
        cuts.add(m.start())
        cuts.add(m.end())
    pos = sorted(cuts)
    heads = dict(marks)
    state = NEUTRAL
    out: list[Segment] = []
    for a, b in zip(pos, pos[1:], strict=False):
        chunk = text[a:b]
        if a in heads:
            state = heads[a]
        s = chunk.strip(" \n\t•·▪●;")
        if not s:
            continue
        out.append(Segment(s, state, state == PREFERRED or bool(PREFERRED_CUE.search(s))))
    return out

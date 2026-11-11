"""Tech tags (rule 10): word-boundary alias match with requires_context for ambiguous tokens."""

import re

from daily_job_tool.normalize.context import NormContext, NormData

MAX_TAGS = 15


_CACHE: dict[int, tuple[re.Pattern[str], dict]] = {}


def _compiled(data: NormData) -> tuple[re.Pattern[str], dict]:
    hit = _CACHE.get(id(data))
    if hit:
        return hit
    alias_to_tag: dict[str, dict] = {}
    for t in data.tech:
        for a in t["aliases"]:
            alias_to_tag.setdefault(a, t)
    ordered = sorted(alias_to_tag, key=len, reverse=True)
    pat = re.compile(
        r"(?<![\w+#.])(?:" + "|".join(re.escape(a) for a in ordered) + r")(?![\w+#])",
    )
    _CACHE[id(data)] = (pat, alias_to_tag)
    return pat, alias_to_tag


def extract_tech_tags(title: str, description_text: str | None, ctx: NormContext) -> list[str]:
    text = (title + "\n" + (description_text or "")).lower()
    pat, alias_to_tag = _compiled(ctx.data)
    listy = re.sub(r"\s*(?:\band\b|\bor\b|&)\s*", ", ", text)  # "Go and Rust" == "Go, Rust"
    seen: dict[str, int] = {}
    ctx_ok: dict[str, bool] = {}
    for m in pat.finditer(text):
        t = alias_to_tag[m.group(0)]
        tag = t["tag"]
        if tag in seen:
            continue
        if t["context"] is not None:
            if tag not in ctx_ok:
                ctx_ok[tag] = bool(t["context"].search(text) or t["context"].search(listy))
            if not ctx_ok[tag]:
                continue
        seen[tag] = m.start()
    return sorted(seen, key=lambda k: seen[k])[:MAX_TAGS]

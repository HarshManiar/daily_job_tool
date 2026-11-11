"""Text helpers: HTML sanitizing, HTML to text, title cleanup, accent folding (rule 1)."""

import html
import re
import unicodedata

import nh3

ALLOWED_TAGS = {"p", "ul", "ol", "li", "strong", "em", "a", "h2", "h3", "h4", "br"}
_INLINE_TAG = re.compile(r"</?(?:strong|em|a|b|i|span|u|code)\b[^>]*>", re.I)
_ANY_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _maybe_unescape(raw: str) -> str:
    """Greenhouse returns entity-escaped HTML (&lt;p&gt;...). Unescape when no real tags exist."""
    if "<" not in raw and "&lt;" in raw:
        return html.unescape(raw)
    return raw


def sanitize_html(raw: str | None) -> str | None:
    if not raw or not raw.strip():
        return None
    raw = _maybe_unescape(raw)
    raw = re.sub(r"<(/?)b(?=[\s>])", r"<\1strong", raw, flags=re.I)
    raw = re.sub(r"<(/?)i(?=[\s>])", r"<\1em", raw, flags=re.I)
    raw = re.sub(r"<h1\b", "<h2", raw, flags=re.I)
    raw = re.sub(r"</h1>", "</h2>", raw, flags=re.I)
    cleaned = nh3.clean(
        raw,
        tags=ALLOWED_TAGS,
        attributes={"a": {"href"}},
        url_schemes={"http", "https", "mailto"},
    )
    return cleaned.strip() or None


def html_to_text(raw: str | None) -> str | None:
    """Plain text with whitespace collapsed to single spaces and entities decoded."""
    if not raw or not raw.strip():
        return None
    s = _maybe_unescape(raw)
    s = re.sub(r"<(?:script|style)\b.*?</(?:script|style)>", " ", s, flags=re.I | re.S)
    s = _INLINE_TAG.sub("", s)
    s = _ANY_TAG.sub(" ", s)
    s = html.unescape(s).replace("\xa0", " ")
    s = _WS.sub(" ", s).strip()
    return s or None


def clean_title(title: str) -> str:
    return _WS.sub(" ", html.unescape(title)).strip()


def fold(s: str) -> str:
    """Lowercase, strip accents, drop dots/apostrophes, hyphens to spaces, collapse whitespace."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[.'’`]", "", s)
    s = re.sub(r"[-_–—]", " ", s)
    return re.sub(r"[ \t\r\n\xa0]+", " ", s).strip()

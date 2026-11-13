"""Print a text outline of the home page (pills, results header, first cards, detail pane) and the
CSS colour tokens, so the layout can be reviewed without a browser.

Usage: uv run python scripts/web_outline.py [http://127.0.0.1:8000/?qs] [N_CARDS]
"""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path

import httpx

CSS = Path(__file__).resolve().parent.parent / "src/daily_job_tool/web/static/app.css"


class Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "svg"):
            self.skip += 1
        if tag in ("input",):
            a = dict(attrs)
            kind = a.get("type")
            if kind in ("checkbox", "radio"):
                self.parts.append(f"[{'x' if 'checked' in a else ' '}]")
            elif kind in ("search", "text", "number"):
                self.parts.append(f"[{a.get('name')}={a.get('value') or ''}]")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "svg"):
            self.skip -= 1
        if tag in ("li", "p", "div", "h1", "h2", "h3", "label", "summary", "button", "a"):
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)

    def text(self) -> str:
        return re.sub(r"\s+", " ", "".join(self.parts)).strip()


def flat(html: str) -> str:
    p = Text()
    p.feed(html)
    return p.text()


def section(html: str, start: str, end: str) -> str:
    i = html.find(start)
    j = html.find(end, i + len(start)) if i >= 0 else -1
    return html[i : j if j > 0 else None] if i >= 0 else ""


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    html = httpx.get(url, timeout=30).text
    top = section(html, '<header class="top">', "</header>")
    print("TOP BAR :", flat(top))
    pills = section(html, '<div class="pills"', '<div class="res-head">')
    print("\nPILLS ROW:")
    for m in re.finditer(
        r'<(summary|a|button) class="(pill[^"]*|allf)"([^>]*)>(.*?)</\1>', pills, re.S
    ):
        label = flat(m.group(4))
        cls = m.group(2)
        extra = " (pressed)" if 'aria-pressed="true"' in m.group(3) else ""
        extra += " (active)" if " on" in cls else ""
        extra += " (filled)" if "fill" in cls else ""
        print(f"  ( {label} )", extra, "dropdown" if m.group(1) == "summary" else "")
    head = section(html, '<div class="res-head">', "<dialog")
    print("\nRESULTS HEADER:", flat(head))
    dlg = section(html, "<dialog", "</dialog>")
    print("\nALL FILTERS DRAWER:", flat(dlg)[:400], "...")
    print("\nLEFT PANE (first cards):")
    cards = re.findall(r'<li class="card[^"]*".*?</li>', section(html, 'id="rows"', "</ul>"), re.S)
    for c in cards[:n]:
        sel = "SELECTED " if "is-selected" in c else ""
        print(f"  [{sel}card] {flat(c)}")
    print(f"  ... {len(cards)} cards in this page")
    print("\nRIGHT PANE (detail):")
    det = section(html, 'id="detail"', "</section>")
    print(" ", flat(det)[:900])
    css = CSS.read_text()
    print("\nCSS COLOUR TOKENS (light | dark):")
    light = dict(re.findall(r"--([\w-]+):([^;]+);", css.split("@media")[0]))
    dark_blk = css.split("prefers-color-scheme:dark){:root{")[1].split("}}")[0]
    dark = dict(re.findall(r"--([\w-]+):([^;]+);", dark_blk))
    for k, v in light.items():
        if k not in ("font", "top-h", "shadow", "overlay"):
            print(f"  --{k}: {v.strip()}  |  {dark.get(k, v).strip()}")


if __name__ == "__main__":
    main()

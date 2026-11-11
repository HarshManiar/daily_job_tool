import pytest

from daily_job_tool.normalize.text import clean_title, html_to_text, sanitize_html
from daily_job_tool.normalize.url import canonical_apply_url, strip_tracking


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://x.com/j/1?utm_source=a&utm_medium=b", "https://x.com/j/1"),
        ("https://x.com/j/1?gh_jid=5&gh_src=abc", "https://x.com/j/1?gh_jid=5"),
        ("https://jobs.lever.co/x/1?lever-source=Twitter", "https://jobs.lever.co/x/1"),
        ("https://jobs.lever.co/x/1?lever-source[]=Twitter&k=v", "https://jobs.lever.co/x/1?k=v"),
        ("https://x.com/j?ref=abc&source=li&id=7", "https://x.com/j?id=7"),
        ("https://x.com/j", "https://x.com/j"),
    ],
)
def test_strip_tracking(url, expected):
    assert strip_tracking(url) == expected


def test_canonical_prefers_employer_domain():
    got = canonical_apply_url(
        [
            "https://boards.greenhouse.io/acme/jobs/1",
            "https://www.acme.com/careers/1?gh_jid=1&utm_x=1",
        ],
        "acme.com",
    )
    assert got == "https://www.acme.com/careers/1?gh_jid=1"


def test_canonical_falls_back_to_ats_page():
    got = canonical_apply_url([None, "https://jobs.lever.co/acme/abc?lever-source=x"], "acme.com")
    assert got == "https://jobs.lever.co/acme/abc"


def test_canonical_refuses_aggregators():
    assert canonical_apply_url(["https://www.linkedin.com/jobs/view/1"], "acme.com") is None
    assert canonical_apply_url(["https://uk.indeed.com/viewjob?jk=1"], None) is None
    got = canonical_apply_url(
        ["https://www.linkedin.com/jobs/view/1", "https://boards.greenhouse.io/a/jobs/2"], None
    )
    assert got == "https://boards.greenhouse.io/a/jobs/2"


def test_canonical_rejects_non_http():
    assert canonical_apply_url(["javascript:alert(1)", "mailto:a@b.c"], None) is None


def test_sanitize_allows_only_listed_tags():
    html = (
        '<h1>Title</h1><p onclick="x()">Hi <b>bold</b> <i>it</i> <a href="https://a.com" '
        'target="_blank" style="x">link</a></p><script>alert(1)</script><div>d</div>'
        '<a href="javascript:alert(1)">bad</a><ul><li>a</li></ul><img src="x.png">'
    )
    out = sanitize_html(html)
    assert out is not None
    assert "<script" not in out and "alert(1)" not in out.replace("javascript:alert(1)", "")
    assert "onclick" not in out and "style" not in out and "target" not in out
    assert "javascript:" not in out and "<img" not in out and "<div" not in out
    assert "<h2>Title</h2>" in out and "<strong>bold</strong>" in out and "<em>it</em>" in out
    assert 'href="https://a.com"' in out and "<li>a</li>" in out


def test_sanitize_unescapes_entity_encoded_html():
    out = sanitize_html("&lt;p&gt;Hello &lt;strong&gt;you&lt;/strong&gt;&lt;/p&gt;")
    assert out == "<p>Hello <strong>you</strong></p>"


def test_html_to_text_collapses_whitespace_and_decodes():
    html = "<p>Hello&nbsp;&amp; welcome</p>\n\n<ul>\n  <li>One</li><li>Two</li></ul><br>Done&#39;s"
    assert html_to_text(html) == "Hello & welcome One Two Done's"


def test_empty_inputs():
    assert sanitize_html(None) is None and sanitize_html("  ") is None
    assert html_to_text(None) is None and html_to_text("") is None


def test_clean_title_decodes_entities():
    assert clean_title("  R&amp;D   Engineer ") == "R&D Engineer"

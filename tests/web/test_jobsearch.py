"""Two-pane job search: new filters, per-job state, card/detail rendering, a11y smoke."""

import re

from sqlalchemy import text

from tests.web.test_pages import titles

H = {"Origin": "http://127.0.0.1:8000"}
HX = {"HX-Request": "true"}


async def t(client, qs: str = "") -> set[str]:
    r = await client.get("/?" + qs)
    assert r.status_code == 200, qs
    return set(titles(r.text))


async def state(db, jid: int):
    async with db.engine.connect() as c:
        return (
            (await c.execute(text("SELECT * FROM job_state WHERE job_id = :i"), {"i": jid}))
            .mappings()
            .first()
        )


# ---- filters -----------------------------------------------------------------------------


async def test_sponsorship_toggle_lifts_only_the_visa_exclusion(client, db):
    c = await db.company("Spons")
    await db.job(c, title="plain")
    await db.job(c, title="offers", visa_status="offered", match_reasons=["visa:offered"])
    nov = dict(
        match_status="excluded",
        visa_status="not_available",
        visa_evidence="We are unable to sponsor visas",
    )
    await db.job(c, title="visa-only", match_reasons=["visa:not_available", "title:not_target"],
                 **nov)  # fmt: skip
    await db.job(c, title="visa+contract", match_reasons=["visa:not_available",
                 "employment:contract"], **nov)  # fmt: skip
    await db.job(c, title="visa+7yrs", match_reasons=["visa:not_available", "years:min_7+"], **nov)
    await db.job(c, title="visa+staffing", match_reasons=["company:staffing",
                 "visa:not_available"], **nov)  # fmt: skip
    await db.job(c, title="contract-only", match_status="excluded",
                 match_reasons=["employment:contract"])  # fmt: skip

    assert await t(client) == {"plain", "offers"}  # toggle ON by default
    assert await t(client, "sponsorship=1") == {"plain", "offers"}
    assert await t(client, "sponsorship=0") == {"plain", "offers", "visa-only"}
    r = await client.get("/?sponsorship=0")
    assert r.text.count("Says no sponsorship") >= 2  # card tag + detail chip/why
    assert "Offers sponsorship" in r.text
    assert "Says no sponsorship" not in (await client.get("/")).text
    # the Filtered-out view is unchanged either way
    f = (await client.get("/filtered?sponsorship=0")).text
    assert {"visa-only", "visa+contract", "contract-only"} <= set(titles(f))


async def test_sponsorship_toggle_markup(client, db):
    c = await db.company()
    await db.job(c)
    on = (await client.get("/")).text
    assert re.search(r'role="button" aria-pressed="true"[^>]*>Did not mention no sponsorship', on)
    off = (await client.get("/?sponsorship=0")).text
    assert re.search(r'aria-pressed="false"[^>]*>Did not mention no sponsorship', off)
    assert 'name="sponsorship" value="0"' in off  # other Apply buttons keep the state


async def test_experience_buckets(client, db):
    c = await db.company("Exp")
    await db.job(c, title="unk")
    await db.job(c, title="y1", years_min=1)
    await db.job(c, title="y0-2", years_min=0, years_max=2)
    await db.job(c, title="y3", years_min=3)
    await db.job(c, title="y2-4", years_min=2, years_max=4)
    await db.job(c, title="y4-6", years_min=4, years_max=6)
    await db.job(c, title="y5+", years_min=5)
    await db.job(c, title="y6", years_min=6, years_max=6)
    assert await t(client, "experience=0-2") == {"unk", "y1", "y0-2", "y2-4"}
    assert await t(client, "experience=3-5") == {"unk", "y3", "y2-4", "y4-6", "y5+"}
    assert await t(client, "experience=5%2B") == {"unk", "y5+", "y6"} | {"y4-6"}
    assert await t(client, "experience=any") == await t(client)
    assert await t(client, "only_stated_years=1") == {
        "y1", "y0-2", "y3", "y2-4", "y4-6", "y5+", "y6"
    }  # fmt: skip
    assert await t(client, "experience=0-2&only_stated_years=1") == {"y1", "y0-2", "y2-4"}
    assert await t(client, "experience=5%2B&only_stated_years=1") == {"y5+", "y6", "y4-6"}
    page = (await client.get("/")).text
    assert (
        "Jobs that don't state years are included" in page and "Only jobs that state years" in page
    )
    assert (await client.get("/?experience=nope")).status_code == 422


async def test_verified_open(client, db):
    c = await db.company("Ver")
    fresh = await db.job(c, title="seen-now", age_days=3)
    await db.job(c, title="seen-3d-ago", age_days=3)
    async with db.engine.begin() as conn:
        await conn.execute(
            text("UPDATE jobs SET last_seen_at = now() - interval '1 hour' WHERE id = :i"),
            {"i": fresh},
        )
        await conn.execute(
            text("UPDATE jobs SET last_seen_at = now() - interval '3 days' WHERE id <> :i"),
            {"i": fresh},
        )
    assert await t(client, "verified=1") == {"seen-now"}
    assert await t(client) == {"seen-now", "seen-3d-ago"}
    page = (await client.get("/?verified=1")).text
    assert "Seen on the employer&#39;s site in the last 48 hours" in page or (
        "Seen on the employer's site in the last 48 hours" in page
    )


async def test_company_multiselect_and_chips(client, db):
    ids = [await db.company(n) for n in ("Alpha Co", "Beta Co", "Gamma Co")]
    for cid, n in zip(ids, "abc", strict=True):
        await db.job(cid, title=f"job-{n}")
    assert await t(client, f"companies={ids[0]}") == {"job-a"}
    assert await t(client, f"companies={ids[0]}&companies={ids[2]}") == {"job-a", "job-c"}
    assert await t(client, f"companies={ids[1]},{ids[2]}") == {"job-b", "job-c"}
    html = (await client.get(f"/?companies={ids[0]}")).text
    assert "Alpha Co" in html and f'name="companies" value="{ids[0]}" checked' in html
    assert (await client.get("/?companies=" + "&companies=".join(map(str, range(1, 22))))
            ).status_code == 422  # max 20  # fmt: skip
    assert (await client.get("/?companies=abc")).status_code == 422


async def test_date_options_and_shortcuts(client, db):
    c = await db.company("Dt")
    for name, age in [("h5", 5 / 24), ("h30", 30 / 24), ("d5", 5), ("d10", 10), ("d20", 20),
                      ("d45", 45)]:  # fmt: skip
        await db.job(c, title=name, age_days=age)
    assert await t(client, "days=all") == {"h5", "h30", "d5", "d10", "d20", "d45"}
    assert await t(client, "days=1") == {"h5"}
    assert await t(client, "days=2") == {"h5", "h30"}
    assert await t(client, "days=7") == {"h5", "h30", "d5"}
    assert await t(client) == {"h5", "h30", "d5", "d10"}  # past 2 weeks is the default
    assert await t(client, "days=30") == {"h5", "h30", "d5", "d10", "d20"}
    assert await t(client, "posted24=1&days=30") == {"h5"}  # shortcuts override the radio
    assert await t(client, "posted48=1&days=7") == {"h5", "h30"}
    assert await t(client, "posted24=1&posted48=1") == {"h5", "h30"}  # both = 48 h
    page = (await client.get("/")).text
    for lbl in ("Any time", "Past 24 hours", "Past 48 hours", "Past week", "Past 2 weeks",
                "Past month", "Posted last 24 hours", "Posted last 48 hours"):  # fmt: skip
        assert lbl in page


async def test_combined_filters_and_drawer_fields(client, db):
    c = await db.company("Comb")
    keep = await db.job(c, title="keep", remote_type="remote", years_min=3, age_days=0.5,
                        salary_min_usd=130000, salary_max_usd=150000, visa_status="offered")  # fmt: skip
    await db.job(c, title="wrong-exp", remote_type="remote", years_min=0, years_max=1, age_days=0.5)
    await db.job(c, title="wrong-mode", remote_type="onsite", years_min=3, age_days=0.5)
    qs = f"remote=remote&experience=3-5&posted24=1&companies={c}&min_pay=100000&verified=1&visa_only=1"
    assert await t(client, qs) == {"keep"}
    page = (await client.get("/?" + qs)).text
    assert 'value="remote" selected' in page and 'value="3-5" checked' in page
    assert 'name="verified" value="1"' in page and "visa_only" in page
    assert keep


async def test_search_box_finds_company_and_description(client, db):
    c = await db.company("Zebra Labs")
    await db.job(c, title="Analyst", description_text="uses airflow daily")
    assert await t(client, "q=airflow") == {"Analyst"}


# ---- state: viewed / dismissed ------------------------------------------------------------


async def test_dismiss_undismiss_viewed_persist(client, db):
    c = await db.company("St")
    a = await db.job(c, title="keep-me")
    b = await db.job(c, title="drop-me")
    r = await client.post(f"/jobs/{b}/dismiss", headers=H, data={"next": "/?days=7"})
    assert r.status_code == 303 and r.headers["location"] == "/?days=7"
    row = await state(db, b)
    assert row["dismissed_at"] is not None
    assert await t(client) == {"keep-me"}  # hidden from every list by default
    assert "drop-me" not in (await client.get("/filtered")).text
    assert await t(client, "show_dismissed=1") == {"drop-me"}
    shown = (await client.get("/?show_dismissed=1")).text
    assert f'action="/jobs/{b}/undismiss"' in shown and "Undo" in shown
    # htmx dismiss returns the small "Undo" card
    r = await client.post(f"/jobs/{a}/dismiss", headers={**H, **HX})
    assert r.status_code == 200 and "Undo" in r.text and f'id="card-{a}"' in r.text
    r = await client.post(f"/jobs/{a}/undismiss", headers={**H, **HX})
    assert r.status_code == 200 and "keep-me" in r.text and f'id="card-{a}"' in r.text
    assert (await state(db, a))["dismissed_at"] is None
    r = await client.post(f"/jobs/{b}/undismiss", headers=H)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert await t(client) == {"keep-me", "drop-me"}
    # viewed
    assert (await state(db, a))["viewed_at"] is None
    assert (await client.post(f"/jobs/{a}/viewed", headers=H)).status_code == 204
    assert (await state(db, a))["viewed_at"] is not None
    assert "is-viewed" in (await client.get("/")).text
    # next= is local-only
    r = await client.post(f"/jobs/{b}/dismiss", headers=H, data={"next": "//evil.example"})
    assert r.headers["location"] == "/"
    # the row goes away with its job
    async with db.engine.begin() as conn:
        await conn.execute(text("DELETE FROM jobs WHERE id = :i"), {"i": b})
    assert await state(db, b) is None


async def test_state_routes_need_origin_and_existing_job(client, db):
    c = await db.company()
    j = await db.job(c)
    for action in ("viewed", "dismiss", "undismiss"):
        assert (await client.post(f"/jobs/{j}/{action}")).status_code == 403
        bad = await client.post(f"/jobs/{j}/{action}", headers={"Origin": "http://evil.example"})
        assert bad.status_code == 403
        assert (await client.post(f"/jobs/999999/{action}", headers=H)).status_code == 404
    assert await state(db, j) is None


async def test_dismissed_excluded_from_counts_and_saved(client, db):
    c = await db.company("Cnt")
    a = await db.job(c, title="a", age_days=0.1)
    await db.job(c, title="b", age_days=0.1)
    await client.post(f"/jobs/{a}/dismiss", headers=H)
    assert "<strong>1</strong> result" in (await client.get("/")).text
    assert (await client.get("/api/v1/jobs")).json()["total"] == 1


# ---- rendering ----------------------------------------------------------------------------


async def test_card_and_detail_content(client, db):
    c = await db.company("Roivant Sciences", slug="roivant")
    j = await db.job(
        c,
        title="Senior Analyst",
        remote_type="hybrid",
        locations='[{"city": "New York", "admin1": "NY"}]',
        salary_min_usd=89200,
        salary_max_usd=150000,
        employment_type="full_time",
        years_min=3,
        years_max=5,
        visa_status="offered",
        visa_evidence="We will sponsor H-1B visas",
        match_tier=1,
        age_days=0.02,
    )
    home = (await client.get("/")).text
    assert f'href="/jobs/{j}"' in home  # real link: works without JS and on narrow screens
    for s in ["Senior Analyst", "Roivant Sciences", "New York, NY (Hybrid)", "$89.2K–$150K/yr",
              "Offers sponsorship", "3–5 yrs", "minutes ago", "Viewed"]:  # fmt: skip
        assert s in home
    assert "T1" not in home and "Tier" not in home and "tier" not in home.lower().split("<main")[1]
    assert re.search(r'<span class="avatar" aria-hidden="true">R</span>', home)
    # first job opens in the detail pane on the home page
    assert f'data-job="{j}"' in home and "Why this matched" in home
    assert "Apply on Roivant Sciences" in home and "Not interested" in home
    assert "We will sponsor H-1B visas" in home
    assert 'href="/companies/roivant"' in home
    assert "Seen on the employer&#39;s site" in home or "Seen on the employer's site" in home


async def test_apply_link_only_http(client, db):
    c = await db.company("Ap")
    ok = await db.job(c, title="ok", apply_url="https://example.com/a")
    bad = await db.job(c, title="bad", apply_url="javascript:alert(1)")
    d = (await client.get(f"/jobs/{ok}")).text
    assert (
        'class="btn-primary apply" href="https://example.com/a" target="_blank" rel="noopener noreferrer"'
        in d
    )
    d = (await client.get(f"/jobs/{bad}")).text
    assert "javascript:" not in d and "Apply on" not in d
    home = (await client.get("/")).text
    assert "javascript:" not in home and 'data-apply="https://example.com/a"' in home


async def test_detail_partial_vs_full_and_sanitize(client, db):
    c = await db.company("Det")
    j = await db.job(
        c,
        title="Detail Job",
        description_html='<p>Hi</p><script>alert(1)</script><img src=x onerror="alert(2)">',
        visa_status="not_available",
        visa_evidence="no visa sponsorship available",
    )
    full = await client.get(f"/jobs/{j}")
    assert "<html" in full.text and "Back to matches" in full.text and "<h1" in full.text
    part = await client.get(f"/jobs/{j}", headers=HX)
    assert "<html" not in part.text and "<article" in part.text and "Detail Job" in part.text
    assert f'hx-post="/jobs/{j}/viewed"' in part.text  # marks viewed when the pane loads
    for body in (full.text, part.text):
        assert "<p>Hi</p>" in body and "<script>alert" not in body and "onerror" not in body
        assert "Says no sponsorship" in body and "no visa sponsorship available" in body
    assert "Vary" in part.headers and "HX-Request" in part.headers["vary"]
    assert (await client.get("/jobs/999999", headers=HX)).status_code == 404


async def test_mobile_markup_card_links_to_detail_page(client, db):
    c = await db.company()
    j = await db.job(c, title="Mobile One")
    html = (await client.get("/")).text
    assert f'<a class="job-title" href="/jobs/{j}">Mobile One</a>' in html
    css = (await client.get("/static/app.css")).text
    assert "max-width:979px" in css and ".detail-pane{display:none}" in css
    assert (await client.get("/static/app.js")).status_code == 200
    assert "<script>" not in html and ' style="' not in html  # CSP: no inline script/style


async def test_new_badge_and_tags_without_tier(client, db):
    c = await db.company("Nw")
    await db.job(c, title="x", match_tier=2, match_reasons=["employment:unknown"])
    html = (await client.get("/")).text
    assert "type not stated" in html and "T2" not in html


async def test_empty_state_has_reset(client, db):
    r = await client.get("/?q=nothingmatchesthis")
    assert "No jobs match these filters" in r.text and "Reset filters" in r.text


# ---- company typeahead --------------------------------------------------------------------


async def test_company_search_endpoint(client, db):
    a = await db.company("Anthem Health")
    b = await db.company("Banana Analytics")
    await db.company("Empty Inc")  # no open jobs: not offered
    await db.job(a, title="x")
    await db.job(b, title="y")
    r = await client.get("/companies/search?q=an")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    names = [i["name"] for i in r.json()["items"]]
    assert names == ["Anthem Health", "Banana Analytics"]  # prefix match first
    assert (await client.get("/companies/search?q=zzz")).json() == {"items": []}
    assert (await client.get("/companies/search")).json() == {"items": []}
    r = await client.get(f"/companies/search?q=an&companies={a}")
    assert [i["name"] for i in r.json()["items"]] == ["Banana Analytics"]  # already picked
    h = await client.get("/companies/search?q=an", headers=HX)
    assert "<html" not in h.text and 'name="companies"' in h.text and "Banana Analytics" in h.text
    assert (await client.get("/companies/search?q=" + "x" * 101)).status_code == 422
    # LIKE wildcards are escaped
    assert (await client.get("/companies/search?q=%25")).json() == {"items": []}


# ---- accessibility smoke ------------------------------------------------------------------


async def test_a11y_smoke(client, db):
    c = await db.company()
    await db.job(c)
    html = (await client.get("/")).text
    assert html.count("aria-pressed=") >= 2
    assert re.search(r'<dialog id="all-filters" class="drawer" aria-labelledby="af-h">', html)
    assert 'id="af-h"' in html and 'aria-label="Close filters"' in html
    assert 'type="search" name="q" form="filters"' in html and 'aria-label="Search jobs"' in html
    assert 'role="status" aria-live="polite"' in html
    assert 'aria-label="Dismiss ' in html and 'aria-label="Keyboard shortcuts"' in html
    assert 'id="detail" tabindex="-1" aria-label="Job details"' in html
    assert 'class="skip"' in html
    for label in ("Jobs", "Date posted", "Company", "Experience", "Verified open", "All filters",
                  "How matches are ranked", "United States"):  # fmt: skip
        assert label in html
    assert "Matches" in html and "Manual check" in html and "Health" in html
    assert "Refresh now" in html or 'hx-get="/refresh/status"' in html
    # every form control in the drawer sits inside a label
    drawer = html.split('<dialog id="all-filters"')[1].split("</dialog>")[0]
    assert drawer.count("<label") >= drawer.count("<select") + drawer.count('type="number"')


async def test_location_control_shows_chosen_place(client, db):
    c = await db.company()
    await db.job(c)
    assert "United States" in (await client.get("/")).text
    page = (await client.get("/?place=nj")).text
    assert "<span>New Jersey</span>" in page


async def test_other_pages_use_new_top_bar(client, db):
    for path in ("/manual", "/health", "/about", "/privacy", "/bot", "/remove", "/saved"):
        r = await client.get(path)
        assert r.status_code == 200, path
        assert 'class="brand"' in r.text and "Manual check" in r.text, path

# Custom career pages: feasibility study (14 companies)

> **Owner decision (2026-11-11): the HARD and BLOCKED companies are dropped and will not be built:** Waystar, Hippocratic AI, Thoughtful AI (hard) and Spring Health (blocked: its job endpoint is disallowed by robots.txt). Everything else in this study is in scope. Revisit Hippocratic AI only if the owner reports where its "View Open Roles" button lands.


Study date: 2026-11-11. Research only: nothing under `src/`, `tests/` or the dev DB was touched, and `data/seeds.csv` was not modified.
All fetches went through `CrawlClient` (UA `DailyJobToolBot/0.1`, robots + rate limits enforced), plus a handful of plain `curl` fetches of `robots.txt` files to read their text. No logins, no form submissions, no bot-wall bypass. Roughly 100-120 page/API requests in total, at most ~25 per company.

Important repo fact: `ADAPTER_MODULES` in `src/daily_job_tool/adapters/registry.py` currently registers only `greenhouse`, `lever`, `ashby`. There is no `workable`, `rippling` or `gem` adapter yet, so "EASY" below means "no new parsing logic needed" and Innovaccer's row needs the (trivial) Workable adapter to exist first.

## Summary

| Company | Verdict | How to fetch | Effort | Adapter shape | Posted date? | Jobs seen |
|---|---|---|---|---|---|---|
| Color Health | EASY | Ashby board `color-health` (hidden behind CareerPuck links) | S (seeds row only) | existing `ashby` | Yes (`publishedAt`) | 17 |
| Innovaccer | EASY (needs Workable adapter) | Workable widget API, account `innovaccer-analytics` | S | `workable` JSON (trivial) | Yes (`published_on`) | 99 (43 US) |
| Carbon Health | MEDIUM | Rippling ATS board `carbon-health` (`api.rippling.com/platform/api/ats/v1/board/carbon-health/jobs`) | S/M | `rippling` JSON list + detail via `__NEXT_DATA__` | Detail only (`createdOn`) | 214 unique (298 location rows) |
| Mindbloom | MEDIUM | Gem public API `api.gem.com/job_board/v0/mindbloom/job_posts/` | S | `gem` JSON | Yes (`first_published_at`) | 5 |
| Transcarent | MEDIUM | Darwinbox candidate API `transcarent.darwinbox.com/ms/candidateapi/job?page=N` | S/M | `darwinbox` JSON | Yes (`created_on`) | 30 |
| Accolade | MEDIUM (duplicate of Transcarent) | `accolade.com/careers` serves the identical Transcarent site (acquired); same Darwinbox board | none extra | same as Transcarent | Yes | (same 30) |
| PharmaACE | MEDIUM | Own JSON API `pharmaace.com/api/V1/careers/jobs` | S | bespoke JSON, or generic `json_list` with config | Yes (`created_at`) | 56 rows, 19 `Open` |
| Hyro | MEDIUM | WordPress HTML list `hyro.ai/careers/` + detail pages (Comeet-sourced) | S/M | "HTML list+detail with selectors" (WP/Yoast) | Approx (Yoast `datePublished` on detail) | 7 |
| Medidata | MEDIUM | `en/career-sitemap.xml` (80 job URLs) + HTML detail pages | M | "sitemap + HTML detail" (WP/Yoast) | Approx (Yoast `datePublished` on detail) | 80 |
| Doceree | MEDIUM | Server-rendered HTML list `doceree.com/careers` + detail pages | S/M | "HTML list+detail with selectors" | No (only a "New" chip) | 24 (11 Noida, 13 NJ) |
| Waystar | HARD | `careers.waystar.com` is a client-side SPA; only `sitemaps/jobs_1.xml` is readable (titles/locations from slugs) | L (or give up) | none | No (sitemap lastmod all 2026-11-08) | 47 (sitemap only) |
| Hippocratic AI | HARD | Open roles sit behind a values-pledge form (Gravity Forms); no public list found | L / not feasible without submitting the form | none | Unknown | unknown |
| Thoughtful AI | HARD | Company merged into Access Healthcare; US jobs on a Paycom SPA that needs an anonymous session JWT | L | `paycom` (session-bound) | Unknown | unknown |
| Spring Health | BLOCKED | Page calls `/sh-app/api/jobs`, which `robots.txt` disallows | n/a | n/a | n/a | not fetched |

Counts: EASY 2 (Color Health, Innovaccer), MEDIUM 8 (Carbon Health, Mindbloom, Transcarent, Accolade, PharmaACE, Hyro, Medidata, Doceree), HARD 3 (Waystar, Hippocratic AI, Thoughtful AI), BLOCKED 1 (Spring Health).

## Per-company evidence

### Color Health: EASY (Ashby)
- Careers page `https://color.com/careers/` is server-rendered; each "newest opportunities" card links to `https://app.careerpuck.com/job-board/color-health/job/<ashby-job-uuid>`. CareerPuck is a front-end skin; its SPA calls `https://api.careerpuck.com/v1/public/job-boards/color-health`, whose JSON has `atsSourcePlatform: "ashby"` and `atsSourceId` = the Ashby job id.
- Ashby board verified: `https://api.ashbyhq.com/posting-api/job-board/color-health` returns 17 jobs (matches CareerPuck's 17). Token `color-health` (plain `color` and `colorhealth` are 404).
- Robots: `api.ashbyhq.com` fine via existing adapter; CareerPuck robots allow all (not needed).
- Samples: "Medical Oncologist, Clinical Care Lead" | Remote, USA | 2026-04-23 | FullTime | jobs.ashbyhq.com/color-health/4b73f0e9-...; "Sr. Product Manager - Patient Services" | South San Francisco, California | 2026-05-08; "Sr. Product Manager - Core Services" | South San Francisco | 2026-06-23.
- Ashby gives description, location, employment type, `publishedAt`, and compensation (with `includeCompensation=true`).
- Fix: row in `data/custom_page_sources.csv`. Risk: very low.

### Innovaccer: EASY (Workable, needs Workable adapter)
- `innovaccer.com/careers` is a Webflow page that only links to `/careers/jobs`. The detector never fetched that subpage. `/careers/jobs` is server-rendered: 95 cards linking `https://apply.workable.com/j/<shortcode>` (shortcode links do not reveal the account).
- Fetching one shortcode page (`apply.workable.com/j/8EE4D3922D`) shows canonical `https://apply.workable.com/innovaccer-analytics/j/8EE4D3922D`, so the token is `innovaccer-analytics` (`innovaccer` and `innovaccer-1` return empty).
- `https://apply.workable.com/api/v1/widget/accounts/innovaccer-analytics` returns 99 jobs (56 India, 43 United States) with `published_on`/`created_at`, `city/state/country`, `employment_type` (blank here), `department`, `url`. `apply.workable.com/robots.txt`: `Disallow:` empty (all allowed).
- Samples: "01-Software Development Engineer - III" | Noida, India | 2026-09-25 | apply.workable.com/j/D1E02E0D44; "1003-Software Development Engineer-III Full Stack (MVC)" | Noida | 2026-09-16; "1004-Staff Engineer-Full Stack (MVC)" | Noida | 2026-09-16.
- Titles carry internal requisition prefixes (e.g. "4772-Manager - GTM Enablement"); consider stripping `^\d+-`. Roughly 56% of jobs are in India, so the US filter matters.
- Description/pay need `?details=true` on the widget endpoint or the detail page (not checked).

### Carbon Health: MEDIUM (Rippling)
- `carbonhealth.com/careers` (Next.js) renders `<div id="rr-job-board" data-job-board-id="carbon-health">` plus `static-assets.ripplingcdn.com/ats/embeds/job-board.v1.js`, which injects an iframe `https://ats.rippling.com/embed/carbon-health/jobs`. Board slug: `carbon-health`.
- Public board: `https://ats.rippling.com/carbon-health/jobs` (Next.js, has `__NEXT_DATA__`). JSON list: `GET https://api.rippling.com/platform/api/ats/v1/board/carbon-health/jobs` returns a flat array of `{uuid, name, department{label}, url, workLocation{label}}` with one row per location (298 rows, 214 unique uuids; dedupe on uuid and merge locations). No pagination needed.
- Robots: `ats.rippling.com/robots.txt` = `Disallow: /internal/` only. `api.rippling.com/robots.txt` returns 404 (no restrictions). Caveat: I found the API path from Rippling's documented board API rather than from a bundle; the iframe page itself ships the same data in `__NEXT_DATA__` as a robots-clean fallback.
- Detail page `ats.rippling.com/carbon-health/jobs/<uuid>` has `__NEXT_DATA__.props.pageProps.apiData.jobPost` with `description` (HTML), `createdOn` (posted date, e.g. 2026-07-22T11:43:35-07:00), `employmentType` ("Salaried, full-time"), `workLocations`, `payRangeDetails`, `department`. `jsonLd` is null.
- Samples: "Area Medical Director (Physician Assistant / Nurse Practitioner)" | Monroe Township, NJ | ats.rippling.com/carbon-health/jobs/b34362b9-...; same title at Pasadena, CA (af968b46-...); "Behavioral Health Clinician - Full Time" | Boston, MA (7c1662ea-...).
- Cost: the list has no dates, so a posted-date filter needs 1 detail fetch per new uuid (214 on first run at 1 req/s, then incremental). Rippling is a common ATS, so a `rippling` adapter will pay off for other companies.

### Mindbloom: MEDIUM (Gem)
- `mindbloom.com/careers` (Webflow) has an inline script: `fetch('https://api.gem.com/job_board/v0/mindbloom/job_posts/')`, then renders cards grouped by department. This is the endpoint the page itself calls.
- Response: JSON array, 5 jobs: `{id, title, absolute_url (jobs.gem.com/mindbloom/<id>), content (HTML), content_plain, created_at, first_published_at, updated_at, employment_type (full_time|part_time), location{name}, location_type, departments[], offices[], requisition_id}`.
- Robots: `api.gem.com/robots.txt` returns 403 (our client still allowed the job_posts call; treat as "no robots published", but flag). `jobs.gem.com/robots.txt` is a 404.
- Samples: "Psychiatric Clinician (PMHNP) Missouri Licensed" | United States - Remote | 2026-09-18 | part_time | jobs.gem.com/mindbloom/am9icG9zdDrE_wmRRrUC67DFi6pAxIDW; "Collaborating Psychiatrist - All States" | Remote | 2026-05-01 | part_time; "Psychiatric Clinician - All States (PMHNP/PA)" | Remote | 2025-12-01 | part_time.
- Nothing posted in the last 14 days at study time; one catch-all "Don't See Your Role? Apply Here!" posting should be filtered. Pay is not in the structured fields.
- Gem boards appear on many startups (Spring Health's talent-community form is also Gem-hosted), so a `gem` adapter is reusable.

### Transcarent: MEDIUM (Darwinbox)
- `transcarent.com/careers` (Next.js + Contentful, 2.4 MB) has the button "TRANSCARENT OPENINGS" linking `https://transcarent.darwinbox.com/ms/candidatev2/main/careers/allJobs`. That page, fetched as a crawler, is a tiny shell (898 bytes, no scripts), i.e. a pure SPA.
- Darwinbox candidate API (public, anonymous): `GET https://transcarent.darwinbox.com/ms/candidateapi/job?page=N` returns `{"status":"success","message":{"jobscount":30,"jobs":[...]}}` with 10 jobs per page (`page=2`, `page=3` verified; `offset` is ignored). Fields: `id`, `title`, `department`, `emp_type`, `officelocation_show_arr`, `created_on` (ISO), `job_posting_on` (epoch), `experience_from_num/to_num`.
- I did not read the SPA bundle (only the crawler shell was served), so the endpoint was identified from Darwinbox's known candidate API and confirmed by response, not from the page's own JS. Robots: `darwinbox.com/robots.txt` 302s to the login page (no robots file served); treat as no restrictions but keep to ~1 req/s.
- Detail: not fetched; Darwinbox detail view is `.../careers/jobDetails/<id>` (SPA) and likely has its own API. List-only gives title/location/department/date, which may be enough for matching; description would need a second endpoint (not investigated).
- Samples: "Senior Manager, Pharmacy Solutions" | Chicago, Illinois | 2026-10-29 | Product Solutions; "VP, Strategic Sales" | Pennsylvania, Remote | 2026-11-05 | Sales; "Fulfillment Associate" | Plymouth Meeting, Pennsylvania | 2026-10-22.
- Separate: `transcarent.com/current-job-openings-medical-group` embeds a Greenhouse board `for=transcarentaffliatedmedicalgroup` (sic). The Greenhouse API `boards-api.greenhouse.io/v1/boards/transcarentaffliatedmedicalgroup/jobs` returns 404 (and `boards.greenhouse.io/embed/...` is robots-disallowed), so that board looks empty or renamed.

### Accolade: MEDIUM (same as Transcarent)
- Transcarent acquired Accolade; `accolade.com/careers` returns a byte-identical page (2,473,379 bytes) to `transcarent.com/careers`. `jobs.accolade.com` is robots-disallowed. Use one source (Transcarent) for both; drop Accolade as a separate seed.

### PharmaACE: MEDIUM (own JSON API)
- `pharmaace.com/career/jobs` (Next.js, client-side). The page chunk `app/career/jobs/page-*.js` dispatches `fetchJobs`, defined in a shared chunk (`498/458/598-*.js`) with `API_BASE_URL = "https://pharmaace.com/api/V1"` and path `/careers/jobs`.
- `GET https://pharmaace.com/api/V1/careers/jobs` returns a plain JSON array (56 rows, no pagination). Fields: `job_id, position, location, practice, position_type (Full_Time), status (Open|Closed), created_at, updated_at, deadline, description/responsibilities/qualifications (HTML), summary`. Filter `status == "Open"` (19 rows; the rest are Closed, including test records such as "Testing Job 5").
- Robots: `pharmaace.com/robots.txt` = `Allow: /` (no API restrictions).
- Samples (Open): "Project Manager - Market Access" | Pune, India | 2026-10-09 | Full_Time; "Senior Consultant - Advanced Analytics" | Princeton, United States | 2026-08-10; "Project Manager - Forecasting" | Thousand Oaks, United States | 2025-09-24. Only 6 of 19 open roles are US-based; no pay fields. Public detail URL pattern not verified (the SPA route is probably `/career/jobs/<job_id>`).
- Effort S: one GET, trivial mapping. Risk: low-medium (API version path `V1`, bespoke).

### Hyro: MEDIUM (WordPress HTML + Comeet)
- `hyro.ai/careers/` (WordPress theme `hyro-v2`) server-renders the list: `li.site-main_section--positions__item` with `data-title`, `data-department`, `data-location`, and `a.site-main_section--positions__link` -> `https://www.hyro.ai/careers/<comeet-uid>/` (e.g. `17.D60`). 7 jobs. Data is Comeet-sourced (detail contains `<script type="comeet-applyform" data-position-uid="17.D60">`), but the Comeet API needs a company token that is not exposed.
- Detail pages have an `h1`, location, department, "Full-time" text, and the description. Posted date: the Yoast JSON-LD `WebPage.datePublished` (e.g. 2026-09-02 for the first job), which is the WP post date, a good proxy. No `JobPosting` JSON-LD.
- Robots: `hyro.ai/robots.txt` only blocks WP internals/query URLs; `/careers/` is allowed. Crawl-delay not set.
- Samples: "Engagement Manager (NYC)" | New York City, US | Delivery; "RVP Health Plans Sales" | Remote, US | Sales; "Software Architect" | Tel Aviv, IL | Engineering.
- Effort S/M (8 requests per run: 1 list + 7 details). Risk: medium (theme CSS classes changed on 2026-10-21 "new site launch").

### Medidata: MEDIUM (WordPress sitemap + HTML detail)
- `medidata.com/en/about-us/medidata-careers/` shows ~26 featured jobs; the full search page `en/careers/job-search/` is a JS catalog with filters, not scrapeable. Robust alternative: WordPress `https://www.medidata.com/en/career-sitemap.xml` lists all 80 job URLs (`/en/careers/<slug>-<reqid>/job/`) with `lastmod`.
- Detail page (server-rendered, Elementor): `h1` title, "Requisition ID", "Category", "Location" (e.g. "United States - NY, New York"), hybrid-policy text, long description; Yoast `datePublished` (e.g. 2026-10-19/20). No `JobPosting` JSON-LD, no employment type or pay found. The "Apply" button hands off to 3ds.com (Dassault Systemes ATS).
- Robots: `medidata.com/robots.txt` disallows only plugins/admin/specific PDFs; `/en/careers/` and `career-sitemap.xml` are allowed.
- Samples: "Senior Data Engineer" | Iselin, NJ (United States - NJ) | 2026-10-19 | /en/careers/senior-data-engineer-549311/job/; "Lead AI & Data Engineer" | New York, NY | 2026-10-20 | -550610; "Senior SDET" | New York, NY | 2026-10-19 | -550579.
- Cost: 1 sitemap + 80 detail fetches per run (or incremental on new URLs; also `lastmod` as a change signal). Effort M. Risk: medium (WordPress/Elementor markup). Underlying system is 3DS (`careers.3ds.com`), which is likely Phenom/Workday-like and could be a better source but was not investigated.

### Doceree: MEDIUM (server-rendered HTML)
- `doceree.com/careers` (Next.js, SSR) lists 24 roles as `li > a.SB_ckW_roleCard[href=/careers/<slug>]` with `.SB_ckW_roleTeam`, `h3.SB_ckW_roleTitle`, location/type spans ("Short Hills, NJ" / "Noida, India", "Full-time"), and a `.SB_ckW_roleNewChip` ("New") on recent postings. The class prefix looks build-generated, so prefer structural selectors (`a[href^="/careers/"] h3`).
- Detail pages: title, Department, Location, Job scope (India/US), Work setting, description. No dates, no JSON-LD, no pay. The "New" chip is the only recency signal, so the 14-day filter cannot be applied exactly (treat "New" as recent, or use first-seen date).
- Robots: `Allow: /`, `Disallow: /api/`, `/admin` (list and detail paths allowed).
- Samples: "Director - Corporate Communications" | Short Hills, NJ | Marketing | Full-time | New; "Senior Manager - HR Operations & HRIS" | Short Hills, NJ | Full-time | New; "AI Engineer / Senior Engineer" | Noida, India | Full-time.
- Effort S/M. Risk: medium-high (class names, no semantic hooks).

### Waystar: HARD
- `careers.waystar.com` (Nuxt SPA with `ssr: false`; config shows `source: "solr"`, `job-folder: "careers-waystar-com"`) renders nothing server-side: both `/jobs/` and a job detail URL return the same 52 KB shell. The old `waystar.com/careers` seed URL errors (FetchError) and redirects people to this host.
- Readable: `robots.txt` allows `/` and points to `careers.waystar.com/sitemaps/index.xml` -> `jobs_1.xml` with 47 job URLs of the form `/atlanta-ga/staff-software-engineer-hybrid-atlanta-ga/<32-hex>/job/`. Title and location can be derived from the slug; `lastmod` is 2026-11-08 for every URL (a regeneration date, not a posting date). No description, no real date, no employment type.
- I could not find the SPA's JSON endpoint: `/api/jobs?limit=100&page=1` returns 404, and the five Nuxt chunks I read did not contain an API URL (search is a lazy chunk). Reading that lazy chunk is the next step if the owner wants it; otherwise Waystar is only a title/location/URL list.
- Samples (from sitemap slugs): "Campaign Operations Coordinator" | Louisville, KY / Atlanta, GA / Lehi, UT; "Staff Software Engineer (Hybrid)" | Atlanta, GA; "Sr Data Scientist" | Lehi, UT.

### Hippocratic AI: HARD
- `hippocraticai.com/careers/` (WordPress + Elementor): the page says "We Are Currently Hiring in All Departments" and shows a values-pledge checklist with a Gravity Forms button "View Open Roles". The job board URL only appears after submitting that form. Submitting forms is outside the study rules, and no public list was found in the HTML, `page-sitemap.xml` (only `/careers/`), or via guessed ATS tokens (Greenhouse/Ashby/Lever `hippocraticai`, Ashby `hippocratic-ai`, Gem `hippocraticai`/`hippocratic-ai` all 404).
- Robots: `Crawl-delay: 10`; no path restrictions of note.
- Option: ask the owner to open "View Open Roles" in a browser once and tell us which ATS URL it lands on; then it would likely become an EASY seeds row.

### Thoughtful AI: HARD
- The seed's careers URL is `accesshealthcare.com/careers`: Thoughtful AI merged into Access Healthcare, and `thoughtful.ai` now serves the Access Healthcare site. The listed jobs are Access Healthcare revenue-cycle/BPO roles (India, Manila, US, technology), unlike the original "Thoughtful AI" company.
- US jobs (`/careers/jobs-in-us`) point to a Paycom portal: `https://www.paycomonline.net/v4/ats/web.php/portal/026A9FD25D42079640FB1EE2904F167E/career-page`. It is a SPA that embeds an anonymous `sessionJWT` in the page and calls a JWT-protected API. Paycom's `robots.txt` is `Disallow: /` with explicit allows including `/v4/ats/web.php/portal/`, `/api/ats/job-postings/`, `/v4/ats/sitemap.php`. I could not confirm the job-postings request shape (the portal JS bundle is a separate file whose name is not in the HTML), and `sitemap.php?clientkey=...` returned 404. Other regions use other pages (`jobs-india`, `manila-job-openings`, `technologyjobs`) that I did not open.
- Recommendation: low value (BPO/RCM roles); skip unless the owner cares about Access Healthcare.

### Spring Health: BLOCKED
- Careers landing links to `springhealth.com/open-roles` (Webflow, which the detector never reached). The page's scripts call `https://www.springhealth.com/sh-app/api/jobs` (plus a clinician network portal at `springhealth.my.site.com/jobposting/s/`, a Salesforce Experience Cloud app).
- `springhealth.com/robots.txt` has `Disallow: /sh-app` (for `*` and for the named AI bots), so the JSON endpoint is off limits and CrawlClient refuses it (RobotsDisallowed). The HTML contains no job cards (client-rendered). Guessed Greenhouse/Ashby/Lever tokens (`springhealth`, `springhealth66`) return 404. Stopped per rules; no further probing.

## Recommended build order

1. **Seeds rows now (no code):** Color Health (`ashby`/`color-health`). Rows are in `data/custom_page_sources.csv`.
2. **Tiny JSON-API adapters (one class each, ~100 lines, reusable across other companies):**
   - `workable` (Innovaccer; also covers many other startups). Also add a shared "follow `/j/<shortcode>` to discover the account slug" step to the detector.
   - `rippling` (Carbon Health): list API + detail `__NEXT_DATA__`.
   - `gem` (Mindbloom): single GET.
   - `darwinbox` (Transcarent, and Accolade as a duplicate): paged GET.
   - A configurable `json_list` generic adapter (URL, items path, field map, optional status filter, optional pagination) could serve PharmaACE, Gem and Darwinbox in one go; Rippling needs the detail fetch for dates.
3. **WordPress/HTML adapter ("HTML list+detail with selectors", config in seeds `config` column):** Hyro (list on `/careers/`, date from Yoast `datePublished`), Medidata (list from `career-sitemap.xml`, same detail date source), Doceree (list `/careers`, no dates). One adapter with per-source selectors serves three companies; a "sitemap as list" variant serves Medidata.
4. **Skip / ask owner:** Waystar (needs the SPA's lazy chunk read; sitemap-only gives no dates), Hippocratic AI (form-gated), Thoughtful AI (merged, Paycom session JWT), Spring Health (robots-blocked).

## Detector findings (what the existing detector got wrong)
- **Color Health:** ATS is hidden behind a CareerPuck skin (`app.careerpuck.com/job-board/<slug>/job/<ashby-uuid>`). Recognise `careerpuck.com` and resolve via `api.careerpuck.com/v1/public/job-boards/<slug>` (`atsSourcePlatform`, `atsSourceId`).
- **Innovaccer:** job links live on `/careers/jobs`, not `/careers`; `apply.workable.com/j/<shortcode>` links omit the account, so the detector must fetch one shortcode page and read the canonical URL for the slug.
- **Carbon Health:** Rippling embed (`#rr-job-board[data-job-board-id]` + `ripplingcdn.com/ats/embeds/job-board.v1.js`) is not in the pattern list.
- **Mindbloom:** inline `fetch('https://api.gem.com/job_board/v0/<token>/job_posts/')` (Gem) not detected.
- **Transcarent/Accolade:** Darwinbox link (`*.darwinbox.com`) and a Greenhouse `embed/job_board/js?for=` snippet are not detected; Accolade's careers URL redirects to Transcarent.
- **Spring Health:** the detector did not follow `/open-roles` (link text "Explore all roles"); and the real endpoint is robots-blocked anyway.
- **Hyro:** server-rendered list backed by Comeet (`comeet-applyform`, `comeet.co`); no known ATS pattern for Comeet.
- **Waystar:** `careers.waystar.com` is a Nuxt SPA (platform not positively identified; the URL scheme resembles iCIMS/Jibe); the seed URL `waystar.com/careers` errors.
- **Thoughtful AI/Accolade:** seeds point at another company's site (merged); `paycomonline.net` portals are not recognised.
- The `robots disallow https://careers.X` / `jobs.X` notes in `data/rejected_classified.csv` come from probing guessed subdomains (`careers.X`, `jobs.X`) and are not evidence of a real job board; I did not verify which of those hosts exist (only `jobs.accolade.com` was confirmed as robots-disallowed).

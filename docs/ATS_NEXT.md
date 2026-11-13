# Next ATS adapters: feasibility study

Probed 2026-11-12 through `CrawlClient` only (robots enforced, <=1 req/s/host, no retries around 403). About 105 content requests plus about 25 robots.txt fetches in total, none over 25 per company. Scope: the 76 `status=detected` rows in `data/rejected_classified.csv`, systems listed below. P = priority industry (pharma/biotech, pharma consulting, health tech, pbm/health plan, specialty pharmacy, healthcare data). Weighted = non-priority + 3 x priority, counted over companies that actually work.

Probe scripts and raw responses: `<scratchpad>/ats_next/` (`h.py`, `ld.py`, `out/`).

## Summary

| System | Companies (P) | Verified working | Method | Robots | Effort | Recommendation |
|---|---|---|---|---|---|---|
| Oracle Recruiting Cloud | 5 (1) Myriad, Perficient, Hackett, Forward Air, Uber | 5/5 | JSON REST `recruitingCEJobRequisitions` + `...Details` | allowed (robots 404 or none) | M | BUILD |
| Phenom | 6 (2) Zelis, Fortrea, Chewy, ICF, Crowe, Yelp | 5/6 (Fortrea blocked) | `sitemap.xml` + per-job JSON-LD | allowed (`*/apply`, `*/jobcart`, tracking paths only) | S (config only) | BUILD via `html_list` sitemap mode |
| ADP WorkforceNow | 2 (2) Cytek, Kindbody | 2/2 | public JSON `job-requisitions` list + detail | `robots.txt` is an HTML redirector, so no rules | M | BUILD |
| BambooHR | 2 (1) Aktana, Berkshire Grey | 2/2 | JSON `/careers/list` + `/careers/{id}/detail` | allow all | S | BUILD |
| Jobvite | 4 (3) Sutro, Citeline, Arcus, Ziff Davis | Sutro, Ziff Davis, Citeline (Talemetry) = 3/4; Arcus slug unknown | HTML list + per-job JSON-LD (Citeline: Talemetry HTML) | `jobs.jobvite.com` robots 404; Citeline allow all | S (config only) | BUILD via `html_list` |
| applytojob / JazzHR | 3 (1) Krystal, Shipwell, Caliber | 2/3 (Caliber has no board) | HTML list + per-job JSON-LD | `Disallow: /cb` only | S (config only) | BUILD via `html_list` |
| Paylocity | 1 (1) Viking | 1/1 | list embedded as `window.pageData` JSON + per-job JSON-LD | robots 404 | S-M | ONE-OFF (low value) |
| SuccessFactors | 1 (1) Indegene | 1/1 | `sitemap.xml` (29 jobs) + HTML detail, no JSON-LD | allowed (`/services/`, `/apply*` etc. disallowed) | S-M | ONE-OFF (needs selectors) |
| SmartRecruiters | 4 (2) Guardant, EVERSANA, Nielsen, M1 | 3/4 (Guardant host unknown) | `careers.smartrecruiters.com` partly SSR HTML list; JSON detail on `jobs.smartrecruiters.com` | public hosts: no valid robots; API host forbidden | M | SKIP pending policy decision |
| Dayforce | 2 (1) Brightline, Gannett | page loads | Next.js SPA; list comes from an undocumented client API | no Disallow (content-signal comments only) | L | SKIP |
| Eightfold | 1 (1) Vizient | no | API returns 403; `Disallow: /` with narrow allows | restrictive | n/a | SKIP |

## Per-system evidence

### Oracle Recruiting Cloud (BUILD, dedicated adapter)

- List: `GET https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList&finder=findReqs;limit=25,offset=0,sortBy=POSTING_DATES_DESC,siteNumber={site}`. Without `expand=requisitionList` the list is empty, but `items[0].TotalJobsCount` still gives the total. Paginate with `offset`; stop when `offset >= TotalJobsCount`. The `;` and `,` must go in the URL string, not in `params`.
- `siteNumber` accepts either `CX_####` or the site code from the careers URL: both worked (`CX_2001`, `CX_6`, `CX_1`, `MovingYouForward`, `UberCareers`).
- List fields: `Id`, `Title`, `PostedDate`, `PrimaryLocation`, `WorkplaceType`, `ShortDescriptionStr`.
- Detail: `.../recruitingCEJobRequisitionDetails?expand=all&onlyData=true&finder=ById;Id="{Id}",siteNumber={site}`. It returns `ExternalDescriptionStr` (HTML), `ExternalPostedStartDate`, `JobSchedule`, `Category`, `workLocation`, and `requisitionFlexFields` (pay appears here when the tenant fills it, for example "Hourly Salary").
- Robots: `ekgn.fa.us6.oraclecloud.com/robots.txt` is 404. The other four hosts were fetched without any robots refusal.
- Tenants (identity confirmed by job content and org name):

| Company | Host | Site | Total jobs |
|---|---|---|---|
| Myriad | `ekgn.fa.us6.oraclecloud.com` | `CX_2001` | 24 |
| Hackett | `eeih.fa.us2.oraclecloud.com` | `CX_6` | 409 |
| Forward Air | `eguq.fa.us2.oraclecloud.com` | `MovingYouForward` | 83 |
| Uber | `iaziqy.fa.ocs.oraclecloud.com` | `UberCareers` | 809 |
| Perficient | `fa-etqd-saasfaprod1.fa.ocs.oraclecloud.com` | `CX_1` | 118 |

- Sample jobs: Myriad "Sr Sales Executive - MRD - Jacksonville" (6418, 2026-11-12, remote); Forward Air "Operations Dock Supervisor" (42368, Des Plaines IL); Perficient "Technical Product Manager - Data and AI" (PF2026000136, Dallas TX).
- Risk: low-medium. The API is versioned (`latest`) but long-lived. Large tenants take about 40 list pages (Uber) plus detail-on-new only. Uber and Hackett include many non-US jobs.

### Phenom (BUILD, config only)

- Host is the tenant's custom careers domain, so it must be discovered (not derivable from the CSV, which holds only the shared CDN script). Found: `careers.chewy.com/us/en`, `careers.zelis.com/us/en`, `careers.icf.com/us/en`, `careers.crowe.com` (no `/us/en`), `www.yelp.careers/us/en`. `careers.yelp.com` returns an HTML page for `sitemap.xml`, so it is not the right host. Fortrea's guessed host `jobs.fortrea.com` is disallowed by robots, so it is unverified.
- List: `/sitemap.xml` (sometimes `/{locale}/sitemap.xml`; Chewy's robots lists `/us/en/sitemap.xml` and a `/veterinarycareers/us/en/sitemap.xml`). Job URLs match `/job/{id}/{slug}` (Crowe: `/job/{id}/{slug}` at the root). Sitemap job counts: Chewy 238, ICF 372, Crowe 169, Zelis 71, Yelp 29. There is no pagination.
- Detail: JSON-LD `JobPosting` with `title`, `datePosted`, `employmentType`, `jobLocation`, `description` (4-15 KB), `identifier`, and `occupationalCategory`. `baseSalary` was not present in the samples.
- Robots (Chewy, Zelis, Yelp): disallows `*/apply`, `*/jobcart`, `*/phenomtrack.min.js`, `*/px-widgets` and similar. Job and sitemap paths are allowed.
- Samples: Zelis "Product Manager, Payment Integrity" (2026-11-02); Chewy "Pharmacist" (R30950, 2026-11-02); Crowe "AI Project Coordinator" (2026-11-08).
- Identity note: ICF's JSON-LD `hiringOrganization.name` reads "IIIIIIUS" (a legal-entity string), so use the config company, not the JSON-LD org name. ICF includes many India roles.
- Risk: low. Sitemap plus JSON-LD survives redesigns, but sitemap `lastmod` is always "now", so do not use it as a posted date (`html_list` already ignores it).

### ADP WorkforceNow (BUILD, dedicated adapter)

- List: `GET https://workforcenow.adp.com/mascsr/default/careercenter/public/events/staffing/v1/job-requisitions?cid={cid}&ccId=19000101_000001&lang=en_US&$top=20&$skip=0`. The total is `meta.totalNumber` (Cytek 14, Kindbody 9). Paginate with `$skip`.
- The list already carries `requisitionTitle`, `postDate`, `requisitionLocations`, `workLevelCode` (employment type) and `payGradeRange` (min/max USD, for example $140,000-$160,000). The description is only in the detail: `.../job-requisitions/{itemID}?cid=...&ccId=...&lang=en_US` returns `requisitionDescription` (HTML).
- Robots: `workforcenow.adp.com/robots.txt` returns an HTML "Federation Redirector" page with status 200, so it holds no rules. This is technically allow, but it is ambiguous, so note it in `ENDPOINTS.md`.
- Samples: Cytek "Jr. Forward Deployed Engineer" (Fremont CA, 2026-11-10, $25-40/h); Cytek "Sr. Business Systems Analyst, NetSuite" (2026-11-09); Kindbody "Senior Embryologist" (San Francisco, $140-160k).
- Risk: medium. The endpoint is the one ADP's own career-centre SPA uses (public, no auth), but it is not a documented API.

### BambooHR (BUILD, S)

- List: `GET https://{co}.bamboohr.com/careers/list` returns `{"meta":{"totalCount":N},"result":[...]}`. There is no pagination (all jobs in one response). Fields: `id`, `jobOpeningName`, `departmentLabel`, `employmentStatusLabel`, `atsLocation{city,state,country}`, `isRemote`.
- Detail: `GET /careers/{id}/detail` returns `result.jobOpening` with `description` (HTML), `datePosted`, `compensation` (for example "$165,000 - $175,000 a year"), `employmentStatusLabel`, and `jobOpeningShareUrl` (apply link).
- Robots: `User-Agent: * Disallow:` (allow all).
- Tenants: `pharmaforceiq` (Aktana's tenant, 9 jobs, Chester PA), `berkshiregrey` (20 jobs, Bedford MA).
- Samples: "Executive Director of Business Development" (id 29, posted 2026-05-11, $165-175k); "Optichannel Engagement Manager" (31); "Senior Software Engineer, Robotics" (Berkshire Grey, 689).
- Risk: low. This also closes the "unverified" note in `ENDPOINTS.md`.

### Jobvite (BUILD via `html_list`)

- List: `https://jobs.jobvite.com/{slug}/jobs`. Static HTML with `<td class="jv-job-list-name"><a href="/{slug}/job/{id}">` and location in `td.jv-job-list-location`. No pagination (departments are grouped in tables on a single page). Totals: Sutro 4, Ziff Davis 58 (slug `ziffdavis`). A second slug, `ziff-davis`, lists 10 jobs with overlapping ids, so it is probably a sub-brand and should be de-duplicated.
- Detail: JSON-LD `JobPosting` with `datePosted`, `employmentType` ("Regular Full-Time"), `jobLocation`, `description`, `identifier`, `industry`, and an empty `baseSalary` shell (currency and values blank in the sample). `html_list` config: `job_link_selector: "td.jv-job-list-name a"`, `base_url: "https://jobs.jobvite.com"`, `id_from: url_path`.
- Robots: `jobs.jobvite.com/robots.txt` is 404 (allow). Unknown slugs 302 to `search.jobvite.com?invalid=1`, which the client refuses, giving a clean "not found".
- Arcus Biosciences: the page embeds `jobs.jobvite.com/__assets__/.../iframe.js` (the CSV token `__assets__` is junk). The company id is injected client side. `arcusbio`, `arcus-biosciences` and `arcusbiosciences` all returned the invalid redirect, so the slug is unknown. Needs a manual lookup.
- Citeline: not on `jobs.jobvite.com`. It is on Talemetry (Jobvite's talent platform) at `careers.citeline.com`: `sitemap.xml` is allowed (robots `Disallow:` empty) but lists only search pages. The list is HTML at `/search/citeline/jobs?page=N` with job links `/jobs/{id}-{slug}`. Job pages have no JSON-LD, so this needs detail selectors (`h1`, `.job-details-inner-js`). Treat it as an `html_list` config with selectors, effort S-M.
- Samples: Sutro "Executive Director, Clinical Development" (oBq0Afwl, posted 2026-10-23); Sutro "Senior Biostatistician"; Citeline "Senior Account Executive" (18427416).
- Risk: low for `jobs.jobvite.com` (stable markup for years).

### applytojob / JazzHR (BUILD via `html_list`)

- List: `https://{slug}.applytojob.com/apply`. `li.list-group-item` entries link to `/apply/{id}/{slug}`. No pagination found (Krystal 14, Shipwell 3).
- Detail: JSON-LD `JobPosting` with `datePosted`, `validThrough`, `employmentType`, `jobLocation`, `baseSalary` (Krystal: hourly min/max), `description`, `jobLocationType`.
- Robots: `User-agent: * Disallow: /cb` only (`SemrushBot` and `dotbot` are blocked entirely).
- Identity: Krystal Biotech and Shipwell boards confirmed by the page title and `hiringOrganization`. `caliber.applytojob.com/apply` returned JazzHR's own marketing page ("For Job Seekers | JazzHR"), so Caliber's slug is unverified or has no active board.
- Samples: Krystal "Biotech Warehouse / Materials Associate" (2LgPDsMiL5, posted 2026-10-14, hourly pay); Krystal "Quality Assurance Associate"; Shipwell "Staff Software Engineer" (remote).
- Risk: low.

### Paylocity (ONE-OFF)

- List page `https://recruiting.paylocity.com/recruiting/jobs/All/{guid}/{NAME}` embeds `window.pageData = {"Jobs":[...]}` (15 jobs for Viking: `JobId`, `JobTitle`, `PublishedDate`, `IsRemote`). The page has no plain job anchors, so `html_list` cannot enumerate it.
- Detail: `https://recruiting.paylocity.com/recruiting/jobs/Details/{JobId}` has JSON-LD with `datePosted`, `baseSalary` (Viking QMS Manager: $120-140k per year), location and description.
- Robots: 404 (allow).
- Samples: "Quality Management Systems Manager (QMS Manager)" (4653513); "Medical Science Liaison, Obesity" (4642833); "Clinical Trial Associate" (4631136).
- It is one company, but priority and small (15 jobs). A minimal dedicated adapter (parse `pageData`, JSON-LD detail) is about 60 lines. Not worth it unless more Paylocity tenants appear in the full 290-company list.

### SuccessFactors (ONE-OFF)

- `https://careers.indegene.com/sitemap.xml` is 200 and robots allows it, but it lists only 29 jobs (`/job/{slug}/{id}/`). The `/search/` page is JS-rendered, so I could not confirm a total. Detail pages have no JSON-LD (3 sampled), so fields need HTML selectors that I did not verify.
- It is one priority company (pharma consulting). An `html_list` sitemap-mode config with selectors would cost about an hour; the sitemap may undercount, so treat the volume as uncertain.

### SmartRecruiters (SKIP unless policy is decided)

- API (`api.smartrecruiters.com`) is forbidden by our policy (robots `Disallow: /` except LinkedInBot) and I did not touch it.
- Observed alternative on public hosts (not API): `https://careers.smartrecruiters.com/{id}?search=&page=N` returns a partly server-rendered list with `jobs.smartrecruiters.com/{id}/{jobId}-{slug}` links (EVERSANA page 0: 44 links, page 1: 26 more; Nielsen 17). `careers.smartrecruiters.com/robots.txt` is an HTML page and `jobs.smartrecruiters.com/robots.txt` is 404, so there are no rules.
- Detail: `GET https://jobs.smartrecruiters.com/{id}/{jobId}-{slug}`. Because `CrawlClient` sends `Accept: application/json` first, this host returned JSON (`jobTitle`, `jobAdLocation`, `employmentType`, `postedDate`, `content.sections.*`). That is content negotiation on the public job-page URL, not the API host.
- Samples: EVERSANA "Medical Information Representative" (Overland Park KS, 2026-11-05); EVERSANA "Medical Information Specialist" (2026-11-03); M1 Finance "Senior IT Systems Engineer" (Chicago, 2026-09-08). M1's careers page is a custom site (`careers.smartrecruiters.com/M1Finance` is not the board).
- Why SKIP: the public list is partial and pagination semantics are unclear, the vendor has explicitly signalled it does not want automated listing, and Guardant's board host was not found (the CSV shows only an inline keyword and a robots-blocked custom host). Decision Q5 in `ENDPOINTS.md` already leaves this open. Revisit only if the owner accepts the ToS risk.

### Dayforce (SKIP)

- `jobs.dayforcehcm.com/{ns}/{site}` is a Next.js SPA. `__NEXT_DATA__` holds only site info (`clientId`, `jobBoardCode`), not postings. The postings come from a client-side API call that I did not reverse-engineer (the page chunk references `jobPostings` and `maxCount`). Robots has only Cloudflare content-signal comments and no Disallow lines. Enumerating it would mean reverse-engineering a private SPA API for 2 companies (1 priority), so skip.

### Eightfold (SKIP)

- `careers.vizientinc.com/robots.txt`: `Disallow: /` with narrow allows (`/careers`, `/api/apply`, `/api/pcsx`, ...). The first API probe (`/api/apply/v2/jobs?domain=vizientinc.com&start=0&num=3`) returned 403 "blocked", so per policy I stopped and made no further attempts. `/sitemap.xml` is disallowed. The only sitemap allowed is the one named in robots (`/careers/sitemap_index.xml?domain=...`), which I did not fetch. Skip: 403 means a bot wall, and we do not circumvent.

## Shared shapes

- Existing `html_list` (config only, no new code): Phenom (sitemap mode, `link_filter_regex: "/job/"`), Jobvite (`td.jv-job-list-name a`), applytojob/JazzHR (`li.list-group-item a`, filter `/apply/[A-Za-z0-9]+/`), Citeline/Talemetry and SuccessFactors Indegene (need selectors). All except Citeline and SuccessFactors have JSON-LD on detail pages, which `html_list` already prefers.
- One new JSON adapter family: BambooHR is the simplest (list + detail JSON). ADP and Oracle are two separate dedicated adapters (different shapes, with pay in ADP's list and the description only in the detail).
- Not shareable: Paylocity (embedded JSON), Dayforce, SmartRecruiters.

## Recommended build order

| # | Work | Companies unlocked (P) | Weighted | Expected volume |
|---|---|---|---|---|
| 1 | `html_list` configs: Phenom (5), Jobvite (Sutro, Ziff Davis), applytojob (Krystal, Shipwell) | 9 (Zelis, Sutro, Krystal are P) | 6 + 3x3 = 15 | about 71 (Zelis) + 238 (Chewy) + 372 (ICF) + 169 (Crowe) + 29 (Yelp) + 62 (Jobvite) + 17 = about 960 jobs |
| 2 | BambooHR adapter (S) | 2 (Aktana P) | 1 + 3 = 4 | about 29 |
| 3 | Oracle Recruiting Cloud adapter (M) | 5 (Myriad P) | 4 + 3 = 7 | about 1,440 (Uber 809, Hackett 409, Perficient 118, Forward Air 83, Myriad 24); filter by country |
| 4 | ADP WorkforceNow adapter (M) | 2 (both P) | 6 | about 23 (Cytek 14, Kindbody 9) |
| 5 | Optional one-offs: Citeline via `html_list` selectors, Paylocity (Viking), SuccessFactors (Indegene) | 3 (all P) | 9 | about 15 + 29 + unknown |

Skip: SmartRecruiters (pending policy), Dayforce, Eightfold. Job-volume numbers are the live totals seen on 2026-11-12 and include non-US roles. Priority-industry jobs are concentrated in Zelis, Sutro, Krystal, Aktana, Myriad, Cytek and Kindbody, so steps 1-4 cover these seven even though total volume is dominated by non-priority tenants (Uber, ICF, Chewy).

## Open items

- Arcus Biosciences' Jobvite slug (embed only), Fortrea's Phenom host (guess blocked by robots), Guardant's SmartRecruiters board, and Caliber's JazzHR board need a manual lookup.
- ADP's `robots.txt` is an HTML page, so confirm that treating it as "no rules" is acceptable.
- Oracle `requisitionFlexFields` and Phenom/Jobvite `baseSalary` were only spot-checked; pay coverage per tenant is unverified.

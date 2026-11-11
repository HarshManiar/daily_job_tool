# ATS endpoints

Probed 2026-11-11 from dev machine. Statuses below are from that probe.

Facts are copied from `docs/ARCHITECTURE.md` §5. Anything §5 does not state is marked `TBD — verify when recording fixtures`.

---

## Greenhouse

- URL(s): `GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`; pay ranges: `GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{id}?pay_transparency=true`; board name: `GET https://boards-api.greenhouse.io/v1/boards/{token}`
- HTTP method: GET
- Key params/body: `content=true` on the list call; `pay_transparency=true` on the job detail call (pay ranges). Verify in task 1.5.
- Pagination: none; a single call returns the whole board.
- robots.txt finding: `/embed/` disallowed only.
- Probe status: ✅ 200
- Notes: Single call returns the whole board. Pay ranges come from the per-job detail call (verify in task 1.5).

## Lever

- URL(s): `GET https://api.lever.co/v0/postings/{site}?mode=json`; EU: `https://api.eu.lever.co/v0/postings/{site}?mode=json`
- HTTP method: GET
- Key params/body: `mode=json`
- Pagination: `skip`/`limit` query params.
- robots.txt finding: allow all.
- Probe status: ✅ 200
- Notes: Response has `salaryRange` and `workplaceType`.

## Ashby

- URL(s): `GET https://api.ashbyhq.com/posting-api/job-board/{name}?includeCompensation=true`
- HTTP method: GET
- Key params/body: `includeCompensation=true`
- Pagination: TBD — verify when recording fixtures
- robots.txt finding: `none` (as recorded in §5)
- Probe status: ✅ 200
- Notes: Response has `isRemote`, `workplaceType`, and compensation tiers.

## Workable

- URL(s): v1 widget (list): `GET https://apply.workable.com/api/v1/widget/accounts/{sub}`; v3 list: `POST https://apply.workable.com/api/v3/accounts/{sub}/jobs`
- HTTP method: GET (v1 widget); POST (v3)
- Key params/body: v3 body and params: TBD — verify when recording fixtures
- Pagination: v3 is paginated by `token`. v1 widget: TBD — verify when recording fixtures
- robots.txt finding: allow all.
- Probe status: ✅ 200 (valid slug)
- Notes: Use the v1 widget for the list. Job details come from the v2/v3 job endpoint (verify).

## SmartRecruiters

- URL(s): `GET https://api.smartrecruiters.com/v1/companies/{id}/postings` (API, not usable under our robots policy); fallback public pages: `https://jobs.smartrecruiters.com/{id}/…` (JSON-LD)
- HTTP method: GET
- Key params/body: TBD — verify when recording fixtures
- Pagination: TBD — verify when recording fixtures
- robots.txt finding: ⛔ `Disallow: /` for `*` (only LinkedInBot allowed). Public pages `jobs.smartrecruiters.com` have no valid robots.txt, so they are allowed.
- Probe status: ✅ 200 (API endpoint)
- Notes: Not usable under our robots policy. Fallback is the public pages via JSON-LD. Decision Q5.

## Recruitee

- URL(s): `GET https://{co}.recruitee.com/api/offers/`
- HTTP method: GET
- Key params/body: TBD — verify when recording fixtures
- Pagination: TBD — verify when recording fixtures
- robots.txt finding: `none` (as recorded in §5)
- Probe status: ✅ 200
- Notes: Full descriptions. Salary fields on some boards.

## Teamtailor

- URL(s): `GET https://{sub}.teamtailor.com/jobs.rss` (public RSS); per-job JSON-LD on the job page. Custom domains are common; detect them via `teamtailor` script/meta.
- HTTP method: GET
- Key params/body: none stated; TBD — verify when recording fixtures
- Pagination: TBD — verify when recording fixtures
- robots.txt finding: `/jobs/internal/` disallowed.
- Probe status: ✅ 200
- Notes: The JSON API needs a customer key, so the RSS feed plus per-job JSON-LD is used instead.

## Personio

- URL(s): `GET https://{co}.jobs.personio.de/xml`; also `https://{co}.jobs.personio.com/xml`
- HTTP method: GET
- Key params/body: `?language=en`
- Pagination: TBD — verify when recording fixtures
- robots.txt finding: allow all.
- Probe status: ✅ 200
- Notes: XML response with descriptions.

## BambooHR

- URL(s): `GET https://{co}.bamboohr.com/careers/list`; `GET https://{co}.bamboohr.com/careers/{id}/detail`
- HTTP method: GET
- Key params/body: TBD — verify when recording fixtures
- Pagination: TBD — verify when recording fixtures
- robots.txt finding: not recorded in §5 (`—`)
- Probe status: ⚠️ unverified (test slug redirected). Confirm with a real customer slug in task 2.11.
- Notes: Needs a real customer slug before it can be confirmed.

## Workday

- URL(s): list: `POST https://{tenant}.{wdN}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs`; detail: `GET https://{tenant}.{wdN}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{externalPath}`; sitemap: `GET https://{tenant}.{wdN}.myworkdayjobs.com/{site}/siteMap.xml`
- HTTP method: POST (list); GET (detail, sitemap)
- Key params/body: list body `{"appliedFacets":{},"limit":20,"offset":0,"searchText":""}`
- Pagination: by 20 (`limit` 20, advance `offset`). `siteMap.xml` is the cheap way to diff before paging.
- robots.txt finding: per tenant; career-site path allowed, `/wday/cxs` not disallowed. Check robots per tenant at runtime.
- Probe status: ✅ 200
- Notes: Big tenants take 100+ requests, so crawl every 24 h at 0.5 req/s.

## Oracle Recruiting Cloud

- URL(s): `GET https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList&finder=findReqs;siteNumber={site},limit=…,offset=…`; detail: `recruitingCEJobRequisitionDetails`
- HTTP method: GET
- Key params/body: `onlyData=true`, `expand=requisitionList`, `finder=findReqs;siteNumber={site},limit=…,offset=…`
- Pagination: `limit`/`offset` inside the `finder` param (exact step size: TBD — verify when recording fixtures)
- robots.txt finding: `none` (as recorded in §5)
- Probe status: ✅ 200
- Notes: Detail call uses the `recruitingCEJobRequisitionDetails` resource.

## iCIMS

- URL(s): `https://careers-{co}.icims.com/jobs/search?pr={n}&in_iframe=1` (HTML); job pages with JSON-LD
- HTTP method: GET
- Key params/body: `pr={n}`, `in_iframe=1`
- Pagination: `pr={n}` (exact scheme: TBD — verify when recording fixtures)
- robots.txt finding: per tenant.
- Probe status: not probed
- Notes: Tier 2, HTML. Stretch goal.

## SuccessFactors

- URL(s): career-site sitemap `/sitemap.xml`; job pages with JSON-LD
- HTTP method: GET
- Key params/body: TBD — verify when recording fixtures
- Pagination: TBD — verify when recording fixtures
- robots.txt finding: per tenant.
- Probe status: not probed
- Notes: Stretch goal.

## Jobvite

- URL(s): `https://jobs.jobvite.com/{co}/jobs` (HTML); job pages
- HTTP method: GET
- Key params/body: TBD — verify when recording fixtures
- Pagination: TBD — verify when recording fixtures
- robots.txt finding: per tenant.
- Probe status: not probed
- Notes: Stretch goal.

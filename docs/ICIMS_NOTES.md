# iCIMS notes (task 2.7b, probed 2026-11-12)

List: `GET https://<host>/jobs/search?ss=1&in_iframe=1&pr=<n>` (HTML, 50 rows/page, n from 0). Chosen over
`/sitemap.xml` because rows carry title/location/req id/category/pay and the sitemap only has URL + lastmod.
Rows: `.iCIMS_JobsTable .row`, `.title a` (href `/jobs/<id>/<slug>/job`), `.header.left` location
(`US-NJ-Cranbury`, may be `US-`), `.header.right` req id, `dl.iCIMS_JobHeaderGroup` fields.
No total count; pager text "Page X of N" in `.iCIMS_Paging`. Past-the-end page: no rows, `iCIMS_ErrorMsg`.
Job page `/jobs/<id>/<slug>/job?in_iframe=1`: schema.org JobPosting JSON-LD (title, datePosted ISO,
jobLocation, employmentType, description HTML, baseSalary min/max/currency, jobLocationType, occupationalCategory).
Missing values are the string UNAVAILABLE. Missing/gone job -> 410.

| company | host | reachable | robots | jobs | datePosted |
|---|---|---|---|---|---|
| Navitus | careers-navitus | yes | allows /jobs/ | 85 (2 pages) | yes |
| Analysis Group | datasciencecareers-analysisgroup | yes | allows | 3 | yes |
| Rocket Pharmaceuticals | careers-rocketpharma | yes | allows | 9 | yes |
| Cotiviti | careers-cotiviti | yes | allows | 76 | yes (list not fetched per job) |
| ZS Associates, Ulta, Cornerstone Research, Rivian, Medpace | various | robots.txt `Disallow: /` | blocked, not crawled, not seeded |
| Certara | detected host login.icims.com; guessed careers-certara | robots `Disallow: /` | not seeded |

Fixtures: untrimmed. The 2160 job body was recorded from the URL without `?in_iframe=1` (same page) and
stored under the `in_iframe=1` URL; the 410 exchange has an empty body.

# Phase 1 report (2026-11-11)

**Status:** working end to end. 296 companies seeded (+ custom-page companies), first full live crawl completed with **0 failed crawls**, local site running at http://127.0.0.1:8000.

## Numbers
| Metric | Value |
|---|---|
| Companies crawled | 297 sources + 5 re-run after adapter registration = all active sources `ok` (Carbon Health, PharmaACE, Transcarent excluded by owner) |
| Sources by adapter | greenhouse 186, ashby 78, lever 33, html_list 3, workable 1, gem 1 (all ok) |
| Open jobs stored (US-eligible) | 17,659 |
| Non-US postings skipped (id+hash only) | 6,426 |
| Match status | include 3,302 · flagged 8,613 · excluded 5,744 |
| Tier 1 / 2 / 3 (not excluded) | 9 / 272 / 43 |
| Default feed (14 days, not excluded) | 1,942 jobs; 33 are tier 1-2 |
| Jobs with stated pay | 14,399 (unverified sample needed) |
| Visa: sponsorship offered / not available | 602 / 2,572 |
| Location rank (non-excluded): NJ / NYC area / US remote / other US | 117 / 2,654 / 2,773 / 6,371 |
| Tests | 1,625 passing |

## What broke / lessons
- The long-running crawl process loads the adapter list at start; adapters registered afterwards (workable, gem, html_list) errored for 5 sources until a fresh `crawl --once`. Restart the worker after adding adapters.
- Four review rounds by Opus found blockers in the crawler (redirects bypassed robots), ingest (permanent non-US decisions, detail failures blocking closes), and match engine (false visa/contract/7+ years exclusions). All fixed with regression tests.

## Open issues to fix before/early in Phase 2
1. `employment:unknown` flags 10,960 jobs (62%) because Greenhouse/Ashby rarely state employment type, so "flagged" is almost meaningless. Proposal: make it informational (chip only), flag only on contract signals.
2. `visa:export_control` flags 939 jobs (5%): check whether generic boilerplate is being caught.
3. Pay: 82% of jobs have a stated max pay. Audit a sample of parsed (not structured) pay values.
4. Site not yet checked visually in a browser by the assistant.
5. Crawler User-Agent contact is still a placeholder.

## Next: Phase 2 (Workday phase, ATS gap adapters, detection at scale, scheduling) — awaiting go-ahead.

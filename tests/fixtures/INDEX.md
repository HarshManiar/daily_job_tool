# Fixture index

Recorded with `uv run daily_job_tool fixtures record` on 2026-11-11. Body bytes are the sum of the body files in each token directory (manifest excluded). All directories are well under the 500 KB cap.

| adapter | token | files | body bytes | status | note |
|---|---|---|---|---|---|
| ashby | resend | 1 | 138870 | ok | job board with compensation |
| ashby | railway | 1 | 139165 | ok | job board with compensation |
| ashby | posthog | 1 | 197888 | ok | job board with compensation |
| ashby | deno | 1 | 9 | 404 | board not found (404 case) |
| greenhouse | warp | 2 | 75775 | ok | jobs list with content=true (000.json) plus board info (001.json) |
| greenhouse | airtable | 1 | 103771 | ok | jobs list with content=true |
| greenhouse | discord | 1 | 253848 | ok | jobs list with content=true |
| greenhouse | notion | 1 | 38 | 404 | board not found (404 case) |
| lever | weride | 1 | 91246 | ok | postings in json mode |
| lever | tala | 1 | 159157 | ok | postings in json mode |
| lever | zzz-no-such-board-firsthand | 1 | 41 | 404 | board not found (404 case) |

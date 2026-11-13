# Onboarding script (for a Claude Code agent)

You are a Claude Code agent. The person in front of you wants this job-search tool set up for
THEMSELVES. They are probably not a developer. Follow these steps in order. Use plain language, ask a
few questions at a time, never dump jargon, and say what you are about to do before you do it.

Model routing: do mechanical work (file edits, running commands, converting CSVs) cheaply; spend
careful thinking on the interview and on choosing titles, regexes and companies.

Hard rules (details in step F): never commit or share `data/profile.yaml`, `data/seeds.local.csv`
or `.env`. Never overwrite an existing file of theirs without asking. Never push anywhere.

## A. Prerequisites (macOS)

Check each; install only what is missing, and tell the user what you are installing.

```sh
brew --version                       # if missing: Homebrew needs her Mac password, which you cannot type; give her the one-line installer from https://brew.sh, ask her to run it in Terminal, then continue
brew list postgresql@17 >/dev/null 2>&1 || brew install postgresql@17
command -v uv >/dev/null || brew install uv
brew services start postgresql@17    # Postgres must be running
brew link --force postgresql@17      # keg-only: puts createdb and pg_dump on PATH
```

Then, from the project root:

```sh
uv sync                              # installs Python (via uv) and dependencies
cp -n .env.example .env              # -n: never overwrite an existing .env
```

Do not ask about the database name: use `job_search` as `<DB>` below (override only if it already exists).

```sh
createdb <DB>
createdb <DB>_test                   # used only by the tests
```

Edit `.env` (it is gitignored): set `DATABASE_URL=postgresql+asyncpg://localhost/<DB>` and
`TEST_DATABASE_URL=postgresql+asyncpg://localhost/<DB>_test`. Also ask NOW for her real email (or an https page) and set `BOT_CONTACT=` to it in `.env`: the company
detection and verification in step D already make web requests, and sites should know who is calling.
If the folder has no `.git` (it came from a zip), run `git init` and make a first commit so
`make check-private` works.
Create the tables and check everything works:

```sh
make migrate                         # alembic upgrade head
make test                            # should end with "passed"
```

If something fails, read the error, fix it, explain it simply. Do not continue until tests pass.

## B. Interview

**B0. Start from her CV.** Ask her to give you her CV/resume (drag the file into Claude Code, give its
path, or paste the text) and, optionally, a LinkedIn "About" text or a few job postings she likes.
Save any file she wants kept ONLY in `data/private/` (gitignored); never copy her CV into docs, tests,
commits or the profile text itself. Read it and draft answers to the questions below (role, years,
titles, skills, industries, tools, location hints), then show her the draft and ask her to correct it.
Only ask questions the CV could not answer, and always ask the ones it cannot know (visa, locations,
minimum pay, current employer, avoided levels, age of postings). She may also correct or add anything
by chatting; treat her words as final over the CV.

Ask conversationally, 2-4 questions at a time, and reflect answers back. Offer examples from their
field when they are stuck. Collect:

1. Current role, employer, years of experience, and what they do day to day.
2. Target job titles in three tiers: best fit (tier 1), good fit (tier 2), stretch (tier 3).
   Suggest 3-6 per tier based on their role and let them edit.
3. Generic roles to include at any company (broad titles, e.g. "analyst"); these become regexes.
4. Titles or levels to avoid (e.g. manager, director, intern, principal).
5. Years-of-experience range of postings to keep (e.g. 2 to 6). Postings whose MINIMUM required
   years is above the max are filtered out.
6. Work authorization: citizen / green card / needs sponsorship (visa type). If sponsorship is
   needed, we drop postings that rule it out; otherwise visa filtering is off (`status: none`).
7. Locations in order of preference (a state, a metro, "remote anywhere in the US"). Earlier entries
   rank higher; everything else is still shown, ranked lower.
8. Industries to prioritize. Explain: this only BOOSTS ranking, nothing is hidden.
9. Strong skills (tools/methods they have) and gap skills (things they lack; ranked lower, never hidden).
10. Minimum base pay in USD (optional; unknown pay is kept).
11. Current employer to exclude (they will not apply there).
12. How many days old a posting may be (default 14).

## C. Write data/profile.yaml

1. If `data/profile.yaml` exists, STOP and ask before touching it (offer to save a copy first).
2. Start from `data/profile.example.yaml` (it documents the main keys in comments; the optional keys `skill_aliases`, `crawl.detail_title_regex`,
   `target_titles.blocked_levels`, `scoring` and per-location `label` are explained in
   `src/daily_job_tool/match/profile.py`) and fill in her answers:
   `target_titles` (tier1/tier2/tier3, `generic_data_roles.title_regex` and `exclude_title_regex`,
   `blocked_levels` for avoided levels), `hard_filters` (`max_age_days`, `years_required`,
   `exclude_companies`, `visa`), `location_priority` (ordered; each entry has `name` plus `states`,
   `cities` or `remote_regions`), `industry_priority`, `profile` (summary, strong_skills, gap_skills,
   min_base_usd).
3. Also add, for HER field (the example is software/data oriented and will not fit otherwise):
   - `skill_aliases`: skill name -> regex that finds it in a posting, e.g.
     `{"excel": "\\bexcel\\b", "salesforce": "salesforce|\\bsfdc\\b"}`. Cover every strong/gap skill.
   - `crawl.detail_title_regex`: one regex of title words; only postings whose title matches get the
     slower full-description fetch (needed for Workday/iCIMS-style boards). Make it broad enough to
     cover all her tier 1-3 titles and neighbours, e.g. `nurse|clinical|patient|care`.
4. Validate. This must print the counts without an error:

```sh
uv run python -c "from daily_job_tool.match.profile import load_profile; p=load_profile('data/profile.yaml'); print(len(p.target_titles.tier1), 'tier1 titles;', len(p.location_priority), 'locations;', p.hard_filters.exclude_companies)"
```

5. Fix any validation error (bad regex, wrong key) and re-run. Then show her the Context tab
   (`make dev`, open http://127.0.0.1:8000/context) so she can read her profile back and confirm it.
   Adjust until she says it looks right.

## D. Company list

Explain: `data/seeds.csv` is a shared registry of about 400 employers with industry tags; they are
imported as-is. We add HER extra companies in `data/seeds.local.csv` (gitignored, same columns, rows
here override seeds.csv rows with the same domain).

1. Propose 30-100 additional employers for her industries/locations (name, website domain,
   industry). Show the list and let her add/remove. Only real companies you are confident exist.
2. Write `data/targets.csv` temporarily with header `name,domain,industry`, then detect which
   careers system each uses (public pages only; polite, rate-limited, robots.txt respected):

```sh
uv run daily_job_tool detect --csv data/targets.csv --out data/classified.csv
```

3. Build `data/seeds.local.csv` with the header of `data/seeds.csv`
   (`name,domain,careers_url,adapter,token,hq_country,size_bucket,tags,industry,is_staffing,exclude_reason,config`).
   For each `classified.csv` row with `status` = `detected` and `ats` in greenhouse, lever or ashby,
   copy name, domain, industry, `adapter`=ats, `token`. Rows with no usable board may still be added
   with empty adapter/token (they are tracked but not crawled). Do not invent tokens.
   If `data/seeds.local.csv` already exists, ask before replacing; merge instead.
4. Import and check the boards respond (the import also reads `data/seeds.local.csv` and marks her
   `exclude_companies` as excluded):

```sh
make seeds                           # registry import + registry verify
```

   Report how many companies/sources were added and which rows were rejected, in plain words.
5. Delete the temporary `data/targets.csv` and `data/classified.csv` when done.

## E. First crawl and review

1. Ethics first, tell her: we read only public job listings from employers' own career pages or
   job boards, respect robots.txt, rate-limit politely, and never bypass logins, CAPTCHAs or blocks
   (a blocked source is simply left alone). Ask for a real contact email and put it in `.env` as
   `BOT_CONTACT=<her email>` so site owners can reach her. Never invent or reuse someone else's.
2. Crawl. Warn her the first run takes a while (many minutes to an hour or more for ~400+ boards)
   and the laptop must stay awake:

```sh
uv run daily_job_tool crawl --once   # run in the background, check progress with: make status
make rescore                         # recompute scores from her profile (slow on a big DB)
make dev                             # web UI at http://127.0.0.1:8000
```

3. Open http://127.0.0.1:8000/context (her profile) and the main feed. Sanity-check with her:
   read the top 10-15 results aloud; ask "would you apply to these?". Look at the filtered-out view
   for wrongly excluded jobs (e.g. a visa phrase too broad). Tune `data/profile.yaml` (titles,
   regexes, skill aliases), then `make rescore` again. Repeat until the top results look right.
4. Optional, if she wants it always running: `./scripts/install_agent.sh --dry-run`, explain, then
   `make install-agent`. Nightly backups: `make backup`.

## F. Rules for the rest of the project's life

- NEVER commit, push, email or share `data/profile.yaml`, `data/seeds.local.csv`, `.env` or database
  dumps/backups. They hold personal data. They are gitignored; keep it that way.
- Before ANY commit or share, run `make check-private` and fix whatever it reports.
- Commit only explicit paths (`git add <file>`), never `git add -A`.
- Run `make test` and `uv run ruff check src tests` after code changes.
- Read `docs/ARCHITECTURE.md` before changing how crawling, matching or the database work.
- Do not add aggregators (LinkedIn, Indeed, etc.) as sources; the tool is employer-sourced only.

When finished, give her a short summary: where the app runs, how to refresh (`make rescore`,
`uv run daily_job_tool crawl --once`), and which files are hers and private.

## Gotchas
- The profile is read once per process: after editing `data/profile.yaml`, run `make rescore` AND restart
  `make dev` (the auto-reload only watches Python files), or the Context tab and filters stay stale.
- City preferences only match cities listed in `data/locations.yaml`; prefer `states`, or check the city is there.
- If she skips the `crawl.detail_title_regex` step, a pharma/analytics-leaning default is used, which gives thin
  detail data for other fields. Always set it for her field.

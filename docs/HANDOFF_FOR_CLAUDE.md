# Handoff: set this job search tool up for a new person

Read this first, then follow `docs/ONBOARDING.md` step by step.

## What this is
A private, local-only job search engine. It collects public job listings straight from employers'
career pages and ATS boards (never LinkedIn, Indeed or Glassdoor), stores them in a local Postgres
database, ranks them against a personal profile and shows them on a web page at
http://127.0.0.1:8000. It was built for one person (a pharma-analytics professional). You are
making a version for a different person, so every personal preference must come from THEM, not from
the example data.

## Paste this to start (the user pastes it into Claude Code, opened in the project folder)
> Read docs/HANDOFF_FOR_CLAUDE.md and docs/ONBOARDING.md, then set this job search tool up for me.
> I am not a developer, so explain things simply and ask me questions a few at a time.

## What you need to have BEFORE starting
| Need | Where it comes from |
|---|---|
| The project folder (code) | The owner sends a zip made with `git archive` (no private files inside) or a private GitHub clone. Unzip anywhere, open that folder in Claude Code. |
| A Mac with internet | The user. Windows or Linux is not covered by these instructions. |
| Homebrew, PostgreSQL 17, `uv` | You install them (ONBOARDING step A). Homebrew itself needs the user to run its one-line installer from https://brew.sh. |
| A name for her private database | Ask the user (suggest `job_search`). |
| Her real contact (email or https URL) | Ask the user. It goes in `.env` as `BOT_CONTACT`, so employers can see who is crawling. Never invent one. |

## What you need FROM THE USER (the interview, ONBOARDING step B)
Current role and years of experience; target job titles in three tiers; broad roles to include
anywhere; levels to avoid; years-of-experience range to keep; work authorization or visa situation;
locations in priority order; industries to prioritize (this only boosts ranking); strong skills and gap
skills; minimum pay; current employer to exclude; how old a posting may be.

## What is deliberately NOT in the package
- The owner's profile (`data/profile.yaml`), database, logs, backups, `.env` and tokens. Do not look
  for them and do not ask for them. The shared `data/profile.example.yaml` is a neutral template.
- Company list tuned to the owner. `data/seeds.csv` is a general registry (~400 companies across
  software, fintech, health tech and pharma). Add the user's own companies to
  `data/seeds.local.csv` (gitignored) and run `make seeds`.

## Files you will create (all private, never commit or share)
`.env`, `data/profile.yaml`, `data/seeds.local.csv`.

## Command cheat sheet (all from the project root)
```
make migrate        create/upgrade tables        make test         run tests
make seeds          import + verify companies    make rescore      recompute matches after profile edits
make dev            start the website            make check-private  scan tracked files for personal data
```
The Context tab at http://127.0.0.1:8000/context shows the active profile; use it to confirm what
you wrote. Profile edits only take effect after `make rescore` and a web restart.

## Rules
- Public listings only. Respect robots.txt. Never bypass logins, CAPTCHAs or bot protection. If a
  site blocks the crawler, record it and move on.
- Keep everything local. The site binds to 127.0.0.1 only; do not expose it.
- Never overwrite an existing file of the user's without asking. Never push or share anything.
- Do the interview before writing the profile. Do not copy the owner's pharma or New Jersey
  assumptions into her profile; leave out anything she did not choose.
- If a command fails, read the error, fix it, and explain it in plain language before continuing.

## Definition of done
Tests pass; `data/profile.yaml` validates; the Context tab shows HER answers; the first crawl has
run; the feed's top results look sensible to her; `make check-private` is clean.

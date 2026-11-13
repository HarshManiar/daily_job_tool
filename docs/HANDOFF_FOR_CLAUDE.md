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

## Who does what before the interview
She has a MacBook, so these are YOURS to do, without asking her (tell her briefly what you are doing):
- Install Homebrew if missing. Its installer asks for her Mac password, which you cannot type: if
  `brew --version` fails, show her the one-line command from https://brew.sh, ask her to run it in Terminal
  and tell you when it finishes, then continue.
- Install PostgreSQL 17 and `uv` with Homebrew, start Postgres, run `brew link --force postgresql@17`.
- Choose the database name yourself: `job_search` (and `job_search_test`). Do not ask her.
- Run `uv sync`, create `.env`, `make migrate`, `make test`.
- The code is already on her Mac: she opens the unzipped project folder in Claude Code.

You MUST ASK HER (never guess or invent): her real contact email or https page for `BOT_CONTACT`, and
every interview answer below. Anything not on the list above is a question for her.

## What you need FROM THE USER (the interview, ONBOARDING step B)
Start by asking for her CV (file, path or pasted text) and pre-fill the answers from it, then confirm
with her. She provides her own details; keep her CV only in `data/private/` (gitignored).
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
`.env`, `data/profile.yaml`, `data/seeds.local.csv`, `data/private/` (her CV and notes).

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

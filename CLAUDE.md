# Daily Job Tool

Local job-search tool: crawls public job listings from employers' own career pages and ATS boards
(Greenhouse, Lever, Ashby, Workday, ...) into Postgres, scores them against a personal profile, and
serves a feed at http://127.0.0.1:8000 (FastAPI + Jinja2 + HTMX). Python 3.12 managed by uv.

## Commands

- Tests: `set -o pipefail; uv run pytest tests -q 2>&1 | tail` (needs Postgres running and a test DB)
- Lint: `uv run ruff check src tests`; format: `uv run ruff format`
- Migrations: `make migrate`; web: `make dev`; status: `make status`
- Re-score after editing the profile: `make rescore`
- Import companies: `make seeds` (data/seeds.csv plus optional data/seeds.local.csv)
- Privacy check: `make check-private`

## Conventions

- Read `docs/ARCHITECTURE.md` before changing crawling, normalization, matching or the schema.
- SQLAlchemy 2 Core (async, explicit SQL), Alembic migrations, Pydantic v2, typer CLI.
- Employer-sourced only: no aggregators. Respect robots.txt, stay polite, never bypass blocks.
- Matching is profile-driven (`src/daily_job_tool/match/profile.py`); do not hardcode a person's
  titles, skills or employers in code or in tracked files.
- Add tests for behaviour changes; keep ruff clean.

## Private data rules

- Never commit, push or share: `data/profile.yaml`, `data/seeds.local.csv`, `.env`, DB dumps/backups.
  All are gitignored. `data/profile.example.yaml` is the neutral tracked template.
- Run `make check-private` before any commit or share. Stage explicit paths only (no `git add -A`).
- Do not push to a remote unless the user asks.

## Personalizing

If the user asks to personalize, set up, or adapt this for themselves, follow `docs/ONBOARDING.md`.

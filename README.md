# Daily Job Tool

Daily Job Tool — job search built only from employers' own career pages and ATS boards.

## Setup

```sh
brew install uv postgresql@17
brew services start postgresql@17
createdb daily_job_tool
createdb daily_job_tool_test
cp .env.example .env
uv sync
make test
```

## Docs

See [docs/](docs/) for architecture, plan, and endpoint notes.

## Everyday use

**Start / stop.** Install once and forget: `make install-agent` (try `./scripts/install_agent.sh --dry-run` first).
That registers three launchd agents in `~/Library/LaunchAgents`: `web` (http://127.0.0.1:8000, restarts if it
dies), `worker` (continuous crawler; polls for due sources every 30 s, restarts if it dies) and `backup`
(nightly 03:30). They start at login. `make uninstall-agent` removes them. For a manual session use
`./scripts/up.sh` (web + worker, Ctrl-C stops both) or `daily_job_tool worker`.

**Postgres** must be running: `brew services start postgresql@17` (Homebrew then starts it at login).
The install script only checks and prints guidance; it never starts it.

**What runs automatically.** The worker crawls sources as they come due (polite per-host rates, block circuit
breaker, per-source wall-clock cap, as in ARCHITECTURE.md section 11); the backup agent dumps the DB nightly.

**Check status.** `make status` (or `uv run daily_job_tool status`): last crawl age, due/in-flight sources,
failing/blocked/paused counts, open jobs, DB size, last backup, worker heartbeat (alive / stale / not
running; the worker rewrites `~/.daily_job_tool/worker_heartbeat.json` every 30 s) and a warning if
`BOT_CONTACT` is unset.

**Sleep.** While at least one source is in flight the worker holds `caffeinate -i -w <pid>`, which prevents
idle sleep (macOS only; disabled with `--no-caffeinate`). It does not stop sleep from closing the lid:
a MacBook with the lid shut still sleeps unless it is on power with an external display. After a sleep the
per-source wall-clock cap and lease expiry recover stalled crawls on their own.

**Backups.** `make backup` writes `~/Backups/daily_job_tool/daily_job_tool-YYYYMMDD-HHMM.dump` (`pg_dump -Fc`,
newest 14 kept; override with `--dir` or `DAILY_JOB_TOOL_BACKUP_DIR`). Restore into a NEW database:
`make restore DUMP=<file> INTO=daily_job_tool_restored`. An existing database is refused unless
`--yes-overwrite` is passed.

**Logs.** `~/Library/Logs/daily_job_tool/{web,worker,backup}.{out,err}.log`; `make logs` tails them.

**BOT_CONTACT.** Put `BOT_CONTACT=<your-contact-email-or-https-url>` in `.env` in the project root, then
reload the agents (`make uninstall-agent install-agent`). It is appended to the crawler User-Agent.

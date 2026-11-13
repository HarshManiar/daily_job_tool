.PHONY: dev up test lint fmt db-up migrate

dev:
	uv run uvicorn daily_job_tool.web.app:app --reload --host 127.0.0.1 --port 8000

up:
	./scripts/up.sh

test:
	uv run pytest -q

lint:
	uv run ruff check . && uv run ruff format --check .

fmt:
	uv run ruff format . && uv run ruff check --fix .

db-up:
	brew services start postgresql@17

migrate:
	uv run alembic upgrade head

.PHONY: install-agent uninstall-agent status logs backup restore

install-agent:
	./scripts/install_agent.sh

uninstall-agent:
	./scripts/uninstall_agent.sh

status:
	uv run daily_job_tool status

logs:
	tail -n 50 -f ~/Library/Logs/daily_job_tool/*.log

backup:
	uv run daily_job_tool backup

# usage: make restore DUMP=~/Backups/daily_job_tool/<file>.dump INTO=daily_job_tool_restored
restore:
	uv run daily_job_tool restore "$(DUMP)" --into "$(INTO)"

.PHONY: check-private

check-private:
	./scripts/check_private.sh

.PHONY: rescore

# Recompute match columns (tier, score inputs, location rank, visa) for all open jobs
# after editing data/profile.yaml. No HTTP; takes a while on a big DB.
rescore:
	uv run daily_job_tool match rescore

.PHONY: seeds

# Import data/seeds.csv plus the optional private data/seeds.local.csv, then check the boards.
seeds:
	uv run daily_job_tool registry import
	uv run daily_job_tool registry verify

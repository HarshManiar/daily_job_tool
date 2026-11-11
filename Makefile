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

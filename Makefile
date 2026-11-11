.PHONY: dev test lint fmt db-up migrate

dev:
	uv run uvicorn firsthand.web.app:app --reload

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

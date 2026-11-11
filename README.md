# Firsthand

Firsthand — job search built only from employers' own career pages and ATS boards.

## Setup

```sh
brew install uv postgresql@17
brew services start postgresql@17
createdb firsthand
createdb firsthand_test
cp .env.example .env
uv sync
make test
```

## Docs

See [docs/](docs/) for architecture, plan, and endpoint notes.

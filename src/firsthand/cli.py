"""Firsthand command-line interface."""

import asyncio
import json
import os
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import typer

from firsthand.http.client import CrawlClient
from firsthand.http.errors import CrawlError, NotFound
from firsthand.http.fixtures import FixtureRecorder, FixtureTooLarge

DEFAULT_UA = "FirsthandBot/0.1 (+https://example.invalid/bot)"

app = typer.Typer(no_args_is_help=True)
fixtures_app = typer.Typer(no_args_is_help=True, help="Record HTTP fixtures for adapter tests.")
app.add_typer(fixtures_app, name="fixtures")


class Method(StrEnum):
    GET = "GET"
    POST = "POST"


@app.callback()
def main() -> None:
    """Firsthand command-line interface."""


@app.command()
def version() -> None:
    """Print the Firsthand version."""
    typer.echo("0.1.0")


@fixtures_app.command("record")
def fixtures_record(
    adapter: Annotated[str, typer.Argument(help="Adapter name, e.g. greenhouse")],
    token: Annotated[str, typer.Argument(help="Board token, e.g. warp")],
    url: Annotated[str, typer.Option("--url", help="Full URL to request")],
    method: Annotated[Method, typer.Option("--method")] = Method.GET,
    json_body: Annotated[str | None, typer.Option("--json", help="JSON request body")] = None,
    config: Annotated[str | None, typer.Option("--config", help="Source config JSON")] = None,
    out: Annotated[Path, typer.Option("--out")] = Path("tests/fixtures"),
) -> None:
    """Perform one request and append it to tests/fixtures/ADAPTER/TOKEN/manifest.json."""
    try:
        body: Any = json.loads(json_body) if json_body is not None else None
        cfg: dict[str, Any] | None = json.loads(config) if config is not None else None
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"invalid JSON: {exc}") from exc
    recorder = FixtureRecorder(out / adapter / token, adapter=adapter, token=token, config=cfg)

    async def run() -> None:
        async with CrawlClient(os.environ.get("USER_AGENT", DEFAULT_UA), recorder=recorder) as c:
            kw = {"json": body} if body is not None else {}
            await c.request(method.value, url, **kw)

    try:
        asyncio.run(run())
    except NotFound:
        typer.echo("note: 404 recorded")  # the client records 404s before raising
    except (CrawlError, FixtureTooLarge) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"saved {recorder.manifest_path} ({recorder.total_bytes()} body bytes)")


fx_app = typer.Typer(no_args_is_help=True, help="Foreign-exchange rates for salary normalization.")
app.add_typer(fx_app, name="fx")


@fx_app.command("fetch")
def fx_fetch() -> None:
    """Fetch the ECB daily reference rates and upsert them into fx_rates (USD per unit)."""
    from sqlalchemy import text

    from firsthand.db.session import get_conn
    from firsthand.normalize.fx import fetch_ecb_rates

    async def run() -> int:
        async with CrawlClient(os.environ.get("USER_AGENT", DEFAULT_UA)) as c:
            day, rates = await fetch_ecb_rates(c)
        stmt = text(
            "INSERT INTO fx_rates (day, currency, usd_per_unit) VALUES (:day, :cur, :rate) "
            "ON CONFLICT (day, currency) DO UPDATE SET usd_per_unit = EXCLUDED.usd_per_unit"
        )
        async with get_conn() as conn:
            for cur, rate in rates.items():
                await conn.execute(stmt, {"day": day, "cur": cur, "rate": rate})
        typer.echo(f"upserted {len(rates)} rates for {day}")
        return len(rates)

    try:
        asyncio.run(run())
    except (CrawlError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc


async def _latest_fx_rates() -> dict[str, float]:
    from sqlalchemy import text

    from firsthand.db.session import get_conn

    async with get_conn() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT currency, usd_per_unit FROM fx_rates "
                    "WHERE day = (SELECT max(day) FROM fx_rates)"
                )
            )
        ).all()
    return {r.currency: float(r.usd_per_unit) for r in rows}


@app.command()
def renormalize(
    all_jobs: Annotated[
        bool, typer.Option("--all", help="Also jobs already at current version")
    ] = False,
) -> None:
    """Re-run the normalizer over stored payloads of open jobs (no HTTP)."""
    from firsthand.db.session import get_engine
    from firsthand.normalize import NormContext
    from firsthand.pipeline.renormalize import renormalize as run_renormalize

    async def run() -> int:
        ctx = NormContext.load(await _latest_fx_rates())
        return await run_renormalize(get_engine(), ctx, only_outdated=not all_jobs)

    typer.echo(f"renormalized {asyncio.run(run())} jobs")


@app.command()
def ingest(
    source: Annotated[int, typer.Option("--source", help="sources.id to crawl once")],
    include_non_us: Annotated[bool, typer.Option("--include-non-us")] = False,
) -> None:
    """Run ONE crawl of one source end-to-end (manual run)."""
    from dataclasses import asdict

    from firsthand.adapters.registry import get_adapter
    from firsthand.db.session import get_engine
    from firsthand.normalize import NormContext
    from firsthand.pipeline.ingest import ingest_source

    async def run() -> dict[str, Any]:
        from sqlalchemy import text

        engine = get_engine()
        async with engine.connect() as conn:
            name = (
                await conn.execute(text("SELECT adapter FROM sources WHERE id = :i"), {"i": source})
            ).scalar_one_or_none()
        if name is None:
            raise typer.BadParameter(f"no such source {source}")
        ctx = NormContext.load(await _latest_fx_rates())
        async with CrawlClient(os.environ.get("USER_AGENT", DEFAULT_UA)) as c:
            res = await ingest_source(
                engine, source, get_adapter(name), c, ctx, us_only=not include_non_us
            )
        await engine.dispose()
        return asdict(res)

    out = asyncio.run(run())
    for k, v in out.items():
        typer.echo(f"{k}: {v}")

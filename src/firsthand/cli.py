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

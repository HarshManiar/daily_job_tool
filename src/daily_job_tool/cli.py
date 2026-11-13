"""Daily Job Tool command-line interface."""

import asyncio
import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import typer

from daily_job_tool.config import get_user_agent
from daily_job_tool.http.client import CrawlClient
from daily_job_tool.http.errors import CrawlError, NotFound
from daily_job_tool.http.fixtures import FixtureRecorder, FixtureTooLarge

DEFAULT_UA = "DailyJobToolBot/0.1 (+https://example.invalid/bot)"

app = typer.Typer(no_args_is_help=True)
fixtures_app = typer.Typer(no_args_is_help=True, help="Record HTTP fixtures for adapter tests.")
app.add_typer(fixtures_app, name="fixtures")


class Method(StrEnum):
    GET = "GET"
    POST = "POST"


@app.callback()
def main() -> None:
    """Daily Job Tool command-line interface."""


@app.command()
def version() -> None:
    """Print the Daily Job Tool version."""
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
        async with CrawlClient(get_user_agent(), recorder=recorder) as c:
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

    from daily_job_tool.db.session import get_conn
    from daily_job_tool.normalize.fx import fetch_ecb_rates

    async def run() -> int:
        async with CrawlClient(get_user_agent()) as c:
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

    from daily_job_tool.db.session import get_conn

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
    from daily_job_tool.db.session import get_engine
    from daily_job_tool.normalize import NormContext
    from daily_job_tool.pipeline.renormalize import renormalize as run_renormalize

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

    from daily_job_tool.adapters.registry import get_adapter
    from daily_job_tool.db.session import get_engine
    from daily_job_tool.normalize import NormContext
    from daily_job_tool.pipeline.ingest import ingest_source

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
        async with CrawlClient(get_user_agent()) as c:
            res = await ingest_source(
                engine, source, get_adapter(name), c, ctx, us_only=not include_non_us
            )
        await engine.dispose()
        return asdict(res)

    out = asyncio.run(run())
    for k, v in out.items():
        typer.echo(f"{k}: {v}")


def _print_summaries(summaries: list[Any]) -> None:
    typer.echo(
        f"{'src':>5}  {'adapter':<12} {'token':<24} {'outcome':<18} "
        f"{'jobs':>5} {'new':>4} {'closed':>6}"
    )
    for s in summaries:
        typer.echo(
            f"{s.source_id:>5}  {s.adapter:<12} {s.token[:24]:<24} {s.outcome:<18} "
            f"{s.jobs_seen:>5} {s.postings_new:>4} {s.postings_closed:>6}"
        )
        if s.error:
            typer.echo(f"       ! {s.error[:160]}")
    typer.echo(f"{len(summaries)} source(s) crawled")


def _run_worker(*, once: bool, limit: int | None, adapter: str | None, source: int | None) -> None:
    from daily_job_tool.db.session import get_engine
    from daily_job_tool.normalize import NormContext
    from daily_job_tool.scheduler.worker import run_worker

    async def run() -> list[Any]:
        engine = get_engine()
        try:
            ctx = NormContext.load(await _latest_fx_rates())
            return await run_worker(
                engine, ctx, once=once, limit=limit, adapter=adapter, source_id=source
            )
        finally:
            await engine.dispose()

    _print_summaries(asyncio.run(run()))


@app.command()
def worker(
    adapter: Annotated[str | None, typer.Option("--adapter")] = None,
    concurrency: Annotated[int, typer.Option("--concurrency")] = 8,
    forever: Annotated[
        bool, typer.Option("--forever/--once", help="Poll for due sources forever (default)")
    ] = True,
    poll_seconds: Annotated[float, typer.Option("--poll-seconds")] = 30.0,
    heartbeat_file: Annotated[
        Path | None, typer.Option("--heartbeat-file", help="Default ~/.daily_job_tool/")
    ] = None,
    no_caffeinate: Annotated[bool, typer.Option("--no-caffeinate")] = False,
) -> None:
    """Run the crawl worker until interrupted (SIGINT/SIGTERM finish in-flight work)."""
    import logging

    from sqlalchemy import text

    from daily_job_tool.db.session import get_engine
    from daily_job_tool.normalize import NormContext
    from daily_job_tool.ops.heartbeat import HeartbeatWriter, default_heartbeat_path
    from daily_job_tool.ops.power import PowerAssertion
    from daily_job_tool.ops.status import DUE_SQL
    from daily_job_tool.scheduler.worker import run_worker

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    log = logging.getLogger("daily_job_tool.worker")

    async def run() -> list[Any]:
        engine = get_engine()
        try:
            ctx = NormContext.load(await _latest_fx_rates())
            async with engine.connect() as conn:
                now = datetime.now(UTC)
                due = (await conn.execute(text(DUE_SQL), {"now": now})).scalar()
                active = (
                    await conn.execute(text("SELECT count(*) FROM sources WHERE status='active'"))
                ).scalar()
                busy = (
                    await conn.execute(
                        text("SELECT count(*) FROM sources WHERE locked_until > :now"), {"now": now}
                    )
                ).scalar()
            log.info(
                "worker start: %s active sources, %s due now, %s in flight (leased); "
                "mode=%s concurrency=%s",
                active, due, busy, "forever" if forever else "once", concurrency,
            )  # fmt: skip
            return await run_worker(
                engine,
                ctx,
                once=not forever,
                adapter=adapter,
                concurrency=concurrency,
                poll_seconds=poll_seconds,
                power=None if no_caffeinate else PowerAssertion(),
                heartbeat=HeartbeatWriter(heartbeat_file or default_heartbeat_path()),
            )
        finally:
            await engine.dispose()

    _print_summaries(asyncio.run(run()))


@app.command()
def crawl(
    once: Annotated[
        bool, typer.Option("--once", help="Required: crawl what is due, then exit")
    ] = False,
    source: Annotated[int | None, typer.Option("--source")] = None,
    adapter: Annotated[str | None, typer.Option("--adapter")] = None,
    limit: Annotated[int | None, typer.Option("--limit")] = None,
) -> None:
    """Crawl all due sources once and print a summary (use `worker` for the daemon)."""
    if not once:
        raise typer.BadParameter("pass --once (or use `daily_job_tool worker` for the daemon)")
    _run_worker(once=True, limit=limit, adapter=adapter, source=source)


registry_app = typer.Typer(no_args_is_help=True, help="Company/source registry.")
app.add_typer(registry_app, name="registry")


@registry_app.command("import")
def registry_import(path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)]) -> None:
    """Idempotently import a seeds CSV into companies and sources."""
    from daily_job_tool.db.session import get_engine
    from daily_job_tool.registry.importer import import_seeds

    async def run() -> Any:
        engine = get_engine()
        try:
            return await import_seeds(engine, path)
        finally:
            await engine.dispose()

    try:
        report = asyncio.run(run())
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(report.render())


@registry_app.command("verify")
def registry_verify(
    limit: Annotated[int | None, typer.Option("--limit")] = None,
    adapter: Annotated[str | None, typer.Option("--adapter")] = None,
) -> None:
    """Fetch each unverified source once; mark dead boards broken. Writes no jobs."""
    from daily_job_tool.db.session import get_engine
    from daily_job_tool.registry.verify import verify_sources

    async def run() -> list[Any]:
        engine = get_engine()
        try:
            async with CrawlClient(get_user_agent()) as c:
                return await verify_sources(engine, c, limit=limit, adapter=adapter)
        finally:
            await engine.dispose()

    rows = asyncio.run(run())
    typer.echo(f"{'src':>5}  {'adapter':<12} {'token':<24} {'result':<10} {'jobs':>5}  board")
    for r in rows:
        typer.echo(
            f"{r.source_id:>5}  {r.adapter:<12} {r.token[:24]:<24} {r.result:<10} {r.jobs:>5}  "
            f"{r.board_name or r.note}"
        )
    ok = sum(r.result == "ok" for r in rows)
    typer.echo(f"{ok}/{len(rows)} ok")


@registry_app.command("remove")
def registry_remove(
    domain: Annotated[str, typer.Argument(help="Company domain (or exact name)")],
    reason: Annotated[str, typer.Option("--reason", help="Why (recorded)")],
    requester: Annotated[str | None, typer.Option("--requester", help="Requester email")] = None,
) -> None:
    """Honor a removal request: close its jobs, stop crawling, block re-seeding."""
    from daily_job_tool.db.session import get_engine
    from daily_job_tool.registry.removal import CompanyNotFound, remove_company

    async def run() -> Any:
        engine = get_engine()
        try:
            return await remove_company(engine, domain, reason, requester)
        finally:
            await engine.dispose()

    try:
        r = asyncio.run(run())
    except CompanyNotFound as exc:
        typer.echo(f"error: no company matches {domain!r}", err=True)
        raise typer.Exit(1) from exc
    if r.already_removed:
        typer.echo(f"{r.company}: already removed (nothing changed beyond re-applying closes)")
    typer.echo(
        f"removed {r.company} ({r.domain or 'no domain'}): {r.sources_removed} source(s), "
        f"{r.postings_closed} posting(s), {r.jobs_closed} job(s) closed"
        + ("; domain blocked" if r.domain else "")
    )


@registry_app.command("blocked")
def registry_blocked() -> None:
    """List sources marked blocked (403/challenge/robots) for manual review."""
    from daily_job_tool.db.session import get_engine
    from daily_job_tool.registry.blocked import list_blocked

    async def run() -> Any:
        engine = get_engine()
        try:
            return await list_blocked(engine)
        finally:
            await engine.dispose()

    rows = asyncio.run(run())
    for r in rows:
        when = r.last_crawled_at.date().isoformat() if r.last_crawled_at else "-"
        typer.echo(
            f"{r.source_id:>5}  {r.company[:28]:<28} {r.adapter:<12} {r.token[:20]:<20} "
            f"{when}  {(r.last_error or '')[:100]}"
        )
    typer.echo(f"{len(rows)} blocked source(s)")


match_app = typer.Typer(no_args_is_help=True, help="Personal match engine (profile-driven).")
app.add_typer(match_app, name="match")


@match_app.command("explain")
def match_explain(job_id: Annotated[int, typer.Argument()]) -> None:
    """Show tier/status/reasons/visa evidence/years for one job."""
    from sqlalchemy import text

    from daily_job_tool.db.session import get_engine

    async def run() -> Any:
        async with get_engine().connect() as c:
            return (
                await c.execute(
                    text(
                        "SELECT j.id, j.title, j.title_norm, co.name AS company, j.match_tier, "
                        "j.match_status, j.match_reasons, j.visa_status, j.visa_evidence, "
                        "j.years_min, j.years_max, j.industry_rank, j.location_rank, j.fit_score, "
                        "j.employment_type, j.salary_max_usd FROM jobs j "
                        "JOIN companies co ON co.id = j.company_id WHERE j.id = :i"
                    ),
                    {"i": job_id},
                )
            ).first()

    r = asyncio.run(run())
    if r is None:
        typer.echo(f"job {job_id} not found")
        raise typer.Exit(1)
    typer.echo(f"job {r.id}: {r.title} @ {r.company}  (title_norm: {r.title_norm})")
    typer.echo(f"  tier={r.match_tier} status={r.match_status} fit={r.fit_score}")
    typer.echo(f"  reasons: {', '.join(r.match_reasons) or '-'}")
    typer.echo(f"  ranks: location={r.location_rank} industry={r.industry_rank}")
    typer.echo(f"  employment={r.employment_type} salary_max_usd={r.salary_max_usd}")
    typer.echo(f"  years: min={r.years_min} max={r.years_max}")
    typer.echo(
        f"  visa: {r.visa_status}" + (f"  evidence: {r.visa_evidence!r}" if r.visa_evidence else "")
    )


@match_app.command("stats")
def match_stats() -> None:
    """Counts by status / tier / reason for open jobs."""
    from sqlalchemy import text

    from daily_job_tool.db.session import get_engine

    async def run() -> dict[str, list[Any]]:
        q = {
            "status": "SELECT match_status AS k, count(*) FROM jobs "
            "WHERE closed_at IS NULL GROUP BY 1 ORDER BY 1",
            "tier": "SELECT coalesce(match_tier::text,'none') AS k, count(*) FROM jobs "
            "WHERE closed_at IS NULL GROUP BY 1 ORDER BY 1",
            "reason": "SELECT r AS k, count(*) FROM jobs, unnest(match_reasons) r "
            "WHERE closed_at IS NULL GROUP BY 1 ORDER BY 2 DESC",
        }
        async with get_engine().connect() as c:
            return {k: list((await c.execute(text(v))).all()) for k, v in q.items()}

    for name, rows in asyncio.run(run()).items():
        typer.echo(f"by {name}:")
        for k, n in rows:
            typer.echo(f"  {k:28} {n}")


@match_app.command("rescore")
def match_rescore() -> None:
    """Recompute visa/years/employment and match columns for all open jobs from stored data.

    No HTTP. Run this once after upgrading (`alembic upgrade head` never re-scores existing
    jobs) and after editing data/profile.yaml (visa phrases, titles, hard filters) or
    company flags.
    """
    from daily_job_tool.db.session import get_engine
    from daily_job_tool.pipeline.renormalize import rescore

    typer.echo(f"rescored {asyncio.run(rescore(get_engine()))} jobs")


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


@app.command()
def web(
    port: Annotated[int, typer.Option("--port")] = 8000,
    host: Annotated[str, typer.Option("--host", help="Loopback only")] = "127.0.0.1",
    reload: Annotated[bool, typer.Option("--reload")] = False,
) -> None:
    """Serve the local web UI at http://127.0.0.1:8000 (never binds a public interface)."""
    if host not in LOOPBACK_HOSTS:
        typer.echo(
            f"error: refusing to bind {host!r}: the web UI is single-user with no auth and "
            "only runs on 127.0.0.1",
            err=True,
        )
        raise typer.Exit(2)
    import uvicorn

    if reload:
        uvicorn.run("daily_job_tool.web.app:app", host=host, port=port, reload=True)
    else:
        from daily_job_tool.web.app import app as web_app

        uvicorn.run(web_app, host=host, port=port)


@app.command()
def detect(
    csv_path: Annotated[
        Path | None, typer.Option("--csv", help="name,domain[,industry] CSV")
    ] = None,
    out: Annotated[Path, typer.Option("--out")] = Path("data/classified.csv"),
    limit: Annotated[int | None, typer.Option("--limit")] = None,
    concurrency: Annotated[int, typer.Option("--concurrency")] = 4,
    domain: Annotated[str | None, typer.Option("--domain", help="Detect a single domain")] = None,
) -> None:
    """Classify which ATS / careers system companies use (no job fetching)."""
    from daily_job_tool.detect.detector import detect_company
    from daily_job_tool.detect.runner import run_batch

    if domain:

        async def one() -> None:
            async with CrawlClient(get_user_agent()) as c:
                d = await detect_company(c, domain)
            typer.echo(json.dumps(d.__dict__, indent=2, default=str))

        asyncio.run(one())
        return
    if csv_path is None:
        raise typer.BadParameter("provide --csv or --domain")
    counts = asyncio.run(run_batch(csv_path, out, limit=limit, concurrency=concurrency))
    typer.echo(f"wrote {out}: {dict(sorted(counts.items()))}")


@app.command()
def status() -> None:
    """Print a short operational status (last crawl, due sources, worker, backups)."""
    from daily_job_tool.config import get_settings
    from daily_job_tool.db.session import get_engine
    from daily_job_tool.ops.status import collect_status, format_status

    async def run() -> dict[str, Any]:
        engine = get_engine()
        try:
            return await collect_status(engine, datetime.now(UTC))
        finally:
            await engine.dispose()

    typer.echo(format_status(asyncio.run(run()), get_settings().bot_contact))


@app.command()
def backup(
    directory: Annotated[
        Path | None, typer.Option("--dir", help="Default ~/Backups/daily_job_tool")
    ] = None,
    keep: Annotated[int, typer.Option("--keep")] = 14,
) -> None:
    """pg_dump -Fc the database, keep the newest 14 dumps."""
    from daily_job_tool.config import get_settings
    from daily_job_tool.ops.backup import BackupError, default_backup_dir, make_backup

    try:
        out, pruned = make_backup(
            get_settings().database_url, directory or default_backup_dir(), keep=keep
        )
    except BackupError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB); pruned {len(pruned)} old dump(s)")


@app.command()
def restore(
    path: Annotated[Path, typer.Argument(help="A .dump file from `backup`")],
    into: Annotated[str, typer.Option("--into", help="NEW database name")],
    yes_overwrite: Annotated[bool, typer.Option("--yes-overwrite")] = False,
) -> None:
    """Restore a dump into a new database (refuses to replace an existing one by default)."""
    from daily_job_tool.config import get_settings
    from daily_job_tool.ops.backup import BackupError, restore_backup

    try:
        restore_backup(get_settings().database_url, path, into, overwrite=yes_overwrite)
    except BackupError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"restored {path} into database {into}")

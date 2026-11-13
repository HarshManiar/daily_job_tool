"""pg_dump backups with pruning, and restore into a new database."""

import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import make_url

KEEP = 14
PREFIX = "daily_job_tool-"
_DBNAME = re.compile(r"^[A-Za-z0-9_]{1,63}$")


class BackupError(Exception):
    pass


def default_backup_dir() -> Path:
    env = os.environ.get("DAILY_JOB_TOOL_BACKUP_DIR")
    return Path(env).expanduser() if env else Path.home() / "Backups/daily_job_tool"


def list_backups(directory: Path) -> list[Path]:
    """Dumps, newest first (names embed a sortable timestamp)."""
    return sorted(directory.glob(f"{PREFIX}*.dump"), reverse=True) if directory.is_dir() else []


def last_backup_time(directory: Path) -> datetime | None:
    files = list_backups(directory)
    if not files:
        return None
    return datetime.fromtimestamp(max(f.stat().st_mtime for f in files)).astimezone()


def prune_backups(directory: Path, keep: int = KEEP) -> list[Path]:
    old = list_backups(directory)[keep:]
    for f in old:
        f.unlink()
    return old


def _conn_args(db_url: str) -> tuple[list[str], dict[str, str], str]:
    u = make_url(db_url)
    args = []
    if u.host:
        args += ["--host", u.host]
    if u.port:
        args += ["--port", str(u.port)]
    if u.username:
        args += ["--username", u.username]
    env = {**os.environ, **({"PGPASSWORD": u.password} if u.password else {})}
    return args, env, u.database or ""


def _run(cmd: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    try:
        r = subprocess.run(cmd, env=env, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise BackupError(f"{cmd[0]} not found on PATH (brew install libpq / postgresql)") from exc
    if r.returncode != 0:
        raise BackupError(f"{cmd[0]} failed: {r.stderr.strip()[:500]}")
    return r


def make_backup(db_url: str, directory: Path, *, now: datetime | None = None, keep: int = KEEP):
    """Dump to <dir>/daily_job_tool-YYYYMMDD-HHMM.dump, prune; returns (path, pruned)."""
    args, env, dbname = _conn_args(db_url)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M")
    out = directory / f"{PREFIX}{stamp}.dump"
    tmp = out.with_name(out.name + ".partial")
    try:
        _run(["pg_dump", "-Fc", *args, "-f", str(tmp), dbname], env)
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
    return out, prune_backups(directory, keep)


def restore_backup(db_url: str, dump: Path, into: str, *, overwrite: bool = False) -> None:
    if not _DBNAME.match(into):
        raise BackupError("database name must be 1-63 letters, digits or underscores")
    if not dump.is_file():
        raise BackupError(f"no such dump: {dump}")
    args, env, _ = _conn_args(db_url)
    q = _run(
        ["psql", *args, "-d", "postgres", "-tAc",
         f"SELECT 1 FROM pg_database WHERE datname = '{into}'"],
        env,
    )  # fmt: skip
    if q.stdout.strip() == "1":
        if not overwrite:
            raise BackupError(
                f"database {into!r} already exists; use --yes-overwrite to replace it"
            )
        _run(["dropdb", *args, "--if-exists", into], env)
    _run(["createdb", *args, into], env)
    _run(["pg_restore", *args, "--no-owner", "-d", into, str(dump)], env)

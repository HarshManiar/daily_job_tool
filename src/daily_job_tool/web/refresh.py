"""'Refresh now': start ONE detached `crawl --once`, tracked by a pid file.

Decision order for POST /refresh:
 1. a launchd worker with an alive heartbeat already refreshes automatically -> do nothing;
 2. a pid file whose process is alive (and younger than MAX_AGE) -> already running;
 3. otherwise spawn (a stale/dead pid file is simply replaced).
The spawner is injectable (create_app(spawner=...)); DAILY_JOB_TOOL_REFRESH_DRYRUN=1 swaps in a
spawner that only logs the argv (used for the live check, never spawns a crawler).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from daily_job_tool.ops.heartbeat import classify, read_heartbeat

log = logging.getLogger("daily_job_tool.web.refresh")

MAX_AGE = timedelta(hours=3)  # a pid file older than this is treated as stale (pid reuse guard)
STALE_HOURS = 6  # Refresh re-crawls anything not crawled in the last 6 hours
Spawner = Callable[[list[str], Path], int]


def default_pid_path() -> Path:
    env = os.environ.get("DAILY_JOB_TOOL_REFRESH_PID")
    return Path(env).expanduser() if env else Path.home() / ".daily_job_tool/refresh.pid"


def default_log_path() -> Path:
    env = os.environ.get("DAILY_JOB_TOOL_REFRESH_LOG")
    return Path(env) if env else Path(__file__).resolve().parents[3] / "logs" / "refresh.log"


def crawl_argv() -> list[str]:
    """Fixed argv, no user input."""
    script = Path(sys.executable).parent / "daily_job_tool"
    if script.exists():
        return [str(script), "crawl", "--once", "--stale-hours", str(STALE_HOURS)]
    return [
        sys.executable,
        "-c",
        "from daily_job_tool.cli import app; app()",
        "crawl",
        "--once",
        "--stale-hours",
        str(STALE_HOURS),
    ]


def real_spawner(argv: list[str], log_path: Path) -> int:
    import subprocess

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as out:
        proc = subprocess.Popen(  # noqa: S603 - argv is fixed in code
            argv,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    return proc.pid


def dryrun_spawner(argv: list[str], log_path: Path) -> int:
    log.warning("REFRESH DRY RUN: would spawn %s (log %s)", argv, log_path)
    return 0


def default_spawner() -> Spawner:
    return (
        dryrun_spawner if os.environ.get("DAILY_JOB_TOOL_REFRESH_DRYRUN") == "1" else real_spawner
    )


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.waitpid(pid, os.WNOHANG)[0] == pid:  # our own finished child: reap the zombie
            return False
    except ChildProcessError:
        pass  # not our child
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (OSError, OverflowError):
        return False
    return True


def read_pidfile(path: Path) -> dict[str, Any] | None:
    try:
        d = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or not isinstance(d.get("pid"), int):
        return None
    try:
        d["started"] = datetime.fromisoformat(d["started_at"])
    except (KeyError, ValueError, TypeError):
        return None
    return d


def running_info(path: Path, now: datetime) -> dict[str, Any] | None:
    """The pid-file record if its process is alive and not stale, else None."""
    d = read_pidfile(path)
    if d is None or now - d["started"] > MAX_AGE or not pid_alive(d["pid"]):
        return None
    return d


@dataclass
class Outcome:
    state: str  # started | running | worker | dryrun
    since: datetime | None = None


def start_refresh(
    *,
    spawner: Spawner,
    pid_path: Path,
    heartbeat_path: Path,
    log_path: Path,
    now: datetime,
) -> Outcome:
    hb = read_heartbeat(heartbeat_path)
    if classify(hb, now) == "alive":
        return Outcome("worker")
    cur = running_info(pid_path, now)
    if cur is not None:
        return Outcome("running", cur["started"])
    pid = spawner(crawl_argv(), log_path)
    if pid <= 0:  # dry run: nothing was started
        return Outcome("dryrun", now)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = pid_path.with_name(pid_path.name + ".tmp")
    tmp.write_text(json.dumps({"pid": pid, "started_at": now.astimezone(UTC).isoformat()}))
    os.replace(tmp, pid_path)
    return Outcome("started", now)


async def progress(conn: AsyncConnection, since: datetime) -> dict[str, int]:
    r = (
        await conn.execute(
            text(
                "SELECT count(*) FILTER (WHERE finished_at IS NOT NULL) AS done,"
                " count(*) FILTER (WHERE finished_at IS NULL) AS in_flight,"
                " coalesce(sum(jobs_new), 0) AS new_jobs "
                "FROM crawl_runs WHERE started_at >= :s"
            ),
            {"s": since},
        )
    ).one()
    return {"done": r.done, "in_flight": r.in_flight, "new_jobs": int(r.new_jobs)}


def worker_summary(hb: dict[str, Any] | None) -> str:
    if not hb:
        return ""
    bits = [f"{hb.get('in_flight', 0)} in flight"]
    if hb.get("last_finished_source"):
        bits.append(f"last finished {hb['last_finished_source']}")
    return ", ".join(bits)

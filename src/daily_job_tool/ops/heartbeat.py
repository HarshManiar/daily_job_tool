"""Worker heartbeat file: a tiny JSON the worker rewrites every ~30 s."""

import json
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HEARTBEAT_INTERVAL = 30.0
STALE_AFTER = 120.0  # seconds without a beat before the worker counts as stale


def default_heartbeat_path() -> Path:
    env = os.environ.get("DAILY_JOB_TOOL_HEARTBEAT")
    return Path(env).expanduser() if env else Path.home() / ".daily_job_tool/worker_heartbeat.json"


def write_heartbeat(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def read_heartbeat(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def classify(hb: dict[str, Any] | None, now: datetime, *, stale_after: float = STALE_AFTER) -> str:
    """'alive', 'stale' (file exists but old) or 'not running'."""
    if not hb:
        return "not running"
    try:
        beat = datetime.fromisoformat(hb["updated_at"])
    except (KeyError, ValueError, TypeError):
        return "stale"
    return "alive" if (now - beat).total_seconds() <= stale_after else "stale"


@dataclass
class HeartbeatWriter:
    path: Path = field(default_factory=default_heartbeat_path)
    interval: float = HEARTBEAT_INTERVAL
    pid: int = field(default_factory=os.getpid)
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    last_poll_at: str | None = None
    last_finished_source: str | None = None
    _last_write: float | None = None

    def poll(self) -> None:
        self.last_poll_at = datetime.now(UTC).isoformat()

    def tick(self, in_flight: int, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and self._last_write is not None and now - self._last_write < self.interval:
            return
        self._last_write = now
        try:
            write_heartbeat(
                self.path,
                {
                    "pid": self.pid,
                    "started_at": self.started_at,
                    "last_poll_at": self.last_poll_at,
                    "in_flight": in_flight,
                    "last_finished_source": self.last_finished_source,
                    "updated_at": datetime.now(UTC).isoformat(),
                },
            )
        except OSError:
            pass  # a heartbeat must never take the worker down

    def clear(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass

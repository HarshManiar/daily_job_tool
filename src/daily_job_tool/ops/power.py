"""Hold a macOS idle-sleep assertion (`caffeinate -i -w <pid>`) while sources are in flight."""

import logging
import os
import subprocess
import sys
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)


def _popen(args: list[str]) -> Any:
    return subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)  # fmt: skip


class PowerAssertion:
    """One caffeinate child at a time: started when work begins, stopped when idle."""

    def __init__(
        self,
        spawner: Callable[[list[str]], Any] = _popen,
        *,
        platform: str | None = None,
        pid: int | None = None,
    ) -> None:
        self._spawn = spawner
        self._enabled = (platform or sys.platform) == "darwin"
        self._pid = pid or os.getpid()
        self._proc: Any = None

    @property
    def active(self) -> bool:
        return self._proc is not None

    def update(self, in_flight: int) -> None:
        if not self._enabled:
            return
        if in_flight > 0 and self._proc is None:
            try:
                self._proc = self._spawn(["caffeinate", "-i", "-w", str(self._pid)])
            except OSError as exc:  # caffeinate missing
                log.warning("cannot hold sleep assertion: %s", exc)
                self._enabled = False
        elif in_flight == 0 and self._proc is not None:
            self.stop()

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.terminate()
            except OSError:
                pass

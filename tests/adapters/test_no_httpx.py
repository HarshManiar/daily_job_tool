import re
from pathlib import Path

ADAPTERS = Path(__file__).resolve().parents[2] / "src" / "daily_job_tool" / "adapters"
PATTERN = re.compile(r"^\s*(import httpx|from httpx)", re.MULTILINE)


def test_adapters_never_import_httpx():
    offenders = [
        str(p) for p in ADAPTERS.rglob("*.py") if PATTERN.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == []

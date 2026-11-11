"""Record and replay HTTP exchanges. Format is documented in tests/fixtures/README.md."""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx

MAX_FIXTURE_BYTES = 500 * 1024
MANIFEST = "manifest.json"
# response headers adapters may rely on (pagination etc.); everything else is dropped
SAVED_HEADERS = ("link", "x-total-count", "x-next-page", "x-total-pages", "location", "retry-after")


class FixtureTooLarge(Exception):
    pass


def _ext_for(content_type: str) -> str:
    ct = content_type.lower()
    if "json" in ct:
        return "json"
    if "xml" in ct:
        return "xml"
    if "html" in ct:
        return "html"
    return "txt"


def _norm_url(url: str) -> tuple[str, str, str, tuple[tuple[str, str], ...]]:
    p = urlsplit(url)
    return (p.scheme.lower(), p.netloc.lower(), p.path, tuple(sorted(parse_qsl(p.query, True))))


class FixtureRecorder:
    """Recorder hook for CrawlClient; appends exchanges to <dir>/manifest.json."""

    def __init__(
        self,
        directory: str | Path,
        *,
        adapter: str | None = None,
        token: str | None = None,
        config: dict[str, Any] | None = None,
        max_bytes: int = MAX_FIXTURE_BYTES,
    ) -> None:
        self.dir = Path(directory)
        self.adapter = adapter or self.dir.parent.name
        self.token = token or self.dir.name
        self.config = config
        self.max_bytes = max_bytes

    @property
    def manifest_path(self) -> Path:
        return self.dir / MANIFEST

    def total_bytes(self) -> int:
        if not self.dir.exists():
            return 0
        return sum(f.stat().st_size for f in self.dir.iterdir() if f.name != MANIFEST)

    def _load(self) -> dict[str, Any]:
        if self.manifest_path.exists():
            return json.loads(self.manifest_path.read_text())
        return {
            "adapter": self.adapter,
            "token": self.token,
            "config": self.config or {},
            "recorded_at": "",
            "exchanges": [],
        }

    def record(
        self,
        method: str,
        url: str,
        request_json: Any,
        status: int,
        headers: httpx.Headers,
        body: bytes,
    ) -> None:
        existed = self.dir.exists()
        manifest = self._load()
        exchanges: list[dict[str, Any]] = manifest["exchanges"]
        key = (method.upper(), _norm_url(url), request_json)
        old = next(
            (e for e in exchanges if (e["method"], _norm_url(e["url"]), e["json"]) == key), None
        )
        old_size = (self.dir / old["file"]).stat().st_size if old else 0
        if self.total_bytes() - old_size + len(body) > self.max_bytes:
            if not existed or not any(self.dir.iterdir()):
                shutil.rmtree(self.dir, ignore_errors=True)
            raise FixtureTooLarge(
                f"fixture dir {self.dir} would exceed {self.max_bytes} bytes; pick a smaller board"
            )
        self.dir.mkdir(parents=True, exist_ok=True)
        ctype = headers.get("content-type", "application/octet-stream")
        if old:
            (self.dir / old["file"]).unlink(missing_ok=True)
            index = int(old["file"].split(".")[0])
        else:
            index = len(exchanges)
            while any(e["file"].startswith(f"{index:03d}.") for e in exchanges):
                index += 1
        fname = f"{index:03d}.{_ext_for(ctype)}"
        (self.dir / fname).write_bytes(body)
        entry = {
            "method": method.upper(),
            "url": url,
            "json": request_json,
            "status": status,
            "content_type": ctype,
            "headers": {h: headers[h] for h in SAVED_HEADERS if h in headers},
            "file": fname,
        }
        if old:
            exchanges[exchanges.index(old)] = entry
        else:
            exchanges.append(entry)
        if self.config is not None:
            manifest["config"] = self.config
        manifest["recorded_at"] = datetime.now(UTC).isoformat()
        self.manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")


class FixtureTransport(httpx.AsyncBaseTransport):
    """Serve responses from recorded manifests; unmatched requests fail loudly."""

    def __init__(self, dirs: str | Path | list[str | Path] | tuple[str | Path, ...]) -> None:
        if isinstance(dirs, str | Path):
            dirs = [dirs]
        self._exchanges: list[tuple[str, Any, Any, dict[str, Any], Path]] = []
        for d in dirs:
            base = Path(d)
            manifest = json.loads((base / MANIFEST).read_text())
            for ex in manifest["exchanges"]:
                self._exchanges.append(
                    (ex["method"].upper(), _norm_url(ex["url"]), ex["json"], ex, base)
                )
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body_json: Any = None
        if request.content:
            try:
                body_json = json.loads(request.content)
            except ValueError:  # not JSON (or not UTF-8): compare raw bytes, never match JSON
                body_json = request.content
        key = _norm_url(str(request.url))
        for method, nurl, ejson, ex, base in self._exchanges:
            if method == request.method.upper() and nurl == key and ejson == body_json:
                return httpx.Response(
                    ex["status"],
                    headers={**ex.get("headers", {}), "content-type": ex["content_type"]},
                    content=(base / ex["file"]).read_bytes(),
                    request=request,
                )
        if request.url.path == "/robots.txt":
            return httpx.Response(404, content=b"", request=request)
        known = "\n  ".join(f"{m} {ex['url']}" for m, _, _, ex, _ in self._exchanges)
        raise AssertionError(
            f"No fixture for {request.method} {request.url} json={body_json!r}.\n"
            f"Recorded exchanges:\n  {known}"
        )

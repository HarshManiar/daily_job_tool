"""Shared helpers for adapter tests."""

import json
from pathlib import Path
from typing import Any

import httpx

from firsthand.adapters.base import SourceRef
from firsthand.http.client import CrawlClient
from firsthand.http.fixtures import FixtureTransport
from firsthand.http.ratelimit import HostLimiter

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def client_for(transport: httpx.AsyncBaseTransport) -> CrawlClient:
    return CrawlClient(
        "firsthand-test/0.1",
        transport=transport,
        limiter=HostLimiter(overrides=(), default_rate=1000, default_burst=1000),
        retry_delays=(0, 0, 0),
    )


def fixture_client(adapter: str, token: str) -> CrawlClient:
    return client_for(FixtureTransport(FIXTURES / adapter / token))


def fixture_json(adapter: str, token: str, name: str = "000.json") -> Any:
    return json.loads((FIXTURES / adapter / token / name).read_text())


def mock_client(handler: Any) -> CrawlClient:
    return client_for(httpx.MockTransport(handler))


def src(adapter: str, token: str, **config: Any) -> SourceRef:
    return SourceRef(adapter=adapter, token=token, config=config)


def json_handler(body: Any, status: int = 200) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(status, json=body)

    return handler

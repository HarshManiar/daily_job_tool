# Fixtures

Recorded HTTP exchanges used by offline adapter tests. Record with:

    uv run firsthand fixtures record ADAPTER TOKEN --url URL [--method POST --json '{...}'] [--config '{...}']

Repeat the command to append more URLs to the same token. Keep each token dir <= 500 KB
(the recorder refuses beyond that), so choose small boards.

## Layout

    tests/fixtures/<adapter>/<token>/manifest.json
    tests/fixtures/<adapter>/<token>/000.json   # body files: NNN.<ext>, ext = json|xml|html|txt

`manifest.json`:

    {
      "adapter": "greenhouse",
      "token": "warp",
      "config": {},
      "recorded_at": "2026-11-11T12:00:00+00:00",
      "exchanges": [
        {"method": "GET", "url": "<full url incl. query>", "json": null,
         "status": 200, "content_type": "application/json", "file": "000.json"}
      ]
    }

`json` is the request JSON body (or null). 404 responses are recorded too.

## Replay in tests

    from firsthand.http.client import CrawlClient
    from firsthand.http.fixtures import FixtureTransport

    client = CrawlClient(ua, transport=FixtureTransport(path), retry_delays=(0, 0, 0))

Matching is on method + full URL (query params order-insensitive) + JSON body. robots.txt
requests not in the manifest get a 404 (allow all). Anything else unmatched raises
`AssertionError` listing the URL. Pass `limiter=HostLimiter(overrides=(), default_rate=1000,
default_burst=1000)` to avoid real rate-limit sleeps in tests with many requests.

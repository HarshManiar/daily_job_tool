# Fixtures

Recorded HTTP exchanges used by offline adapter tests. Record with:

    uv run daily_job_tool fixtures record ADAPTER TOKEN --url URL [--method POST --json '{...}'] [--config '{...}']

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
         "status": 200, "content_type": "application/json",
         "headers": {"link": "<...>; rel=\"next\"", "x-total-count": "42"}, "file": "000.json"}
      ]
    }

`json` is the request JSON body (or null). 404 responses are recorded too.
`headers` holds an allowlist of response headers (link, x-total-count, x-next-page,
x-total-pages, location, retry-after) and is replayed with the response; manifests without
it still load. Redirects are followed by the client, and the exchange is recorded under the
original URL. A request body that is not JSON never matches a recorded exchange.

## Replay in tests

    from daily_job_tool.http.client import CrawlClient
    from daily_job_tool.http.fixtures import FixtureTransport

    client = CrawlClient(ua, transport=FixtureTransport(path), retry_delays=(0, 0, 0))

Matching is on method + full URL (query params order-insensitive) + JSON body. robots.txt
requests not in the manifest get a 404 (allow all). Anything else unmatched raises
`AssertionError` listing the URL. Pass `limiter=HostLimiter(overrides=(), default_rate=1000,
default_burst=1000)` to avoid real rate-limit sleeps in tests with many requests.

## html_list fixtures (config-driven HTML adapter)

`tests/fixtures/html_list/{hyro,medidata,doceree}/` hold a list page (or XML sitemap) plus 3
detail pages each. The source `config` the adapter was written against is stored in each
`manifest.json` (`config`) and the adapter tests read it from there, so the seeds row and the
tests cannot drift. These fixtures are hand-trimmed to stay under 500 KB (`"trimmed"` key in
the manifest): `<script>`/`<style>`/`<svg>`/comments removed (JSON-LD kept), Hyro pages cut to
`<main>`, and Medidata's sitemap cut from 80 to 10 URLs. Config schema: see the module docstring
of `src/daily_job_tool/adapters/html_list.py`.

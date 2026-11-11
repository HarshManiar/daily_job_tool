"""Exceptions raised by the crawl client. Adapters may catch these; they never see httpx."""


class CrawlError(Exception):
    """Base class for every error raised by CrawlClient."""


class RobotsDisallowed(CrawlError):
    def __init__(self, url: str) -> None:
        super().__init__(f"robots.txt disallows {url}")
        self.url = url


class DomainBlocked(CrawlError):
    """Host is on the aggregator blocklist; never fetched (ARCHITECTURE.md section 0)."""

    def __init__(self, host: str) -> None:
        super().__init__(f"domain {host} is blocklisted")
        self.host = host


class CrawlDelayTooLong(CrawlError):
    """robots.txt Crawl-delay exceeds what we are willing to wait; host is skipped."""

    def __init__(self, host: str, delay: float) -> None:
        super().__init__(f"{host} requests Crawl-delay {delay:g}s; skipping host")
        self.host = host
        self.delay = delay


class HostCircuitOpen(CrawlError):
    def __init__(self, host: str) -> None:
        super().__init__(f"circuit open for host {host}")
        self.host = host


class FetchError(CrawlError):
    def __init__(self, url: str, status: int | None = None, message: str = "") -> None:
        super().__init__(f"{url} -> {status if status is not None else 'no response'}: {message}")
        self.url = url
        self.status = status
        self.message = message


class NotFound(FetchError):
    def __init__(self, url: str, status: int = 404) -> None:
        super().__init__(url, status, "not found" if status == 404 else "gone")


class Blocked(FetchError):
    """401/403 or a bot-challenge page. Never retried, never worked around."""

    def __init__(self, url: str, status: int | None = None, message: str = "blocked") -> None:
        super().__init__(url, status, message)


class ResponseTooLarge(FetchError):
    def __init__(self, url: str, max_bytes: int) -> None:
        super().__init__(url, None, f"response exceeds {max_bytes} bytes")
        self.max_bytes = max_bytes

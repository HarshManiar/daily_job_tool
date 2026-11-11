"""Exceptions raised by the crawl client. Adapters may catch these; they never see httpx."""


class CrawlError(Exception):
    """Base class for every error raised by CrawlClient."""


class RobotsDisallowed(CrawlError):
    def __init__(self, url: str) -> None:
        super().__init__(f"robots.txt disallows {url}")
        self.url = url


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
    def __init__(self, url: str) -> None:
        super().__init__(url, 404, "not found")


class Blocked(FetchError):
    """401/403 or a bot-challenge page. Never retried, never worked around."""

    def __init__(self, url: str, status: int | None = None, message: str = "blocked") -> None:
        super().__init__(url, status, message)


class ResponseTooLarge(FetchError):
    def __init__(self, url: str, max_bytes: int) -> None:
        super().__init__(url, None, f"response exceeds {max_bytes} bytes")
        self.max_bytes = max_bytes

from firsthand.http.ratelimit import HostLimiter, TokenBucket


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.now += s


async def test_bucket_burst_then_paced():
    t = FakeTime()
    b = TokenBucket(1.0, 2, t.clock, t.sleep)
    for _ in range(2):
        await b.acquire()
    assert t.now == 0
    await b.acquire()
    assert t.now == 1.0
    await b.acquire()
    assert t.now == 2.0


async def test_bucket_refills_while_idle():
    t = FakeTime()
    b = TokenBucket(1.0, 2, t.clock, t.sleep)
    await b.acquire()
    await b.acquire()
    t.now += 10
    await b.acquire()
    await b.acquire()
    assert t.sleeps == []


def test_rates():
    lim = HostLimiter()
    assert lim.rate_for("example.com") == 1.0
    assert lim.rate_for("boards-api.greenhouse.io") == 2.0
    assert lim.rate_for("api.lever.co") == 2.0
    assert lim.rate_for("api.eu.lever.co") == 2.0
    assert lim.rate_for("api.ashbyhq.com") == 2.0
    assert lim.rate_for("acme.wd5.myworkdayjobs.com") == 0.5


async def test_workday_paced_at_half_rps():
    t = FakeTime()
    lim = HostLimiter(clock=t.clock, sleep=t.sleep)
    for _ in range(3):
        await lim.acquire("acme.wd5.myworkdayjobs.com")
    assert t.now == 2.0  # burst 2, then one token every 2s


async def test_crawl_delay_lowers_rate():
    t = FakeTime()
    lim = HostLimiter(clock=t.clock, sleep=t.sleep)
    lim.set_crawl_delay("example.com", 5)
    assert lim.rate_for("example.com") == 0.2
    await lim.acquire("example.com")
    await lim.acquire("example.com")
    assert t.now == 5.0


def test_crawl_delay_never_raises_rate():
    lim = HostLimiter()
    lim.set_crawl_delay("example.com", 0.1)
    assert lim.rate_for("example.com") == 1.0


async def test_hosts_independent():
    t = FakeTime()
    lim = HostLimiter(clock=t.clock, sleep=t.sleep)
    for host in ("a.com", "b.com"):
        await lim.acquire(host)
        await lim.acquire(host)
    assert t.now == 0

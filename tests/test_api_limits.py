"""The small in-process guards of W0.7: a bounded LRU cache, a per-key rate limiter, CORS origins from the
environment, and the client address the limiter keys on. No server, no network, no wall clock (a fake clock)."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from starlette.datastructures import Headers

from api.limits import LruCache, RateLimiter, client_ip, parse_origins, positive_int, rate_key

DEFAULT_ORIGINS = ["null", "http://localhost:5500"]


# --- LRU cache -------------------------------------------------------------------------------------------------------


def test_the_cache_never_holds_more_than_its_size() -> None:
    cache: LruCache[str, int] = LruCache(3)
    for i in range(10):
        cache[f"k{i}"] = i
    assert len(cache) == 3 and [k in cache for k in ("k6", "k7", "k8", "k9")] == [False, True, True, True]


def test_reading_an_entry_keeps_it_and_the_least_recently_used_goes() -> None:
    cache: LruCache[str, int] = LruCache(2)
    cache["a"], cache["b"] = 1, 2
    assert cache["a"] == 1  # a is now the most recently used
    cache["c"] = 3
    assert "a" in cache and "b" not in cache and "c" in cache


def test_asking_whether_a_key_is_there_does_not_keep_it_alive() -> None:
    cache: LruCache[str, int] = LruCache(2)
    cache["a"], cache["b"] = 1, 2
    assert "a" in cache
    cache["c"] = 3
    assert "a" not in cache  # `in` is not a use


def test_a_missing_key_raises_keyerror_and_clear_empties_the_cache() -> None:
    cache: LruCache[str, int] = LruCache(2)
    with pytest.raises(KeyError):
        cache["nope"]
    cache["a"] = 1
    cache.clear()
    assert len(cache) == 0 and "a" not in cache


def test_get_is_one_atomic_read_that_returns_the_default_and_counts_as_a_use() -> None:
    cache: LruCache[str, int] = LruCache(2)
    cache["a"], cache["b"] = 1, 2
    assert cache.get("a") == 1 and cache.get("zzz") is None and cache.get("zzz", 7) == 7
    cache["c"] = 3  # a was just read, so b is the least recently used
    assert "a" in cache and "b" not in cache


def test_a_cache_must_hold_at_least_one_entry() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        LruCache(0)


def test_the_cache_stays_bounded_under_concurrent_writers() -> None:
    cache: LruCache[int, int] = LruCache(50)

    def write(base: int) -> None:
        for i in range(500):
            cache[base + i] = i

    threads = [threading.Thread(target=write, args=(t * 1000,)) for t in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(cache) == 50


# --- rate limiter -------------------------------------------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_calls_up_to_the_limit_pass_and_the_next_one_is_told_when_to_retry() -> None:
    clock = Clock()
    limiter = RateLimiter(limit=3, window_s=60.0, clock=clock)
    assert [limiter.hit("1.2.3.4") for _ in range(3)] == [None, None, None]
    clock.now += 10
    retry = limiter.hit("1.2.3.4")
    assert retry == pytest.approx(50.0)  # the first call leaves the window 60 s after it was made


def test_the_window_slides_so_the_oldest_call_frees_a_place() -> None:
    clock = Clock()
    limiter = RateLimiter(limit=2, window_s=60.0, clock=clock)
    assert limiter.hit("ip") is None
    clock.now += 30
    assert limiter.hit("ip") is None
    assert limiter.hit("ip") is not None  # blocked
    clock.now += 31  # 61 s after the first call: only the second is still inside the window
    assert limiter.hit("ip") is None
    assert limiter.hit("ip") is not None


def test_a_blocked_call_is_not_counted_so_waiting_always_helps() -> None:
    clock = Clock()
    limiter = RateLimiter(limit=1, window_s=60.0, clock=clock)
    assert limiter.hit("ip") is None
    for _ in range(100):
        assert limiter.hit("ip") is not None  # hammering does not push the release further away
    clock.now += 60
    assert limiter.hit("ip") is None


def test_each_address_has_its_own_budget() -> None:
    limiter = RateLimiter(limit=1, window_s=60.0, clock=Clock())
    assert limiter.hit("a") is None and limiter.hit("b") is None
    assert limiter.hit("a") is not None and limiter.hit("b") is not None


def test_idle_addresses_are_forgotten_so_memory_stays_bounded() -> None:
    clock = Clock()
    limiter = RateLimiter(limit=1, window_s=60.0, clock=clock)
    for i in range(RateLimiter.SWEEP_ABOVE + 10):
        limiter.hit(f"10.0.{i // 256}.{i % 256}")
    clock.now += 120  # every address is idle now
    limiter.hit("fresh")
    assert limiter.tracked_keys() <= 2


def test_a_crowd_inside_one_window_does_not_sweep_on_every_call(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    limiter = RateLimiter(limit=1, window_s=60.0, clock=clock)
    sweeps: list[float] = []
    real = limiter._sweep
    monkeypatch.setattr(limiter, "_sweep", lambda now: sweeps.append(now) or real(now))
    for i in range(RateLimiter.SWEEP_ABOVE + 200):
        limiter.hit(f"k{i}")
    assert sweeps == []  # nobody is idle yet, so a full scan under the lock would find nothing


def test_a_limiter_needs_a_positive_limit_and_window() -> None:
    with pytest.raises(ValueError):
        RateLimiter(limit=0, window_s=60.0)
    with pytest.raises(ValueError):
        RateLimiter(limit=1, window_s=0.0)


# --- CORS origins and settings --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("raw", [None, "", "   ", " , ,"])
def test_unset_or_blank_origins_keep_the_defaults(raw: str | None) -> None:
    assert parse_origins(raw, DEFAULT_ORIGINS) == DEFAULT_ORIGINS


def test_a_list_from_the_environment_replaces_the_defaults() -> None:
    raw = " https://verity.example , https://demo.example/ ,,"
    assert parse_origins(raw, DEFAULT_ORIGINS) == ["https://verity.example", "https://demo.example"]


def test_the_returned_defaults_are_a_copy() -> None:
    origins = parse_origins(None, DEFAULT_ORIGINS)
    origins.append("https://x.example")
    assert DEFAULT_ORIGINS == ["null", "http://localhost:5500"]


def test_positive_int_reads_a_setting_or_names_the_bad_one() -> None:
    assert positive_int(None, 20, "VERITY_VOICE_RATE_PER_MIN") == 20
    assert positive_int(" 7 ", 20, "VERITY_VOICE_RATE_PER_MIN") == 7
    for bad in ("0", "-3", "many", "2.5"):
        with pytest.raises(ValueError, match="VERITY_VOICE_RATE_PER_MIN"):
            positive_int(bad, 20, "VERITY_VOICE_RATE_PER_MIN")


# --- who is calling -----------------------------------------------------------------------------------------------------


def request(host: str | None, forwarded: str | None = None) -> SimpleNamespace:
    raw = [] if forwarded is None else [(b"x-forwarded-for", forwarded.encode())]
    return SimpleNamespace(client=None if host is None else SimpleNamespace(host=host), headers=Headers(raw=raw))


def test_the_peer_address_is_used_and_a_forwarded_header_is_ignored_by_default() -> None:
    assert client_ip(request("10.0.0.5", "6.6.6.6"), trust_proxy=False) == "10.0.0.5"


def test_behind_one_trusted_proxy_the_address_the_proxy_saw_is_used() -> None:
    # The proxy APPENDS the peer it saw; anything before it was written by the caller and can be forged.
    assert client_ip(request("10.0.0.5", "9.9.9.9, 203.0.113.7"), trust_proxy=True) == "203.0.113.7"
    assert client_ip(request("10.0.0.5", "203.0.113.7"), trust_proxy=True) == "203.0.113.7"


def test_every_header_line_of_x_forwarded_for_is_read_not_just_the_first() -> None:
    headers = Headers(raw=[(b"x-forwarded-for", b"9.9.9.9"), (b"x-forwarded-for", b"203.0.113.7")])
    req = SimpleNamespace(client=SimpleNamespace(host="10.0.0.5"), headers=headers)
    assert client_ip(req, trust_proxy=True) == "203.0.113.7"


def test_an_ipv6_client_gets_one_budget_per_64_not_one_per_address() -> None:
    # A /64 is what one subscriber gets: rotating through its 2**64 addresses must not buy a fresh budget each time.
    assert rate_key("2001:db8:1:2:aaaa:bbbb:cccc:dddd") == rate_key("2001:db8:1:2::1")
    assert rate_key("2001:db8:1:2::1") != rate_key("2001:db8:1:3::1")


def test_ipv4_and_unparseable_addresses_are_used_as_they_are() -> None:
    assert rate_key("203.0.113.7") == "203.0.113.7"
    assert rate_key("::ffff:203.0.113.7") == "203.0.113.7"  # an IPv4 client seen through a dual-stack socket
    assert rate_key("unknown") == "unknown" and rate_key("testclient") == "testclient"


def test_a_trusted_proxy_setting_without_the_header_falls_back_to_the_peer() -> None:
    assert client_ip(request("10.0.0.5"), trust_proxy=True) == "10.0.0.5"
    assert client_ip(request(None), trust_proxy=False) == "unknown"

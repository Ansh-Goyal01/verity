"""Small in-process guards for the API (W0.7): a bounded cache, a per-address rate limiter, CORS origins and
numeric settings read from the environment. No dependency, no I/O. State lives in this process, which is how the
demo runs (one worker); a second worker would simply get its own budget and its own cache.
"""

from __future__ import annotations

import ipaddress
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Hashable, Sequence
from typing import Any, Generic, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class LruCache(Generic[K, V]):
    """A bounded dict: the least recently read or written entry is dropped first. ``in`` is not a use.

    Requests run in a thread pool, so every operation takes the lock (a move-then-evict is two steps).
    """

    def __init__(self, maxsize: int) -> None:
        if maxsize < 1:
            raise ValueError("an LRU cache must hold at least 1 entry")
        self.maxsize = maxsize
        self._data: OrderedDict[K, V] = OrderedDict()
        self._lock = threading.Lock()

    def __getitem__(self, key: K) -> V:
        with self._lock:
            value = self._data[key]  # KeyError for a missing key, like a dict
            self._data.move_to_end(key)
            return value

    def get(self, key: K, default: V | None = None) -> V | None:
        """One atomic read. Use this, never ``if k in cache: cache[k]``: another thread's insert can evict ``k``
        between the two calls, and the second would raise."""
        with self._lock:
            if key not in self._data:
                return default
            self._data.move_to_end(key)
            return self._data[key]

    def __setitem__(self, key: K, value: V) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)

    def __contains__(self, key: object) -> bool:
        with self._lock:
            return key in self._data

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


class RateLimiter:
    """At most ``limit`` calls per ``window_s`` seconds for each key (sliding window).

    A blocked call is not recorded, so a client that keeps hammering is released as soon as its oldest allowed call
    leaves the window, never later. Once more than ``SWEEP_ABOVE`` keys are tracked, idle ones are forgotten, at most
    once per window (a full scan under the lock on every call would make a crowd of addresses slow everyone down).
    """

    SWEEP_ABOVE = 1024

    def __init__(self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        if limit < 1 or window_s <= 0:
            raise ValueError("a rate limiter needs limit >= 1 and window_s > 0")
        self.limit, self.window_s, self._clock = limit, window_s, clock
        self._hits: dict[str, deque[float]] = {}
        self._last_sweep = clock()
        self._lock = threading.Lock()

    def hit(self, key: str) -> float | None:
        """Record a call. ``None`` means allowed; otherwise the seconds until a call would be allowed."""
        now = self._clock()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] >= self.window_s:
                hits.popleft()
            if len(hits) >= self.limit:
                return hits[0] + self.window_s - now
            hits.append(now)
            if len(self._hits) > self.SWEEP_ABOVE and now - self._last_sweep >= self.window_s:
                self._sweep(now)
            return None

    def _sweep(self, now: float) -> None:
        self._last_sweep = now
        idle = [k for k, hits in self._hits.items() if not hits or now - hits[-1] >= self.window_s]
        for key in idle:
            del self._hits[key]

    def tracked_keys(self) -> int:
        with self._lock:
            return len(self._hits)


def parse_origins(raw: str | None, default: Sequence[str]) -> list[str]:
    """CORS origins from a comma-separated setting. When it names any, it REPLACES the defaults; unset or blank keeps
    them. A trailing slash is dropped: a browser never sends one in ``Origin``, so it could never match."""
    origins = [o.strip().rstrip("/") for o in (raw or "").split(",") if o.strip()]
    return origins or list(default)


def positive_int(raw: str | None, default: int, name: str) -> int:
    """A numeric setting that must be a whole number >= 1. Fails at startup, naming the setting."""
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        value = 0
    if value < 1:
        raise ValueError(f"{name} must be a whole number of at least 1, not {raw!r}")
    return value


def client_ip(request: Any, trust_proxy: bool) -> str:
    """The address a rate limit is counted against.

    By default ``request.client``: the TCP peer, unless uvicorn's own proxy-header handling rewrote it (it does when
    the peer is in FORWARDED_ALLOW_IPS, and with ``*`` it takes the LEFTMOST entry, which the caller writes).
    Behind exactly one trusted proxy (``VERITY_TRUST_PROXY=1``) this reads the raw ``X-Forwarded-For`` itself, whatever
    uvicorn did: the proxy appends the peer it saw, so only the LAST entry is trustworthy. Every header line counts
    (uvicorn joins them too), not just the first.
    """
    if trust_proxy:
        last = ",".join(request.headers.getlist("x-forwarded-for")).split(",")[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "unknown"


def rate_key(address: str) -> str:
    """What a rate limit counts: the address itself, except that an IPv6 client is counted per /64 (the block one
    subscriber gets), so rotating through 2**64 addresses does not buy a fresh budget each time. IPv4 and anything
    that is not an IP ("unknown", a test client's name) are used as they are."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    if ip.version == 4:
        return str(ip)
    if ip.ipv4_mapped is not None:  # an IPv4 client seen through a dual-stack socket
        return str(ip.ipv4_mapped)
    return str(ipaddress.ip_network(f"{ip}/64", strict=False))

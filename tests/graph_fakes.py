"""Fake Neo4j driver for query-shape tests: records every query, replays canned rows, can fail on demand."""

from __future__ import annotations


class FakeRecord:
    def __init__(self, data: dict) -> None:
        self._data = data

    def data(self) -> dict:
        return self._data


class FakeDriver:
    """``canned`` rows are returned one list per query, in order. ``fail_after=n`` makes the (n+1)th query raise
    (the link dropping mid-request); ``connectivity_error`` makes ``verify_connectivity`` raise."""

    def __init__(self, canned: list[list[dict]] | None = None, *, connectivity_error: Exception | None = None,
                 fail_after: int | None = None) -> None:
        self.log: list[tuple[str, dict]] = []
        self.canned = list(canned or [])
        self.closed = False
        self.connectivity_error = connectivity_error
        self.fail_after = fail_after

    def session(self, database: str | None = None) -> "FakeDriver":
        return self

    def __enter__(self) -> "FakeDriver":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def run(self, query: str, **params: object) -> list[FakeRecord]:
        if self.fail_after is not None and len(self.log) >= self.fail_after:
            raise ConnectionError("link dropped")
        self.log.append((query, params))
        return [FakeRecord(r) for r in (self.canned.pop(0) if self.canned else [])]

    def verify_connectivity(self) -> None:
        if self.connectivity_error is not None:
            raise self.connectivity_error

    def close(self) -> None:
        self.closed = True

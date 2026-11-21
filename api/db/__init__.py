"""Private Postgres access through the API's server credentials."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row

Connection = psycopg.Connection[dict[str, Any]]


@contextmanager
def transaction() -> Iterator[Connection]:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise HTTPException(503, "Account storage is unavailable.")
    try:
        # Avoid session-level prepared statements so this also works with a transaction pooler.
        with psycopg.connect(url, connect_timeout=4, row_factory=dict_row, prepare_threshold=None) as db:
            yield db
    except (psycopg.OperationalError, psycopg.errors.UndefinedTable, psycopg.errors.InvalidSchemaName):
        raise HTTPException(503, "Account storage is unavailable.") from None


def owned(row: dict | None) -> dict:
    if row is None:
        raise HTTPException(404, "Record not found.")
    return row


def expire_trips(db: Connection, user_id: str) -> None:
    db.execute("""DELETE FROM verity_private.trips
                  WHERE user_id = %s AND NOT saved AND created_at < now() - interval '90 days'""", (user_id,))

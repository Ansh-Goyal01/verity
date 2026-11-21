"""Remember each keyed account write in the private audit log alongside its original result."""
import json
from typing import Annotated
from uuid import UUID

from fastapi import Header, HTTPException
from . import Connection

WriteKey = Annotated[UUID | None, Header(alias="Idempotency-Key")]


def lookup(db: Connection, user: str, key: UUID | None, operation: list[str]) -> str | None:
    if key is None:
        return None
    row = db.execute("SELECT target FROM verity_private.audit_log WHERE user_id = %s AND action = %s",
                     (user, "write:" + str(key))).fetchone()
    if row is None:
        return None
    saved = json.loads(row["target"])
    if saved["operation"] != operation:
        raise HTTPException(409, "This request key already belongs to another operation.")
    return saved["result"]


def remember(db: Connection, user: str, key: UUID | None, operation: list[str], result: str) -> None:
    if key is not None:
        db.execute("INSERT INTO verity_private.audit_log (user_id, action, target) VALUES (%s, %s, %s)",
                   (user, "write:" + str(key), json.dumps({"operation": operation, "result": result})))

"""Share only the selected trip's decision, stops and Bingo Line."""

import hashlib
import secrets
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response

from .db import expire_trips, owned, transaction
from .routes_me import Account

router = APIRouter(prefix="/api")


@router.post("/trips/{trip_id}/share", status_code=201)
def create_share(trip_id: UUID, user: Account, response: Response) -> dict:
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode("ascii")).digest()
    with transaction() as db:
        expire_trips(db, user.id)
        row = db.execute("""
            INSERT INTO verity_private.share_links (trip_id, token_hash, expires_at)
            SELECT id, %s, now() + interval '48 hours' FROM verity_private.trips
            WHERE user_id = %s AND id = %s RETURNING expires_at
        """, (digest, user.id, trip_id)).fetchone()
    response.headers["Cache-Control"] = "no-store"
    return {"token": token, **owned(row)}


@router.get("/share/{token}")
def resolve_share(token: str, response: Response) -> dict:
    if len(token) != 43 or not token.isascii():
        raise HTTPException(404, "Record not found.")
    digest = hashlib.sha256(token.encode("ascii")).digest()
    with transaction() as db:
        row = db.execute("""
            SELECT t.decision, t.plan -> 'stops' AS stops, t.plan -> 'bingo_line' AS bingo_line
            FROM verity_private.share_links s JOIN verity_private.trips t ON s.trip_id = t.id
            WHERE s.token_hash = %s AND s.expires_at > now()
              AND (t.saved OR t.created_at >= now() - interval '90 days')
        """, (digest,)).fetchone()
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return owned(row)

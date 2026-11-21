"""Operator claims and graph views restricted to approved claims."""

from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Depends, Path, Query
from pydantic import Field

from .auth import User, require_role
from .db import owned, transaction
from .db.graph import graph_query
from .models import Req

router = APIRouter(prefix="/api")
Operator = Annotated[User, Depends(require_role("cpo"))]
Admin = Annotated[User, Depends(require_role("admin"))]
ChargerID = Annotated[str, Path(min_length=1, max_length=120)]


class ClaimInput(Req):
    operator_id: str = Field(min_length=1, max_length=120, pattern=r"^\S(?:.*\S)?$")


def approved_operators(user: User) -> list[str]:
    with transaction() as db:
        rows = db.execute("""
            SELECT operator_id FROM verity_private.cpo_claims
            WHERE user_id = %s AND approved_at IS NOT NULL ORDER BY operator_id
        """, (user.id,)).fetchall()
    return [row["operator_id"] for row in rows]


def operator_chargers(operator_id: str) -> list[dict]:
    return graph_query("cpo_chargers", operator_id)


@router.get("/cpo/directory")
def directory(user: Operator) -> dict:
    from .main import get_service

    service = get_service()
    return {"operators": sorted({c.operator for c in service.chargers if c.operator}),
            "corridors": ["noida-agra", *sorted(service.resolver.packaged)]}


@router.post("/cpo/claims", status_code=201)
def request_claim(req: ClaimInput, user: Operator) -> dict:
    with transaction() as db:
        return db.execute("""
            INSERT INTO verity_private.cpo_claims (user_id, operator_id) VALUES (%s, %s)
            ON CONFLICT (user_id, operator_id) DO UPDATE SET operator_id = EXCLUDED.operator_id
            RETURNING id, operator_id, approved_at
        """, (user.id, req.operator_id)).fetchone()


@router.get("/cpo/claims")
def list_claims(user: Operator) -> list[dict]:
    with transaction() as db:
        return db.execute("""
            SELECT id, operator_id, approved_at FROM verity_private.cpo_claims
            WHERE user_id = %s ORDER BY operator_id
        """, (user.id,)).fetchall()


@router.get("/admin/cpo-claims")
def pending_claims(user: Admin, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)) -> list[dict]:
    with transaction() as db:
        return db.execute("""
            SELECT id, operator_id, user_id FROM verity_private.cpo_claims
            WHERE approved_at IS NULL AND user_id <> %s ORDER BY operator_id, id LIMIT %s OFFSET %s
        """, (user.id, limit, offset)).fetchall()


@router.post("/admin/cpo-claims/{claim_id}/approve")
def approve_claim(claim_id: UUID, user: Admin) -> dict:
    with transaction() as db:
        row = db.execute("""
            UPDATE verity_private.cpo_claims SET approved_by = %s, approved_at = now()
            WHERE id = %s AND approved_at IS NULL AND user_id <> %s RETURNING id, operator_id, approved_at
        """, (user.id, claim_id, user.id)).fetchone()
        if row is None:
            return owned(db.execute("""
                SELECT id, operator_id, approved_at FROM verity_private.cpo_claims
                WHERE id = %s AND approved_at IS NOT NULL AND user_id <> %s
            """, (claim_id, user.id)).fetchone())
        db.execute("INSERT INTO verity_private.audit_log (user_id, action, target) VALUES (%s, %s, %s)",
                   (user.id, "approve_cpo_claim", str(claim_id)))
        return row


@router.get("/cpo/chargers")
def chargers(user: Operator) -> dict:
    output = {"chargers": [], "open_reports": []}
    for operator_id in approved_operators(user):
        rows = operator_chargers(operator_id)
        ids = {row["charger_id"] for row in rows}
        output["chargers"].extend(rows)
        output["open_reports"].extend(
            row for row in graph_query("cpo_open_reports", operator_id) if row.get("charger_id") in ids)
    return output


@router.get("/cpo/suspected-rings")
def rings(user: Operator) -> list[dict]:
    ids = sorted({row["charger_id"] for operator in approved_operators(user) for row in operator_chargers(operator)})
    return graph_query("suspected_rings", ids) if ids else []


@router.get("/cpo/critical-chargers")
def critical(user: Operator, corridor_id: str = Query(min_length=1, max_length=120),
             usable_range_km: float = Query(gt=0, le=2000)) -> list[dict]:
    ids = {row["charger_id"] for operator in approved_operators(user) for row in operator_chargers(operator)}
    return [row for row in graph_query("critical_chargers", corridor_id, usable_range_km)
            if row.get("charger_id") in ids] if ids else []


@router.get("/cpo/shared-grid-risk")
def grid_risk(user: Operator) -> list[dict]:
    return [row for operator in approved_operators(user) for row in graph_query("shared_grid_risk", operator)]


@router.post("/cpo/chargers/{charger_id}/fixed")
def mark_fixed(charger_id: ChargerID, user: Operator) -> dict:
    for operator in approved_operators(user):
        if any(row["charger_id"] == charger_id for row in operator_chargers(operator)):
            at = datetime.now(timezone.utc)
            graph_query("record_fix", charger_id, user.id, at)
            return {"charger_id": charger_id, "fixed_at": at}
    raise HTTPException(404, "Charger not found.")

"""Vehicles and booking acceptance for the authenticated fleet manager."""

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import Field

from .auth import User, require_role
from .db import owned, transaction
from .routes_me import CarInput
from .routes_trips import Journey, plan_vehicle
from .service import EngineService

Manager = Annotated[User, Depends(require_role("fleet_manager"))]


class FleetVehicleInput(CarInput):
    start_soc_pct: float = Field(ge=0, le=100)


def build_router(get_service: Callable[[], EngineService]) -> APIRouter:
    router = APIRouter(prefix="/api/fleet")

    @router.get("/vehicles")
    def list_vehicles(user: Manager, limit: int = Query(50, ge=1, le=100),
                      offset: int = Query(0, ge=0)) -> list[dict]:
        with transaction() as db:
            return db.execute("""
                SELECT id, model_id, nickname, battery_health_pct, start_soc_pct
                FROM verity_private.fleet_vehicles WHERE user_id = %s ORDER BY id LIMIT %s OFFSET %s
            """, (user.id, limit, offset)).fetchall()

    @router.post("/vehicles", status_code=201)
    def add_vehicle(vehicle: FleetVehicleInput, user: Manager) -> dict:
        with transaction() as db:
            return db.execute("""
                INSERT INTO verity_private.fleet_vehicles
                    (user_id, model_id, nickname, battery_health_pct, start_soc_pct)
                VALUES (%s, %s, %s, %s, %s) RETURNING id, model_id, nickname, battery_health_pct, start_soc_pct
            """, (user.id, vehicle.model_id, vehicle.nickname, vehicle.battery_health_pct,
                  vehicle.start_soc_pct)).fetchone()

    @router.put("/vehicles/{vehicle_id}")
    def update_vehicle(vehicle_id: UUID, vehicle: FleetVehicleInput, user: Manager) -> dict:
        with transaction() as db:
            return owned(db.execute("""
                UPDATE verity_private.fleet_vehicles
                SET model_id = %s, nickname = %s, battery_health_pct = %s, start_soc_pct = %s
                WHERE user_id = %s AND id = %s RETURNING id, model_id, nickname, battery_health_pct, start_soc_pct
            """, (vehicle.model_id, vehicle.nickname, vehicle.battery_health_pct, vehicle.start_soc_pct,
                  user.id, vehicle_id)).fetchone())

    @router.delete("/vehicles/{vehicle_id}", status_code=204)
    def delete_vehicle(vehicle_id: UUID, user: Manager) -> Response:
        with transaction() as db:
            owned(db.execute("""DELETE FROM verity_private.fleet_vehicles
                                WHERE user_id = %s AND id = %s RETURNING id""", (user.id, vehicle_id)).fetchone())
        return Response(status_code=204)

    @router.post("/booking/check")
    def check_booking(booking: Journey, user: Manager, svc: EngineService = Depends(get_service),
                      limit: int = Query(3, ge=1, le=5), offset: int = Query(0, ge=0)) -> list[dict]:
        decisions = []
        for vehicle in list_vehicles(user, limit, offset):
            plan = plan_vehicle(svc, booking, vehicle, vehicle["start_soc_pct"])
            decisions.append({"vehicle_id": vehicle["id"], "accepted": plan["decision"] == "GO",
                              "decision": plan["decision"], "reason": plan["reason"]})
        return decisions

    return router

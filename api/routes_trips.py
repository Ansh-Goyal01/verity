"""Authenticated trip planning, arrival records and quick charger reports."""

from collections.abc import Callable
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.types.json import Jsonb
from pydantic import Field, AwareDatetime

from verity.calibrate import calibrate
from verity.dispatch import VehicleContext
from verity.observations import screen_session
from verity.vehicles import get_prior

from .db import expire_trips, owned, transaction
from .db.graph import graph_query
from .db.receipts import WriteKey, lookup, remember
from .models import LatLon, Req
from .routes_me import Account, get_car, vehicle_catalog
from .service import EngineService, demo_dispatch_request


class Journey(Req):
    origin: LatLon
    destination: LatLon
    departure_time: AwareDatetime
    passengers: int = Field(ge=0, le=8)
    ac_on: bool
    target_confidence: float = Field(gt=0.5, le=0.99)


class TripInput(Journey):
    car_id: UUID
    start_soc_pct: float = Field(ge=0, le=100)


class Arrival(Req):
    arrival_pct: float = Field(ge=0, le=100)


class SocReading(Req):
    t_s: float = Field(ge=0, le=259_200)  # seconds since the trip started
    pct: float = Field(ge=0, le=100)


class MeasuredSegment(Req):
    """One measured stretch of the drive, in the units the energy model uses."""

    length_m: float = Field(gt=0, le=200_000)
    v_mps: float = Field(ge=0, le=60)
    accel_mps2: float = Field(ge=-10, le=10)
    grade_rad: float = Field(ge=-0.5, le=0.5)
    dt_s: float = Field(gt=0, le=7_200)
    temp_c: float = Field(ge=-40, le=60)
    surface: float = Field(default=1.0, ge=1, le=3)
    headwind_mps: float = Field(default=0.0, ge=-40, le=40)


class Observation(Req):
    soc: list[SocReading] = Field(min_length=2, max_length=500)
    segments: list[MeasuredSegment] = Field(min_length=1, max_length=5_000)
    charged_during_trip: bool


class QuickReport(Req):
    charger_id: str = Field(min_length=1, max_length=120)
    status: Literal["working", "not_working"]


def plan_vehicle(service: EngineService, journey: Journey, car: dict, start_soc_pct: float) -> dict:
    model = vehicle_catalog()[car["model_id"]]
    prior = get_prior(model["engine_prior_class"])
    nominal = float(model["battery_nominal_kwh"]) * car["battery_health_pct"] / 100
    context = VehicleContext(str(car["id"]), model["engine_prior_class"],
                             calibrate(prior.u_mean, prior.u_cov, [], prior.base, nominal),
                             journey.passengers, journey.ac_on, 0)
    request = journey.model_dump(mode="json", exclude={"car_id", "start_soc_pct"})
    return service.dispatch({**request, "vehicle_id": context.vehicle_id, "start_soc_pct": start_soc_pct},
                            private_vehicle=context)


def build_router(get_service: Callable[[], EngineService]) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/sample-trip")
    def sample_trip() -> dict:
        request = demo_dispatch_request("V12")
        del request["vehicle_id"]
        return {"model_id": "tata-nexon-ev-45", "request": request, "is_simulated": True}

    @router.post("/trips", status_code=201)
    def plan_trip(req: TripInput, user: Account, svc: EngineService = Depends(get_service)) -> dict:
        car = get_car(req.car_id, user)
        result = plan_vehicle(svc, req, car, req.start_soc_pct)
        with transaction() as db:
            # Recheck ownership at insertion in case the car was deleted during planning.
            return owned(db.execute("""
                INSERT INTO verity_private.trips (user_id, car_id, request, plan_id, decision, plan)
                SELECT user_id, id, %s, %s, %s, %s FROM verity_private.cars
                WHERE user_id = %s AND id = %s
                RETURNING id, car_id, plan_id, decision, plan, created_at, saved, arrival_pct
            """, (Jsonb(req.model_dump(mode="json")), result["plan_id"], result["decision"], Jsonb(result),
                  user.id, req.car_id)).fetchone())

    @router.get("/trips")
    def list_trips(user: Account, limit: int = Query(50, ge=1, le=100),
                   offset: int = Query(0, ge=0)) -> list[dict]:
        with transaction() as db:
            expire_trips(db, user.id)
            return db.execute("""
                SELECT id, car_id, plan_id, decision, created_at, saved, arrival_pct
                FROM verity_private.trips WHERE user_id = %s
                ORDER BY created_at DESC, id LIMIT %s OFFSET %s
            """, (user.id, limit, offset)).fetchall()

    @router.get("/trips/{trip_id}")
    def get_trip(trip_id: UUID, user: Account) -> dict:
        with transaction() as db:
            expire_trips(db, user.id)
            row = db.execute("""
                SELECT id, car_id, request, plan_id, decision, plan, created_at, saved, arrival_pct
                FROM verity_private.trips WHERE user_id = %s AND id = %s
            """, (user.id, trip_id)).fetchone()
        return owned(row)

    @router.post("/trips/{trip_id}/save")
    def save_trip(trip_id: UUID, user: Account) -> dict:
        with transaction() as db:
            expire_trips(db, user.id)
            row = db.execute("""UPDATE verity_private.trips SET saved = true
                                WHERE user_id = %s AND id = %s RETURNING id, saved""",
                             (user.id, trip_id)).fetchone()
        return owned(row)

    @router.post("/trips/{trip_id}/arrival")
    def arrival(trip_id: UUID, req: Arrival, user: Account) -> dict:
        with transaction() as db:
            expire_trips(db, user.id)
            row = db.execute("""UPDATE verity_private.trips SET arrival_pct = %s
                                WHERE user_id = %s AND id = %s RETURNING id, arrival_pct""",
                             (req.arrival_pct, user.id, trip_id)).fetchone()
        return {**owned(row), "calibration_applied": False}

    @router.post("/trips/{trip_id}/observation", status_code=201)
    def observation(trip_id: UUID, req: Observation, user: Account) -> dict:
        """Record measured segments and the SoC window; a session with intermediate charging is kept but ineligible.

        Nothing here changes an estimate: learned battery estimates stay off. The first record for a trip wins,
        so a retry after an uncertain write returns the stored result and cannot add a second observation.
        """
        screening = screen_session([(r.t_s, r.pct) for r in req.soc], req.charged_during_trip)
        with transaction() as db:
            expire_trips(db, user.id)
            db.execute("""
                INSERT INTO verity_private.trip_observations
                    (trip_id, user_id, soc, segments, declared_charging, intermediate_charging, eligible, reason,
                     delta_soc_pct)
                SELECT id, user_id, %s, %s, %s, %s, %s, %s, %s FROM verity_private.trips
                WHERE user_id = %s AND id = %s
                ON CONFLICT (trip_id) DO NOTHING
            """, (Jsonb([r.model_dump() for r in req.soc]), Jsonb([s.model_dump() for s in req.segments]),
                  req.charged_during_trip, screening.intermediate_charging, screening.eligible, screening.reason,
                  screening.delta_soc_pct, user.id, trip_id))
            row = owned(db.execute("""
                SELECT trip_id, eligible, intermediate_charging, reason, delta_soc_pct, created_at
                FROM verity_private.trip_observations WHERE user_id = %s AND trip_id = %s
            """, (user.id, trip_id)).fetchone())
        eligible = row.pop("eligible")
        return {**row, "eligible_for_calibration": eligible, "calibration_applied": False}

    @router.get("/reports/{report_id}")
    def get_report(report_id: UUID, user: Account) -> dict:
        with transaction() as db:
            return owned(db.execute("""
                SELECT id, charger_id, status, created_at, synced_at
                FROM verity_private.reports WHERE user_id = %s AND id = %s
            """, (user.id, report_id)).fetchone())

    @router.post("/reports/quick", status_code=201)
    def quick_report(req: QuickReport, user: Account, svc: EngineService = Depends(get_service), key: WriteKey = None) -> dict:
        if not any(charger.id == req.charger_id for charger in svc.chargers):
            raise HTTPException(404, "Charger not found.")
        with transaction() as db:
            db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (user.id,))
            operation = ["quick-report", req.charger_id, req.status]
            report_id = lookup(db, user.id, key, operation)
            if report_id is None:
                row = db.execute("""
                    SELECT id, status, created_at FROM verity_private.reports
                    WHERE user_id = %s AND charger_id = %s
                      AND (synced_at IS NULL OR created_at > now() - interval '10 minutes')
                    ORDER BY created_at DESC LIMIT 1
                """, (user.id, req.charger_id)).fetchone()
                if row is not None and row["status"] != req.status:
                    raise HTTPException(429, "Try again after the charger report cooldown.")
                if row is None:
                    row = db.execute("""
                        INSERT INTO verity_private.reports (user_id, charger_id, status) VALUES (%s, %s, %s)
                        RETURNING id, status, created_at
                    """, (user.id, req.charger_id, req.status)).fetchone()
                report_id = row["id"]
                remember(db, user.id, key, operation, str(report_id))
        with transaction() as db:
            report = owned(db.execute("""
                SELECT id, charger_id, status, created_at, synced_at
                FROM verity_private.reports WHERE user_id = %s AND id = %s FOR UPDATE
            """, (user.id, report_id)).fetchone())
            if report["synced_at"] is None:
                graph_query("record_user_report", user.id, req.charger_id, req.status, report["created_at"])
                report["synced_at"] = db.execute("""
                    UPDATE verity_private.reports SET synced_at = now()
                    WHERE user_id = %s AND id = %s RETURNING synced_at
                """, (user.id, row["id"])).fetchone()["synced_at"]
        return report

    return router

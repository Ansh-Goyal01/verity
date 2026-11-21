"""Profile and garage endpoints."""

import csv
import os
from functools import lru_cache
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

import jwt
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from verity.paths import REPO_ROOT

from .auth import User, current_user
from .db import owned, transaction

router = APIRouter(prefix="/api")
Account = Annotated[User, Depends(current_user)]


@router.get("/auth/config")
def browser_auth_config() -> dict:
    """Publish only the browser-safe Supabase settings used for password sign-in."""
    project = os.environ.get("SUPABASE_URL", "").rstrip("/")
    url = urlsplit(project)
    key = os.environ.get("SUPABASE_PUBLISHABLE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
    public = key.startswith("sb_publishable_")
    if key and not public:
        try:
            # This checks the key type for publication. It never authenticates a user.
            public = jwt.decode(key, options={"verify_signature": False}).get("role") == "anon"
        except jwt.PyJWTError:
            public = False
    if not (public and url.scheme == "https" and url.hostname and
            not (url.username or url.password or url.path or url.query or url.fragment)):
        return {"available": False}
    return {"available": True, "url": project, "publishable_key": key}


@lru_cache(maxsize=1)
def vehicle_catalog() -> dict[str, dict[str, str]]:
    with (REPO_ROOT / "data" / "corpus" / "vehicles_india.csv").open(encoding="utf-8", newline="") as catalog:
        return {row["vehicle_id"]: row for row in csv.DictReader(catalog)}


class CarInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    model_id: str = Field(max_length=80)
    nickname: str = Field(min_length=1, max_length=80)
    battery_health_pct: float = Field(gt=0, le=100)

    @field_validator("model_id")
    @classmethod
    def known_model(cls, value: str) -> str:
        if value not in vehicle_catalog():
            raise ValueError("Select a vehicle model from the catalog.")
        return value


@router.get("/vehicle-models")
def vehicle_models() -> list[dict]:
    return [{k: row[k] for k in ("vehicle_id", "make", "model", "variant", "battery_nominal_kwh")}
            for row in vehicle_catalog().values()]


@router.get("/me")
def me(user: Account) -> dict:
    with transaction() as db:
        profile = db.execute("""
            INSERT INTO verity_private.profiles (id, role) VALUES (%s, %s)
            ON CONFLICT (id) DO UPDATE SET role = EXCLUDED.role
            RETURNING id, display_name, role
        """, (user.id, user.role)).fetchone()
        cars = db.execute("""SELECT id, model_id, nickname, battery_health_pct
                             FROM verity_private.cars WHERE user_id = %s ORDER BY id""", (user.id,)).fetchall()
    return {"profile": profile, "cars": cars}


@router.post("/me/cars", status_code=201)
def add_car(car: CarInput, user: Account) -> dict:
    with transaction() as db:
        return db.execute("""
            INSERT INTO verity_private.cars (user_id, model_id, nickname, battery_health_pct)
            VALUES (%s, %s, %s, %s) RETURNING id, model_id, nickname, battery_health_pct
        """, (user.id, car.model_id, car.nickname, car.battery_health_pct)).fetchone()


@router.get("/me/cars/{car_id}")
def get_car(car_id: UUID, user: Account) -> dict:
    with transaction() as db:
        return owned(db.execute("""
            SELECT id, model_id, nickname, battery_health_pct
            FROM verity_private.cars WHERE user_id = %s AND id = %s
        """, (user.id, car_id)).fetchone())


@router.put("/me/cars/{car_id}")
def edit_car(car_id: UUID, car: CarInput, user: Account) -> dict:
    with transaction() as db:
        return owned(db.execute("""
            UPDATE verity_private.cars SET model_id = %s, nickname = %s, battery_health_pct = %s
            WHERE user_id = %s AND id = %s RETURNING id, model_id, nickname, battery_health_pct
        """, (car.model_id, car.nickname, car.battery_health_pct, user.id, car_id)).fetchone())


@router.delete("/me/cars/{car_id}", status_code=204)
def delete_car(car_id: UUID, user: Account) -> Response:
    with transaction() as db:
        if not db.execute("DELETE FROM verity_private.cars WHERE user_id = %s AND id = %s", (user.id, car_id)).rowcount:
            raise HTTPException(404, "Record not found.")
    return Response(status_code=204)

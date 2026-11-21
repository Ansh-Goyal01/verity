"""Bridge to the finale graph query interface supplied by the engine."""

from importlib import import_module
from typing import Any

from fastapi import HTTPException
from neo4j.exceptions import DriverError, Neo4jError


def graph_query(name: str, *args: Any) -> list[dict]:
    try:
        module = import_module("verity.trust.queries")
    except ModuleNotFoundError as exc:
        if exc.name != "verity.trust.queries":
            raise
        raise HTTPException(503, "Charger graph is unavailable.") from None
    try:
        query = getattr(module, name, None)
        if query is None:
            raise NotImplementedError
        return query(*args)["rows"]
    except (DriverError, Neo4jError, NotImplementedError):
        raise HTTPException(503, "Charger graph is unavailable.") from None

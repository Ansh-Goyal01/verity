"""Account route dispatch to the application's charger graph."""

from typing import Any

from fastapi import HTTPException
from neo4j.exceptions import DriverError, Neo4jError


def graph_query(name: str, *args: Any) -> list[dict]:
    # main also imports the account routers. Resolve the service after app initialization.
    from api.main import get_service
    from api.operator_graph import OperatorGraph

    graph = OperatorGraph(get_service())
    try:
        return getattr(graph, name)(*args) or []
    except (DriverError, Neo4jError, OSError):
        raise HTTPException(503, "Charger graph is unavailable.") from None

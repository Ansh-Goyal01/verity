"""Test defaults: the suite never touches a real Neo4j unless a test opts in explicitly.

A developer machine has NEO4J_* credentials in .env, and the API service would otherwise connect to AuraDB
(slow, networked, and /api/health would say "connected"). Live tests build their own service with
``graph_backend="neo4j"`` and skip when the database is unreachable.
"""

import os

os.environ.setdefault("VERITY_GRAPH", "memory")

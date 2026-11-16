"""Test defaults: the suite never touches a real Neo4j unless a test opts in explicitly.

A developer machine has NEO4J_* credentials in .env, and the API service would otherwise connect to AuraDB
(slow, networked, and /api/health would say "connected"). Live tests build their own service with
``graph_backend="neo4j"`` and skip when the database is unreachable.
"""

import os

os.environ.setdefault("VERITY_GRAPH", "memory")
# The voice rate limit (W0.7) counts calls in a 60 s wall-clock window. Give the whole test process a budget no test
# can exhaust, so no outcome depends on how fast the suite runs; tests of the limiter itself build their own limiter.
os.environ.setdefault("VERITY_VOICE_RATE_PER_MIN", "1000")

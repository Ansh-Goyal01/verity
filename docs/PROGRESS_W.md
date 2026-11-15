# Finale build W — progress

Authority: `docs/TASKS_W.md` (sessions) and the Opus 5.5 winning-build guide in `docs/`.
Start every session with `/clear`, then `continue docs/TASKS_W.md`. One session per context.

## Sessions

- [ ] S1 Bingo Line agrees with the plan (W0.1) + deadline line (W0.0)
- [ ] S2 No certainty on screen + reason sentence (W0.2, W0.6)
- [ ] S3 Green on every machine + hardening (W0.4, W0.7)
- [ ] S4 One source of truth + offline (W0.3, W0.5)
- [ ] S5 Sarvam client + spoken ride-check (W3.1, W3.2)
- [ ] S6 Graph structure + names + graph endpoints (W2.1, W2.5, W2.6)
- [ ] S7 Spoken report into the graph + OCM check-ins (W3.3, W1.2)
- [ ] S8 Graph independence + Cypher chains + E6 (W2.2, W2.3)
- [ ] S9 Rings as graph analytics + E5b (W2.4)
- [ ] S10 Real data in, ledger out (W1.3, W1.4, W1.5, W3.6) — after corpus freeze
- [ ] S11 README, disclosure, deploy + Done-means audit (W5.1–W5.3)
- [ ] S12 Venue voice kit + extra languages (W3.5, W3.4)

## Baseline (before S1)

- HEAD `9302586` (F12). Guide reports 362 passed / 1 failed (CRLF, Linux only) / 1 skipped on Linux.
- Stray nested clone `verity/` in the repo root (older R-series history of the GitHub remote). Untracked;
  never stage it. Ansh can delete it or move it out of the repo.
- 2026-11-16: new Aura instance live (username is `neo4j`, not the instance id). Smoke test OK.
  `tests/test_graph_parity.py`: 9 passed, 1 failed -
  `test_simulated_reports_and_sources_are_tagged_and_syncing_again_changes_nothing`: re-sync with only
  `demo_reports` drops REPORTED 28 -> 14 and Source 24 -> 10 (likely the service's own reports, e.g. the
  driver-demo scenario, being removed by the replace-style sync). Probably never run live before (skips when
  Neo4j is unreachable). **S6: diagnose first, decide sync-vs-test, log in DECISIONS.md.** Until fixed, the
  full `uv run pytest` is red whenever Neo4j is reachable. `VERITY_GRAPH=memory` does NOT skip these tests (checked). Sessions S1-S5
  run the suite with an empty URI, which skips the 10 parity tests (checked): bash `NEO4J_URI= uv run pytest`,
  PowerShell `$env:NEO4J_URI=''; uv run pytest`. Report that the parity tests were skipped this way.

## Handoff notes (newest last, at most 15 lines per session)

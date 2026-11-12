# Finale build progress (F0–F12)

Authority: docs/TASKS_FINALE.md. Commit directly to main, local only; one commit per milestone ("F<n>: ...").
Scope of this session (user instruction 2026-11-12): F0, F1 and F2 only, then stop. F3+ not started.

## Checklist

- [x] F0 Pre-flight
- [ ] F1 Collection kit for the teammate
- [ ] F2 Vehicle specs
- [ ] F3 Enrichment
- [ ] F4 E1-India (real, public, self-reported)
- [ ] F5 Charger evidence
- [ ] F6 Voice of customer from app reviews
- [ ] F7 Sarvam voice loop
- [ ] F8 Engine upgrades
- [ ] F9 Range Passport
- [ ] F10 Driver demo scenario
- [ ] F11 One command and documents
- [ ] F12 Wrap-up

## F0 Pre-flight (2026-11-12)

- `git pull --no-edit origin main`: already up to date at 68fb724.
- `git branch` listed main plus two local branches, `feat/neo4j-load-bearing` and
  `fix/demo-scenario-pinned-chargers`. `git branch --merged main` confirmed both were fully merged, so they
  were removed with the safe `git branch -d` (which refuses unmerged branches). `git branch` now lists only
  main. The remote-tracking ref `origin/claude/mission-control-frontend` is not a local branch and was left alone.
- Baseline `uv run pytest`: **244 passed, 10 skipped, 1 warning** (154 s). All 10 skips are the live Neo4j
  parity tests in tests/test_graph_parity.py: "Neo4j unavailable (ServiceUnavailable)". Credentials are
  present in .env, but the Aura instance did not answer (an AuraDB Free instance pauses when idle).
  Manual step: resume the instance in the Aura console, then re-run `uv run python scripts/neo4j_smoke_test.py`.
- Untracked folder `verity/` at the repo root is an older standalone clone of this repository (its own .git,
  newest commit 7a4b995, which is already in main's history). It was not modified, staged or deleted;
  every commit in this track stages explicit paths only. The owner may delete it or keep it.

### Why R4b (BMW i3) and R5 (Sarvam) were blocked, and what unblocks them

| Item | Why it was blocked (docs/PROGRESS_FINISH.md) | What unblocks it | State on this machine today |
|---|---|---|---|
| R4b BMW i3 evaluation | `uv run python -m verity.datasets.bmw_i3` reported the raw folder data/raw/bmw_i3/ missing or empty; the dataset is downloaded by hand from IEEE DataPort (DOI 10.21227/6jr9-5235) after sign-in. | Put the 70 TripXnn.csv files and Overview.xlsx directly in data/raw/bmw_i3/; the loader check must say `loader_ready`, `n_trips=70`. Then freeze training-only exclusions and run the held-out evaluation exactly once, keeping the fixed split. | **Raw data now present**: loader check returns `loader_ready`, `n_trips=70` (72 files in the folder). The held-out evaluation itself is still NOT DONE; it belongs to F9 in this track. |
| R5 Sarvam verification | `SARVAM_API_KEY` absent from .env, so no endpoint, auth header, field or model-id verification and no TTS/STT call was made. | Add the key to .env locally; verify the current official endpoints, header, request fields and model ids from Sarvam's docs; make at most one TTS and one STT call; stop on authentication failure; never log the key. | **Key now present** in .env (presence checked only; value never printed). No call made yet; verification belongs to F7. |

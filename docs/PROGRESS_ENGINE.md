# Engine track progress (Ansh) — overnight build

Started 2026-10-27. Source of truth for the milestones is `docs/TASKS_ANSH.md`.
Commit message per milestone: `A<n>: <summary>`.

## Environment (recorded in A0)

| Tool | Version |
|---|---|
| Python | 3.12.10 |
| uv | 0.11.23 |
| node | v24.16.0 (not used by the engine) |
| git | 2.54.0.windows.1 |
| OS | Windows 11 (Git Bash + PowerShell) |

## Milestones

- [x] A0 Pre-flight
- [x] A1 Scaffold
- [ ] A2 Physics and vehicle priors
- [ ] A3 Truth simulator
- [ ] A4 Calibration and E1
- [ ] A5 Forecast and E2
- [ ] A6 Trust engine and E5
- [ ] A7 Routing and chargers
- [ ] A8 Dispatch, E3 and E4
- [ ] A9 API per docs/API_CONTRACT.md
- [ ] A10 Quality pass
- [ ] A11 Morning report

## Notes per milestone

### A0 Pre-flight (done)
- Versions recorded above. No `.env` exists yet (so Neo4j and Sarvam are unconfigured tonight).
- Layout fixed: root had `PROMPTS.md`, `TASKS_ANSH.md` and the plan PDF; `verity-starter/` and
  `verity-engine/` held nested copies. Everything unique is now at the CLAUDE.md locations
  (`docs/PROMPTS.md`, `docs/PLAN.md` + `docs/PLAN.pdf`, `docs/API_CONTRACT.md`, `.env.example`,
  `.gitignore`); the three duplicates were byte-identical (checked with `diff -q`); both shell
  folders are gone.
- `git init -b main` done (identity already configured). Nothing is ever pushed.
- CLAUDE.md updated for the engine/frontend split (see DECISIONS.md).
- Plan content: `docs/PLAN.md` is a placeholder, so the PDF was text-extracted to read it.

### A1 Scaffold (done)
- `pyproject.toml` (uv, hatchling, Python >= 3.11, only the CLAUDE.md dependency list; pytest in the
  dev group). `engine/verity` and `api` install editable, so `import verity` / `import api` work
  from anywhere.
- Docstring stubs for every module in the CLAUDE.md layout (`physics`, `vehicles`, `routing`,
  `calibrate`, `forecast`, `dispatch`, `trust/*`, `api/main.py`); they are filled in A2-A9.
- `engine/verity/schemas.py`: pydantic v2 models for `verity.trip.v1`, charger record and report.
  Timestamps must be timezone-aware, a trip needs >= 2 GPS points and >= 2 SoC readings, and
  `is_simulated` is mandatory so nothing can be forgotten to be labelled.
- `tests/test_schemas.py`: 18 tests (valid sample, alias round-trip, 13 malformed variants, charger,
  report). `uv run pytest` -> 18 passed.
- `README.md`, `docs/AI_USAGE.md`, `docs/DECISIONS.md`, `data/trips/README.md` created.
- NOT created, by instruction: `logger/`, `web/`, `design/`.

## Blocked items

(none yet)

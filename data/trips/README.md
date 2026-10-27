# Real trip logs

Put real trips exported by the logger here, one `verity.trip.v1` JSON file per trip
(see `CLAUDE.md` for the schema; `"is_simulated"` must be `false`).

- Keep `driver_id` anonymous (`anon-01`, `anon-02`, ...). GPS traces reveal where people live, so
  think before committing them to a public repo.
- Nothing in this folder is ever used for tuning once it has been assigned to the held-out split
  in `data/splits/`.
- Simulated trips never go here; they live in `data/sim/`.

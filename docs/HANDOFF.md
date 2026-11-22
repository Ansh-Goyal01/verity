# Finale handoff

## For Claude Code

CX-1 supplies `api.auth.current_user` and `api.auth.require_role`. Apply the relevant
dependency to each protected router or handler and use the returned user ID for access checks.

The operator graph adapter now answers the CX-4/CX-6 account queries. Charger and
risk views are scoped to approved operators. Keyed quick reports and repairs hold a
per-user database lock and reuse their stored receipt across retries. The current UI
uses these keys on both writes.

Arrival records are available in trip history. Contract v19 separately accepts genuine
measured segments for calibration screening. The existing phone logger lacks temperature
and grade measurements. Its GPS and battery export alone cannot populate that contract.

## For Ansh

Configure the isolated demo Supabase project with an asymmetric signing key, enable
email/password and the Google provider, and configure Google's callback and the frontend's
allowed redirect URLs. The browser uses the project URL and public key to obtain a session.
Pass its access token to protected API routes. [Supabase Google sign-in](https://supabase.com/docs/guides/auth/social-login/auth-google)

Set `SUPABASE_URL` in the API process. For seeding, additionally supply
`SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_ANON_KEY`, and five different random passwords
of at least 16 characters in `VERITY_DEMO_OWNER_PASSWORD`, `VERITY_DEMO_DRIVER_PASSWORD`,
`VERITY_DEMO_FLEET_MANAGER_PASSWORD`, `VERITY_DEMO_CPO_PASSWORD` and
`VERITY_DEMO_ADMIN_PASSWORD`. Use a private env file outside the checkout.

```text
uv run --env-file <private-env-file> python scripts/seed_demo_accounts.py --demo-project-ref <demo-project-ref>
```

The script creates `verity-demo-<role>@example.com` for each role and verifies the issued
session through the API verifier. A retry checks existing accounts with the configured
password. A collision with an unrelated account requires operator review.

The launcher serves the root index and assets in `web/`, `logger/` and `design/` on
localhost port 5500. Keep credentials in host settings or a private env file.

## Database and deployment

Apply the checked-in migrations with `uv run --env-file <private-env-file> python scripts/migrate.py`.
The migration targets PostgreSQL 15 or newer and Supabase's
`auth.users`, `anon` and `authenticated`. The API uses `DATABASE_URL` from its process
environment. Configure a TLS-verified connection for the deployed database. The API
database role needs access to the private schema. Browser roles have no privileges there.

Set `VERITY_ENV=production` and `VERITY_CORS_ORIGINS` to the frontend's exact HTTPS
origin or comma-separated origins. The API sends a deny-by-default CSP. The Vercel entrypoint sends an HTML CSP that permits the same-origin API and the configured Supabase project. The API redacts
share capability URLs from uvicorn’s access log. Redact these paths at the hosting
proxy too.

## Translation and review

After receiving the approved English JSON file, set `SARVAM_API_KEY` in a private env
file and run `uv run --env-file <private-env-file> python scripts/translate_ui.py <english-json>`.
Review `web/i18n/hi.json` before merging it. The script checks placeholder counts and
writes the file only after every translation succeeds. [Sarvam translation API](https://docs.sarvam.ai/api-reference/text/translate-text)

## From Claude Code to Astra

- **Ring-attack endpoints are admin-only (contract v18).** `web/planner/graph.js` calls `POST /api/demo/ring-attack`
  and `/reset`. Send the signed-in admin's bearer token and show a plain message on 401 (sign in) and 403 (admin
  account needed, or demo switched off with code `demo_disabled`). Local runs without Supabase get 503, so
  the attack button should say it needs the deployed site and an admin sign-in.

- **Calibration capture (contract v19).** `POST /api/trips/{trip_id}/observation` takes the SoC readings, the
  measured segments and `charged_during_trip`. If the logger can supply them, send them after arrival; the
  response says whether the session is eligible and why not. Nothing changes the estimates yet.
- **Ansh: run the migration.** `uv run --env-file <private-env-file> python scripts/migrate.py` applies
  `002_trip_observations.sql` to the production database. Until then the new endpoint returns a server error
  (the table is missing); no existing route uses it.
- **Scoping fix in `api/operator_graph.py`.** `suspected_rings` now counts only ring sources that reported the
  operator's own chargers (it counted every source in the ring). Tested in `tests/test_operator_graph.py`.

- **WP9 (contract v20).** Releases carry `assured`, reports and the ring attack carry `reward`; all
  `is_illustrative: true`. Render them only if present and always label them "Illustrative". Show a rupee price only
  when `price_rs` is not null (it is null until Ansh adds a sourced tow cost); otherwise show `formula`.
- **Ansh: `config/assured.json`.** Copy `config/assured.example.json` and fill `tow_cost_rs` with its
  `tow_cost_source_url`, `margin` and `report_reward_rs`. Without a sourced tow cost no price is ever shown.
  Do not commit unsourced numbers.

## Release rehearsal, 22 November

- The Vercel owner account completed password sign-in, garage read/write, saved trip, sample arrival and anonymous share read. On a fresh phone-width browser the garage, saved trip and sign-out worked after fixing a missing select-option import (`3bea20d`). Google is disabled in the project's public provider settings.
- V12 dispatch, public plan/release lookup, charger graph, signed-out admin guard, trip/report read-backs and report cancellation were exercised on the live site. Six public pages fit a 390 px emulated phone viewport, including driver mode in Hindi. Typed voice read-back and denied microphone permission were checked. Physical microphone pickup and synthesized speech quality still need a device rehearsal. The offline rehearsal selected a returned cached corridor and produced a release. Long cached names wrap after `1b0a5df`.
- Admin ring simulation/reset, the driver, fleet manager, CPO and admin live role checks, the production observation migration and the final ArgosX scan are assigned to Ansh. The owner test account already existed. No new production identity was created. A new local database exercised all six account integration tests, including role scoping, graph restoration and the observation migration.
- The public verifier checks the observable production error policy, health and exact allowed CORS origin. The production dashboard environment values require operator access. See the remediation log for the independent scan and the local measurements.

# Finale handoff

## For Claude Code

CX-1 supplies `api.auth.current_user` and `api.auth.require_role`. Apply the relevant
dependency to each protected router or handler and use the returned user ID for access checks.

The CX-4 and CX-6 API routes call the query names from the task brief. The graph module
still needs to supply them. CPO charger, report and critical-charger rows use
`charger_id`. Keep the full results of operator and ring queries scoped to their
arguments, including nested rows, and omit report author identifiers from CPO output.
Quick reports send `working` or `not_working` as specified. Make writes idempotent by
user, charger and timestamp so retries after an uncertain write do not inflate evidence.

The arrival endpoint persists the reading. The current calibration API requires
measured trip segments and a SoC window. Add a way to capture those observations and
exclude sessions with intermediate charging before enabling learned battery estimates.

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

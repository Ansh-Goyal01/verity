# Finale handoff

## For Claude Code

CX-1 supplies `api.auth.current_user` and `api.auth.require_role`. Apply the relevant
dependency to each protected router or handler and use the returned user ID for access checks.

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

The local launcher in `start.py` serves the entire checkout on port 5500, including any
root `.env`. Restrict its static file serving to public assets before storing credentials
in the checkout or exposing the launcher through a tunnel. Use host settings for deployment.

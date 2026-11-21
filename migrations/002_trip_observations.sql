BEGIN;

-- Measured trip observations for battery calibration: one per trip, owned by the trip's user.
-- Recorded always; `eligible` says whether physics calibration may use it (no intermediate charging).
CREATE TABLE verity_private.trip_observations (
    trip_id uuid PRIMARY KEY REFERENCES verity_private.trips(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    soc jsonb NOT NULL,
    segments jsonb NOT NULL,
    declared_charging boolean NOT NULL,
    intermediate_charging boolean NOT NULL,
    eligible boolean NOT NULL,
    reason text NOT NULL,
    delta_soc_pct double precision NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX trip_observations_user ON verity_private.trip_observations (user_id, created_at DESC);

ALTER TABLE verity_private.trip_observations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON ALL TABLES IN SCHEMA verity_private FROM PUBLIC, anon, authenticated;

COMMIT;

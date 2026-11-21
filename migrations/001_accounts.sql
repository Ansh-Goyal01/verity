BEGIN;

CREATE SCHEMA verity_private;
REVOKE ALL ON SCHEMA verity_private FROM PUBLIC, anon, authenticated;

CREATE TABLE verity_private.profiles (
    id uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    display_name text NOT NULL DEFAULT '',
    role text NOT NULL CHECK (role IN ('owner', 'driver', 'fleet_manager', 'cpo', 'admin'))
);

CREATE TABLE verity_private.cars (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    model_id text NOT NULL,
    nickname text NOT NULL CHECK (char_length(nickname) BETWEEN 1 AND 80),
    battery_health_pct double precision NOT NULL CHECK (battery_health_pct > 0 AND battery_health_pct <= 100),
    UNIQUE (user_id, id)
);

CREATE TABLE verity_private.trips (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    car_id uuid,
    request jsonb NOT NULL,
    plan_id text NOT NULL,
    decision text NOT NULL CHECK (decision IN ('GO', 'NO_GO', 'INSUFFICIENT_EVIDENCE')),
    plan jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    saved boolean NOT NULL DEFAULT false,
    arrival_pct double precision CHECK (arrival_pct BETWEEN 0 AND 100),
    FOREIGN KEY (user_id, car_id) REFERENCES verity_private.cars(user_id, id) ON DELETE SET NULL (car_id)
);
CREATE INDEX trips_user_created ON verity_private.trips (user_id, created_at DESC, id);

CREATE TABLE verity_private.reports (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    charger_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('working', 'not_working')),
    created_at timestamptz NOT NULL DEFAULT now(),
    synced_at timestamptz
);
CREATE INDEX reports_user_charger ON verity_private.reports (user_id, charger_id, created_at DESC);

CREATE TABLE verity_private.fleet_vehicles (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    model_id text NOT NULL,
    nickname text NOT NULL CHECK (char_length(nickname) BETWEEN 1 AND 80),
    battery_health_pct double precision NOT NULL CHECK (battery_health_pct > 0 AND battery_health_pct <= 100),
    start_soc_pct double precision NOT NULL CHECK (start_soc_pct BETWEEN 0 AND 100),
    UNIQUE (user_id, id)
);

CREATE TABLE verity_private.cpo_claims (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    operator_id text NOT NULL,
    approved_by uuid REFERENCES auth.users(id) ON DELETE CASCADE,
    approved_at timestamptz,
    CHECK ((approved_by IS NULL) = (approved_at IS NULL)),
    UNIQUE (user_id, operator_id)
);

CREATE TABLE verity_private.share_links (
    token_hash bytea PRIMARY KEY CHECK (octet_length(token_hash) = 32),
    trip_id uuid NOT NULL REFERENCES verity_private.trips(id) ON DELETE CASCADE,
    expires_at timestamptz NOT NULL
);
CREATE INDEX share_links_trip ON verity_private.share_links (trip_id);

CREATE TABLE verity_private.audit_log (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id uuid REFERENCES auth.users(id) ON DELETE SET NULL,
    action text NOT NULL,
    target text NOT NULL,
    at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE verity_private.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE verity_private.cars ENABLE ROW LEVEL SECURITY;
ALTER TABLE verity_private.trips ENABLE ROW LEVEL SECURITY;
ALTER TABLE verity_private.reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE verity_private.fleet_vehicles ENABLE ROW LEVEL SECURITY;
ALTER TABLE verity_private.cpo_claims ENABLE ROW LEVEL SECURITY;
ALTER TABLE verity_private.share_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE verity_private.audit_log ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON ALL TABLES IN SCHEMA verity_private FROM PUBLIC, anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA verity_private FROM PUBLIC, anon, authenticated;

COMMIT;

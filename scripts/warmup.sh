#!/bin/sh
# Wake the hosted API and compute one Noida–Agra plan before the pitch.
set -eu

api=${1:-${VERITY_API_URL:-}}
api=${api%/}
case "$api" in
  https://*|http://127.0.0.1:*|http://localhost:*) ;;
  *) printf '%s\n' 'Usage: sh scripts/warmup.sh https://your-api.example' >&2; exit 2 ;;
esac

curl --proto '=https,http' --fail --silent --show-error --retry 6 --retry-max-time 100 --max-time 30 \
  -- "$api/api/health"
printf '\n'
curl --proto '=https,http' --fail --silent --show-error --max-time 90 --output /dev/null \
  --header 'Content-Type: application/json' \
  --data '{"vehicle_id":"V12","origin":{"lat":28.5355,"lon":77.3910},"destination":{"lat":27.1767,"lon":78.0081},"departure_time":"2026-10-28T14:00:00+05:30","start_soc_pct":62,"passengers":4,"ac_on":true,"target_confidence":0.9}' \
  -- "$api/api/dispatch"
printf '%s\n' 'Cached Noida–Agra plan warmed.'

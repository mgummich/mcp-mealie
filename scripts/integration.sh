#!/usr/bin/env bash
# Spin up a throwaway Mealie in Docker, run the integration tests against it,
# tear it down. Requires docker compose and uv.
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE="docker compose -f tests/integration/compose.yml"
URL="http://localhost:9925"

$COMPOSE up -d
trap '$COMPOSE down -v' EXIT

echo "waiting for Mealie at $URL ..."
for _ in $(seq 90); do
    curl -fs "$URL/api/app/about" >/dev/null && break
    sleep 2
done
curl -fs "$URL/api/app/about" >/dev/null || { echo "Mealie never came up"; exit 1; }

# Fresh containers ship a default admin; its JWT works as a bearer token.
TOKEN=$(curl -fs -X POST "$URL/api/auth/token" \
    -d 'username=changeme@example.com&password=MyPassword' |
    python3 -c 'import sys, json; print(json.load(sys.stdin)["access_token"])')

MEALIE_INTEGRATION=1 MEALIE_URL="$URL" MEALIE_API_TOKEN="$TOKEN" \
    uv run --extra dev pytest tests/integration -v

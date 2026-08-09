#!/usr/bin/env bash
# Spin up a throwaway Mealie in Docker, run the integration tests against it,
# tear it down. Requires docker compose and uv.
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE="docker compose -f tests/integration/compose.yml"
URL="http://localhost:19925"

$COMPOSE up -d
trap '$COMPOSE down -v' EXIT

echo "waiting for Mealie at $URL ..."
for _ in $(seq 90); do
    curl -fs "$URL/api/app/about" >/dev/null && break
    sleep 2
done
curl -fs "$URL/api/app/about" >/dev/null || { echo "Mealie never came up"; exit 1; }

# Fresh containers ship a default admin; its JWT works as a bearer token.
AUTH_RESPONSE=$(curl -s -X POST "$URL/api/auth/token" \
    -d 'username=changeme@example.com&password=MyPassword')
TOKEN=$(printf '%s' "$AUTH_RESPONSE" |
    python3 -c 'import sys, json; print(json.load(sys.stdin)["access_token"])') || {
    echo "failed to mint token; auth response was:" >&2
    printf '%s\n' "$AUTH_RESPONSE" >&2
    exit 1
}

# env -u: the tests must not inherit read-only or SSL settings from the
# caller's shell.
env -u MEALIE_READ_ONLY -u MEALIE_VERIFY_SSL \
    MEALIE_INTEGRATION=1 MEALIE_URL="$URL" MEALIE_API_TOKEN="$TOKEN" \
    uv run --extra dev pytest tests/integration -v

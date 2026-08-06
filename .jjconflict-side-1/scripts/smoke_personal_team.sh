#!/usr/bin/env bash
# Live smoke for 个人 = 单人真团队 (execution-architecture v4).
#
# Proves the whole loop against the running demo backend (:8799):
#   1. GET /teams/my-teams auto-provisions the personal team, sorts it first,
#      and flags it personal:true.
#   2. Registering a machine to the personal team (加机器 on its 算力 page) is
#      the same connector call as for a shared team.
#   3. A NEW personal project (no team) sees that machine in its compute
#      profiles — routed through the owner's personal team, zero manual setup.
#
# Self-cleaning: removes the device↔team binding and the probe project.
# Usage: bash scripts/smoke_personal_team.sh [base_url]
set -euo pipefail

BASE="${1:-http://127.0.0.1:8799}"
DEVICE="f616e74eebe1" # alice's enrolled MacBook in the demo DB

say() { printf '\n== %s\n' "$*"; }
fail() {
  printf 'SMOKE FAIL: %s\n' "$*" >&2
  exit 1
}

say "login alice"
TOKEN=$(curl -sf "$BASE/users/auth/login" -H 'content-type: application/json' \
  -d '{"username":"alice","password":"demo12345"}' | jq -r '.data.accessToken')
[ -n "$TOKEN" ] && [ "$TOKEN" != "null" ] || fail "login"
AUTH="Authorization: Bearer $TOKEN"

say "my-teams: personal team provisioned, first, flagged"
TEAMS=$(curl -sf "$BASE/teams/my-teams" -H "$AUTH")
echo "$TEAMS" | jq -c '.data.teams | map({id, name, personal})'
FIRST_PERSONAL=$(echo "$TEAMS" | jq -r '.data.teams[0].personal')
PERSONAL_TID=$(echo "$TEAMS" | jq -r '.data.teams[0].id')
[ "$FIRST_PERSONAL" = "true" ] || fail "first team is not the personal team"

say "register device to the personal team (team #$PERSONAL_TID)"
curl -sf -X POST "$BASE/connector/my/devices/$DEVICE/teams" -H "$AUTH" \
  -H 'content-type: application/json' -d "{\"team_id\": $PERSONAL_TID}" |
  jq -c '{device_id, team_ids}'

say "create a personal project (no team)"
# NB: /api/projects is the cheesex surface (owner_handle, no team); bare
# /projects is the main app's team-scoped project API.
PID=$(curl -sf "$BASE/api/projects" -H "$AUTH" -H 'content-type: application/json' \
  -d "{\"name\":\"smoke-personal-$$\",\"owner_handle\":\"alice\"}" | jq -r '.data.id')
[ -n "$PID" ] && [ "$PID" != "null" ] || fail "project create"
echo "project: $PID"

say "compute profiles for the personal project"
PROFILES=$(curl -sf "$BASE/api/projects/$PID/compute-profiles" -H "$AUTH")
echo "$PROFILES" | jq -c '.data.profiles | map({id, available})'

say "cleanup (unbind device, delete probe project)"
curl -sf -X DELETE "$BASE/connector/my/devices/$DEVICE/teams/$PERSONAL_TID" -H "$AUTH" >/dev/null
# The cheesex surface has no DELETE /api/projects — expect 405; drop the row by hand:
#   docker exec cheesex-pg psql -U cheesex -d fusion_test \
#     -c "DELETE FROM projects WHERE name LIKE 'smoke-personal-%';"
curl -s -o /dev/null -w 'project delete: %{http_code} (405 expected — clean via psql, see comment)\n' \
  -X DELETE "$BASE/api/projects/$PID" -H "$AUTH"

# The device profile must have been offered while the binding existed.
echo "$PROFILES" | jq -e '.data.profiles | map(.id) | index("device") != null' >/dev/null ||
  fail "device profile missing from personal project"
echo
echo "SMOKE OK: personal team = first-class compute target"

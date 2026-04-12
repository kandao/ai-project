#!/usr/bin/env bash
# test/smoke/test_health.sh
#
# Smoke tests for GET /health and unauthenticated access guards.
# Requires the backend to be running at BASE_URL (default: http://localhost:8000).
#
# Usage:
#   bash test/smoke/test_health.sh
#   BASE_URL=http://my-server:8000 bash test/smoke/test_health.sh

set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
PASS=0
FAIL=0

# ── helpers ────────────────────────────────────────────────────────────────────

green() { printf '\033[32m%s\033[0m\n' "$*"; }
red()   { printf '\033[31m%s\033[0m\n' "$*"; }

assert_status() {
  local label="$1" expected="$2" actual="$3"
  if [ "$actual" = "$expected" ]; then
    green "  PASS  $label (HTTP $actual)"
    PASS=$((PASS + 1))
  else
    red   "  FAIL  $label — expected HTTP $expected, got HTTP $actual"
    FAIL=$((FAIL + 1))
  fi
}

assert_body() {
  local label="$1" expected="$2" actual="$3"
  if echo "$actual" | grep -qF "$expected"; then
    green "  PASS  $label (body contains '$expected')"
    PASS=$((PASS + 1))
  else
    red   "  FAIL  $label — expected '$expected' in: $actual"
    FAIL=$((FAIL + 1))
  fi
}

echo ""
echo "=== Health & auth-guard smoke tests  (target: $BASE_URL) ==="
echo ""

# ── 1. Health endpoint ─────────────────────────────────────────────────────────
echo "1. GET /health"

HEALTH_BODY=$(curl -s "$BASE_URL/health")
HEALTH_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/health")

assert_status "health returns 200"         "200"  "$HEALTH_STATUS"
assert_body   "health body is {status:ok}" '"ok"' "$HEALTH_BODY"

# ── 2. Protected routes reject unauthenticated requests ────────────────────────
echo ""
echo "2. Protected routes — no token"

for ENDPOINT in "/api/documents/" "/api/chat/sessions" "/api/analytics/"; do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL$ENDPOINT")
  assert_status "GET $ENDPOINT without token → 401" "401" "$STATUS"
done

# ── 3. Protected routes reject malformed JWT ──────────────────────────────────
echo ""
echo "3. Protected routes — invalid JWT"

for ENDPOINT in "/api/documents/" "/api/chat/sessions"; do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL$ENDPOINT" \
    -H "Authorization: Bearer this.is.not.a.valid.jwt")
  assert_status "GET $ENDPOINT with bad JWT → 401" "401" "$STATUS"
done

# ── 4. OpenAPI schema is accessible ────────────────────────────────────────────
echo ""
echo "4. GET /docs (OpenAPI)"

DOCS_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/docs")
assert_status "OpenAPI docs reachable" "200" "$DOCS_STATUS"

# ── summary ────────────────────────────────────────────────────────────────────
echo ""
echo "──────────────────────────────────────"
if [ "$FAIL" -eq 0 ]; then
  green "All $PASS tests passed."
else
  red "$FAIL / $((PASS + FAIL)) tests FAILED."
  exit 1
fi

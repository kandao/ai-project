#!/usr/bin/env bash
# test/smoke/test_auth.sh
#
# Smoke tests for POST /api/auth/register and POST /api/auth/login.
# Requires the backend to be running at BASE_URL (default: http://localhost:8000).
#
# Usage:
#   bash test/smoke/test_auth.sh
#   BASE_URL=http://my-server:8000 bash test/smoke/test_auth.sh

set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
PASS=0
FAIL=0

# ── helpers ────────────────────────────────────────────────────────────────────

green()  { printf '\033[32m%s\033[0m\n' "$*"; }
red()    { printf '\033[31m%s\033[0m\n' "$*"; }

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

assert_field() {
  local label="$1" field="$2" body="$3"
  if echo "$body" | python3 -c "import json,sys; d=json.load(sys.stdin); assert '$field' in d" 2>/dev/null; then
    green "  PASS  $label (field '$field' present)"
    PASS=$((PASS + 1))
  else
    red   "  FAIL  $label — field '$field' missing in: $body"
    FAIL=$((FAIL + 1))
  fi
}

# ── unique test email to avoid cross-run collisions ────────────────────────────
EMAIL="smoketest-$(date +%s)@example.com"
PASSWORD="smokeSecret99"

echo ""
echo "=== Auth smoke tests  (target: $BASE_URL) ==="
echo "    test email: $EMAIL"
echo ""

# ── 1. Register ────────────────────────────────────────────────────────────────
echo "1. POST /api/auth/register"

BODY=$(curl -s -X POST "$BASE_URL/api/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\",\"name\":\"Smoke User\"}")
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE_URL/api/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"smoketest2-$(date +%s)@example.com\",\"password\":\"pw\",\"name\":\"x\"}")

# Extract token from the first register call
TOKEN=$(echo "$BODY" | python3 -c "import json,sys; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null || true)

assert_field "register returns access_token"  "access_token"  "$BODY"
assert_field "register returns user_id"        "user_id"       "$BODY"
assert_field "register returns email"          "email"         "$BODY"

# ── 2. Duplicate registration → 409 ───────────────────────────────────────────
echo ""
echo "2. POST /api/auth/register (duplicate email)"

DUP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE_URL/api/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"other\"}")
assert_status "duplicate email rejected" "409" "$DUP_STATUS"

# ── 3. Login with correct credentials → 200 ───────────────────────────────────
echo ""
echo "3. POST /api/auth/login (correct password)"

LOGIN_BODY=$(curl -s -X POST "$BASE_URL/api/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}")
LOGIN_STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE_URL/api/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}")

assert_status "login succeeds"             "200"            "$LOGIN_STATUS"
assert_field  "login returns access_token" "access_token"   "$LOGIN_BODY"

# ── 4. Login with wrong password → 401 ────────────────────────────────────────
echo ""
echo "4. POST /api/auth/login (wrong password)"

BAD_STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE_URL/api/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"wrongpassword\"}")
assert_status "wrong password rejected" "401" "$BAD_STATUS"

# ── 5. Login for non-existent user → 401 ──────────────────────────────────────
echo ""
echo "5. POST /api/auth/login (unknown email)"

NOUSER_STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE_URL/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"email":"nobody@doesnotexist.invalid","password":"pw"}')
assert_status "unknown user rejected" "401" "$NOUSER_STATUS"

# ── 6. JWT usable on protected endpoint ───────────────────────────────────────
echo ""
echo "6. GET /api/documents/ with JWT from register"

DOC_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/api/documents/" \
  -H "Authorization: Bearer $TOKEN")
assert_status "JWT grants access to documents" "200" "$DOC_STATUS"

# ── summary ────────────────────────────────────────────────────────────────────
echo ""
echo "──────────────────────────────────────"
if [ "$FAIL" -eq 0 ]; then
  green "All $PASS tests passed."
else
  red "$FAIL / $((PASS + FAIL)) tests FAILED."
  exit 1
fi

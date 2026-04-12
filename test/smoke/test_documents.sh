#!/usr/bin/env bash
# test/smoke/test_documents.sh
#
# Smoke tests for POST/GET/DELETE /api/documents/.
# Requires the backend to be running at BASE_URL (default: http://localhost:8000).
#
# Usage:
#   bash test/smoke/test_documents.sh
#   BASE_URL=http://my-server:8000 bash test/smoke/test_documents.sh

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

# ── register a throw-away user to get a JWT ────────────────────────────────────
EMAIL="doc-smoke-$(date +%s)@example.com"

REGISTER_BODY=$(curl -s -X POST "$BASE_URL/api/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"pw12345\"}")
TOKEN=$(echo "$REGISTER_BODY" | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])")
AUTH="Authorization: Bearer $TOKEN"

echo ""
echo "=== Document smoke tests  (target: $BASE_URL) ==="
echo "    test user: $EMAIL"
echo ""

# ── 1. Empty document list ─────────────────────────────────────────────────────
echo "1. GET /api/documents/ (empty)"

LIST_BODY=$(curl -s "$BASE_URL/api/documents/" -H "$AUTH")
LIST_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/api/documents/" -H "$AUTH")

assert_status "empty list returns 200"   "200"       "$LIST_STATUS"
assert_field  "response has 'documents'" "documents" "$LIST_BODY"

# ── 2. Upload a TXT document ───────────────────────────────────────────────────
echo ""
echo "2. POST /api/documents/ (upload .txt)"

TMP=$(mktemp /tmp/smoke_XXXXXX.txt)
echo "This is a smoke test document." > "$TMP"

UPLOAD_BODY=$(curl -s -X POST "$BASE_URL/api/documents/" \
  -H "$AUTH" \
  -F "file=@$TMP;type=text/plain")
UPLOAD_STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE_URL/api/documents/" \
  -H "$AUTH" \
  -F "file=@$TMP;type=text/plain")
rm -f "$TMP"

assert_status "upload returns 201"       "201"      "$UPLOAD_STATUS"
assert_field  "upload returns doc_id"    "doc_id"   "$UPLOAD_BODY"
assert_field  "upload returns status"    "status"   "$UPLOAD_BODY"

DOC_ID=$(echo "$UPLOAD_BODY" | python3 -c "import json,sys; print(json.load(sys.stdin).get('doc_id',''))" 2>/dev/null || true)

# ── 3. Reject unsupported file type ───────────────────────────────────────────
echo ""
echo "3. POST /api/documents/ (unsupported type .exe)"

TMP_BAD=$(mktemp /tmp/smoke_XXXXXX.exe)
echo "binary" > "$TMP_BAD"

BAD_TYPE_STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE_URL/api/documents/" \
  -H "$AUTH" \
  -F "file=@$TMP_BAD;type=application/octet-stream")
rm -f "$TMP_BAD"

assert_status "unsupported type rejected with 422" "422" "$BAD_TYPE_STATUS"

# ── 4. Reject empty file ──────────────────────────────────────────────────────
echo ""
echo "4. POST /api/documents/ (empty file)"

TMP_EMPTY=$(mktemp /tmp/smoke_XXXXXX.txt)
: > "$TMP_EMPTY"  # create truly empty file

EMPTY_STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE_URL/api/documents/" \
  -H "$AUTH" \
  -F "file=@$TMP_EMPTY;type=text/plain")
rm -f "$TMP_EMPTY"

assert_status "empty file rejected with 422" "422" "$EMPTY_STATUS"

# ── 5. Delete the uploaded document ───────────────────────────────────────────
if [ -n "$DOC_ID" ]; then
  echo ""
  echo "5. DELETE /api/documents/$DOC_ID"

  DEL_STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE \
    "$BASE_URL/api/documents/$DOC_ID" -H "$AUTH")
  assert_status "delete returns 200" "200" "$DEL_STATUS"

  # Confirm it no longer appears in the list
  LIST_AFTER=$(curl -s "$BASE_URL/api/documents/" -H "$AUTH")
  if echo "$LIST_AFTER" | python3 -c "
import json, sys
docs = json.load(sys.stdin).get('documents', [])
assert all(d['doc_id'] != '$DOC_ID' for d in docs)
" 2>/dev/null; then
    green "  PASS  deleted doc no longer in list"
    PASS=$((PASS + 1))
  else
    red   "  FAIL  deleted doc still appears in list"
    FAIL=$((FAIL + 1))
  fi
fi

# ── 6. Delete non-existent doc → 404 ──────────────────────────────────────────
echo ""
echo "6. DELETE /api/documents/<fake-id>"

FAKE_DEL=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE \
  "$BASE_URL/api/documents/00000000-0000-0000-0000-000000000000" -H "$AUTH")
assert_status "delete unknown doc → 404" "404" "$FAKE_DEL"

# ── summary ────────────────────────────────────────────────────────────────────
echo ""
echo "──────────────────────────────────────"
if [ "$FAIL" -eq 0 ]; then
  green "All $PASS tests passed."
else
  red "$FAIL / $((PASS + FAIL)) tests FAILED."
  exit 1
fi

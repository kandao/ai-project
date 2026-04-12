#!/usr/bin/env bash
# test/smoke/run_all.sh
#
# Run all smoke test suites in order.
# Exits with code 1 if any suite fails.
#
# Usage:
#   bash test/smoke/run_all.sh
#   BASE_URL=http://my-server:8000 bash test/smoke/run_all.sh

set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
export BASE_URL

DIR="$(cd "$(dirname "$0")" && pwd)"

green() { printf '\033[32m%s\033[0m\n' "$*"; }
red()   { printf '\033[31m%s\033[0m\n' "$*"; }
bold()  { printf '\033[1m%s\033[0m\n'  "$*"; }

bold "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
bold " DocQA smoke tests   →   $BASE_URL"
bold "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

SUITES=(
  "$DIR/test_health.sh"
  "$DIR/test_auth.sh"
  "$DIR/test_documents.sh"
)

SUITE_PASS=0
SUITE_FAIL=0

for SUITE in "${SUITES[@]}"; do
  NAME=$(basename "$SUITE")
  echo ""
  if bash "$SUITE"; then
    SUITE_PASS=$((SUITE_PASS + 1))
  else
    red "Suite FAILED: $NAME"
    SUITE_FAIL=$((SUITE_FAIL + 1))
  fi
done

echo ""
bold "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ "$SUITE_FAIL" -eq 0 ]; then
  green "All $SUITE_PASS suite(s) passed."
else
  red "$SUITE_FAIL / $((SUITE_PASS + SUITE_FAIL)) suite(s) FAILED."
  exit 1
fi

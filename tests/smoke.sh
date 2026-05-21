#!/usr/bin/env bash
# Claude Gallery — macOS Smoke Tests
# Run after installation to verify the server is working correctly.
# Usage: bash tests/smoke.sh
# Exit code 0 = all tests passed, 1 = one or more failed.

PORT=${1:-7477}
BASE="http://localhost:$PORT"
STARTUP_TIMEOUT=20
PASSED=0
FAILED=0

GREEN='\033[0;32m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

pass() { echo -e "  ${GREEN}PASS${NC}  $1"; ((PASSED++)); }
fail() { echo -e "  ${RED}FAIL${NC}  $1 — $2"; ((FAILED++)); }

echo ""
echo -e "${CYAN}Claude Gallery Smoke Tests${NC}"
echo -e "${CYAN}==========================${NC}"
echo ""

# ── 1. Wait for server to start ───────────────────────────────────────
echo "Waiting for server on port $PORT..."
deadline=$((SECONDS + STARTUP_TIMEOUT))
up=0
while [ $SECONDS -lt $deadline ]; do
    if curl -sf --max-time 2 "$BASE" > /dev/null 2>&1; then
        up=1; break
    fi
    sleep 0.5
done
if [ $up -eq 0 ]; then
    echo -e "  ${RED}FATAL${NC}  Server did not respond within ${STARTUP_TIMEOUT}s"
    exit 1
fi
echo -e "  Server is up."
echo ""

# ── 2. GET / returns 200 and contains expected content ────────────────
body=$(curl -sf --max-time 5 "$BASE" 2>/dev/null)
code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$BASE")
if [ "$code" = "200" ] && echo "$body" | grep -q "Artifact Gallery"; then
    pass "GET / returns 200 with gallery HTML"
else
    fail "GET / returns 200 with gallery HTML" "status=$code"
fi

# ── 3. GET /list returns valid JSON ───────────────────────────────────
code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$BASE/list")
body=$(curl -sf --max-time 5 "$BASE/list" 2>/dev/null)
if [ "$code" = "200" ] && echo "$body" | grep -q '"files"'; then
    pass "GET /list returns valid JSON with 'files' key"
else
    fail "GET /list returns valid JSON with 'files' key" "status=$code, body=$body"
fi

# ── 4. GET /notify?file=smoke-test.txt returns 200 ───────────────────
code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$BASE/notify?file=smoke-test.txt")
if [ "$code" = "200" ]; then
    pass "GET /notify?file=smoke-test.txt returns 200"
else
    fail "GET /notify?file=smoke-test.txt returns 200" "status=$code"
fi

# ── 5. GET /files/nonexistent returns 404 ────────────────────────────
code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$BASE/files/does-not-exist-smoke.txt")
if [ "$code" = "404" ]; then
    pass "GET /files/nonexistent returns 404"
else
    fail "GET /files/nonexistent returns 404" "status=$code"
fi

# ── 6. CLAUDE.md was injected ────────────────────────────────────────
CLAUDE_MD="$HOME/.claude/CLAUDE.md"
if [ -f "$CLAUDE_MD" ] && grep -q "claude-gallery-start" "$CLAUDE_MD"; then
    pass "CLAUDE.md contains injected gallery instructions"
else
    fail "CLAUDE.md contains injected gallery instructions" "marker not found in $CLAUDE_MD"
fi

# ── Summary ───────────────────────────────────────────────────────────
echo ""
if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}Results: $PASSED passed, $FAILED failed${NC}"
else
    echo -e "${RED}Results: $PASSED passed, $FAILED failed${NC}"
fi
echo ""

[ $FAILED -eq 0 ] && exit 0 || exit 1

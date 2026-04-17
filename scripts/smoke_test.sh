#!/usr/bin/env bash
# smoke_test.sh — Validacao pos-deploy
# Uso: BASE_URL=https://meusite.com ./scripts/smoke_test.sh
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
EXIT_CODE=0
PASS=0
FAIL=0

check() {
  local name="$1"
  local url="$2"
  local expected_status="${3:-200}"
  local expected_body="${4:-}"

  local response status body
  response=$(curl -sS -o /tmp/smoke_body -w "%{http_code}" -L "$url" || echo "000")
  status="$response"
  body=$(cat /tmp/smoke_body)

  if [[ "$status" != "$expected_status" ]]; then
    echo "FAIL [$name] expected HTTP $expected_status, got $status — $url"
    FAIL=$((FAIL + 1))
    EXIT_CODE=1
    return
  fi

  if [[ -n "$expected_body" ]] && ! echo "$body" | grep -q "$expected_body"; then
    echo "FAIL [$name] body missing '$expected_body' — $url"
    FAIL=$((FAIL + 1))
    EXIT_CODE=1
    return
  fi

  echo "OK   [$name] $url ($status)"
  PASS=$((PASS + 1))
}

echo "Smoke test contra: $BASE_URL"
echo "─────────────────────────────"

check "healthcheck"     "$BASE_URL/health/"                '200' '"status"'
check "manifest"        "$BASE_URL/manifest.json"          '200' '"name"'
check "service-worker"  "$BASE_URL/sw.js"                  '200' 'CACHE'
check "robots"          "$BASE_URL/robots.txt"             '200' 'User-agent'
check "sitemap"         "$BASE_URL/sitemap.xml"            '200' 'urlset'
check "home"            "$BASE_URL/"                       '200' '<html'
check "agendar"         "$BASE_URL/agendar/"               '200' '<form'
check "equipe"          "$BASE_URL/equipe/"                '200' '<html'
check "lgpd"            "$BASE_URL/lgpd/meus-dados/"       '200' '<form'
check "api-schema"      "$BASE_URL/api/schema/"            '200' 'openapi'
check "admin-redirect"  "$BASE_URL/painel/"                '302'

echo "─────────────────────────────"
echo "PASS: $PASS  FAIL: $FAIL"
exit $EXIT_CODE

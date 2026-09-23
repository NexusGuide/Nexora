#!/usr/bin/env bash
#
# Register a panel and test the adapter against it, interactively.
#
#   bash scripts/register-panel.sh
#
# Credentials are typed at the prompt, never passed as arguments: an argument
# would land in the shell history and in `ps` output for every user on the box.
# They go straight to the API over HTTPS and are encrypted before storage.
#
# This exists because the admin panel is phase 7 and registering a panel is
# otherwise a hand-written curl with a credential in it.

set -uo pipefail

API="${NEXORA_API:-}"
if [ -z "$API" ]; then
    # Derive it from .env when run from the deployment directory.
    domain=$(grep -E '^NEXUS_DOMAIN=' .env 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d ' ')
    [ -n "$domain" ] && API="https://$domain"
fi
[ -n "$API" ] || { echo "Set NEXORA_API, or run this from the deployment directory." >&2; exit 1; }

red()   { printf '\033[0;31m%s\033[0m\n' "$*"; }
green() { printf '\033[0;32m%s\033[0m\n' "$*"; }
dim()   { printf '\033[0;90m%s\033[0m\n' "$*"; }

json() { python3 -c "import sys,json;d=json.load(sys.stdin);print($1)" 2>/dev/null; }

echo
dim "API: $API"
echo

# --- sign in ----------------------------------------------------------------
printf 'Nexora username: '; read -r AU
printf 'Nexora password: '; read -rs AP; echo

LOGIN=$(curl -sS -X POST "$API/api/v1/auth/login" \
    -H 'Content-Type: application/json' \
    -d "$(python3 -c '
import json,sys
print(json.dumps({"identifier": sys.argv[1], "password": sys.argv[2]}))' "$AU" "$AP")")
unset AP

TOKEN=$(echo "$LOGIN" | json "d['data']['tokens']['access_token']")
if [ -z "$TOKEN" ]; then
    red "Sign-in failed."
    echo "$LOGIN" | head -c 300; echo
    exit 1
fi
green "Signed in as $AU"

# --- panel details ----------------------------------------------------------
echo
dim "The base URL is the panel's root — no /dashboard, no trailing path."
dim "The adapter appends /api/... itself."
printf 'Panel base URL:      '; read -r PURL
printf 'Panel admin user:    '; read -r PU
printf 'Panel admin password: '; read -rs PP; echo
printf 'Name for this panel [main]: '; read -r PNAME
PNAME="${PNAME:-main}"

# Built with json.dumps so a password containing quotes, backslashes or $
# cannot break the request or be mangled by the shell.
BODY=$(python3 -c '
import json,sys
print(json.dumps({
    "name": sys.argv[1], "panel_type": "PASARGUARD",
    "base_url": sys.argv[2].rstrip("/"),
    "username": sys.argv[3], "password": sys.argv[4],
}))' "$PNAME" "$PURL" "$PU" "$PP")
unset PP

PANEL=$(curl -sS -X POST "$API/api/v1/admin/panels" \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d "$BODY")
unset BODY

PID=$(echo "$PANEL" | json "d['data']['id']")
if [ -z "$PID" ]; then
    red "Could not register the panel."
    echo "$PANEL" | head -c 400; echo
    exit 1
fi
green "Panel registered: $PID"

# --- the moment of truth ----------------------------------------------------
# The PasarGuard adapter's endpoint paths were written from documentation and
# have never run against a live panel. This is what proves them.
echo
dim "Testing the adapter against the live panel..."
RESULT=$(curl -sS -X POST "$API/api/v1/admin/panels/$PID/test" \
    -H "Authorization: Bearer $TOKEN")

clear
echo
if echo "$RESULT" | grep -q '"reachable":true'; then
    green "REACHABLE — the adapter's endpoints match this panel."
else
    red "NOT REACHABLE"
    echo
    echo "$RESULT" | python3 -m json.tool 2>/dev/null || echo "$RESULT"
    echo
    dim "Send this output on; the adapter's paths need aligning with your panel."
fi
echo
dim "Panel id: $PID"

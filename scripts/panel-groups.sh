#!/usr/bin/env bash
#
# Choose which panel groups new customers are placed in.
#
#   bash scripts/panel-groups.sh
#
# On a group-based panel (newer PasarGuard), group membership is what grants a
# user any inbound. A customer provisioned into no group gets an account and
# no config. This lists the panel's groups, live, and stores your choice —
# after the backend has checked that each group exists, is enabled, and
# actually grants an inbound.
#
# A stopgap until the admin panel (phase 7) does this in a UI.

set -uo pipefail

API="${NEXORA_API:-}"
if [ -z "$API" ]; then
    domain=$(grep -E '^NEXUS_DOMAIN=' .env 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d ' ')
    [ -n "$domain" ] && API="https://$domain"
fi
[ -n "$API" ] || { echo "Set NEXORA_API, or run from the deployment directory." >&2; exit 1; }

red()   { printf '\033[0;31m%s\033[0m\n' "$*"; }
green() { printf '\033[0;32m%s\033[0m\n' "$*"; }
dim()   { printf '\033[0;90m%s\033[0m\n' "$*"; }
jq_()   { python3 -c "import sys,json;d=json.load(sys.stdin);print($1)" 2>/dev/null; }

echo
printf 'Nexora admin username: '; read -r AU
printf 'Nexora admin password: '; read -rs AP; echo
LOGIN=$(curl -sS -X POST "$API/api/v1/auth/login" -H 'Content-Type: application/json' \
    -d "$(python3 -c 'import json,sys;print(json.dumps({"identifier":sys.argv[1],"password":sys.argv[2]}))' "$AU" "$AP")")
unset AP
TOKEN=$(echo "$LOGIN" | jq_ "d['data']['tokens']['access_token']")
[ -n "$TOKEN" ] || { red "Sign-in failed."; echo "$LOGIN" | head -c 300; echo; exit 1; }
AUTH=(-H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json')

PANELS=$(curl -sS "$API/api/v1/admin/panels" "${AUTH[@]}")
PANEL_ID=$(echo "$PANELS" | jq_ "d['data'][0]['id']")
PANEL_NAME=$(echo "$PANELS" | jq_ "d['data'][0]['name']")
[ -n "$PANEL_ID" ] || { red "No panel registered. Run scripts/register-panel.sh first."; exit 1; }

echo
green "Panel '$PANEL_NAME'"
GROUPS=$(curl -sS "$API/api/v1/admin/panels/$PANEL_ID/groups" "${AUTH[@]}")
echo "$GROUPS" | python3 -c "
import sys, json
d = json.load(sys.stdin)
if not d.get('success'):
    print('  could not read groups:', d.get('error', {}).get('message')); sys.exit(1)
rows = d['data']
if not rows:
    print('  this panel has no groups — it does not use the group model'); sys.exit(1)
print()
print('    id  name                 inbounds  state')
for g in rows:
    state = 'disabled' if g['is_disabled'] else ('DEFAULT' if g['is_default'] else '')
    print(f\"  {g['id']:>4}  {g['name'][:20]:20} {g['inbound_count']:>8}  {state}\")
" || exit 1

echo
dim "Enter one or more ids, separated by spaces."
printf 'Groups for new customers: '; read -r IDS

BODY=$(python3 -c '
import json, sys
print(json.dumps({"group_ids": [int(x) for x in sys.argv[1].split()]}))' "$IDS" 2>/dev/null) \
    || { red "Ids must be numbers."; exit 1; }

RESULT=$(curl -sS -X PUT "$API/api/v1/admin/panels/$PANEL_ID/groups" "${AUTH[@]}" -d "$BODY")
if echo "$RESULT" | grep -q '"success":true'; then
    green "Saved. New customers on '$PANEL_NAME' go into group(s): $IDS"
else
    red "Refused:"
    echo "$RESULT" | jq_ "d['error']['message']"
    exit 1
fi

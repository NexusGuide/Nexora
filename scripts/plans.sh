#!/usr/bin/env bash
#
# List, add, hide and archive the plans customers can buy.
#
#   bash scripts/plans.sh
#
# A stopgap until the admin panel (phase 7) does this in the app. Everything
# goes through the admin API, so the same validation and audit log apply as
# they will from the UI.
#
#   ACTIVE    shown in the app and can be ordered
#   HIDDEN    not shown, cannot be ordered; existing subscriptions unaffected
#   ARCHIVED  retired; kept only so old orders still point at something
#
# Changing or retiring a plan never touches what a customer already bought:
# every order stores its own snapshot of the plan.

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
err_()  { python3 -c "
import sys, json
try:
    d = json.load(sys.stdin); e = d.get('error') or {}
    print(e.get('message') or d)
except ValueError:
    print('unexpected response')"; }

echo
printf 'Nexora admin username: '; read -r AU
printf 'Nexora admin password: '; read -rs AP; echo
LOGIN=$(curl -sS -X POST "$API/api/v1/auth/login" -H 'Content-Type: application/json' \
    -d "$(python3 -c 'import json,sys;print(json.dumps({"identifier":sys.argv[1],"password":sys.argv[2]}))' "$AU" "$AP")")
unset AP
TOKEN=$(echo "$LOGIN" | jq_ "d['data']['tokens']['access_token']")
[ -n "$TOKEN" ] || { red "Sign-in failed."; echo "$LOGIN" | err_; exit 1; }
AUTH=(-H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json')

list_plans() {
    curl -sS "$API/api/v1/admin/plans" "${AUTH[@]}" | python3 -c "
import sys, json
d = json.load(sys.stdin)
if not d.get('success'):
    print('  could not list plans:', (d.get('error') or {}).get('message')); sys.exit(1)
rows = [p for p in d['data'] if p['status'] != 'ARCHIVED']
if not rows:
    print('  no plans yet'); sys.exit()
print()
print('   #  name                      days   traffic   devices        price  status')
for i, p in enumerate(rows, 1):
    gb = p.get('traffic_limit_bytes') or 0
    traffic = 'unlimited' if not gb else f'{gb / 1024**3:g} GB'
    print(f\"  {i:>2}  {p['name'][:24]:24} {p['duration_days']:>5}  {traffic:>9}  {p['device_limit']:>7}  \"
          f\"{p['price']:>11} {p['currency']}  {p['status']}\")
" || return 1
}

# The id of the Nth non-archived plan, in the order list_plans prints them.
plan_id_at() {
    curl -sS "$API/api/v1/admin/plans" "${AUTH[@]}" | python3 -c "
import sys, json
rows = [p for p in json.load(sys.stdin).get('data', []) if p['status'] != 'ARCHIVED']
n = int(sys.argv[1])
if 1 <= n <= len(rows): print(rows[n-1]['id'])" "$1" 2>/dev/null
}

add_plan() {
    echo
    printf 'Name (shown to customers, e.g. "30 days / 50 GB"): '; read -r NAME
    printf 'Duration in days: ';                 read -r DAYS
    printf 'Traffic in GB (0 = unlimited): ';    read -r GB
    printf 'Price in Toman (e.g. 150000): ';     read -r PRICE
    printf 'Devices [1]: ';                      read -r DEV
    printf 'Position in the list, lower first [0]: '; read -r SORT
    BODY=$(python3 -c '
import json, sys
name, days, gb, price, dev, sort = sys.argv[1:7]
print(json.dumps({"name": name.strip(), "duration_days": int(days),
                  "traffic_limit_gb": float(gb), "price": price.strip(),
                  "device_limit": int(dev or 1), "sort_order": int(sort or 0),
                  "currency": "IRT"}))' "$NAME" "$DAYS" "$GB" "$PRICE" "$DEV" "$SORT" 2>/dev/null) \
        || { red "Days, traffic, devices and position must be numbers."; return; }
    RESULT=$(curl -sS -X POST "$API/api/v1/admin/plans" "${AUTH[@]}" -d "$BODY")
    if echo "$RESULT" | grep -q '"success":true'; then
        green "Added. It is ACTIVE and shows in the app now."
    else
        red "Refused: $(echo "$RESULT" | err_)"
    fi
}

set_status() {
    printf 'Plan number: '; read -r N
    ID=$(plan_id_at "$N")
    [ -n "$ID" ] || { red "No plan with that number."; return; }
    RESULT=$(curl -sS -X PATCH "$API/api/v1/admin/plans/$ID" "${AUTH[@]}" -d "{\"status\":\"$1\"}")
    if echo "$RESULT" | grep -q '"success":true'; then
        green "Plan $N is now $1."
    else
        red "Refused: $(echo "$RESULT" | err_)"
    fi
}

while true; do
    list_plans || exit 1
    echo
    dim "a) add   h) hide   s) show (make active)   r) retire (archive)   q) quit"
    printf '> '; read -r CHOICE
    case "$CHOICE" in
        a) add_plan ;;
        h) set_status HIDDEN ;;
        s) set_status ACTIVE ;;
        r) set_status ARCHIVED ;;
        q|"") exit 0 ;;
        *) red "Unknown choice." ;;
    esac
done

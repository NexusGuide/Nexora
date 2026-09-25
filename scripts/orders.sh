#!/usr/bin/env bash
#
# Confirm card-to-card payments: list the orders waiting for one, and mark the
# one you have received money for as paid.
#
#   bash scripts/orders.sh
#
# Until a payment gateway exists (phase 6), "Buy" in the app creates an order
# that waits as PENDING. Confirming it here is what creates the customer's
# panel account and their configs — within seconds, through the worker.
#
# Confirming is idempotent: doing it twice provisions once. It is audit-logged
# under your admin account. A stopgap until the admin panel (phase 7).

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

list_orders() {
    curl -sS "$API/api/v1/admin/orders?order_status=PENDING" "${AUTH[@]}" | python3 -c "
import sys, json
d = json.load(sys.stdin)
if not d.get('success'):
    print('  could not list orders:', (d.get('error') or {}).get('message')); sys.exit(1)
rows = d['data']
if not rows:
    print('  no orders are waiting for payment'); sys.exit()
print()
print('   #  when (UTC)        customer          plan                          amount')
for i, o in enumerate(rows, 1):
    when = (o['created_at'] or '')[:16].replace('T', ' ')
    print(f\"  {i:>2}  {when:16}  {o['username'][:16]:16}  {o['plan_name'][:28]:28} \"
          f\"{o['amount']:>10} {o['currency']}\")
" || return 1
}

order_id_at() {
    curl -sS "$API/api/v1/admin/orders?order_status=PENDING" "${AUTH[@]}" | python3 -c "
import sys, json
rows = json.load(sys.stdin).get('data', [])
n = int(sys.argv[1])
if 1 <= n <= len(rows): print(rows[n-1]['id'])" "$1" 2>/dev/null
}

confirm() {
    printf 'Order number you have received payment for: '; read -r N
    ID=$(order_id_at "$N")
    [ -n "$ID" ] || { red "No order with that number."; return; }
    printf 'Confirm payment for order %s? Type yes: ' "$N"; read -r SURE
    [ "$SURE" = "yes" ] || { dim "Not confirmed."; return; }
    RESULT=$(curl -sS -X POST "$API/api/v1/admin/orders/$ID/confirm-payment" "${AUTH[@]}")
    if echo "$RESULT" | grep -q '"success":true'; then
        green "Paid. The customer's service is being created and appears in the app shortly."
    else
        red "Refused: $(echo "$RESULT" | err_)"
    fi
}

while true; do
    list_orders || exit 1
    echo
    dim "c) confirm a payment   r) refresh   q) quit"
    printf '> '; read -r CHOICE
    case "$CHOICE" in
        c) confirm ;;
        r) ;;
        q|"") exit 0 ;;
        *) red "Unknown choice." ;;
    esac
done

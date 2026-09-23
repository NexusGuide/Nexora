#!/usr/bin/env bash
#
# Run one real purchase end to end, against a live deployment and a live panel.
#
#   bash scripts/smoke-purchase.sh
#
# It creates a plan, a server, a throwaway customer, an order; confirms the
# payment; waits for the worker to provision; and prints the configs that come
# back. That is the whole commercial path of the product in one run.
#
# THIS TOUCHES YOUR REAL PANEL. It creates one user there, named so it is
# obvious it is a test, and prints the name at the end so you can remove it.
# Run it against a panel you are willing to have a test user on.

set -uo pipefail

API="${NEXORA_API:-}"
if [ -z "$API" ]; then
    domain=$(grep -E '^NEXUS_DOMAIN=' .env 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d ' ')
    [ -n "$domain" ] && API="https://$domain"
fi
[ -n "$API" ] || { echo "Set NEXORA_API, or run from the deployment directory." >&2; exit 1; }

red()   { printf '\033[0;31m%s\033[0m\n' "$*"; }
green() { printf '\033[0;32m%s\033[0m\n' "$*"; }
step()  { printf '\n\033[0;36m==>\033[0m \033[1m%s\033[0m\n' "$*"; }
dim()   { printf '\033[0;90m%s\033[0m\n' "$*"; }

jq_() { python3 -c "import sys,json;d=json.load(sys.stdin);print($1)" 2>/dev/null; }
pyjson() { python3 -c 'import json,sys;print(json.dumps(json.loads(sys.argv[1])))' "$1"; }

fail() { red "$1"; shift; echo "$*" | head -c 500; echo; exit 1; }

STAMP=$(date +%s)
CUST="smoketest_$STAMP"
# The customer's address lives on the deployment's own domain. ".invalid" and
# similar reserved names are rejected by the email validator, correctly.
MAIL_DOMAIN="${API#https://}"; MAIL_DOMAIN="${MAIL_DOMAIN%%/*}"

echo
dim "API: $API"
dim "This creates a user on your live panel. Ctrl+C now if that is not wanted."
echo

# --- admin sign-in -----------------------------------------------------------
printf 'Nexora admin username: '; read -r AU
printf 'Nexora admin password: '; read -rs AP; echo

LOGIN=$(curl -sS -X POST "$API/api/v1/auth/login" -H 'Content-Type: application/json' \
    -d "$(python3 -c 'import json,sys;print(json.dumps({"identifier":sys.argv[1],"password":sys.argv[2]}))' "$AU" "$AP")")
unset AP
ADMIN_TOKEN=$(echo "$LOGIN" | jq_ "d['data']['tokens']['access_token']")
[ -n "$ADMIN_TOKEN" ] || fail "Admin sign-in failed." "$LOGIN"
green "Signed in as $AU"

AUTH=(-H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json')

# --- the panel and the node it fronts ---------------------------------------
step "Panel"
PANELS=$(curl -sS "$API/api/v1/admin/panels" "${AUTH[@]}")
PANEL_ID=$(echo "$PANELS" | jq_ "d['data'][0]['id']")
PANEL_NAME=$(echo "$PANELS" | jq_ "d['data'][0]['name']")
[ -n "$PANEL_ID" ] || fail "No panel registered." "Run scripts/register-panel.sh first."
green "Using panel '$PANEL_NAME' ($PANEL_ID)"

step "Server"
# Reuse a server already registered against this panel. Creating one per run
# would leave a trail of duplicate, healthy server rows that provisioning is
# free to pick for real customers.
SERVERS=$(curl -sS "$API/api/v1/admin/servers" "${AUTH[@]}")
SERVER_ID=$(echo "$SERVERS" | python3 -c "
import sys,json
d=json.load(sys.stdin)
m=[s for s in d.get('data',[]) if s.get('panel_id')==sys.argv[1]]
print(m[0]['id'] if m else '')" "$PANEL_ID" 2>/dev/null)

if [ -n "$SERVER_ID" ]; then
    green "Reusing server $SERVER_ID"
else
    dim "The address subscribers actually connect to — the node this panel fronts."
    printf 'Server host (e.g. de1.example.com): '; read -r SHOST
    printf 'Server port [443]: '; read -r SPORT
    SPORT="${SPORT:-443}"
    SERVER=$(curl -sS -X POST "$API/api/v1/admin/servers" "${AUTH[@]}" \
        -d "$(python3 -c '
import json,sys
print(json.dumps({"panel_id":sys.argv[1],"name":"node-"+sys.argv[2],
                  "host":sys.argv[2],"port":int(sys.argv[3])}))' \
            "$PANEL_ID" "$SHOST" "$SPORT")")
    SERVER_ID=$(echo "$SERVER" | jq_ "d['data']['id']")
    [ -n "$SERVER_ID" ] || fail "Could not create the server." "$SERVER"
    green "Server $SERVER_ID"
fi

# --- a plan to sell ----------------------------------------------------------
# The smoke plan is free, and ordering requires it to be ACTIVE — which also
# puts it in the public store. It must not outlive the run, so it is archived
# on exit however the script ends, and any left over from an earlier run that
# died before cleaning up is archived first.
archive_plan() {
    curl -sS -X PATCH "$API/api/v1/admin/plans/$1" "${AUTH[@]}" \
        -d '{"status":"ARCHIVED"}' >/dev/null 2>&1
}

step "Plan"
LEFTOVER=$(curl -sS "$API/api/v1/admin/plans" "${AUTH[@]}" | python3 -c "
import sys,json
for p in json.load(sys.stdin).get('data',[]):
    if p['name'].startswith('Smoke test') and p['status']=='ACTIVE':
        print(p['id'])" 2>/dev/null)
for id in $LEFTOVER; do
    archive_plan "$id" && dim "Archived a smoke plan left by an earlier run: $id"
done
PLAN=$(curl -sS -X POST "$API/api/v1/admin/plans" "${AUTH[@]}" \
    -d "$(pyjson '{"name":"Smoke test 1GB / 1 day","duration_days":1,
                   "traffic_limit_gb":1,"device_limit":1,"price":"0.00",
                   "currency":"IRT","sort_order":999}')")
PLAN_ID=$(echo "$PLAN" | jq_ "d['data']['id']")
[ -n "$PLAN_ID" ] || fail "Could not create the plan." "$PLAN"
trap 'archive_plan "$PLAN_ID"' EXIT
green "Plan $PLAN_ID (archived automatically when this script exits)"

# --- a customer --------------------------------------------------------------
# A throwaway account, so the test never runs as the owner and therefore
# exercises the same path a real buyer takes.
step "Customer"
CPASS="Sm0ke-$STAMP-Test"
REG=$(curl -sS -X POST "$API/api/v1/auth/register" -H 'Content-Type: application/json' \
    -d "$(python3 -c '
import json,sys
print(json.dumps({"username":sys.argv[1],"email":sys.argv[1]+"@"+sys.argv[3],
                  "password":sys.argv[2]}))' "$CUST" "$CPASS" "$MAIL_DOMAIN")")
echo "$REG" | grep -q '"success":true' || fail "Could not register the test customer." "$REG"

CLOGIN=$(curl -sS -X POST "$API/api/v1/auth/login" -H 'Content-Type: application/json' \
    -d "$(python3 -c '
import json,sys
print(json.dumps({"identifier":sys.argv[1],"password":sys.argv[2]}))' "$CUST" "$CPASS" "$MAIL_DOMAIN")")
CTOKEN=$(echo "$CLOGIN" | jq_ "d['data']['tokens']['access_token']")
[ -n "$CTOKEN" ] || fail "Test customer could not sign in." "$CLOGIN"
CAUTH=(-H "Authorization: Bearer $CTOKEN" -H 'Content-Type: application/json')
green "Customer $CUST"

# --- buy ---------------------------------------------------------------------
step "Order"
ORDER=$(curl -sS -X POST "$API/api/v1/orders" "${CAUTH[@]}" \
    -d "$(python3 -c '
import json,sys
print(json.dumps({"plan_id":sys.argv[1],"idempotency_key":"smoke-"+sys.argv[2]}))' \
        "$PLAN_ID" "$STAMP")")
ORDER_ID=$(echo "$ORDER" | jq_ "d['data']['id']")
[ -n "$ORDER_ID" ] || fail "Could not place the order." "$ORDER"
green "Order $ORDER_ID"

# Sent twice on purpose: the same idempotency key must return the same order
# rather than creating a second one. This is the guard that stops a retry
# charging a customer twice, and it is cheap to prove here.
ORDER2=$(curl -sS -X POST "$API/api/v1/orders" "${CAUTH[@]}" \
    -d "$(python3 -c '
import json,sys
print(json.dumps({"plan_id":sys.argv[1],"idempotency_key":"smoke-"+sys.argv[2]}))' \
        "$PLAN_ID" "$STAMP")")
if [ "$(echo "$ORDER2" | jq_ "d['data']['id']")" = "$ORDER_ID" ]; then
    green "Idempotency holds — the repeated order returned the original"
else
    red "IDEMPOTENCY BROKEN — a retry created a second order"
fi

step "Payment"
PAY=$(curl -sS -X POST "$API/api/v1/admin/orders/$ORDER_ID/confirm-payment" "${AUTH[@]}" -d '{}')
echo "$PAY" | grep -q '"newly_paid":true' || fail "Payment confirmation failed." "$PAY"
green "Paid, provisioning queued"

# --- the worker does its thing ----------------------------------------------
step "Waiting for provisioning"
dim "The worker creates the panel user, then fetches its configs."
SUB_STATUS=""
for i in $(seq 1 30); do
    SUBS=$(curl -sS "$API/api/v1/subscriptions" "${CAUTH[@]}")
    SUB_STATUS=$(echo "$SUBS" | jq_ "d['data'][0]['status']")
    SUB_ID=$(echo "$SUBS" | jq_ "d['data'][0]['id']")
    printf '\r  %-12s (%ds)' "${SUB_STATUS:-none}" "$i"
    [ "$SUB_STATUS" = "ACTIVE" ] && break
    sleep 1
done
echo
if [ "$SUB_STATUS" != "ACTIVE" ]; then
    red "Subscription did not become ACTIVE (last: ${SUB_STATUS:-none})"
    echo
    dim "The worker's log will say why:"
    dim "  docker compose logs --tail 40 worker"
    exit 1
fi
green "Subscription $SUB_ID is ACTIVE"

# --- the thing the customer actually receives -------------------------------
step "Configs"
CONFIGS=$(curl -sS "$API/api/v1/configs" "${CAUTH[@]}")
COUNT=$(echo "$CONFIGS" | jq_ "len(d['data'])")

clear
echo
if [ "${COUNT:-0}" -gt 0 ] 2>/dev/null; then
    green "END TO END: a paid order produced $COUNT config(s)."
    echo
    # config_data is the connection URI and carries the subscriber's
    # credential, so only its scheme and host are shown — enough to tell that
    # it is a real config, not enough to use or to leak in a screenshot.
    echo "$CONFIGS" | python3 -c "
import sys, json, urllib.parse
for c in json.load(sys.stdin)['data']:
    uri = c.get('config_data') or ''
    scheme = uri.split('://', 1)[0] if '://' in uri else '?'
    host = c.get('host') or ''
    port = c.get('port') or ''
    print(f\"  {c.get('protocol') or scheme:9} {c.get('name','')[:36]:36} {host}:{port}\")
"
else
    red "The subscription is ACTIVE but no config came back."
    echo "$CONFIGS" | head -c 400; echo
    dim "The panel accepted the user but returned no links — check the inbound"
    dim "configuration on the panel side."
fi

echo
dim "The smoke plan has been archived. One thing is left for you:"
dim "  delete the panel user $CUST in PasarGuard"

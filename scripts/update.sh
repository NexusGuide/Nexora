#!/usr/bin/env bash
#
# Update a running deployment to the latest code.
#
#   bash scripts/update.sh
#
# pull -> migrate -> rebuild -> restart nginx -> verify. Every step that was
# being typed by hand after each change, in the order that works.
#
# The nginx restart is not optional. nginx resolves the name "api" to an IP
# once, when it starts. Rebuilding recreates the api container, which can come
# back on a different IP inside Docker's network; nginx keeps sending traffic
# to the old one and answers 502 Bad Gateway until it is restarted. It happens
# only sometimes — whenever Docker hands out a different address — which is
# what makes it confusing when it does.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

step() { printf '\n\033[0;36m==>\033[0m \033[1m%s\033[0m\n' "$*"; }
ok()   { printf '\033[0;32m  ok\033[0m  %s\n' "$*"; }
die()  { printf '\n\033[0;31merror\033[0m  %s\n' "$*" >&2; exit 1; }

[ -f .env ] || die "No .env here. Run this from the deployment directory."
DOMAIN=$(grep -E '^NEXUS_DOMAIN=' .env | tail -n1 | cut -d= -f2- | tr -d ' ')

step "Pulling"
git pull --ff-only

step "Applying migrations"
docker compose run --rm migrate

step "Rebuilding api, worker and scheduler"
docker compose up -d --build api worker scheduler

step "Waiting for the api"
for i in $(seq 1 30); do
    if curl -fsS --max-time 2 http://127.0.0.1:8000/health >/dev/null 2>&1; then
        ok "api healthy after ${i}s"
        break
    fi
    [ "$i" = 30 ] && die "The api did not become healthy. See: docker compose logs --tail 50 api"
    sleep 1
done

step "Restarting nginx so it picks up the api's current address"
docker compose --profile full restart nginx
sleep 2

step "Verifying through the public hostname"
if [ -n "$DOMAIN" ] && curl -fsS --max-time 10 "https://$DOMAIN/api/v1/plans" >/dev/null; then
    ok "https://$DOMAIN answers"
else
    die "The api is healthy but https://$DOMAIN does not answer. See: docker compose logs --tail 30 nginx"
fi

printf '\n\033[0;32mUpdated.\033[0m\n'

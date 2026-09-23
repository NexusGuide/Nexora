#!/usr/bin/env bash
#
# Deploy the Nexus VPN backend to a single host.
#
# Safe to re-run: it creates what is missing and leaves what exists alone. In
# particular it never regenerates .env, because rotating ENCRYPTION_KEY would
# make every stored panel credential unreadable.
#
#   ./scripts/deploy.sh api.your-domain.com you@your-email.com
#
# The one thing it cannot do for you is DNS: the domain's A record must
# already point at this server, or Let's Encrypt cannot prove you own it.

set -euo pipefail

DOMAIN="${1:-}"
EMAIL="${2:-}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

die()  { printf '\n\033[0;31merror\033[0m  %s\n' "$*" >&2; exit 1; }
step() { printf '\n\033[0;36m==>\033[0m \033[1m%s\033[0m\n' "$*"; }
ok()   { printf '\033[0;32m  ok\033[0m  %s\n' "$*"; }
warn() { printf '\033[0;33mwarn\033[0m  %s\n' "$*"; }

usage="Usage: $0 <domain> <email>

  <domain>  the API hostname, e.g. api.example.com
  <email>   a real address; Let's Encrypt sends certificate expiry warnings there

Example:
  $0 api.example.com you@example.com

Both arguments are required — pass them literally, not as placeholders."

[ -n "$DOMAIN" ] || die "No domain given.
$usage"
[ -n "$EMAIL" ] || die "No email given — Let's Encrypt requires one.
$usage"
case "$EMAIL" in
    *@*.*) ;;
    *) die "'$EMAIL' does not look like an email address.
$usage" ;;
esac

# --- 1. Docker ---------------------------------------------------------------
step "Checking Docker"
if ! command -v docker >/dev/null 2>&1; then
    warn "Docker is not installed; installing from get.docker.com"
    curl -fsSL https://get.docker.com | sh
fi
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is missing. Install docker-compose-plugin."
docker info >/dev/null 2>&1 || die "The Docker daemon is not running (or this user cannot reach it). Try: sudo systemctl start docker"
ok "$(docker --version)"

# --- 2. Ports ----------------------------------------------------------------
# nginx needs 80 and 443. On a host that already runs a panel or another web
# server they are taken, and the failure would otherwise surface halfway
# through as an unexplained container crash — or, worse, take the other
# service down.
step "Checking that ports 80 and 443 are free"
busy=""
for p in 80 443; do
    if ss -tlnH "sport = :$p" 2>/dev/null | grep -q . ; then
        busy="$busy $p"
    fi
done
if [ -n "$busy" ]; then
    echo
    ss -tlnp '( sport = :80 or sport = :443 )' 2>/dev/null || true
    echo
    die "Port(s)$busy are already in use by the process shown above.
Deploying now would fail, or take that service offline.
Either stop it, or put Nexora behind it as a reverse-proxy target instead of
running this script — see docs/deployment.md."
fi
ok "80 and 443 are free"

# --- 3. DNS ------------------------------------------------------------------
# Checked before anything is built, because a wrong A record fails at the very
# last step otherwise, after several minutes of work.
step "Checking that $DOMAIN points here"
resolved=$(getent hosts "$DOMAIN" 2>/dev/null | awk '{print $1}' | head -n1 || true)
public=$(curl -fsS --max-time 10 https://api.ipify.org 2>/dev/null || true)
if [ -z "$resolved" ]; then
    die "$DOMAIN does not resolve. Create an A record pointing at this server first."
elif [ -n "$public" ] && [ "$resolved" != "$public" ]; then
    warn "$DOMAIN resolves to $resolved but this server appears to be $public."
    warn "If you are behind a proxy (Cloudflare), that is expected; otherwise the certificate will fail."
    printf '  Continue anyway? [y/N] '; read -r a; [ "$a" = y ] || exit 1
else
    ok "$DOMAIN -> $resolved"
fi

# --- 4. Configuration --------------------------------------------------------
step "Configuration"
if [ -f .env ]; then
    ok ".env exists — leaving it untouched"
    warn "Regenerating it would rotate ENCRYPTION_KEY and orphan every stored panel credential."
else
    [ -x scripts/generate-secrets.sh ] || chmod +x scripts/generate-secrets.sh
    ./scripts/generate-secrets.sh --write
    {
        echo ""
        echo "# --- written by scripts/deploy.sh ---"
        echo "APP_ENV=production"
        echo "APP_DEBUG=false"
        echo "NEXUS_DOMAIN=$DOMAIN"
        echo "PUBLIC_API_URL=https://$DOMAIN"
        echo "ALLOWED_HOSTS=$DOMAIN"
        echo "CORS_ORIGINS=https://$DOMAIN"
        echo "POSTGRES_USER=nexus"
        echo "POSTGRES_DB=nexusvpn"
    } >> .env

    # The database password is generated here rather than by hand, and the
    # URL is assembled from it so the two can never disagree.
    pgpass="$(openssl rand -hex 24)"
    printf 'POSTGRES_PASSWORD=%s\n' "$pgpass" >> .env
    printf 'DATABASE_URL=postgresql+asyncpg://nexus:%s@postgres:5432/nexusvpn\n' "$pgpass" >> .env
    printf 'REDIS_URL=redis://redis:6379/0\n' >> .env
    chmod 600 .env
    ok "wrote .env (mode 600) with freshly generated secrets"
    warn "Back up ENCRYPTION_KEY from .env somewhere off this server. Lose it and every stored panel credential is unrecoverable."
fi

# Read values out of .env WITHOUT sourcing it. .env legitimately contains
# <angle-bracket> placeholders for settings nobody has configured yet, and to
# the shell "<" is a redirect, so `. ./.env` aborts on the first one. Compose
# reads the file with its own parser and is unaffected.
env_get() {
    grep -E "^$1=" .env 2>/dev/null | tail -n1 | cut -d= -f2- \
        | sed 's/[[:space:]]\+#.*$//; s/[[:space:]]*$//'
}

# An optional setting left at "<something>" is not a value, it is an unfilled
# blank. Blank it out so nothing downstream treats the placeholder as real.
if grep -qE '^(PAYMENT_API_KEY|SMTP_PASSWORD|SMTP_USER|SMTP_HOST|SENTRY_DSN)=<[^>]*>' .env; then
    sed -i -E 's/^(PAYMENT_API_KEY|SMTP_PASSWORD|SMTP_USER|SMTP_HOST|SENTRY_DSN)=<[^>]*>/\1=/' .env
    ok "cleared unfilled optional placeholders in .env"
fi

env_domain="$(env_get NEXUS_DOMAIN)"
case "$env_domain" in
    ""|"<"*)  ;;                       # absent or still a placeholder
    "$DOMAIN") ;;                      # agrees with the argument
    *) warn "NEXUS_DOMAIN in .env is '$env_domain', not '$DOMAIN'. Using the .env value."
       DOMAIN="$env_domain" ;;
esac

step "Rendering the nginx configuration for $DOMAIN"
# sed rather than envsubst: envsubst ships in gettext-base, which a minimal
# Ubuntu image does not have, and it would also try to expand nginx's own
# $host and $request_uri. One placeholder, one substitution.
sed "s|\${NEXUS_DOMAIN}|$DOMAIN|g" \
    infrastructure/nginx/nginx.conf.template \
    > infrastructure/nginx/nginx.conf
grep -q '${NEXUS_DOMAIN}' infrastructure/nginx/nginx.conf \
    && die "The nginx template still contains an unsubstituted placeholder."
ok "infrastructure/nginx/nginx.conf"

# --- 5. Application ----------------------------------------------------------
step "Building and starting the datastores"
docker compose up -d postgres redis
docker compose run --rm migrate
ok "migrations applied"

step "Starting the API, worker and scheduler"
docker compose up -d --build api worker scheduler

# --- 6. Certificate ----------------------------------------------------------
# The bootstrap problem: nginx will not start without a certificate, and
# certbot cannot get one without nginx serving the ACME challenge. Solved by
# starting nginx on a throwaway self-signed certificate, then replacing it.
step "TLS certificate"
live="/etc/letsencrypt/live/$DOMAIN"
has_real_cert=$(docker compose run --rm --entrypoint sh certbot -c \
    "[ -f $live/fullchain.pem ] && [ ! -f $live/.self-signed ] && echo yes || echo no" | tr -d '\r')

if [ "$has_real_cert" = yes ]; then
    ok "a certificate for $DOMAIN already exists"
else
    docker compose run --rm --entrypoint sh certbot -c "
        mkdir -p $live &&
        openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
          -keyout $live/privkey.pem -out $live/fullchain.pem \
          -subj '/CN=$DOMAIN' >/dev/null 2>&1 &&
        touch $live/.self-signed"
    ok "temporary self-signed certificate in place"

    docker compose --profile full up -d nginx
    sleep 3

    docker compose run --rm --entrypoint sh certbot -c "rm -rf $live" >/dev/null
    if docker compose run --rm certbot certonly \
        --webroot -w /var/www/certbot \
        -d "$DOMAIN" --email "$EMAIL" \
        --agree-tos --no-eff-email --non-interactive; then
        ok "certificate issued for $DOMAIN"
    else
        die "Certificate issuance failed. The usual causes are a wrong A record, port 80 blocked by a firewall, or Let's Encrypt rate limits. The stack is running; fix the cause and re-run this script."
    fi
fi

step "Starting nginx"
docker compose --profile full up -d nginx
docker compose exec nginx nginx -s reload >/dev/null 2>&1 || docker compose restart nginx

# --- 7. Renewal --------------------------------------------------------------
# Renewal uses the webroot nginx is already serving, so nothing stops.
step "Installing the renewal timer"
cron_line="0 3 * * * cd $ROOT && docker compose run --rm certbot renew --webroot -w /var/www/certbot --quiet && docker compose exec -T nginx nginx -s reload"
if crontab -l 2>/dev/null | grep -Fq "certbot renew"; then
    ok "renewal cron already installed"
else
    (crontab -l 2>/dev/null || true; echo "$cron_line") | crontab -
    ok "renewal runs daily at 03:00"
fi

if crontab -l 2>/dev/null | grep -Fq "backup-db.sh"; then
    ok "backup cron already installed"
else
    (crontab -l 2>/dev/null || true; echo "30 2 * * * cd $ROOT && ./scripts/backup-db.sh >> /var/log/nexus-backup.log 2>&1") | crontab -
    ok "database backup runs daily at 02:30"
fi

# --- 8. Verify ---------------------------------------------------------------
# Checked from outside over the real hostname, because that is the only proof
# that DNS, TLS, nginx and the API all agree.
step "Verifying"
sleep 5
if curl -fsS --max-time 15 "https://$DOMAIN/api/v1/plans" >/dev/null 2>&1; then
    ok "https://$DOMAIN/api/v1/plans responds"
else
    warn "https://$DOMAIN/api/v1/plans did not respond yet. Check: docker compose logs --tail 50 nginx api"
fi

cat <<EOF

$(printf '\033[0;32mDeployed.\033[0m')

  API        https://$DOMAIN
  Logs       docker compose logs -f api
  Status     docker compose ps

Next:

  1. Back up ENCRYPTION_KEY from .env somewhere off this server:
       grep ^ENCRYPTION_KEY= .env

  2. Set API_BASE_URL to https://$DOMAIN/ in the GitHub repository
     variables, so CI builds an APK that talks to this server.

  3. Claim the owner account. Register normally first, then promote it —
     the promote endpoint closes for good once an administrator exists:

       curl -sS -X POST https://$DOMAIN/api/v1/auth/register \\
         -H 'Content-Type: application/json' \\
         -d '{"username":"YOURNAME","email":"you@example.com","password":"CHOOSE-A-STRONG-ONE"}'

       curl -sS -X POST https://$DOMAIN/api/v1/admin/bootstrap \\
         -H 'Content-Type: application/json' \\
         -d "{\\"secret\\":\\"\$(grep ^ADMIN_BOOTSTRAP_SECRET= .env | cut -d= -f2-)\\",\\"identifier\\":\\"YOURNAME\\"}"

EOF

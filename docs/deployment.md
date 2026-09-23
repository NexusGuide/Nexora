# Deployment

## Prerequisites

- A Linux host (Ubuntu 22.04+ or Debian 12+). Docker is installed by the deploy script if missing.
- A domain with DNS pointing at the host (`api.example.com`)
- Ports 80 and 443 open; **nothing else**

## First deployment

```bash
git clone https://github.com/NexusGuide/Nexora.git
cd Nexora
./scripts/deploy.sh api.your-domain.com you@your-email.com
```

That is the whole thing. The script installs Docker if it is missing,
generates every secret, renders the nginx config for your domain, applies the
migrations, obtains the TLS certificate, installs the renewal and backup cron
jobs, and finishes by calling the API over the public hostname — which is the
only check that proves DNS, TLS, nginx and the API all agree.

It is safe to re-run. It never regenerates an existing `.env`, because
rotating `ENCRYPTION_KEY` would make every stored panel credential unreadable.

**The one thing it cannot do for you is DNS.** The domain's A record must
already point at the server, and the script checks that before doing any work
— a wrong record otherwise fails at the last step, after several minutes.

### What the script writes into `.env`

```ini
APP_ENV=production
APP_DEBUG=false
NEXUS_DOMAIN=api.your-domain.com
PUBLIC_API_URL=https://api.your-domain.com
ALLOWED_HOSTS=api.your-domain.com
CORS_ORIGINS=https://api.your-domain.com
```

The backend refuses to start if `APP_ENV=production` is combined with
`APP_DEBUG=true`, a wildcard in `CORS_ORIGINS` or `ALLOWED_HOSTS`, a plain-HTTP
`PUBLIC_API_URL`, or a SQLite database. That refusal is the point: a
misconfigured production deploy fails loudly at boot rather than quietly
serving with the guard rails down.

### How the certificate is obtained

There is a bootstrap problem worth understanding, because it is the part that
most hand-written deployments get stuck on: **nginx will not start without a
certificate, and certbot cannot obtain one without nginx serving the ACME
challenge.**

The script breaks the cycle by starting nginx on a throwaway self-signed
certificate, letting certbot use the webroot that nginx is now serving, then
replacing the certificate and reloading. Renewal then uses the same webroot,
so nothing has to be stopped to renew — the cron job at 03:00 renews and
reloads in place.

### Doing it by hand

If you would rather not run the script:

```bash
./scripts/generate-secrets.sh --write
$EDITOR .env                                   # set the values listed above
sed "s|\${NEXUS_DOMAIN}|api.your-domain.com|g" \
    infrastructure/nginx/nginx.conf.template > infrastructure/nginx/nginx.conf

docker compose up -d postgres redis
docker compose run --rm migrate
docker compose up -d --build api worker scheduler
docker compose --profile full up -d nginx
docker compose run --rm certbot certonly --webroot -w /var/www/certbot \
    -d api.your-domain.com --email you@your-email.com --agree-tos --no-eff-email
docker compose exec nginx nginx -s reload
```

`infrastructure/nginx/nginx.conf` is git-ignored: the template is tracked, the
rendered file carries your domain and stays out of the repository.

### After the first deployment

1. **Back up `ENCRYPTION_KEY`** from `.env`, somewhere that is not this server.
2. **Set `API_BASE_URL`** to `https://api.your-domain.com/` in the GitHub
   repository variables, so CI builds an APK that talks to this server.
3. **Claim the owner account.** A fresh deployment has no administrator, and
   every admin route requires one. Register through the normal endpoint, then
   promote that account with `ADMIN_BOOTSTRAP_SECRET`:

   ```bash
   curl -sS -X POST https://api.your-domain.com/api/v1/auth/register \
     -H 'Content-Type: application/json' \
     -d '{"username":"you","email":"you@example.com","password":"a-strong-one"}'

   curl -sS -X POST https://api.your-domain.com/api/v1/admin/bootstrap \
     -H 'Content-Type: application/json' \
     -d "{\"secret\":\"$(grep ^ADMIN_BOOTSTRAP_SECRET= .env | cut -d= -f2-)\",\"identifier\":\"you\"}"
   ```

   `POST /api/v1/admin/bootstrap` **closes permanently** the moment any user
   holds an admin role — from then on it answers 409 to everything, including
   a correct secret, so it cannot be used to guess one afterwards. It only
   raises the privileges of an account that already exists; it never creates
   one.

## Why Postgres and Redis have no published ports

`docker-compose.yml` gives neither a `ports:` section, and they sit on an
`internal: true` network. They are reachable from `api`, `worker`, `scheduler`
and `migrate`, and from nowhere else.

A published `5432:5432` is reachable from the whole Internet, regardless of the
host firewall, because Docker writes its own iptables rules that bypass UFW.
Exposed Postgres and Redis instances are found and compromised within hours.

For a database shell, go through the container:

```bash
docker compose exec postgres psql -U nexus -d nexusvpn
docker compose exec redis redis-cli
```

For a GUI client, tunnel over SSH:

```bash
ssh -L 5432:localhost:5432 user@server -N
```

## Updating

```bash
git pull
docker compose build api worker scheduler
docker compose run --rm migrate
docker compose --profile full up -d
```

Migrations run as a separate one-shot service, not on API startup: two API
replicas starting together would otherwise race to migrate the same database.

## Secrets in production

`.env` on the host is the simplest correct option: mode 600, owned by the
deploy user, never in git, never in the image, backed up separately.

At larger scale, use a secret manager (Vault, AWS Secrets Manager, Docker
Swarm secrets) and inject the values as environment variables. Nothing in the
code reads a file path for a secret, so any injection mechanism works without
a change.

### Backing up `ENCRYPTION_KEY`

Keep it **outside** the database backups. A backup containing both the
ciphertext and the key protects nothing. Store it in a password manager or a
sealed envelope, and verify you can read it before you need it.

Losing it means every stored panel credential must be re-entered by hand.

## Backups

```bash
# Encrypted nightly dump
docker compose exec -T postgres pg_dump -U nexus nexusvpn \
  | gzip \
  | gpg --encrypt --recipient ops@example.com \
  > /backup/nexusvpn-$(date +%F).sql.gz.gpg
```

Keep daily for 7 days, weekly for 4 weeks, monthly for 12 months. Copy them
off the production host — a backup that dies with the server is not a backup.

## Disaster recovery

1. **Provision** a new host with Docker, and clone the repository.
2. **Restore the environment**: copy `.env` from your secret store. Critically,
   restore the *same* `ENCRYPTION_KEY`, or panel credentials will not decrypt.
3. **Restore the database**:
   ```bash
   docker compose up -d postgres
   gpg --decrypt backup.sql.gz.gpg | gunzip \
     | docker compose exec -T postgres psql -U nexus -d nexusvpn
   ```
4. **Run migrations**: `docker compose run --rm migrate`
5. **Start services**: `docker compose --profile full up -d`
6. **Verify**:
   - `curl https://api.example.com/health`
   - `/health/database` and `/health/redis` return 200
   - Log in as a test user
   - Run a panel connection test from the admin panel — this is what proves
     `ENCRYPTION_KEY` was restored correctly

Practise this on a throwaway host before you need it. A recovery procedure that
has never been run is a hypothesis.

## Monitoring

Set `SENTRY_DSN` for error tracking, and `METRICS_ENABLED=true` to expose
Prometheus metrics. Alert on: `/health` failing, 5xx rate, panel connection
failures, and refresh-token reuse detections — the last one means a token
leaked, and a spike means it is being exploited.

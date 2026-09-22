# Deployment

## Prerequisites

- A Linux host with Docker and Docker Compose v2
- A domain with DNS pointing at the host (`api.example.com`)
- Ports 80 and 443 open; **nothing else**

## First deployment

```bash
git clone https://github.com/<your-account>/NexusVPN.git
cd NexusVPN

./scripts/generate-secrets.sh --write     # creates .env, mode 600
$EDITOR .env                              # set the production values below
```

Production `.env` must have:

```ini
APP_ENV=production
APP_DEBUG=false
PUBLIC_API_URL=https://api.example.com
CORS_ORIGINS=https://admin.example.com
ALLOWED_HOSTS=api.example.com
```

The backend refuses to start if `APP_ENV=production` is combined with
`APP_DEBUG=true`, a wildcard in `CORS_ORIGINS` or `ALLOWED_HOSTS`, a plain-HTTP
`PUBLIC_API_URL`, or a SQLite database. That refusal is the point: a
misconfigured production deploy fails loudly at boot rather than quietly
serving with the guard rails down.

### TLS

Obtain a certificate before starting nginx:

```bash
mkdir -p infrastructure/nginx/certs
certbot certonly --standalone -d api.example.com -d admin.example.com
cp /etc/letsencrypt/live/api.example.com/fullchain.pem infrastructure/nginx/certs/
cp /etc/letsencrypt/live/api.example.com/privkey.pem  infrastructure/nginx/certs/
chmod 600 infrastructure/nginx/certs/privkey.pem
```

`infrastructure/nginx/certs/` is git-ignored. Replace `api.example.com` in
`infrastructure/nginx/nginx.conf` with your domain.

### Start

```bash
docker compose up -d postgres redis
docker compose run --rm migrate
docker compose --profile full up -d
docker compose ps
curl https://api.example.com/health
```

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

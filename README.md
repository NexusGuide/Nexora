# Nexus VPN

A professional Xray VPN client and service platform: an Android app, a backend
API, and a web admin panel. Users register, buy a plan, pay, and receive a
working config automatically — no Telegram required.

> **Status: phase 4 of 8.** The backend is now functionally complete for the
> purchase-to-connect path, and the Android client covers sign-in, store,
> services and configs. The VPN engine, payment gateways and the admin panel
> are not built yet. Nothing here pretends to work: an unfinished integration
> returns `501 NOT_IMPLEMENTED` rather than a fake success, and the app's
> Connect button is disabled with an explanation. See [Roadmap](#roadmap).

```
PUBLIC REPOSITORY != PUBLIC SECRETS
```

This repository is designed to be public. Every secret lives in the runtime
environment of a real deployment, never in git. See [SECURITY.md](SECURITY.md).

---

## Architecture

The Android app never talks to an Xray panel. It talks to the backend over
HTTPS; the backend holds the panel credentials, encrypted at rest.

```
Android App
    |  HTTPS
    v
Backend API ──┬── Auth
              ├── Users / Store / Orders / Payments
              ├── Subscriptions / Configs
              └── Panel Manager
                       ├── PasarGuard   (implemented)
                       ├── X-UI         (phase 8)
                       ├── Marzban      (phase 8)
                       └── Custom       (operator-supplied)
```

Business logic calls `panel_manager.adapter_for(panel)` and then the
`PanelAdapter` interface. No module imports a specific panel, so a new panel is
a new adapter rather than an edit to the subscription flow.

## Features in this phase

- **Authentication** — Argon2id passwords, access/refresh tokens signed with
  separate secrets, refresh-token rotation with reuse detection, per-account and
  per-IP lockout.
- **Panel credential encryption** — Fernet (AES-128-CBC + HMAC-SHA256) at rest,
  keyed from the environment; credentials never appear in an API response, a
  log, or a `repr()`.
- **Secret hygiene** — `.gitignore`, a pre-commit hook, CI scanning over full
  git history, and runtime config validation that refuses placeholder secrets.
- **Structured logging** — JSON with `request_id`, `user_id`, endpoint, status
  and latency, with mandatory redaction applied at the handler.
- **Docker topology** — api, worker, scheduler, postgres, redis, nginx, with the
  datastores unreachable from outside the compose network.
- **Idempotent ordering** — a unique constraint, a row lock and a plan snapshot,
  so a retried request or a replayed payment callback never charges twice or
  provisions twice.
- **Background provisioning** — a Redis Streams queue with at-least-once
  delivery, retry with backoff and a dead-letter stream. A panel outage delays
  a subscription; it never corrupts one.
- **Enforced device limits, password reset, rate limiting** and wired-up
  Sentry/Prometheus, plus a verifying backup script.

## Requirements

- Docker and Docker Compose v2 (recommended), or
- Python 3.11+, PostgreSQL 14+, Redis 7+ for a local install
- Android Studio (phase 4), Node 20+ (phase 7)

## Installation

```bash
git clone https://github.com/<your-account>/NexusVPN.git
cd NexusVPN

# 1. Install the git hooks that block secrets from being committed.
./scripts/install-hooks.sh

# 2. Create .env with freshly generated secrets.
./scripts/generate-secrets.sh --write

# 3. Fill in the rest of .env by hand (payment keys, domains, CORS).
$EDITOR .env

# 4. Start the stack.
docker compose up -d postgres redis
docker compose run --rm migrate
docker compose up -d api
```

The API is then on `http://localhost:8000`, with interactive docs at
`/docs` (development only — disabled when `APP_ENV=production`).

```bash
curl http://localhost:8000/health
```

### Local development without Docker

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

cp ../.env.example .env && ../scripts/generate-secrets.sh   # paste the output
export $(grep -v '^#' .env | xargs)

alembic upgrade head
uvicorn app.main:app --reload
```

## Environment variables

`.env.example` is the authoritative list. The ones without a safe default:

| Variable | Purpose | How to generate |
|---|---|---|
| `DATABASE_URL` | Async Postgres URL | — |
| `JWT_SECRET` | Signs access tokens | `openssl rand -hex 32` |
| `JWT_REFRESH_SECRET` | Signs refresh tokens (must differ) | `openssl rand -hex 32` |
| `ENCRYPTION_KEY` | Encrypts panel credentials | `python -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"` |
| `ADMIN_BOOTSTRAP_SECRET` | Creates the first owner account | `openssl rand -hex 32` |

`./scripts/generate-secrets.sh` produces all of them at once.

The backend **will not start** if any is missing, shorter than 32 characters,
left at a placeholder, or reused across the two JWT secrets. In production it
also rejects `APP_DEBUG=true`, wildcard CORS, wildcard hosts and a plain-HTTP
public URL.

## Database migrations

```bash
docker compose run --rm migrate                      # apply
docker compose run --rm api alembic downgrade -1     # roll back one
docker compose run --rm api alembic revision --autogenerate -m "add plans"
```

The URL comes from `DATABASE_URL`; `alembic.ini` deliberately leaves
`sqlalchemy.url` empty so no password can be committed there.

## Tests

```bash
cd backend && pytest
```

The suite covers configuration rejection of weak secrets, password hashing,
token separation and expiry, panel-credential encryption, log redaction, and
the authentication flows including lockout and refresh reuse detection.

## API

All endpoints live under `/api/v1`. Responses use one envelope:

```json
{ "success": true, "data": {}, "request_id": "..." }
```

```json
{ "success": false,
  "error": { "code": "SUBSCRIPTION_EXPIRED", "message": "Subscription has expired" },
  "request_id": "..." }
```

Implemented:

| Area | Endpoints |
|---|---|
| Auth | `POST /auth/register`, `/auth/login`, `/auth/refresh`, `/auth/logout`, `/auth/forgot-password`, `/auth/reset-password`, `/auth/verify`, `GET /auth/me` |
| User | `GET`/`PATCH /me`, `GET /me/devices`, `DELETE /me/devices/{id}` |
| Store | `GET /plans`, `/plans/{id}` |
| Orders | `POST /orders`, `GET /orders`, `/orders/{id}`, `POST /orders/{id}/cancel` |
| Subscriptions | `GET /subscriptions`, `/subscriptions/{id}`, `POST /subscriptions/{id}/renew`, `/subscriptions/{id}/refresh` |
| Configs | `GET /configs`, `/configs/{id}`, `POST /configs/{id}/activate`, `DELETE /configs/{id}` |
| Admin | `POST /admin/panels`, `/admin/panels/{id}/test`, `/admin/servers`, `/admin/orders/{id}/confirm-payment` |
| Health | `GET /health`, `/health/database`, `/health/redis`, `/health/panels` |

`POST /orders` is idempotent: send an `idempotency_key` and a retried request
returns the original order with 200 rather than creating a second one.

Full schema at `/docs` in development.

## Getting the Android app

You do not need an Android toolchain to get an installable APK. Push, then
open **Actions → Android**, and download the `nexora-debug-*.apk` artifact from
the finished run. A `v*` tag attaches the same APK to the GitHub release.

It is a debug build, signed with the standard debug key. The release keystore
is not in this repository and never will be, so CI cannot sign a production
build — see [docs/android.md](docs/android.md) for building locally and for
producing a signed release yourself.

## Deployment

See [docs/deployment.md](docs/deployment.md). In short: put nginx or Caddy in
front with a real certificate, keep Postgres and Redis off the public Internet,
set `APP_ENV=production`, and back up `ENCRYPTION_KEY` somewhere other than the
database server.

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| 1 | Repository, Docker, Postgres, Redis, FastAPI, auth | **Done** |
| 2 | Users, plans, subscriptions, orders | **Done** |
| 3 | Panel manager, PasarGuard, config system | **Done** |
| 4 | Android app: login, home, services, store | **Done** (builds in CI) |
| 5 | VPN engine (Xray core), connect, ping | **In progress**: connect, ping, notification; traffic stats pending |
| 6 | Payment, renewal, expiration, notifications | Not started |
| 7 | Admin panel, RBAC, logs, analytics | **Web panel done** (`/admin`); analytics pending |
| 8 | Multi-panel (X-UI, Marzban), auto server | Not started |

## Documentation

- [architecture.md](docs/architecture.md)
- [security.md](docs/security.md)
- [deployment.md](docs/deployment.md)
- [panel-adapters.md](docs/panel-adapters.md)
- [workers.md](docs/workers.md)
- [android.md](docs/android.md)
- [admin-panel.md](docs/admin-panel.md)
- [contributing.md](docs/contributing.md)

## License

[MIT](LICENSE). The Android app bundles the Xray core under LGPL-3.0 /
MPL-2.0; see [NOTICE.md](NOTICE.md).

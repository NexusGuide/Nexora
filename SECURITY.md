# Security Policy

## The governing principle

```
PUBLIC REPOSITORY != PUBLIC SECRETS
```

The whole source tree is meant to be public. Every secret lives only in the
runtime environment of a real deployment. Anyone can clone this repository and
run the platform with their own configuration; nobody can learn anything about
somebody else's deployment by reading it.

## What must never enter the repository

| Category | Examples | Where it belongs instead |
|---|---|---|
| Token signing keys | `JWT_SECRET`, `JWT_REFRESH_SECRET` | `.env` / secret manager |
| Data-at-rest key | `ENCRYPTION_KEY` | `.env` / secret manager, backed up off-server |
| Database credentials | `DATABASE_URL`, `POSTGRES_PASSWORD` | `.env` / secret manager |
| Payment gateway keys | Zibal / Zarinpal / IDPay merchant keys | `.env` / secret manager |
| Panel credentials | PasarGuard / X-UI / Marzban user + password + API key | **Encrypted in the database**, entered via the admin panel |
| Push credentials | FCM service-account JSON | File on the server, path via `FCM_CREDENTIALS_FILE` |
| Signing material | Android keystore, `keystore.properties`, TLS private keys | CI secret store / server filesystem |
| Real data | Production dumps, user exports, logs | Never in git |

`.env.example` carries descriptions in angle brackets, not working values.

## The four layers that enforce this

1. **`.gitignore`** — every secret-bearing file pattern is excluded before it
   can be staged: `.env*` (except the example), keys, certificates, `*.db`,
   dumps, backups, logs, keystores, `google-services.json`, service accounts.
2. **Pre-commit hook** (`.githooks/pre-commit`, installed by
   `scripts/install-hooks.sh`) — blocks forbidden file types, runs
   `gitleaks protect --staged`, and applies a fallback pattern check when
   gitleaks is not installed locally.
3. **CI secret scan** (`.github/workflows/security.yml`) — runs gitleaks over
   the **full git history** on every push and pull request, so a secret that
   was committed and later deleted still fails the build.
4. **Runtime validation** (`backend/app/core/config.py`) — the backend refuses
   to start when a secret is missing, too short, left at a placeholder, or
   reused across `JWT_SECRET` / `JWT_REFRESH_SECRET`. In production it
   additionally rejects `APP_DEBUG=true`, wildcard CORS and wildcard hosts.

## Panel credentials

Panel credentials are the most sensitive data the platform holds, because they
grant control over a live Xray panel.

- They are **never** shipped in the Android APK. The app talks only to this
  backend over HTTPS; the backend talks to panels.
- They are stored encrypted at rest with AES-128-CBC + HMAC-SHA256 (Fernet),
  keyed by `ENCRYPTION_KEY`, which exists only in the environment.
- They are never returned by any API response. Schemas expose
  `has_credentials: bool`, never the values.
- They are never logged: the logging filter redacts known-sensitive keys and
  token-shaped strings before a record is emitted.

**`ENCRYPTION_KEY` is not recoverable.** If it is lost, stored panel
credentials cannot be decrypted and must be re-entered by hand. Back it up
separately from the database backups — a backup that contains both the
ciphertext and the key protects nothing.

## Authentication

- Passwords are hashed with Argon2id (memory-hard, per-password salt). Plain
  passwords are never stored or logged.
- Access tokens are short-lived (default 30 minutes); refresh tokens are
  long-lived (default 30 days) and **rotate on every use**.
- Refresh tokens are stored as SHA-256 hashes, so a database leak does not
  hand over usable sessions.
- Reuse of an already-rotated refresh token is treated as theft: the entire
  token family for that user is revoked immediately.
- Login is rate-limited per account and per IP, with lockout after repeated
  failures.

## Reporting a vulnerability

Do not open a public issue for a security problem. Report it privately through
GitHub's *Security → Report a vulnerability* on this repository, or by email to
the maintainer. Please include reproduction steps and the affected version or
commit. You will get an acknowledgement within 72 hours.

## If a secret is ever exposed

Rotating is mandatory — deleting the commit is not enough, because the value
may already be cloned or cached.

1. **Rotate the value at its source first** (gateway dashboard, panel, database
   password, new `openssl rand -hex 32`).
2. Deploy the new value to the environment.
3. Revoke what the old value could reach (active sessions, API keys).
4. Only then clean history, with `git filter-repo` or the BFG, and force-push.
5. Ask GitHub Support to purge cached views of the affected commits.

For `ENCRYPTION_KEY` specifically, rotation requires re-encrypting stored panel
credentials — see `docs/security.md` for the procedure.

## Supported versions

While the project is pre-1.0, only the latest commit on `main` receives
security fixes.

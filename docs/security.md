# Security design

This document explains *how* the guarantees in [SECURITY.md](../SECURITY.md)
are implemented, and what to do when something goes wrong.

## 1. Why a public repository is safe here

Three properties, each enforced by code rather than by discipline:

1. **No secret has a default.** `backend/app/core/config.py` declares every
   secret as a required field. There is no `jwt_secret: str = "dev-secret"`
   anywhere, so there is nothing to accidentally ship.
2. **A placeholder is an error, not a value.** The validator rejects
   `<angle-bracket>` text, `changeme`, `placeholder`, values under 32
   characters, and values with fewer than 8 distinct characters. A copied
   `.env.example` cannot boot.
3. **Secrets that must differ, must differ.** `JWT_SECRET` and
   `JWT_REFRESH_SECRET` sharing a value would let a refresh token be presented
   as an access token. The config layer refuses it.

Run `pytest tests/test_config_security.py` to see each rule exercised.

## 2. Defence in depth against a committed secret

| Layer | Runs when | Catches |
|---|---|---|
| `.gitignore` | Always | Secret-bearing files, before staging |
| `.githooks/pre-commit` | `git commit` | Forbidden file types; gitleaks on staged diff; fallback regexes |
| `.github/workflows/security.yml` | Push / PR / weekly | gitleaks over **full history**; tracked forbidden files; filled-in `.env.example` |
| `config.py` validators | Process start | A weak or placeholder secret reaching runtime |

The CI scan uses `fetch-depth: 0` deliberately. A secret that was committed on
Monday and deleted on Tuesday is still in the history and still compromised;
scanning only the tip would report a clean repository.

## 3. Panel credentials

### Storage

Panel credentials exist in three states, and only one of them is at rest:

```
admin panel form  ──encrypt──▶  encrypted_* columns  ──decrypt──▶  PanelCredentials
   (in flight,                     (at rest,                        (in memory,
    over TLS)                       ciphertext)                      one operation)
```

`app/panels/manager.py` is the only module that decrypts. Keeping that surface
to one file is what makes it auditable: to check that no code path leaks a
credential, you read one file, not the whole service layer.

### Why they never appear in a response

`Panel` exposes `has_credentials: bool`. There is no schema field that could
serialize `encrypted_password`, so a future endpoint cannot leak one by
forgetting to exclude it — the field simply does not exist on the way out.

### Why they never appear in a log

Two mechanisms:

- `PanelCredentials.__repr__` and `__str__` are overridden to print
  `[REDACTED]`. A traceback that includes the dataclass prints nothing useful.
- `RedactionFilter` is attached to the root log handler and scrubs
  known-sensitive keys, `Bearer` tokens, JWT-shaped strings, connection URLs
  with inline passwords, and Telegram bot tokens.

Redaction at the *handler* rather than the call site matters: it protects log
lines nobody remembered to audit, including ones from third-party libraries.

### Key rotation

Rotating `ENCRYPTION_KEY` requires re-encrypting everything it protects, since
Fernet gives no way to decrypt with a key that did not encrypt:

1. Put the service in maintenance mode.
2. Back up the database.
3. With **both** the old and new keys available, for each panel: decrypt with
   the old key, encrypt with the new, write back in one transaction.
4. Deploy the new key, restart, and verify with a panel connection test.
5. Destroy the old key.

Until a rotation script ships (phase 7), do this with a one-off script that
imports `CredentialCipher` twice with different keys.

If the key is *lost* rather than rotated, the credentials are unrecoverable.
`PanelManager` detects this and raises `PANEL_CREDENTIALS_UNREADABLE` with an
instruction to re-enter them, rather than failing with a generic crypto error.

## 4. Authentication

### Password storage

Argon2id, `time_cost=3`, `memory_cost=64 MiB`, `parallelism=4`. Memory-hardness
is the point: it denies an attacker the GPU parallelism that makes bcrypt and
PBKDF2 cracking cheap. Hashes are re-evaluated on each login and transparently
upgraded when the parameters change.

### Token model

| | Access | Refresh |
|---|---|---|
| Lifetime | 30 min (default) | 30 days (default) |
| Secret | `JWT_SECRET` | `JWT_REFRESH_SECRET` |
| Stored | Not stored | SHA-256 hash |
| `typ` claim | `access` | `refresh` |

The `typ` claim is verified explicitly *and* the secrets differ, so confusing
the two requires two independent failures.

### Rotation and reuse detection

Every refresh issues a new token and marks the old one used. Both carry the
same `family_id`.

Presenting an already-used token means one of two things: the client retried,
or the token was stolen and the attacker got there first. Neither can be
distinguished, so the safe action is the same — revoke the whole family. The
legitimate user logs in again; the attacker's stolen token is dead.

```
login ──▶ T1
T1 used ──▶ T2   (T1 marked used)
T1 used again ──▶ family revoked; T2 dies too
```

### Account enumeration

A wrong username and a wrong password return the same `AUTHENTICATION_FAILED`
with the same message. The service verifies against a dummy Argon2 hash when
the user does not exist, so both paths take comparable time — an attacker gains
nothing from either the response or the latency. Registration conflicts on
username, email and phone all return one message for the same reason.

### Brute force

Failures are recorded per identifier and per IP. Once `LOGIN_MAX_ATTEMPTS`
failures occur within `LOGIN_LOCKOUT_MINUTES`, even the correct password is
refused for the rest of the window. nginx adds an edge limit of 5 requests per
minute on the auth endpoints, in front of the application's own.

## 5. Transport and headers

The API sets `X-Content-Type-Options`, `X-Frame-Options: DENY`,
`Referrer-Policy: no-referrer`, a restrictive `Permissions-Policy`, and
`Content-Security-Policy: default-src 'none'; frame-ancestors 'none'` (the API
serves JSON — nothing should ever be executed or framed). HSTS is added when
`APP_ENV=production`.

`X-Forwarded-For` is overwritten by nginx, never appended. If it were appended,
a client could prepend a forged address and evade IP-based rate limiting.

## 6. Error handling

Clients get a stable error code, a safe message and a `request_id`. Stack
traces, driver messages and upstream panel responses stay in the (redacted)
logs. A panel's own error text is never forwarded verbatim — it can contain
internal hostnames and tokens.

Validation errors return field names and constraints but never submitted
values, since a rejected body often contains the password.

## 7. Threat model

Addressed here:

- Repository is public → no secret in it, verified continuously over history.
- Database is dumped or leaked → passwords are Argon2id, refresh tokens are
  hashes, panel credentials are ciphertext whose key is not in the database.
- APK is decompiled → it contains no panel credential, because the app never
  holds one.
- Credential stuffing → lockout, edge rate limits, no enumeration oracle.
- Stolen refresh token → rotation with reuse detection bounds the damage.
- Log aggregation is breached → logs carry no secrets by construction.

Not yet addressed (later phases):

- Payment webhook signature verification (phase 6)
- Admin RBAC enforcement beyond the dependency scaffolding (phase 7)
- Audit-log write coverage for every sensitive action (phase 7)
- Per-endpoint Redis rate limiting (phase 2)

# Architecture

## The one rule that shapes everything

**The Android app never talks to an Xray panel.**

Panel credentials grant full control over a panel. An APK is a file on a
stranger's phone: anything inside it is public, whatever obfuscation is
applied. So the app holds no panel credential, and the backend is the only
thing that ever authenticates to a panel.

```
Android App ──HTTPS──▶ Backend API ──▶ Panel Manager ──▶ PasarGuard / X-UI / Marzban
 (user tokens)          (business logic,     (decrypts
                         panel credentials    credentials
                         encrypted at rest)   per operation)
```

Everything else follows: the app's API surface is about *subscriptions and
configs*, not about panel users; the backend owns the mapping between the two.

## Layers

```
backend/app/
├── api/           HTTP: routing, middleware, dependencies. No business rules.
│   ├── deps.py        current user, RBAC guards, client metadata
│   ├── middleware.py  request IDs, access logs, security headers
│   └── v1/            versioned routers
├── core/          Cross-cutting: config, crypto, logging, error types.
├── db/            Engine, session factory, custom column types.
├── models/        SQLAlchemy tables.
├── schemas/       Pydantic request/response contracts.
├── services/      Business rules. The layer that owns invariants.
├── panels/        Panel abstraction + adapters.
├── payments/      Payment provider abstraction (phase 6).
└── workers/       Background jobs and scheduler.
```

The dependency direction is one-way: `api → services → models`. A router never
queries a table directly, and a service never imports FastAPI. That is what
lets the same service back an HTTP endpoint, a worker job and a test without
change.

## Why the panel abstraction exists

The spec requires PasarGuard now and X-UI, Marzban and custom panels later. If
the subscription flow called PasarGuard directly, adding Marzban would mean
editing the subscription flow — and every edit to a working payment path is a
risk.

Instead:

```python
adapter = panel_manager.adapter_for(panel)
async with adapter:
    panel_user = await adapter.create_user(username, traffic_limit_bytes=..., expire_at=...)
```

`PanelAdapter` (`app/panels/base.py`) defines eleven methods. `PanelManager`
picks the implementation from the panel row and hands it decrypted credentials.
Adding a panel means writing one class; nothing above it changes.

Panels that are not implemented yet are registered as *unimplemented* and raise
`NotImplementedYetError` (HTTP 501). They are not stubbed with fake success —
a stub that returns a plausible object is how a user ends up paying for a
subscription that was never created.

## Idempotency

Two places where "do it twice" must not mean "charge twice" or "create twice":

- `PasarGuardAdapter.create_user` checks for an existing user first and returns
  it rather than creating a duplicate. A retried job is therefore safe.
- Orders and payments (phase 2/6) will use a database transaction plus a Redis
  lock keyed on the order, with the payment provider's transaction id as the
  idempotency key. A boolean `is_paid` flag is not sufficient — the spec is
  explicit about this (rule 56) and it is right: two concurrent callbacks can
  both read `false` before either writes `true`.

## Request lifecycle

```
nginx  ──▶ TrustedHost ──▶ CORS ──▶ RequestContext ──▶ SecurityHeaders ──▶ router
             │                         │                                      │
             │                         ├─ assigns request_id                  ├─ dependency: current user
             │                         └─ logs endpoint/status/latency        └─ service call
             └─ rejects unknown Host                                                  │
                                                                                      ▼
                                                                              AppError handler
                                                                              → error envelope
```

Every response carries `X-Request-ID`, and every log line for that request
carries the same value, so a user reporting "it failed at 14:32" can be traced
to exact log lines.

## Data model

Phase 1 tables:

- `users`, `user_devices` — identity and device limits
- `refresh_tokens` — hashed, with `family_id` for rotation
- `login_attempts` — brute-force lockout
- `panels`, `servers`, `server_groups` — infrastructure
- `audit_logs` — sensitive admin actions

Phase 2 adds `plans`, `plan_servers`, `subscriptions`, `configs`, `orders`,
`payments`, `wallets`, `wallet_transactions`; phase 6/7 add `support_tickets`,
`support_messages`, `notifications`.

Enum columns use `EnumString` (`app/db/types.py`) rather than a native database
enum: it stores a VARCHAR and returns the Python enum member. A plain `String`
column would return a bare `str`, which silently breaks identity checks like
`user.status is UserStatus.ACTIVE` while equality still passes — the kind of
mismatch that becomes an authorization bug. A native DB enum would make adding
a value a schema migration.

## Background work

Heavy or slow operations do not run inside a request. Creating a panel user
after payment involves an HTTP call to a third-party panel that may be slow or
down; doing it inline would make the payment callback time out and the gateway
retry.

```
payment verified ──▶ enqueue create_panel_user ──▶ worker ──▶ panel
                                                      │
                                                      └─ on failure: retry with backoff
```

The worker and scheduler processes exist in this phase with their job handlers
marked TODO, so the deployment topology is real from the start.

## Deliberate non-goals for phase 1

- No plan, order, payment or subscription endpoints.
- No admin panel.
- No Android code.
- No Redis rate limiting (nginx and the database-backed lockout cover the auth
  endpoints; per-endpoint limits land with phase 2).

Each is absent rather than faked.

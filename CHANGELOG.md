# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While the project is below `0.1.0`, the API is unstable and any release may
change it without a deprecation period.

## [Unreleased]

### Added — phase 2: plans, orders and subscriptions

- `Plan`, `PlanServer`, `Order`, `Payment`, `Subscription` and `Config` tables,
  with the indexes the spec requires (rule 55) and money stored as `NUMERIC`,
  never float.
- **Order idempotency** backed by a UNIQUE constraint on `idempotency_key`, so
  a duplicate submission loses the insert race in the database rather than
  relying on a check-then-insert that two requests can both pass. A repeated
  `POST /orders` returns the original order with 200 instead of 201.
- **Paid exactly once**: `mark_paid` takes a row lock and reports whether it
  actually transitioned, so a replayed gateway callback cannot provision a
  second subscription.
- **Plan snapshot** stored on each order, so editing a plan's price or quota
  does not retroactively change what an existing customer bought.
- Subscription lifecycle: create, activate, renew, suspend, resume, expire,
  and usage recording that suspends on an exhausted quota. Early renewal
  extends from the current expiry rather than from today.
- Endpoints: `/me`, `/me/devices`, `/plans`, `/orders`, `/subscriptions`.
  Revoking a device also revokes the refresh tokens bound to it.
- 27 further tests covering idempotency, the state machines and ownership
  isolation.

### Notes
- Panel provisioning still belongs to phase 3, so a paid order creates a
  `PENDING` subscription and `/subscriptions/{id}/refresh` and `/configs`
  return `501` rather than a fake result.

## [0.0.1] — 2026-09-22

First tagged release. Phase 1 of 8: the foundation a public repository needs
before any business logic is worth writing.

### Added

**Security and configuration**
- Settings validation that refuses to start on a missing, too-short,
  placeholder or reused secret, and that rejects unsafe production
  configuration (`APP_DEBUG=true`, wildcard CORS or hosts, plain-HTTP public
  URL, SQLite).
- Argon2id password hashing with transparent rehash on parameter change.
- Access and refresh tokens signed with *separate* secrets, with an explicit
  `typ` claim check.
- Refresh-token rotation with reuse detection: a replayed token revokes its
  whole family.
- Per-account and per-IP login lockout.
- Panel credentials encrypted at rest with Fernet, decrypted only inside
  `PanelManager`, never returned by an API or printed by a `repr`.
- Structured JSON logging with redaction applied at the log handler, so it does
  not depend on call sites remembering.
- Security headers, trusted-host and CORS middleware; request IDs on every
  response and log line.

**Panel integration**
- `PanelAdapter` interface with eleven methods, normalising traffic, expiry and
  configs across panel types.
- PasarGuard adapter with idempotent user creation.
- X-UI, Marzban and Custom registered as unimplemented, returning
  `501 NOT_IMPLEMENTED` rather than a fake success.

**Infrastructure**
- `docker-compose.yml` with api, worker, scheduler, postgres, redis and nginx;
  Postgres and Redis publish no ports and sit on an internal network.
- nginx configuration with tighter rate limits on authentication endpoints.
- GitHub Actions for lint, tests, migration up/down, image build, and a secret
  scan over full git history.

**Repository hygiene**
- `.gitignore`, pre-commit hook, gitleaks configuration and
  `scripts/generate-secrets.sh`.
- Documentation: architecture, security, deployment, panel adapters,
  contributing.

### Known limitations
- No plans, orders, payments or subscriptions yet.
- No Android application and no admin panel.
- Worker and scheduler run as no-op processes; job handlers arrive in phase 2.
- The PasarGuard adapter's endpoint paths have not been exercised against a
  live panel — verify with a connection test before production use.

[Unreleased]: https://github.com/NexusGuide/Nexora/compare/v0.0.1...HEAD
[0.0.1]: https://github.com/NexusGuide/Nexora/releases/tag/v0.0.1

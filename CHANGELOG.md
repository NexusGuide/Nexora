# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While the project is below `0.1.0`, the API is unstable and any release may
change it without a deprecation period.

## [Unreleased]

### Added — building the Android app in CI

- **The Gradle wrapper was missing.** `android/` carried
  `gradle-wrapper.properties` but neither `gradle-wrapper.jar` nor the
  `gradlew`/`gradlew.bat` scripts, so `./gradlew` could not work in any
  environment — not locally, not in CI. All three are now committed, from
  Gradle's own `v8.11.1` tag.
- **`.github/workflows/android.yml`** builds the app on GitHub's runners,
  which already have the Android SDK. It runs the unit tests and lint,
  assembles a debug APK, uploads it as a run artifact, and attaches it to the
  release on a `v*` tag. Getting an installable APK now needs nothing
  installed locally.
  - `gradle/actions/wrapper-validation` checks the committed wrapper jar
    against the official Gradle checksums. A tampered wrapper jar executes
    arbitrary code with the repository checked out, so this is not optional.
  - The backend URL comes from a repository **variable**, not a secret,
    because a URL is configuration. No secret is involved anywhere in the
    workflow.
  - The APK is a *debug* build. The release keystore is absent from this
    repository by design, so CI cannot sign a production build and does not
    pretend to (spec rule 67).
- **A second job scans the built APK** for secret-shaped values, reading
  extracted strings rather than grepping text files — a text-only grep over a
  DEX passes unconditionally and proves nothing. On a hit it names the file
  and not the match, because CI logs are public.
- `backend.yml`: quoted the in-memory SQLite URL. A plain scalar ending in
  `:` is ambiguous YAML and strict parsers reject the file outright.

### Fixed — the executable bit, and how it went missing

- **`.githooks/pre-commit` and all three `scripts/*.sh` had been rewritten to
  mode 644** by a commit made from a Windows clone, where git does not track
  the executable bit. This was not cosmetic: **git skips a hook it cannot
  execute and says nothing**, so the secret-blocking pre-commit hook had
  quietly stopped running, and `./scripts/install-hooks.sh` — step one of the
  README's install instructions — would fail on a fresh clone. Modes restored.
- **A CI guard now asserts all five files are mode 755**, naming the file and
  the one-line fix. The failure mode is silent by nature, so it needed a check
  rather than a note in a document.
- **`.gitattributes`** normalises line endings to LF, with `*.bat`/`*.cmd`
  pinned to CRLF and binaries marked. A CRLF reaching a shebang line makes
  Linux look for an interpreter called `/bin/sh\r` and report "no such file or
  directory" against the script — an error that points nowhere near the cause.

### Added — phase 4: the Android client

- Kotlin + Jetpack Compose app under `android/`: sign-in, home, store,
  services, configs, profile and device management, in Persian and English
  with RTL, and light/dark themes. Clean architecture —
  feature → domain → data → core, one direction only.
- **Keystore-backed token storage.** `EncryptedSharedPreferences` keyed from
  the Android Keystore (spec rule 36). On a device where Keystore
  initialisation fails it retries after deleting the file and only then falls
  back to plain storage, recording it in `isUsingInsecureFallback` so the app
  can tell the user rather than silently downgrading.
- **A refresh authenticator that cannot sign users out by accident.** The
  backend revokes a whole token family when a refresh token is reused, so
  parallel 401s firing parallel refreshes would look like theft and kill every
  session. Refresh is serialised behind a lock and re-checked inside it. A
  *rejected* refresh ends the session; an *unreachable* one does not, because a
  timeout says nothing about whether the session is valid.
- **Device identity** from a random per-install UUID, never `ANDROID_ID`,
  IMEI or the advertising ID.
- Errors classified once into `AppError`, carrying whether a retry could help,
  with screens branching on the backend's stable error code rather than its
  message.
- Release builds are HTTPS-only by network security config; body-level HTTP
  logging is debug-gated and ProGuard strips `android.util.Log`.

### Notes
- **The build is unverified until the first CI run finishes.** The app has
  never been compiled. Static checks pass — imports resolve, every referenced
  string exists in both locales with matching format arguments, ViewModels are
  annotated, and every `libs.*` reference resolves in the version catalog —
  but expect compile errors on the first real build.
- Connecting is phase 5: the Connect button is present and disabled with an
  explanation, not a fake success.

### Added — phase 3.5: the gaps before shipping an app

- **Device limits are enforced.** A plan's device count was stored and sent to
  the panel but never checked, so a one-device plan allowed unlimited devices.
  Login now registers against the highest allowance among a user's active
  subscriptions (the highest, not the sum — stacking cheap plans must not beat
  an expensive cap), and a refusal returns the device list so the app can offer
  a revoke instead of a dead end. A device already registered is never blocked.
- **Password reset and email verification**, replacing three `501`s. Tokens are
  stored hashed, single-use and short-lived; using one revokes every session,
  because a reset is what someone does when they believe they are compromised.
  `forgot-password` answers identically whether or not the account exists.
- **Rate limiting** with a Redis sliding window, keyed on the user when
  authenticated and the IP otherwise, tighter on `/auth/`. It fails open: a
  limiter that takes the API down when its cache dies is a worse outage than
  the abuse it prevents.
- **Sentry and Prometheus** wired, having previously been settings that nothing
  read. Sentry events pass through the same redaction as logs, with PII and
  request bodies off. `/metrics` is restricted to private addresses and
  disabled unless `METRICS_ENABLED`.
- **`scripts/backup-db.sh`**: daily/weekly/monthly tiers, optional GPG
  encryption, a size floor, and `--verify` that restores the dump into a
  throwaway database and counts the tables — an unverified backup is a
  hypothesis.

### Added — phase 3: provisioning worker and config system

- **Job queue** on Redis Streams with a consumer group: at-least-once
  delivery, retry with widening backoff (5s → 10m), a dead-letter stream after
  5 attempts, and `xautoclaim` recovery of jobs a crashed worker left pending.
  `InMemoryQueue` implements the same protocol for tests and single-process
  development.
- **Distributed locks** so two workers cannot act on one subscription at once.
  Advisory only — correctness still rests on the idempotent handlers.
- **ProvisioningService**: paid order → pick a healthy server → create the
  panel account → fetch configs → ACTIVE. The panel account is created *before*
  the subscription is activated, so a panel failure leaves a PENDING
  subscription and a retry, never an active subscription with nothing behind
  it. A retry reuses the same panel username rather than orphaning accounts.
- **Config system**: a defensive URI parser (vless/vmess/trojan and others)
  that degrades rather than raising on malformed panel output, plus storage
  that replaces configs wholesale on refresh while preserving which one the
  user had chosen.
- **Worker and scheduler** are now real processes, not placeholders, and run by
  default in `docker-compose.yml`. Scheduled: expiry sweep (5m), usage sync
  (15m), panel health (10m).
- **Endpoints**: `GET /configs`, `/configs/{id}`, `POST /configs/{id}/activate`,
  `DELETE /configs/{id}`; `POST /subscriptions/{id}/refresh` now returns 202 and
  queues the panel round-trip instead of returning 501; `GET /health/panels`.
- **Admin endpoints** (RBAC-guarded, audit-logged) for registering panels and
  servers, testing a panel's credentials, and confirming a manual payment —
  enough to run the phase-3 flow before the admin panel exists in phase 7.
- 35 further tests, including the PasarGuard adapter against a mocked HTTP
  panel and provisioning under panel failure.

### Notes
- Payment gateways remain phase 6. `POST /admin/orders/{id}/confirm-payment`
  covers card-to-card and other offline payment in the meantime.
- Expiry disables the panel account *before* marking the subscription expired;
  if the panel is unreachable the subscription stays ACTIVE for the next sweep
  rather than being marked dead while its panel account still works.

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

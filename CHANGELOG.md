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

### Fixed — the worker could not reach any panel

The first live purchase paid, queued provisioning, and then failed five times
with *"No server is currently available for this plan"* — while the panel's
connection test had passed minutes earlier.

The cause was in `docker-compose.yml`. The `internal` network is deliberately
`internal: true`, so the database and Redis have no route to the Internet. But
the worker and scheduler were placed on **only** that network — and calling
panels on the Internet is the worker's entire job. The connection test passed
because it runs in the API container, which is also on `edge`. The scheduler's
health check, unable to reach anything, then marked the healthy panel
`UNREACHABLE`, and that removed every server behind it from provisioning. The
error named the last link in that chain, not the first.

- `worker` and `scheduler` join `edge`. Postgres and Redis stay on `internal`
  alone; that property is unchanged.
- `scripts/smoke-purchase.sh` now tests the panel **before** buying, so a
  reachability problem stops the run instead of stranding a paid order, and
  the test restores a panel a failed health check wrongly marked unreachable.

### Added — recovering a customer who paid and received nothing

The same failure exposed a gap with no workaround. Provisioning dead-letters
after its last retry, and because it failed before the panel account existed,
the subscription row was rolled back with it. The order was PAID with no
subscription, and nothing could recover it: `confirm-payment` only queues on
the transition to paid, and `/subscriptions/{id}/reprovision` needs a
subscription that does not exist. For a real customer that is "paid, got
nothing, and support cannot fix it without editing the database".

`POST /api/v1/admin/orders/{id}/reprovision` re-queues provisioning for a paid
order. It refuses unpaid orders, is audit-logged, and is safe to call on an
order that already succeeded, because provisioning is idempotent and reuses the
existing subscription and panel username. Four tests, including that a
customer cannot call it.

### Added — plan management, and an end-to-end purchase test

**There was no way to create a plan.** Panels and servers had admin endpoints;
plans did not, so the store was permanently empty and nothing could be bought
without writing SQL by hand — the same gap the panel endpoints were added to
close in phase 3, with plans overlooked.

- `POST /api/v1/admin/plans`, `GET /api/v1/admin/plans` (including inactive
  ones, unlike the store listing) and `PATCH /api/v1/admin/plans/{id}`.
  Traffic is given in GB, because that is how plans are sold and how panels
  express quotas, and converted to bytes once so no other layer has to agree
  what a GB is.
- Finance may set pricing alongside the operators who run the fleet; Support
  may not.
- A plan is retired by setting its status to `ARCHIVED`, never deleted: orders reference it,
  and each order already carries its own snapshot, so editing a plan cannot
  retroactively change what an existing customer bought.

**`scripts/smoke-purchase.sh`** runs the commercial path in one go against a
live deployment: create plan and server, register a throwaway customer, order,
confirm payment, wait for the worker to provision, print the configs. It sends
the order twice with the same idempotency key and reports whether the second
returned the original — the guard that stops a retry charging twice, checked
against the real database rather than a test double.

It prints only each config's protocol, name and host. `config_data` is the
connection URI and carries the subscriber's credential, so the run can be
shared without leaking one.

Its first live run failed in two instructive ways. The throwaway customer's
email used `.invalid`, a reserved name the email validator correctly refuses.
And by then it had already created a **free plan, ACTIVE and therefore listed
in the public store** — ordering requires ACTIVE, so the plan cannot simply be
created hidden. The script now archives its plan on exit however it ends,
archives any smoke plan an earlier failed run left behind, and reuses an
existing server rather than leaving duplicate healthy server rows that
provisioning would be free to pick for real customers.

### Verified — the PasarGuard adapter reaches a live panel

Open since phase 1, and the longest-standing risk in the project: the
adapter's endpoint paths were written from documentation and had never been
run against a real panel. `POST /api/v1/admin/panels/{id}/test` now passes
against a production PasarGuard instance.

What that proves and what it does not: authentication at `/api/admin/token`
and the base-URL handling are correct. It does **not** prove that user create,
read, reset and delete agree with that panel's API version — reachability is
one call. `docs/panel-adapters.md` now says exactly that rather than either
extreme.

### Added — `scripts/register-panel.sh`

Registering a panel meant a hand-written `curl` with the panel's admin
password inside it. That is wrong twice over: an argument lands in the shell
history and in `ps` output for every user on the box, and a password
containing a quote, a backslash or a `$` breaks the JSON or gets mangled by
the shell before it is sent.

The script prompts for credentials instead of taking them as arguments, builds
the request body with `json.dumps` so no password can break it, registers the
panel and runs the adapter test — clearing the screen first, so the result can
be shared without the credentials above it.

It also exists for a practical reason: pasting a multi-line block that contains
`read` does not work. The shell consumes the block's own later lines as answers
to the earlier prompts. A script file separates the code from the input.

### Added — there was no way to create the first administrator

`ADMIN_BOOTSTRAP_SECRET` was required at boot, validated for entropy and
placeholders, redacted in logs, documented in the README — and **read by
nothing**. A fresh deployment had no administrator, every admin route requires
one, and there was no route that could grant it. The only way in was editing
the database by hand. The deploy script cheerfully printed "create the first
admin account with ADMIN_BOOTSTRAP_SECRET", which could not be done.

`POST /api/v1/admin/bootstrap` now does it, built so it is a deployment step
rather than a back door:

- **It closes permanently.** The moment any user holds an admin role it
  answers 409 to everything. The window is the deployment window.
- **The closed check runs before the secret check**, so a late caller cannot
  use the status code to tell a correct secret from a wrong one — otherwise a
  closed endpoint becomes an oracle for guessing the secret it still holds.
- **It never creates accounts.** The operator registers through the normal
  endpoint; this only raises an existing account's privileges, so there is no
  second user-creation path.
- The secret is compared with `hmac.compare_digest`, and the promotion is
  audit-logged like every other admin action.

Eight tests at the HTTP layer rather than against the service beneath it,
because what matters is precisely what a caller on the Internet can do with
it — including that a wrong secret grants nothing and that the closed
endpoint's answer is byte-identical whether the secret was right or wrong.

### Fixed — deploy.sh aborted reading its own .env

The first real deployment got as far as writing `.env` and then died with
`./.env: line 32: syntax error near unexpected token 'newline'`.

The script read `.env` with `. ./.env`. But `.env` legitimately contains
`<angle-bracket>` placeholders for settings nobody has configured yet, and to
the shell `<` is a redirect — so sourcing aborts on the first one. Eight lines
in `.env.example` have this shape, so this was never going to work; it only
surfaced now because nothing had run the script end to end before.

- Values are now read with `grep`/`cut`, taking the **last** occurrence so the
  block the script appends wins over the template's placeholder, and stripping
  inline `# comments`. Compose reads the same file with its own parser and was
  never affected.
- An optional setting still reading `<something>` is an unfilled blank, not a
  value, so `PAYMENT_API_KEY`, `SMTP_*` and `SENTRY_DSN` are emptied rather
  than left holding literal placeholder text.
- **Ports 80 and 443 are now checked before any work**, printing what holds
  them. On a host already running a panel this otherwise surfaced halfway
  through as an unexplained container failure — or took the other service down.
- A missing email argument now explains itself with usage and an example,
  and an address that is not one is rejected before Docker is touched.

### Added — one-command deployment

Deploying meant following a page of manual steps, and one of them could not
work as written: the nginx config served the ACME challenge from
`/var/www/certbot`, but nothing mounted that directory and no certbot existed,
so the certificate had to be obtained out of band and copied in by hand.

- **`scripts/deploy.sh`** takes a domain and an email and does the rest:
  installs Docker if missing, generates the secrets, renders nginx for the
  domain, applies migrations, obtains the certificate, installs the renewal
  and backup cron jobs, and verifies over the public hostname — the only check
  that proves DNS, TLS, nginx and the API agree. Re-running is safe, and it
  never regenerates an existing `.env`, because rotating `ENCRYPTION_KEY`
  would orphan every stored panel credential.
- **The certificate bootstrap is handled properly.** nginx will not start
  without a certificate and certbot cannot get one without nginx serving the
  challenge; the script starts nginx on a throwaway self-signed certificate,
  issues the real one over the webroot, then reloads. Renewal uses the same
  webroot, so nothing stops to renew.
- **The domain is no longer hardcoded.** `nginx.conf` became
  `nginx.conf.template` with a `${NEXUS_DOMAIN}` placeholder; the rendered
  file is git-ignored, so no deployment's hostname is committed. Rendering
  uses `sed`, not `envsubst`, which is absent from a minimal Ubuntu image and
  would also try to expand nginx's own `$host`.
- DNS is checked **before** any work, since a wrong A record otherwise fails
  at the final step after several minutes.
- `certbot` service and the `certbot_conf` / `certbot_webroot` volumes added
  to compose; nginx mounts both read-only, because it serves them and certbot
  writes them.

### Verified — the stack runs on the upgraded dependencies

Booted the API on the new pins and exercised it over HTTP rather than trusting
the unit tests, since Starlette went from 0.48 to 1.6 and unit tests can pass
while middleware and lifespan break. Startup, the middleware chain, structured
logging, migrations, register, login, `/me`, RBAC refusal, and refresh-token
reuse detection all behave. The rate limiter fails open without Redis, as
designed.

One thing this proved that had never been checked: **`/auth/refresh` returns
`{access_token, refresh_token, token_type, expires_in}` directly under `data`,
which is exactly the shape the Android `TokenPairDto` expects.** The client's
refresh path had never been compared against a running server.

One honest imprecision found: on reuse detection the API answers "All sessions
were revoked", but an already-issued access token keeps working until it
expires, because it is a stateless JWT and only the refresh family is revoked.
That is a normal trade-off, not a bug, but the message promises more than it
delivers.

### The app builds

`nexora-debug-main-f339805.apk`, 19.1 MB, assembled by CI: unit tests pass,
lint runs, the APK is produced, and the secret scan over the built APK finds
nothing. The Android client had never been compiled before this point, so
everything in phase 4 up to now was unverified by construction.

### Fixed — the first Kotlin compilation

With resources linking, the compiler ran for the first time and found seven
errors in three clusters. All three were mine, and two came from files I
reconstructed in phase 4 after overwriting the originals.

- **`Outcome.getOrNull()`.** The rewritten `Outcome` exposed a `dataOrNull`
  property that nothing in the codebase used, while `ProfileViewModel` — which
  I did not write — called `getOrNull()`. Restored as a function, named to
  match `kotlin.Result` so unwrapping an `Outcome` and unwrapping a
  `runCatching` read alike. `errorOrNull` became a function for symmetry.
- **The Retrofit converter was the wrong artifact.** `AppModule` imports
  `retrofit2.converter.kotlinx.serialization.asConverterFactory`, which is
  Square's own converter, but the catalog declared JakeWharton's, whose
  package is `com.jakewharton.retrofit2.converter…`. Switched to
  `com.squareup.retrofit2:converter-kotlinx-serialization`, versioned from the
  same `retrofit` reference so the two cannot drift apart.
- **The splash screen library was never declared.** `MainActivity` called
  `installSplashScreen()` with nothing providing it. Adding the dependency
  alone would have turned a compile error into a launch crash, because that
  call requires the activity's theme to derive from `Theme.SplashScreen`: so
  `Theme.Nexora.Starting` now does, hands over via `postSplashScreenTheme`,
  and draws the launcher foreground on the per-configuration launch colour.

### Added — CI reports build failures where they can actually be read

A failed Android build could only be diagnosed by someone signed in with
repository access opening the step log and copying it out by hand. Annotations
and the job summary, unlike step logs, render for any viewer.

- **`scripts/ci-annotate-gradle.sh`** parses a failed Gradle run and emits a
  GitHub annotation per error, anchored to the right file, line and column, so
  the failure appears on the run summary and inline on the diff. It handles
  the three shapes this project produces — Kotlin/Java compiler errors, AAPT
  resource errors, and Gradle's own "What went wrong" block — skips warnings,
  and caps output at 40 so a cascade cannot bury the first real error.
- The Gradle steps now `tee` their output, with `set -o pipefail` so the
  step's exit status stays Gradle's rather than `tee`'s — without it a failing
  build would report success.
- The full Gradle log is uploaded with the reports artifact.

### Security — dependency advisories

CI's dependency audit was failing, and it was right to. Three packages had
published advisories at their pinned versions; two of them sit directly on
the security path this project is built around.

- **`pyjwt` 2.10.1 → 2.14.0** (7 advisories). This library verifies every
  access and refresh token the API issues.
- **`cryptography` 46.0.1 → 50.0.1** (7 advisories). This backs the Fernet
  cipher that encrypts panel credentials at rest.
- **`fastapi` 0.118.0 → 0.141.1** (6 advisories, in `starlette`). Starlette is
  transitive; 0.118 resolves to starlette 0.48, and 0.141 to 1.6. It is
  deliberately still not pinned directly, so it cannot drift out of the range
  FastAPI supports.

Verified rather than assumed: the upgraded set resolves cleanly, `pip-audit`
reports no known vulnerabilities, and all 161 backend tests pass on it.

Worth noting for later: this is what the weekly scheduled run of the security
workflow is for. These advisories were published after the pins were chosen,
so nothing was wrong when they were written — pins age into vulnerabilities on
their own, which is why the audit runs on a schedule and not only on push.

### Fixed — the first Android build

The first CI build failed in `:app:processDebugResources`, before a line of
Kotlin was compiled: the manifest referenced `@mipmap/ic_launcher` and
`@mipmap/ic_launcher_round`, and the app had no `mipmap` directory at all.
Phase 4 shipped a manifest whose icons were never drawn.

- **Launcher icons**, in the app's own palette (teal shield, navy `N`) rather
  than a placeholder: an adaptive icon for API 26+, a `<monochrome>` layer for
  themed icons on API 33+, and real PNGs at five densities because `minSdk` is
  24 and API 24–25 do not understand adaptive icons. The PNGs are rendered
  from the same coordinates as the vector, so the two cannot drift apart.
- **The launch background was wrong in both themes.** `values/` and
  `values-night/` both held the same navy, so a light-theme launch flashed
  dark before Compose painted `#F8FAFC`. Each now matches its scheme's
  `background` in `core/ui/Theme.kt`.
- **Deleted `values-night/themes.xml`**, which redefined `Theme.Nexora`
  *without* `windowBackground` — so in dark mode the launch theme fell back to
  the Material light background, producing exactly the white flash the theme
  exists to prevent.
- Audited every `@string`/`@color`/`@drawable`/`@mipmap`/`@xml` reference in
  the manifest, resources and Kotlin against what is defined. Nothing else is
  missing, so the next build should reach the compiler.

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

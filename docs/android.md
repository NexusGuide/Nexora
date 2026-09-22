# Android client

## The rule that shapes the app

**The app never talks to a panel.** It talks to the backend over HTTPS, and
the backend holds every panel credential. An APK is a file on a stranger's
phone; anything inside it is public, whatever obfuscation is applied.

So the app's API surface is about *subscriptions and configs*, never about
panel users. Nothing in this module knows a panel exists.

## Layers

```
feature/    Composables + ViewModels. Hold UiState, never call the network.
   ↓
domain/     Use cases and models. No Android imports, no Retrofit.
   ↓
data/       Repositories, DTOs, mappers. Owns the network and the cache.
   ↓
core/       Cross-cutting: security, networking, formatting, theme.
```

The dependency direction is one way. A composable cannot reach a Retrofit
service even by accident, because it has no import path to one.

## The refresh authenticator

This is the most consequential class in the app, and the one to be careful
editing.

The backend **rotates refresh tokens and treats reuse as theft**: presenting an
already-used refresh token revokes the entire token family and signs the user
out everywhere. That turns the obvious implementation into a bug that only
appears under load:

> The home screen fires three requests. The access token has expired, so all
> three come back 401. Three refreshes go out with the same refresh token. The
> first rotates it. The other two look exactly like a stolen token being
> replayed — and the backend does the right thing, killing every session.

The user is signed out for no reason, and it happens most on a slow network,
when several screens load at once. So:

1. **The refresh is serialised** behind a lock. A burst of 401s produces one
   refresh.
2. **The token is re-checked inside the lock.** A thread that waited finds the
   token already renewed and retries with it.

### Rejected is not the same as unreachable

A refresh can fail two ways, and treating them alike is its own bug:

| Outcome | Meaning | Action |
|---|---|---|
| Server answered 400/401/403 | The refresh token is dead | Clear the session, sign out |
| No answer — timeout, DNS, TLS | Says nothing about the session | Fail this request, **keep the session** |

Clearing on a network error would sign users out every time they walk into a
lift. `AuthAuthenticatorTest` covers both paths.

## Token storage

`TokenStore` uses `EncryptedSharedPreferences`, keyed from the Android
Keystore — hardware-backed where a TEE or StrongBox exists. Spec rule 36
requires this: a plain preferences file is readable text to anyone with root.

Keystore initialisation does fail on some devices — a corrupted keystore, an
OEM bug, a changed lock screen. The store tries again after deleting the
existing file, and only then falls back to unencrypted storage — recording it
in `isUsingInsecureFallback` so the UI can tell the user. A silent downgrade
would be worse than the failure.

Backups are excluded in `data_extraction_rules.xml`: the Keystore key is not
backed up, so a restored copy would be undecryptable anyway, and a silently
broken session is worse than a missing one.

## Device identity

`DeviceIdentity` generates a random UUID on first run and stores it encrypted.

Deliberately **not** `ANDROID_ID`, IMEI or the advertising ID: those are
restricted, shared across apps, or survive uninstall — tracking identifiers,
where all this needs is "is this the same installation as last time".

It resets on reinstall, which costs the user one device slot until they revoke
the stale entry. That is the right trade against a permanent hardware id.

## Error handling

`ApiCall` classifies every failure once, into `AppError`. The distinction the
UI cares about is **retryable or not**: a timeout deserves a retry button, a
rejected order does not, and offering one would just reproduce the rejection.

Screens branch on the backend's stable `code`, never on the message — messages
are prose and get reworded.

## Localization and RTL

Every user-facing string is in `res/values/strings.xml` with a Persian
counterpart in `res/values-fa/`. Nothing is hardcoded in a composable.

`android:supportsRtl="true"` plus Compose's `LayoutDirection` handles mirroring.
Use `start`/`end` padding, never `left`/`right`, or Persian layouts break.

Traffic figures use binary units (1 GB = 1024 MB) to match how panels and plans
express quotas — showing "107.4 GB" for a 100 GB plan looks like a bug to the
customer.

## Testing

```bash
./gradlew testDebugUnitTest
```

`core/common` is pure Kotlin with no Android imports, so `Formatting` and
`Validation` are unit-testable without an emulator. `AuthAuthenticatorTest`
uses `MockWebServer` and covers the concurrency case above.

## Not implemented

The VPN engine is phase 5. `VpnController` does not exist yet, and the Connect
button is disabled with an explanation rather than a fake success (spec rule
67). Payments are phase 6: ordering works, paying does not.

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

`DeviceIdentity` sends the backend a SHA-256 of `ANDROID_ID` with an
app-specific prefix. On Android 8+ `ANDROID_ID` is scoped to the app's signing
key, the device and the user, so it is stable across an uninstall and
reinstall, differs in every other app, and resets with a factory reset. The
hash keeps even the app-scoped value off the server.

It used to be a random UUID made on first run. That reset on every reinstall,
and a customer on a one-device plan was then locked out by their own phone —
which is exactly what happened on the first real install.

Stability depends on the signing key staying the same: a build signed with a
different key sees a different `ANDROID_ID`. That is one more reason CI signs
with a fixed debug key (see Building). IMEI, serials and the advertising ID are
not used. Where `ANDROID_ID` is missing, a random UUID in encrypted storage is
the fallback.

If a device is still counted twice — a factory reset, a new phone — the
device-limit dialog offers to sign the old entry out in favour of this one.

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

## Building

### Without a local toolchain — GitHub Actions

This is the shortest path to an installable APK, and it needs nothing on your
own machine: no Android SDK, no Gradle, no JDK.

1. Push to `main` (or run **Actions → Android → Run workflow**).
2. Open the run, wait for **Build and test**, and download the
   `nexora-debug-<branch>-<sha>.apk` artifact at the bottom of the page.
3. Push a `v*` tag and the same APK is attached to the GitHub release.

The APK is a **debug** build, signed with the standard Android debug key. It
installs on a phone and it is not a production artifact — the release keystore
is deliberately absent from this repository, so CI cannot produce a signed
release build and does not pretend to.

The backend URL is configuration, not a secret, so CI reads it from a
repository **variable** rather than a secret:

> Settings → Secrets and variables → Actions → **Variables** → New variable
> `API_BASE_URL` = `https://api.your-domain.com/` (trailing slash required)

Without it the build falls back to `http://10.0.2.2:8000/`, the emulator's
address for the host machine — correct for a developer, useless on a phone.

A second job unpacks the APK and scans its extracted strings for
secret-shaped values. It reads binaries with `strings`, not `grep` over text
files, because a text-only scan of a DEX would pass unconditionally and prove
nothing. On a hit it names the file and **not** the match: CI logs are public.

#### A stable debug key

A runner has no `~/.android/debug.keystore`, so without help every CI build is
signed with a new throwaway key, and Android will not install an APK over one
signed differently: each update would need an uninstall, losing the sign-in.
Store a debug keystore, base64-encoded, as the repository **secret**
`DEBUG_KEYSTORE_B64` (standard debug parameters: store and key password
`android`, alias `androiddebugkey`) and the workflow signs with it. It is a
debug key only; the release key never goes to CI.

### Locally

```bash
bash scripts/fetch-xray-core.sh   # once: the pinned Xray core, checksum-verified
cd android
./gradlew :app:assembleDebug      # → app/build/outputs/apk/debug/app-debug.apk
```

The Xray core (60 MB of native code) is not in the repository; the build stops
with the command above if it is missing. See [NOTICE.md](../NOTICE.md).

The Gradle wrapper is committed, so `./gradlew` downloads Gradle 8.11.1 itself
and no separate Gradle installation is needed. You still need a JDK 17 and the
Android SDK (API 35 platform, build-tools 35.0.0) — installing Android Studio
provides both.

Point the build at your backend by creating `android/local.properties`, which
is git-ignored:

```properties
api.base.url.debug=http://10.0.2.2:8000/
api.base.url=https://api.your-domain.com/
```

On Windows use `gradlew.bat` rather than `./gradlew`, and run it from the
`android` directory — not from `C:\Windows\System32`.

If the SDK manager cannot reach Google, the JVM is the usual reason: it does
not honour the Windows system proxy. Either pass the proxy explicitly:

```powershell
.\sdkmanager.bat --sdk_root="D:\Android\Sdk" `
  --proxy=http --proxy_host=127.0.0.1 --proxy_port=10808 `
  "platform-tools" "platforms;android-35" "build-tools;35.0.0"
```

(substituting your local proxy's port), or skip the problem and use the CI
build above.

## Testing

```bash
./gradlew testDebugUnitTest
```

`core/common` and the parser and config builder in `core/vpn` are pure
Kotlin with no Android imports, so they are unit-testable without an emulator. `AuthAuthenticatorTest`
uses `MockWebServer` and covers the concurrency case above.

## The VPN engine

`core/vpn` holds it, in three layers:

- **`ProxyUri`** parses the share links the panel hands out — `vless://`,
  `vmess://`, `trojan://`, `ss://` in its three encodings — over `tcp` (with or
  without the HTTP header), `ws`, `grpc`, `httpupgrade` and `xhttp`, with no
  security, TLS or Reality. Anything else is refused with a reason rather
  than half-configured: `hysteria2`, `tuic`, `kcp`, the removed `h2`
  transport, and Shadowsocks plugins.
- **`XrayConfigBuilder`** turns one into an Xray configuration. Traffic enters
  through a `tun` inbound only — no SOCKS or HTTP port on 127.0.0.1, which any
  app on the phone could use. DNS is answered by the core and its lookups go
  through the proxy. The local network, and by default Iranian domains and IP
  ranges, go direct.
- **`NexoraVpnService`** creates the interface, excludes Nexora's own traffic
  from it (otherwise the connection to the server would loop back into the
  tunnel it carries), hands the file descriptor to the core, and holds the
  notification. `VpnController` is what screens call; the configuration is
  passed to the service in memory, never in an Intent.

Both pure-Kotlin layers depend on nothing but the standard library, JSON
included (`MiniJson`), so `XrayConfigTest` runs anywhere. Every configuration
shape it produces has also been checked with `xray run -test` from the same
Xray build the library contains.

## Not implemented

- **Traffic statistics during a session.** The config enables the counters;
  nothing reads them yet.
- **Choosing a server.** Home connects to the subscription's active config.
  Switching between the configs of a subscription is the next step.
- **A setting for the Iran bypass.** It is on; there is no switch yet.
- **Always-on VPN.** When Android starts the service by itself there is no
  configuration in memory, so it stops rather than pretend to connect.

Payments are phase 6: ordering works, paying goes through an admin's manual
confirmation.

# Nexora — Android client

The Nexora app: sign in, buy a plan, see your services and configs.

## Building

The Gradle wrapper JAR is not committed (it is a binary, and a wrapper JAR
from an untrusted source is a known supply-chain vector). Generate it once:

```bash
cd android
gradle wrapper --gradle-version 8.11.1
```

Or simply open `android/` in Android Studio, which creates it for you.

Then:

```bash
./gradlew assembleDebug          # APK in app/build/outputs/apk/debug/
./gradlew testDebugUnitTest      # unit tests
./gradlew lint
```

## Pointing at your backend

Create `android/local.properties` (git-ignored):

```properties
sdk.dir=/path/to/Android/sdk
api.base.url.debug=http://10.0.2.2:8000/
api.base.url=https://api.example.com/
```

`10.0.2.2` is how the Android emulator reaches your host machine's
`localhost`. On a physical device, use your machine's LAN address.

## Signing a release

Create `android/keystore.properties` (git-ignored — never commit it):

```properties
storeFile=/absolute/path/to/nexora-release.jks
storePassword=...
keyAlias=nexora
keyPassword=...
```

Without this file the release build is simply unsigned, so a fresh clone
still builds.

## Structure

```
app/src/main/java/com/nexora/vpn/
├── core/
│   ├── common/      Outcome, AppError, Validation, Formatting — pure, testable
│   ├── network/     Retrofit, interceptor, refresh authenticator, error mapping
│   ├── security/    Keystore-backed token storage, device identity
│   └── ui/          Theme, shared components, UiState
├── data/            DTOs, mappers, repository implementations
├── domain/          Models, repository interfaces, use cases
├── feature/         One package per screen: state, ViewModel, composable
└── di/              Hilt modules
```

UI never touches the network layer directly: a screen observes a `UiState`
from its ViewModel, which calls a use case, which calls a repository.

## What works, and what does not

Implemented: sign-in, plan browsing, ordering, services, configs, profile,
device management, dark/light themes, Persian and English with RTL.

**Connecting is not implemented.** The VPN engine (Xray/v2rayNG integration)
is phase 5. The Connect button is present and disabled with an explanation —
not a fake success.

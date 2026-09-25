#!/usr/bin/env bash
#
# Download the Xray core the Android app is built with, and verify it.
#
#   bash scripts/fetch-xray-core.sh
#
# The core is AndroidLibXrayLite (https://github.com/2dust/AndroidLibXrayLite),
# LGPL-3.0: Xray-core compiled for Android with gomobile. It is not committed —
# 60 MB of native code does not belong in git history — so every build fetches
# this exact release and refuses to use it unless its SHA-256 matches.
#
# To move to a newer core: change VERSION, run this script with
# XRAY_CORE_UPDATE=1 to print the new checksum, check the release notes, paste
# the checksum below, and run the app's config tests against it.

set -euo pipefail

VERSION="v26.9.9"
SHA256="9ecf4c921568d8f4cb8550d3bafe08ff6f1d1984f45a6ad183dcdf52ee9302de"
URL="https://github.com/2dust/AndroidLibXrayLite/releases/download/${VERSION}/libv2ray.aar"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$ROOT/android/app/libs/libv2ray.aar"

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d' ' -f1
    else
        shasum -a 256 "$1" | cut -d' ' -f1
    fi
}

if [ -f "$DEST" ] && [ "$(sha256_of "$DEST")" = "$SHA256" ]; then
    echo "Xray core $VERSION already present and verified."
    exit 0
fi

mkdir -p "$(dirname "$DEST")"
TMP="$DEST.download"
trap 'rm -f "$TMP"' EXIT

echo "Downloading Xray core $VERSION…"
curl -fsSL --retry 3 -o "$TMP" "$URL"

ACTUAL="$(sha256_of "$TMP")"
if [ "${XRAY_CORE_UPDATE:-}" = "1" ]; then
    echo "SHA-256 of $VERSION: $ACTUAL"
    exit 0
fi
if [ "$ACTUAL" != "$SHA256" ]; then
    echo "Checksum mismatch for $URL" >&2
    echo "  expected $SHA256" >&2
    echo "  got      $ACTUAL" >&2
    echo "Refusing to build with a core that is not the one this repository pins." >&2
    exit 1
fi

mv "$TMP" "$DEST"
echo "Xray core $VERSION verified and saved to android/app/libs/."

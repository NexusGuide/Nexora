#!/usr/bin/env bash
# =============================================================================
# Generate fresh secrets for a Nexus VPN deployment.
#
#   ./scripts/generate-secrets.sh            # print to stdout
#   ./scripts/generate-secrets.sh --write    # create .env from .env.example
#                                            # with generated values filled in
#
# Output is NEVER written to git-tracked files other than .env (git-ignored).
# Keep the ENCRYPTION_KEY backed up somewhere outside the database server:
# losing it makes every stored panel credential permanently unreadable.
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WRITE=false
[[ "${1:-}" == "--write" ]] && WRITE=true

need() { command -v "$1" >/dev/null 2>&1 || { echo "error: '$1' is required" >&2; exit 1; }; }
need openssl
need python3

hex32()  { openssl rand -hex 32; }
fernet() { python3 -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"; }
pw()     { openssl rand -base64 24 | tr -d '\n/+=' | cut -c1-28; }

JWT_SECRET="$(hex32)"
JWT_REFRESH_SECRET="$(hex32)"
ENCRYPTION_KEY="$(fernet)"
ADMIN_BOOTSTRAP_SECRET="$(hex32)"
POSTGRES_PASSWORD="$(pw)"

if [[ "$WRITE" == false ]]; then
  cat <<EOF
# --- generated $(date -u +%Y-%m-%dT%H:%M:%SZ) --------------------------------
# Paste into your .env (never into a tracked file).

POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
DATABASE_URL=postgresql+asyncpg://nexus:${POSTGRES_PASSWORD}@postgres:5432/nexusvpn

JWT_SECRET=${JWT_SECRET}
JWT_REFRESH_SECRET=${JWT_REFRESH_SECRET}
ENCRYPTION_KEY=${ENCRYPTION_KEY}
ADMIN_BOOTSTRAP_SECRET=${ADMIN_BOOTSTRAP_SECRET}
EOF
  exit 0
fi

ENV_FILE="${ROOT}/.env"
if [[ -e "$ENV_FILE" ]]; then
  echo "error: ${ENV_FILE} already exists — refusing to overwrite." >&2
  echo "       Remove or back it up first, or run without --write." >&2
  exit 1
fi

cp "${ROOT}/.env.example" "$ENV_FILE"
chmod 600 "$ENV_FILE"

set_var() {
  python3 - "$ENV_FILE" "$1" "$2" <<'PY'
import re, sys
path, key, value = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path) as f:
    text = f.read()
text, n = re.subn(rf"(?m)^{re.escape(key)}=.*$", f"{key}={value}", text)
if n == 0:
    text = text.rstrip("\n") + f"\n{key}={value}\n"
with open(path, "w") as f:
    f.write(text)
PY
}

set_var POSTGRES_PASSWORD       "$POSTGRES_PASSWORD"
set_var DATABASE_URL            "postgresql+asyncpg://nexus:${POSTGRES_PASSWORD}@postgres:5432/nexusvpn"
set_var JWT_SECRET              "$JWT_SECRET"
set_var JWT_REFRESH_SECRET      "$JWT_REFRESH_SECRET"
set_var ENCRYPTION_KEY          "$ENCRYPTION_KEY"
set_var ADMIN_BOOTSTRAP_SECRET  "$ADMIN_BOOTSTRAP_SECRET"

echo "Wrote ${ENV_FILE} (mode 600) with generated secrets."
echo
echo "Still to fill in by hand:"
echo "  PAYMENT_API_KEY, FCM_*, SENTRY_DSN, CORS_ORIGINS, ALLOWED_HOSTS, PUBLIC_API_URL"
echo
echo "Back up ENCRYPTION_KEY off-server. Without it, stored panel credentials"
echo "cannot be decrypted after a restore."

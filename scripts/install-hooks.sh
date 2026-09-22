#!/usr/bin/env bash
# Install the repository's git hooks. Run once after cloning:
#   ./scripts/install-hooks.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1 || { echo "error: not a git repository" >&2; exit 1; }

git -C "$ROOT" config core.hooksPath .githooks
chmod +x "$ROOT/.githooks/"* 2>/dev/null || true

echo "Hooks installed (core.hooksPath = .githooks)."
if command -v gitleaks >/dev/null 2>&1; then
  echo "gitleaks found — full scanning enabled on commit."
else
  echo "NOTE: gitleaks is not installed; the hook falls back to pattern checks."
  echo "      Install it for full coverage: https://github.com/gitleaks/gitleaks"
fi

#!/usr/bin/env bash
# =============================================================================
# Nexus VPN — PostgreSQL backup (spec rule 58)
#
#   ./scripts/backup-db.sh                    # dump, compress, prune
#   ./scripts/backup-db.sh --verify           # also restore-test the dump
#   BACKUP_GPG_RECIPIENT=ops@example.com ./scripts/backup-db.sh   # encrypted
#
# Run from cron on the host:
#   15 3 * * *  cd /srv/nexusvpn && ./scripts/backup-db.sh --verify >> /var/log/nexus-backup.log 2>&1
#
# TWO THINGS THIS SCRIPT CANNOT DO FOR YOU
#
# 1. Copy the backup off this machine. A backup that dies with the server is
#    not a backup. Add an rclone/rsync/S3 step below, or pull it from elsewhere.
#
# 2. Back up ENCRYPTION_KEY. It lives in .env, not in the database, and without
#    it a restored database's panel credentials cannot be decrypted. Store it
#    separately — and NOT beside these dumps, or encrypting them protects
#    nothing.
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-${ROOT}/backups}"
RETAIN_DAILY="${RETAIN_DAILY:-7}"
RETAIN_WEEKLY="${RETAIN_WEEKLY:-4}"
RETAIN_MONTHLY="${RETAIN_MONTHLY:-12}"
COMPOSE_SERVICE="${COMPOSE_SERVICE:-postgres}"
VERIFY=false
[[ "${1:-}" == "--verify" ]] && VERIFY=true

log() { printf '%s  %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

# --- configuration ----------------------------------------------------------
[[ -f "${ROOT}/.env" ]] || die "${ROOT}/.env not found. Run from the deployment."

# Read only what is needed, without exporting the whole file into this shell.
get_env() { grep -E "^${1}=" "${ROOT}/.env" | head -1 | cut -d= -f2- | tr -d '"'"'"''; }

PGUSER="$(get_env POSTGRES_USER)"
PGDATABASE="$(get_env POSTGRES_DB)"
[[ -n "$PGUSER" ]] || die "POSTGRES_USER is not set in .env"
[[ -n "$PGDATABASE" ]] || PGDATABASE=nexusvpn

command -v docker >/dev/null 2>&1 || die "docker is required"

# --- naming -----------------------------------------------------------------
# Tagged by tier so pruning can keep different amounts of each.
DAY_OF_WEEK="$(date -u +%u)"     # 7 = Sunday
DAY_OF_MONTH="$(date -u +%d)"
STAMP="$(date -u +%Y%m%d-%H%M%S)"

TIER=daily
[[ "$DAY_OF_WEEK" == "7" ]] && TIER=weekly
[[ "$DAY_OF_MONTH" == "01" ]] && TIER=monthly

mkdir -p "${BACKUP_DIR}"
chmod 700 "${BACKUP_DIR}"

OUTFILE="${BACKUP_DIR}/nexusvpn-${TIER}-${STAMP}.sql.gz"

# --- dump -------------------------------------------------------------------
log "Dumping ${PGDATABASE} (${TIER})"

# --clean --if-exists so the dump can be restored over an existing database.
if ! docker compose -f "${ROOT}/docker-compose.yml" exec -T "${COMPOSE_SERVICE}" \
        pg_dump -U "${PGUSER}" -d "${PGDATABASE}" --clean --if-exists \
    | gzip -9 > "${OUTFILE}.partial"; then
    rm -f "${OUTFILE}.partial"
    die "pg_dump failed"
fi

# Written to .partial first and renamed only on success, so a backup
# interrupted halfway is never mistaken for a complete one.
mv "${OUTFILE}.partial" "${OUTFILE}"
chmod 600 "${OUTFILE}"

SIZE="$(du -h "${OUTFILE}" | cut -f1)"
log "Wrote ${OUTFILE} (${SIZE})"

# A dump far smaller than expected usually means the database was empty or the
# dump errored early — worth failing on rather than silently keeping.
MIN_BYTES="${MIN_BACKUP_BYTES:-10240}"
ACTUAL_BYTES="$(stat -c%s "${OUTFILE}" 2>/dev/null || stat -f%z "${OUTFILE}")"
if (( ACTUAL_BYTES < MIN_BYTES )); then
    die "Backup is only ${ACTUAL_BYTES} bytes — expected at least ${MIN_BYTES}."
fi

# --- optional encryption ----------------------------------------------------
if [[ -n "${BACKUP_GPG_RECIPIENT:-}" ]]; then
    command -v gpg >/dev/null 2>&1 || die "gpg is required for encryption"
    log "Encrypting for ${BACKUP_GPG_RECIPIENT}"
    gpg --batch --yes --encrypt --recipient "${BACKUP_GPG_RECIPIENT}" \
        --output "${OUTFILE}.gpg" "${OUTFILE}"
    chmod 600 "${OUTFILE}.gpg"
    shred -u "${OUTFILE}" 2>/dev/null || rm -f "${OUTFILE}"
    OUTFILE="${OUTFILE}.gpg"
    log "Encrypted to ${OUTFILE}"
fi

# --- verification -----------------------------------------------------------
# An unverified backup is a hypothesis. This restores into a throwaway database
# and counts the tables, which catches a truncated or corrupt dump.
if [[ "$VERIFY" == true ]]; then
    if [[ -n "${BACKUP_GPG_RECIPIENT:-}" ]]; then
        log "Skipping verification: dump is encrypted (decrypt manually to test)"
    else
        VERIFY_DB="verify_$(date -u +%s)"
        log "Verifying by restoring into ${VERIFY_DB}"

        compose() { docker compose -f "${ROOT}/docker-compose.yml" exec -T "${COMPOSE_SERVICE}" "$@"; }

        compose createdb -U "${PGUSER}" "${VERIFY_DB}" \
            || die "could not create verification database"

        if gunzip -c "${OUTFILE}" | compose psql -U "${PGUSER}" -d "${VERIFY_DB}" -q >/dev/null 2>&1; then
            TABLES="$(compose psql -U "${PGUSER}" -d "${VERIFY_DB}" -tAc \
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")"
            compose dropdb -U "${PGUSER}" "${VERIFY_DB}" || true

            TABLES="$(echo "$TABLES" | tr -d '[:space:]')"
            if [[ "${TABLES:-0}" -lt 5 ]]; then
                die "Verification failed: only ${TABLES} tables restored"
            fi
            log "Verified: ${TABLES} tables restored cleanly"
        else
            compose dropdb -U "${PGUSER}" "${VERIFY_DB}" || true
            die "Verification failed: the dump did not restore"
        fi
    fi
fi

# --- retention --------------------------------------------------------------
prune() {
    local tier="$1" keep="$2"
    local files
    mapfile -t files < <(find "${BACKUP_DIR}" -maxdepth 1 -name "nexusvpn-${tier}-*" -print0 \
        | xargs -0 -r ls -1t 2>/dev/null || true)
    local count=${#files[@]}
    (( count > keep )) || return 0
    for (( i = keep; i < count; i++ )); do
        log "Pruning $(basename "${files[$i]}")"
        rm -f "${files[$i]}"
    done
}

prune daily "${RETAIN_DAILY}"
prune weekly "${RETAIN_WEEKLY}"
prune monthly "${RETAIN_MONTHLY}"

TOTAL="$(find "${BACKUP_DIR}" -maxdepth 1 -name 'nexusvpn-*' | wc -l | tr -d ' ')"
log "Done. ${TOTAL} backup(s) in ${BACKUP_DIR}"

cat <<EOF

  Remember: this backup is still on the production server.
  Copy it somewhere else, and keep ENCRYPTION_KEY somewhere else again.
EOF

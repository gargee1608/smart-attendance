#!/usr/bin/env bash
# pg_dump plus a tar of the storage tree, encrypted with BACKUP_ENCRYPTION_KEY.
# TODO M12: when RESTIC_REPOSITORY is set and restic is installed, also copy
# the archive into that restic repo. Full per-org export UI is not in v1.
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  exec sudo -E bash "$0" "$@"
fi

APP="${APP_DIR:-/opt/smartattendance/application}"
DATA="${DATA_ROOT:-/opt/smartattendance}"
ENV_FILE="${ENV_FILE:-$DATA/config/.env}"
# shellcheck disable=SC1090
set -a
source "$ENV_FILE"
set +a

if [[ -z "${BACKUP_ENCRYPTION_KEY:-}" ]]; then
  echo "BACKUP_ENCRYPTION_KEY is missing from $ENV_FILE" >&2
  exit 1
fi

mkdir -p "$DATA/backups"
STAMP="$(date -u +%Y%m%d%H%M%S)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

restore() {
  local archive="$1"
  if [[ "${RESTORE_CONFIRM:-}" != "yes" ]]; then
    echo "Refusing to restore. Re-run with RESTORE_CONFIRM=yes." >&2
    exit 1
  fi
  openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_ENCRYPTION_KEY -in "$archive" | tar -xzf - -C "$WORK"
  docker compose -f "$APP/docker-compose.yml" --env-file "$ENV_FILE" exec -T postgres \
    psql -U postgres -d smartattendance -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'smartattendance' AND pid <> pg_backend_pid();" >/dev/null || true
  docker compose -f "$APP/docker-compose.yml" --env-file "$ENV_FILE" exec -T postgres \
    psql -U postgres -d postgres -c "DROP DATABASE IF EXISTS smartattendance WITH (FORCE);"
  docker compose -f "$APP/docker-compose.yml" --env-file "$ENV_FILE" exec -T postgres \
    psql -U postgres -d postgres -c "CREATE DATABASE smartattendance;"
  docker compose -f "$APP/docker-compose.yml" --env-file "$ENV_FILE" exec -T postgres \
    psql -U postgres -d smartattendance < "$WORK/database.sql"
  rm -rf "$DATA/storage"
  mkdir -p "$DATA/storage"
  tar -xzf "$WORK/storage.tar.gz" -C "$DATA/storage"
  echo "Restore finished. Restart the stack: docker compose -f $APP/docker-compose.yml --env-file $ENV_FILE up -d"
}

if [[ "${1:-}" == "restore" ]]; then
  restore "${2:?Pass the .tar.gz.enc archive}"
  exit 0
fi

docker compose -f "$APP/docker-compose.yml" --env-file "$ENV_FILE" exec -T postgres \
  pg_dump -U postgres --clean --if-exists smartattendance > "$WORK/database.sql"
tar -C "$DATA/storage" -czf "$WORK/storage.tar.gz" .
tar -C "$WORK" -czf - database.sql storage.tar.gz \
  | openssl enc -aes-256-cbc -salt -pbkdf2 -pass env:BACKUP_ENCRYPTION_KEY \
    -out "$DATA/backups/smartattendance-$STAMP.tar.gz.enc"
chmod 600 "$DATA/backups/smartattendance-$STAMP.tar.gz.enc"

if [[ -n "${RESTIC_REPOSITORY:-}" ]] && command -v restic >/dev/null 2>&1; then
  restic backup "$DATA/backups/smartattendance-$STAMP.tar.gz.enc" "$DATA/config/.env"
else
  echo "restic is not configured. The encrypted archive is the v1 backup."
fi

find "$DATA/backups" -name 'smartattendance-*.tar.gz.enc' -mtime +14 -delete
echo "Wrote $DATA/backups/smartattendance-$STAMP.tar.gz.enc"

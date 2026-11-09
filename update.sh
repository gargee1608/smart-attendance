#!/usr/bin/env bash
# Rebuild Smart Attendance from the installed checkout and roll back if health fails.
# TODO M15: pin a signed GitHub release tag instead of pulling the current branch.
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  exec sudo -E bash "$0" "$@"
fi

APP="${APP_DIR:-/opt/smartattendance/application}"
DATA="${DATA_ROOT:-/opt/smartattendance}"
ENV_FILE="${ENV_FILE:-$DATA/config/.env}"
cd "$APP"
# shellcheck disable=SC1090
set -a
source "$ENV_FILE"
set +a

STAMP="$(date -u +%Y%m%d%H%M%S)"
mkdir -p "$DATA/backups"
SHA=""
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  SHA="$(git rev-parse HEAD)"
  echo "$SHA" > "$DATA/backups/pre-update-$STAMP.sha"
  git pull --ff-only
else
  echo "No git metadata in $APP. Rebuilding the files already on disk."
fi

docker compose --env-file "$ENV_FILE" up -d --build

healthy=0
for _ in $(seq 1 36); do
  if curl -fsS "http://127.0.0.1:${PUBLIC_HTTP_PORT:-18480}/api/health" >/dev/null; then
    healthy=1
    break
  fi
  sleep 5
done

if [[ "$healthy" -ne 1 ]]; then
  echo "Update failed the health check." >&2
  if [[ -n "$SHA" ]]; then
    echo "Checking out $SHA and rebuilding." >&2
    git checkout "$SHA"
    docker compose --env-file "$ENV_FILE" up -d --build
  fi
  exit 1
fi

echo "Update is healthy ($STAMP)."

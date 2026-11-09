#!/usr/bin/env bash
# Install Smart Attendance on Ubuntu 24.04. Safe to re-run.
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  exec sudo -E bash "$0" "$@"
fi

if [[ -f /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
else
  echo "This installer needs Ubuntu 24.04." >&2
  exit 1
fi

if [[ "${ID:-}" != "ubuntu" || "${VERSION_ID:-}" != "24.04" ]]; then
  echo "This installer is for Ubuntu 24.04. Detected ${PRETTY_NAME:-unknown}." >&2
  exit 1
fi

install_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    return
  fi
  apt-get update
  apt-get install -y ca-certificates curl gnupg rsync openssl
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
}

start_docker() {
  if docker info >/dev/null 2>&1; then
    return
  fi
  if command -v service >/dev/null 2>&1; then
    service docker start || true
  fi
  if ! docker info >/dev/null 2>&1; then
    mkdir -p /var/log
    dockerd >/var/log/dockerd.log 2>&1 &
    local _i
    for _i in $(seq 1 30); do
      if docker info >/dev/null 2>&1; then
        return
      fi
      sleep 1
    done
    echo "Docker did not start. See /var/log/dockerd.log" >&2
    exit 1
  fi
}

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST=/opt/smartattendance
APP="$DEST/application"

apt-get update
apt-get install -y ca-certificates curl gnupg rsync openssl
install_docker
start_docker

# Docker on Ubuntu can leave an iptables-legacy FORWARD DROP beside the nftables
# rules. That drops traffic on Compose bridge networks, so the API never reaches
# Postgres. Accept forwarded traffic on the legacy table when that policy is present.
if command -v iptables-legacy >/dev/null 2>&1; then
  if iptables-legacy -S FORWARD 2>/dev/null | grep -q -- '-P FORWARD DROP'; then
    iptables-legacy -P FORWARD ACCEPT || true
    iptables-legacy -C FORWARD -j ACCEPT 2>/dev/null || iptables-legacy -A FORWARD -j ACCEPT || true
  fi
fi
if command -v ip6tables-legacy >/dev/null 2>&1; then
  if ip6tables-legacy -S FORWARD 2>/dev/null | grep -q -- '-P FORWARD DROP'; then
    ip6tables-legacy -P FORWARD ACCEPT || true
    ip6tables-legacy -C FORWARD -j ACCEPT 2>/dev/null || ip6tables-legacy -A FORWARD -j ACCEPT || true
  fi
fi

mkdir -p \
  "$APP" \
  "$DEST/storage/organizations" \
  "$DEST/storage/imports" \
  "$DEST/storage/exports" \
  "$DEST/storage/temporary" \
  "$DEST/database" \
  "$DEST/backups" \
  "$DEST/logs" \
  "$DEST/config"

if [[ "$SRC" != "$APP" ]]; then
  rsync -a --delete \
    --exclude data \
    --exclude node_modules \
    --exclude frontend/node_modules \
    --exclude 'models/*.onnx' \
    --exclude 'models/*.partial' \
    "$SRC/" "$APP/"
  mkdir -p "$APP/models"
  if compgen -G "$SRC/models/*.onnx" > /dev/null; then
    cp -n "$SRC/models/"*.onnx "$APP/models/" || true
  fi
fi

ENV_FILE="$DEST/config/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  POSTGRES_PASSWORD="$(openssl rand -hex 24)"
  APP_DB_PASSWORD="$(openssl rand -hex 24)"
  ADMIN_DB_PASSWORD="$(openssl rand -hex 24)"
  JWT_SECRET="$(openssl rand -hex 32)"
  FILE_ENCRYPTION_KEY="$(openssl rand -base64 32 | tr -d '\n')"
  BACKUP_ENCRYPTION_KEY="$(openssl rand -hex 32)"
  INITIAL_ADMIN_EMAIL="${INITIAL_ADMIN_EMAIL:-admin@smartattendance.local}"
  INITIAL_ADMIN_PASSWORD="Sa-$(openssl rand -hex 12)"
  cat > "$ENV_FILE" <<EOF
SMARTATTENDANCE_ENV=production
DATA_ROOT=/opt/smartattendance
PUBLIC_HTTP_PORT=18480
PUBLIC_HTTPS_PORT=18443
PUBLIC_BASE_URL=http://127.0.0.1:18480
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
APP_DB_PASSWORD=${APP_DB_PASSWORD}
ADMIN_DB_PASSWORD=${ADMIN_DB_PASSWORD}
JWT_SECRET=${JWT_SECRET}
FILE_ENCRYPTION_KEY=${FILE_ENCRYPTION_KEY}
BACKUP_ENCRYPTION_KEY=${BACKUP_ENCRYPTION_KEY}
INITIAL_ADMIN_EMAIL=${INITIAL_ADMIN_EMAIL}
INITIAL_ADMIN_PASSWORD=${INITIAL_ADMIN_PASSWORD}
OLLAMA_MODEL=qwen2.5:7b
EOF
  chmod 600 "$ENV_FILE"
  printf '%s\n' "$INITIAL_ADMIN_PASSWORD" > "$DEST/config/initial_admin_password.txt"
  chmod 600 "$DEST/config/initial_admin_password.txt"
  echo "Wrote secrets to $ENV_FILE"
else
  echo "Keeping existing $ENV_FILE"
fi
ln -sfn "$ENV_FILE" "$APP/.env"

cd "$APP"
# shellcheck disable=SC1090
set -a
source "$ENV_FILE"
set +a
bash scripts/download_models.sh
docker compose --env-file "$ENV_FILE" up -d --build

echo "Waiting for the API"
ready=0
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${PUBLIC_HTTP_PORT:-18480}/api/health" >/dev/null; then
    ready=1
    break
  fi
  sleep 5
done
if [[ "$ready" -ne 1 ]]; then
  echo "The API did not become healthy. Check: docker compose -f $APP/docker-compose.yml logs" >&2
  exit 1
fi

echo "Pulling ${OLLAMA_MODEL:-qwen2.5:7b}"
pulled=0
for _ in 1 2 3; do
  if docker compose --env-file "$ENV_FILE" exec -T ollama ollama pull "${OLLAMA_MODEL:-qwen2.5:7b}"; then
    pulled=1
    break
  fi
  sleep 5
done
if [[ "$pulled" -ne 1 ]]; then
  echo "Ollama model pull failed. The rest of the stack is up. Retry with:" >&2
  echo "  docker compose -f $APP/docker-compose.yml exec ollama ollama pull ${OLLAMA_MODEL:-qwen2.5:7b}" >&2
  exit 1
fi

echo
echo "Smart Attendance is running."
echo "  HTTP   http://127.0.0.1:${PUBLIC_HTTP_PORT:-18480}"
echo "  HTTPS  https://127.0.0.1:${PUBLIC_HTTPS_PORT:-18443}"
echo "  Email  $(grep '^INITIAL_ADMIN_EMAIL=' "$ENV_FILE" | cut -d= -f2-)"
echo "  Password file  $DEST/config/initial_admin_password.txt"

#!/bin/sh
set -eu
mkdir -p /data/storage/imports /data/storage/exports /data/storage/temporary /data/storage/organizations /data/logs /data/backups
if id app >/dev/null 2>&1; then
  chown -R app:app /data || true
  exec gosu app "$@"
fi
exec "$@"

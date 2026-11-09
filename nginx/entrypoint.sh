#!/bin/sh
set -eu
mkdir -p /etc/nginx/certs
if [ ! -s /etc/nginx/certs/server.crt ] || [ ! -s /etc/nginx/certs/server.key ]; then
  openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
    -keyout /etc/nginx/certs/server.key \
    -out /etc/nginx/certs/server.crt \
    -subj "/CN=smartattendance.local"
fi
exec nginx -g "daemon off;"

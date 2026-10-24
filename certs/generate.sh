#!/usr/bin/env bash
# Генерує власний CA та серверний сертифікат для Mosquitto (TLS 8883).
# Приватні ключі лишаються локально (див. .gitignore).
set -euo pipefail
cd "$(dirname "$0")"

openssl genrsa -out ca.key 4096 2>/dev/null
openssl req -x509 -new -nodes -key ca.key -sha256 -days 1825 \
  -subj "/C=UA/O=IoT MC1/CN=IoT MC1 Local CA" -out ca.crt

openssl genrsa -out server.key 2048 2>/dev/null
openssl req -new -key server.key -subj "/C=UA/O=IoT MC1/CN=mosquitto" -out server.csr
cat > server.ext <<EXT
subjectAltName=DNS:mosquitto,DNS:localhost,IP:127.0.0.1
extendedKeyUsage=serverAuth
basicConstraints=CA:FALSE
EXT
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out server.crt -days 825 -sha256 -extfile server.ext 2>/dev/null

# ключ брокера має читатися процесом mosquitto у контейнері (навчальний стенд)
chmod 644 server.key ca.crt server.crt
chmod 600 ca.key
echo "Готово: certs/ca.crt, certs/server.crt, certs/server.key"

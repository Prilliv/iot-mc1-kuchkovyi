#!/bin/sh
# Точка входу контейнера Mosquitto:
# будує файл паролів із змінних MQTT_PASS_<USER> (файл .env) — паролі не лежать у репозиторії.
set -eu
DIR=/tmp/mosquitto
mkdir -p "$DIR"
PASSWD="$DIR/passwd"
rm -f "$PASSWD"

count=0
for name in $(env | grep '^MQTT_PASS_' | cut -d= -f1); do
  user=$(echo "${name#MQTT_PASS_}" | tr 'A-Z' 'a-z')
  eval "pw=\${$name}"
  if [ -z "$pw" ] || [ "$pw" = "change_me" ]; then
    echo "ПОМИЛКА: $name порожній або change_me (виконайте scripts/make-env.sh)"; exit 1
  fi
  if [ -f "$PASSWD" ]; then mosquitto_passwd -b "$PASSWD" "$user" "$pw"
  else mosquitto_passwd -b -c "$PASSWD" "$user" "$pw"; fi
  count=$((count+1))
done
echo "Створено користувачів MQTT: $count"

cp /mosquitto/config/acl "$DIR/acl"
chown mosquitto:mosquitto "$DIR/passwd" "$DIR/acl" 2>/dev/null || true
chmod 600 "$DIR/passwd" "$DIR/acl"
chown mosquitto:mosquitto /mosquitto/data 2>/dev/null || true
exec "$@"

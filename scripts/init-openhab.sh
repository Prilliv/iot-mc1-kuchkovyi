#!#!/bin/sh
set -x  # Увімкнути режим дебагу (друкуватиме кожну команду в лог)

echo "--- START INIT ---"
echo "OPENHAB_MQTT_PASS length: ${#OPENHAB_MQTT_PASS}"

sed "s|__OPENHAB_MQTT_PASS__|${OPENHAB_MQTT_PASS}|g" /templates/broker.things.tpl > /out-things/broker.things
chown "${HOST_UID:-1000}:${HOST_GID:-1000}" /out-things/broker.things
chmod 644 /out-things/broker.things

CACERTS_PATH=$(find /opt/java/openjdk -name cacerts -o -name jssecacerts 2>/dev/null | head -n 1)
echo "CACERTS_PATH found: $CACERTS_PATH"

cp -f "$CACERTS_PATH" /ts/truststore.p12
chmod 644 /ts/truststore.p12

keytool -importcert -noprompt -alias iot-mc1-ca -file /ca.crt \
  -keystore /ts/truststore.p12 -storepass changeit >/dev/null

echo "init: broker.things створено, truststore готовий"
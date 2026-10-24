```bash
#!/usr/bin/env bash
# Демонстрація безпеки MQTT:
# неавторизовані дії мають бути відхилені,
# легітимний адміністратор повинен отримати телеметрію.

cd "$(dirname "$0")/.." || exit 1

set -a
. ./.env
set +a

EXEC=(docker compose exec -T mosquitto)

PASS=0
FAIL=0

step() {
    printf '\n\033[1;34m=== %s ===\033[0m\n' "$1"
}

result() {
    if [ "$1" -eq 0 ]; then
        printf '\033[0;32m[PASS]\033[0m %s\n' "$2"
        PASS=$((PASS + 1))
    else
        printf '\033[0;31m[FAIL]\033[0m %s\n' "$2"
        FAIL=$((FAIL + 1))
    fi
}

# Запуск mosquitto_sub всередині контейнера.
# Пароль передається окремим аргументом, не вбудовується в командний текст.
sub_with_password() {
    local password="$1"
    shift

    "${EXEC[@]}" sh -c '
        exec mosquitto_sub "$@"
    ' sh "$@" -P "$password"
}

# 1. Анонімний доступ заборонено.
step "1. Підключення без логіна та пароля"

output=$("${EXEC[@]}" mosquitto_sub \
    -h localhost -p 8883 \
    --cafile /mosquitto/certs/ca.crt \
    -t 'flat/#' -W 3 2>&1)
rc=$?

printf '%s\n' "$output"
if [ "$rc" -ne 0 ] && ! printf '%s' "$output" | grep -qi 'File not found'; then
    result 0 "Анонімне підключення відхилено"
else
    result 1 "Не вдалося підтвердити відмову в анонімному доступі"
fi

# 2. Неправильний пароль.
step "2. Невірний пароль адміністратора"

output=$("${EXEC[@]}" sh -c '
    exec mosquitto_sub -h localhost -p 8883 \
      --cafile /mosquitto/certs/ca.crt \
      -u admin -P "$1" -t "flat/#" -W 3
' sh wrong 2>&1)
rc=$?

printf '%s\n' "$output"
if [ "$rc" -ne 0 ] && ! printf '%s' "$output" | grep -qi 'File not found'; then
    result 0 "Неправильний пароль відхилено"
else
    result 1 "Не вдалося підтвердити відмову з неправильним паролем"
fi

# 3. TLS відсутній.
step "3. Підключення без TLS до порту 8883"

output=$("${EXEC[@]}" sh -c '
    exec mosquitto_sub -h localhost -p 8883 \
      -u admin -P "$1" -t "flat/#" -W 3
' sh "$MQTT_PASS_ADMIN" 2>&1)
rc=$?

printf '%s\n' "$output"
if [ "$rc" -ne 0 ]; then
    result 0 "Підключення без TLS не пройшло"
else
    result 1 "Клієнт несподівано підключився без TLS"
fi

# 4. Відкритий MQTT на порту 1883 має бути недоступним.
step "4. Порт 1883 — слухача бути не повинно"

output=$("${EXEC[@]}" sh -c '
    exec mosquitto_sub -h localhost -p 1883 \
      -u admin -P "$1" -t "flat/#" -W 3
' sh "$MQTT_PASS_ADMIN" 2>&1)
rc=$?

printf '%s\n' "$output"
if printf '%s' "$output" | grep -qiE 'connection refused|connection reset|network is unreachable'; then
    result 0 "Порт 1883 відхиляє з'єднання"
else
    result 1 "Не підтверджено відсутність слухача на 1883"
fi

# 5. Сенсор руху не повинен публікувати команду обігрівачу.
step "5. ACL — заборона публікації в heater/set"

output=$("${EXEC[@]}" sh -c '
    exec mosquitto_pub -V mqttv5 \
      -h localhost -p 8883 \
      --cafile /mosquitto/certs/ca.crt \
      -u motion_living -P "$1" \
      -t flat/living/heater/set \
      -m "{\"state\":\"OFF\"}" -q 1
' sh "$MQTT_PASS_MOTION_LIVING" 2>&1)
rc=$?

printf '%s\n' "$output"
if [ "$rc" -ne 0 ]; then
    result 0 "Публікація команди відхилена"
else
    result 1 "Публікація завершилася без помилки; перевір ACL і журнал брокера"
fi

# 6. Сенсор руху не повинен підписуватися на команди світла.
step "6. ACL — заборона підписки на light/set"

output=$("${EXEC[@]}" sh -c '
    exec mosquitto_sub -V mqttv5 \
      -h localhost -p 8883 \
      --cafile /mosquitto/certs/ca.crt \
      -u motion_living -P "$1" \
      -t flat/living/light/set -W 3 -v
' sh "$MQTT_PASS_MOTION_LIVING" 2>&1)
rc=$?

printf '%s\n' "$output"
if printf '%s' "$output" | grep -qiE 'not authorized|not authorised|subscription.*denied|SUBACK.*(0x80|128)'; then
    result 0 "Брокер відхилив підписку через ACL"
else
    result 1 "Відмову в підписці не підтверджено; перевір журнал брокера"
fi

# 7. Адміністратор має отримати телеметрію.
step "7. Легітимний адміністратор отримує телеметрію"

output=$("${EXEC[@]}" sh -c '
    exec mosquitto_sub \
      -h localhost -p 8883 \
      --cafile /mosquitto/certs/ca.crt \
      -u admin -P "$1" \
      -t flat/living/temperature/telemetry \
      -C 1 -W 8 -v
' sh "$MQTT_PASS_ADMIN" 2>&1)
rc=$?

printf '%s\n' "$output"
if [ "$rc" -eq 0 ] && printf '%s' "$output" | grep -q 'flat/living/temperature/telemetry'; then
    result 0 "Адміністратор отримав телеметрію через TLS"
else
    result 1 "Адміністратор не отримав телеметрію"
fi

# 8. Вивід журналу — інформаційний, не окремий тест доступу.
step "8. Журнал брокера — відмови та помилки"

docker compose logs --no-color --tail 400 mosquitto 2>&1 |
    grep -Ei 'denied|not authori[sz]ed|tls|ssl|refused|error|ACL|CONNACK|SUBACK' |
    tail -25 || true

printf '\n==============================\n'
printf 'PASS: %s\n' "$PASS"
printf 'FAIL: %s\n' "$FAIL"
printf '==============================\n'

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0
```
// ШАБЛОН. Файл conf/things/broker.things генерується init-контейнером (пароль із .env).
Bridge mqtt:broker:mosquitto "Mosquitto (TLS 8883)" [
    host="mosquitto",
    port=8883,
    secure=true,
    hostnameValidation=true,
    certificatePinning=false,
    username="openhab",
    password="__OPENHAB_MQTT_PASS__",
    clientID="openhab-mc1",
    qos=1,
    keepAlive=30
] {
    // ---------- Сенсори ----------
    Thing topic motion_living "Motion Living" {
        Channels:
            Type switch : motion "Motion" [ stateTopic="flat/living/motion/telemetry", transformationPattern="JSONPATH:$.value", on="1", off="0" ]
            Type string : status "Status" [ stateTopic="flat/living/motion/status" ]
    }
    
    Thing topic motion_bedroom "Motion Bedroom" {
        Channels:
            Type switch : motion "Motion" [ stateTopic="flat/bedroom/motion/telemetry", transformationPattern="JSONPATH:$.value", on="1", off="0" ]
            Type string : status "Status" [ stateTopic="flat/bedroom/motion/status" ]
    }

    Thing topic temp_living "Temperature Living" {
        Channels:
            Type number : value "Temperature" [ stateTopic="flat/living/temperature/telemetry", transformationPattern="JSONPATH:$.value" ]
            Type string : status "Status" [ stateTopic="flat/living/temperature/status" ]
    }

    Thing topic hum_living "Humidity Living" {
        Channels:
            Type number : value "Humidity" [ stateTopic="flat/living/humidity/telemetry", transformationPattern="JSONPATH:$.value" ]
            Type string : status "Status" [ stateTopic="flat/living/humidity/status" ]
    }

    Thing topic lux_hall "Lux Hall" {
        Channels:
            Type number : value "Lux" [ stateTopic="flat/hall/lux/telemetry", transformationPattern="JSONPATH:$.value" ]
            Type string : status "Status" [ stateTopic="flat/hall/lux/status" ]
    }

    Thing topic power_hall "Power Hall" {
        Channels:
            Type number : value "Power" [ stateTopic="flat/hall/power/telemetry", transformationPattern="JSONPATH:$.value" ]
            Type string : status "Status" [ stateTopic="flat/hall/power/status" ]
    }

    // ---------- Актуатори ----------
    Thing topic light_living "Light Living" {
        Channels:
            Type switch : power "Power" [ stateTopic="flat/living/light/state", transformationPattern="JSONPATH:$.state", commandTopic="flat/living/light/set" ]
            Type number : level "Level" [ stateTopic="flat/living/light/state", transformationPattern="JSONPATH:$.brightness", commandTopic="flat/living/light/brightness/set" ]
            Type string : status "Status" [ stateTopic="flat/living/light/status" ]
    }

    Thing topic light_bedroom "Light Bedroom" {
        Channels:
            Type switch : power "Power" [ stateTopic="flat/bedroom/light/state", transformationPattern="JSONPATH:$.state", commandTopic="flat/bedroom/light/set" ]
            Type number : level "Level" [ stateTopic="flat/bedroom/light/state", transformationPattern="JSONPATH:$.brightness", commandTopic="flat/bedroom/light/brightness/set" ]
            Type string : status "Status" [ stateTopic="flat/bedroom/light/status" ]
    }

    Thing topic heater_living "Heater Living" {
        Channels:
            Type switch : power "Power" [ stateTopic="flat/living/heater/state", transformationPattern="JSONPATH:$.heating", commandTopic="flat/living/heater/set" ]
            Type number : setpoint "Setpoint" [ stateTopic="flat/living/heater/state", transformationPattern="JSONPATH:$.setpoint", commandTopic="flat/living/heater/setpoint/set" ]
            Type string : status "Status" [ stateTopic="flat/living/heater/status" ]
    }
}
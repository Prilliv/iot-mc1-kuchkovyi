#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Симулятор пристроїв «Енергоощадна розумна квартира» (Варіант 1).

Кожен пристрій — окремий MQTT-клієнт зі своїм користувачем, TLS, Last Will і retain-статусом.
Усі пристрої читають значення зі спільної спрощеної фізичної моделі квартири (клас Flat):
температура змінюється плавно (нагрів / тепловтрати), потужність залежить від світла й обігрівача,
освітленість — від «дня/ночі» та ввімкненого світла, рух — від присутності людини.

Запуск:
    python devices.py --scenario normal        # старт симулятора з початковим сценарієм
    python devices.py --send overload          # надіслати сценарій у вже запущений симулятор
"""
import argparse
import json
import math
import os
import random
import signal
import sys
import threading
import time

import paho.mqtt.client as mqtt

HOST = os.getenv("MQTT_HOST", "mosquitto")
PORT = int(os.getenv("MQTT_PORT", "8883"))
CA_FILE = os.getenv("MQTT_CA", "/certs/ca.crt")

OBJ = "flat"
KEEPALIVE = 10                       # с; після обриву брокер опублікує Last Will за ~1.5×KEEPALIVE
SCENARIO_TOPIC = f"{OBJ}/sim/scenario/set"

# Обґрунтування QoS (див. README):
QOS_TELEMETRY = 0   # часті виміри; старе значення неважливе, коли приходить нове
QOS_COMMAND = 1     # команди set не можна губити (команди ідемпотентні, дублікат не шкодить)
QOS_STATE = 1       # підтвердження стану (retain) — openHAB отримає актуальний стан після перезапуску
QOS_STATUS = 1      # online/offline + Last Will (retain)

SCENARIOS = {
    "normal": "День, людина у вітальні, світла не потрібно",
    "dark_motion": "Темно + рух у вітальні  -> правило 1: світло 70 %",
    "bedroom_dark": "Темно + рух у спальні    -> правило 1: світло 70 %",
    "away": "Нікого вдома -> світло гасне (5 хв), через 30 хв режим «Економ» 17 °C (правила 1 і 2)",
    "return": "Людина повернулась (вітальня) -> режим «Комфорт» 21 °C (правило 2)",
    "overload": "Додаткове навантаження +2400 Вт на 120 с -> правило 3 (тривога + вимкнення обігрівача)",
}


def log(*args):
    print(time.strftime("%H:%M:%S"), *args, flush=True)


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


# --------------------------------------------------------------------------------------
#  Модель квартири
# --------------------------------------------------------------------------------------
class Flat:
    HEATER_W = 1500.0
    ROOMS = ("living", "bedroom")

    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = threading.RLock()
        self.t0 = clock()
        self.t_air = 19.8            # °C у квартирі
        self.t_out = 6.0             # °C надворі
        self.ambient = 180.0         # лк природного світла (поточне)
        self.ambient_target = 180.0  # лк, до якого плавно прямує ambient
        self.presence = "living"     # none | living | bedroom
        self.extra_load_w = 0.0      # сторонній прилад (сценарій overload)
        self.extra_until = 0.0
        self.light_pct = {r: 0 for r in self.ROOMS}
        self.heater_on = True
        self.heater_setpoint = 21.0
        self.heating = False         # чи реально гріє зараз (термостат з гістерезисом)
        self._burst = {r: {"on": False, "until": 0.0} for r in self.ROOMS}

    # крок моделі, dt у секундах
    def step(self, dt):
        with self.lock:
            now = self.clock()
            self.t_out = 6.0 + 2.0 * math.sin((now - self.t0) / 1800.0)

            # термостат з гістерезисом ±0.3 °C
            if self.heater_on:
                if self.t_air < self.heater_setpoint - 0.3:
                    self.heating = True
                elif self.t_air > self.heater_setpoint + 0.3:
                    self.heating = False
            else:
                self.heating = False

            gain = 0.0095 if self.heating else 0.0            # °C/с від обігрівача
            people = 0.0002 if self.presence != "none" else 0.0
            loss = 0.0005 * (self.t_air - self.t_out)          # тепловтрати (τ ≈ 33 хв)
            self.t_air += (gain + people - loss) * dt

            # природне світло плавно прямує до цілі
            self.ambient += (self.ambient_target - self.ambient) * min(1.0, dt / 8.0)

            if self.extra_load_w and now > self.extra_until:
                self.extra_load_w = 0.0

            # PIR: коли людина в кімнаті — серії спрацювань (ON 6–15 с, пауза 3–12 с)
            for room, b in self._burst.items():
                if self.presence != room:
                    b["on"] = False
                    continue
                if now >= b["until"]:
                    b["on"] = not b["on"]
                    b["until"] = now + (random.uniform(6, 15) if b["on"] else random.uniform(3, 12))

    def motion(self, room):
        with self.lock:
            return self._burst[room]["on"]

    def temperature(self):
        with self.lock:
            return self.t_air + random.gauss(0, 0.05)

    def humidity(self):
        with self.lock:
            now = self.clock()
            rh = 46.0 + (21.0 - self.t_air) * 1.2 + 2.0 * math.sin((now - self.t0) / 900.0)
            return clamp(rh + random.gauss(0, 0.3), 25.0, 75.0)

    def lux(self):
        with self.lock:
            lamps = 0.4 * sum(self.light_pct.values())         # 100 % лампи ≈ +40 лк у коридорі
            return max(0.0, (self.ambient + lamps) * (1 + random.gauss(0, 0.02)))

    def power_w(self):
        with self.lock:
            now = self.clock()
            fridge = 140.0 if ((now - self.t0) % 1200) < 480 else 8.0   # компресор холодильника
            base = 110.0 + fridge                                        # роутер, standby, холодильник
            lamps = 60.0 * self.light_pct["living"] / 100 + 40.0 * self.light_pct["bedroom"] / 100
            heater = self.HEATER_W if self.heating else 0.0
            total = base + lamps + heater + self.extra_load_w
            return max(0.0, total * (1 + random.gauss(0, 0.015)))


def apply_scenario(flat, name):
    with flat.lock:
        if name == "normal":
            flat.presence, flat.ambient_target, flat.extra_load_w = "living", 180.0, 0.0
        elif name == "dark_motion":
            flat.presence, flat.ambient_target = "living", 4.0
        elif name == "bedroom_dark":
            flat.presence, flat.ambient_target = "bedroom", 4.0
        elif name == "away":
            flat.presence = "none"
        elif name == "return":
            flat.presence = "living"
        elif name == "overload":
            flat.extra_load_w = 2400.0
            flat.extra_until = flat.clock() + 120.0
        else:
            return False
    log(f"СЦЕНАРІЙ «{name}»: {SCENARIOS[name]}")
    return True


# --------------------------------------------------------------------------------------
#  MQTT-пристрої
# --------------------------------------------------------------------------------------
class Device:
    listens = False      # чи підписаний на .../set

    def __init__(self, flat, zone, name, user):
        self.flat, self.zone, self.name, self.user = flat, zone, name, user
        self.base = f"{OBJ}/{zone}/{name}"
        self.t_tel = f"{self.base}/telemetry"
        self.t_set = f"{self.base}/set"
        self.t_state = f"{self.base}/state"
        self.t_status = f"{self.base}/status"

        password = os.getenv("MQTT_PASS_" + user.upper())
        if not password:
            sys.exit(f"Немає пароля у змінній MQTT_PASS_{user.upper()} (див. .env)")

        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"sim-{user}",
            protocol=mqtt.MQTTv311,
        )
        self.client.username_pw_set(user, password)
        self.client.tls_set(ca_certs=CA_FILE)           # перевірка сертифіката брокера нашим CA + hostname
        # Last Will: якщо процес «впаде», брокер сам опублікує offline (retain)
        self.client.will_set(self.t_status, "offline", qos=QOS_STATUS, retain=True)
        self.client.reconnect_delay_set(1, 10)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    # ----- життєвий цикл -----
    def start(self):
        self.client.connect_async(HOST, PORT, KEEPALIVE)
        self.client.loop_start()

    def stop(self):
        try:
            info = self.client.publish(self.t_status, "offline", qos=QOS_STATUS, retain=True)
            info.wait_for_publish(2)
        except Exception:
            pass
        self.client.disconnect()
        self.client.loop_stop()

    # ----- колбеки paho v2 -----
    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log(f"[{self.user}] помилка підключення: {reason_code}")
            return
        log(f"[{self.user}] підключено (TLS), публікую status=online")
        client.publish(self.t_status, "online", qos=QOS_STATUS, retain=True)
        if self.listens:
            client.subscribe(self.t_set, qos=QOS_COMMAND)
        self.on_connected()

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        log(f"[{self.user}] відключено: {reason_code}")

    def _on_message(self, client, userdata, msg):
        if msg.topic == self.t_set:
            self.handle_message(msg.payload)

    # ----- хуки -----
    def on_connected(self):
        pass

    def handle_message(self, payload):
        pass

    def tick(self, now):
        pass

    # ----- публікація -----
    def publish_json(self, topic, obj, qos, retain=False):
        self.client.publish(topic, json.dumps(obj, ensure_ascii=False), qos=qos, retain=retain)

    def publish_telemetry(self, value, unit):
        self.publish_json(self.t_tel, {"value": value, "unit": unit, "ts": int(time.time())}, QOS_TELEMETRY)


# ---------------- сенсори ----------------
class Sensor(Device):
    unit = ""
    period = 5.0

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._next = 0.0

    def read(self):
        raise NotImplementedError

    def tick(self, now):
        if now >= self._next:
            self.publish_telemetry(self.read(), self.unit)
            self._next = now + self.period


class MotionSensor(Sensor):
    unit = "bool"
    period = 10.0   # heartbeat; зміни публікуються одразу

    def __init__(self, flat, zone, user):
        super().__init__(flat, zone, "motion", user)
        self._last = None

    def read(self):
        return 1 if self.flat.motion(self.zone) else 0

    def tick(self, now):
        value = self.read()
        if value != self._last or now >= self._next:
            self._last = value
            self.publish_telemetry(value, self.unit)
            self._next = now + self.period


class TemperatureSensor(Sensor):
    unit = "C"

    def __init__(self, flat, zone, user):
        super().__init__(flat, zone, "temperature", user)

    def read(self):
        return round(self.flat.temperature(), 2)


class HumiditySensor(Sensor):
    unit = "%"

    def __init__(self, flat, zone, user):
        super().__init__(flat, zone, "humidity", user)

    def read(self):
        return round(self.flat.humidity(), 1)


class LuxSensor(Sensor):
    unit = "lx"

    def __init__(self, flat, zone, user):
        super().__init__(flat, zone, "lux", user)

    def read(self):
        return int(round(self.flat.lux()))


class PowerMeter(Sensor):
    unit = "W"
    period = 3.0

    def __init__(self, flat, zone, user):
        super().__init__(flat, zone, "power", user)

    def read(self):
        return round(self.flat.power_w(), 1)


# ---------------- актуатори ----------------
class Actuator(Device):
    listens = True
    state_period = 30.0

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._last_sig = None
        self._next = 0.0

    def signature(self):
        raise NotImplementedError

    def publish_state(self):
        raise NotImplementedError

    def apply(self, cmd):
        raise NotImplementedError

    def on_connected(self):
        self._last_sig = None       # примусово перепублікувати стан після (пере)підключення

    def handle_message(self, payload):
        try:
            cmd = json.loads(payload.decode("utf-8"))
            if not isinstance(cmd, dict):
                raise ValueError("очікується JSON-об'єкт")
        except (ValueError, UnicodeDecodeError) as e:
            log(f"[{self.user}] ігнорую некоректну команду {payload[:60]!r}: {e}")
            return
        log(f"[{self.user}] команда set: {cmd}")
        try:
            with self.flat.lock:
                self.apply(cmd)
        except (TypeError, ValueError) as e:
            log(f"[{self.user}] невалідні значення в команді: {e}")
            return
        self.publish_state()
        self._last_sig = self.signature()

    def tick(self, now):
        sig = self.signature()
        if sig != self._last_sig or now >= self._next:
            self._last_sig = sig
            self._next = now + self.state_period
            self.publish_state()


class Light(Actuator):
    def __init__(self, flat, zone, user):
        super().__init__(flat, zone, "light", user)
        self.last_nonzero = 100

    def signature(self):
        return self.flat.light_pct[self.zone]

    def publish_state(self):
        pct = self.flat.light_pct[self.zone]
        self.publish_json(self.t_state, {"state": "ON" if pct > 0 else "OFF", "brightness": pct},
                          QOS_STATE, retain=True)

    def apply(self, cmd):
        pct = self.flat.light_pct[self.zone]
        if "state" in cmd:
            s = str(cmd["state"]).upper()
            if s == "ON":
                pct = pct if pct > 0 else self.last_nonzero
            elif s == "OFF":
                pct = 0
            else:
                raise ValueError(f"state має бути ON/OFF, а не {cmd['state']!r}")
        if "brightness" in cmd:
            pct = int(clamp(float(cmd["brightness"]), 0, 100))
        if pct > 0:
            self.last_nonzero = pct
        self.flat.light_pct[self.zone] = pct


class Heater(Actuator):
    def __init__(self, flat, zone, user):
        super().__init__(flat, zone, "heater", user)

    def signature(self):
        f = self.flat
        return (f.heater_on, f.heater_setpoint, f.heating)

    def publish_state(self):
        f = self.flat
        self.publish_json(
            self.t_state,
            {"state": "ON" if f.heater_on else "OFF", "setpoint": f.heater_setpoint, "heating": f.heating},
            QOS_STATE, retain=True,
        )

    def apply(self, cmd):
        f = self.flat
        if "state" in cmd:
            s = str(cmd["state"]).upper()
            if s not in ("ON", "OFF"):
                raise ValueError(f"state має бути ON/OFF, а не {cmd['state']!r}")
            f.heater_on = s == "ON"
            if not f.heater_on:
                f.heating = False
        if "setpoint" in cmd:
            f.heater_setpoint = clamp(round(float(cmd["setpoint"]) * 2) / 2, 10.0, 28.0)   # крок 0.5 °C


class ScenarioController(Device):
    """Слухає flat/sim/scenario/set і перемикає сценарії (для демонстрації правил)."""
    listens = True

    def __init__(self, flat, user):
        super().__init__(flat, "sim", "controller", user)
        self.t_set = SCENARIO_TOPIC

    def handle_message(self, payload):
        name = payload.decode("utf-8", "ignore").strip()
        if not apply_scenario(self.flat, name):
            log(f"[{self.user}] невідомий сценарій {name!r}; доступні: {', '.join(SCENARIOS)}")


# --------------------------------------------------------------------------------------
def send_scenario(name):
    """Клієнт-тригер: публікує назву сценарію (окремий користувач sim_trigger, право лише на запис)."""
    password = os.getenv("MQTT_PASS_SIM_TRIGGER")
    if not password:
        sys.exit("Немає MQTT_PASS_SIM_TRIGGER")
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="sim-trigger-cli", protocol=mqtt.MQTTv311)
    c.username_pw_set("sim_trigger", password)
    c.tls_set(ca_certs=CA_FILE)
    c.connect(HOST, PORT, 10)
    c.loop_start()
    info = c.publish(SCENARIO_TOPIC, name, qos=QOS_COMMAND)
    info.wait_for_publish(5)
    ok = info.is_published()
    c.loop_stop()
    c.disconnect()
    print(f"Сценарій «{name}» надіслано" if ok else "Не вдалося надіслати (перевірте пароль/ACL)")
    return 0 if ok else 1


def build_devices(flat):
    return [
        MotionSensor(flat, "living", "motion_living"),
        MotionSensor(flat, "bedroom", "motion_bedroom"),
        TemperatureSensor(flat, "living", "temp_living"),
        HumiditySensor(flat, "living", "hum_living"),
        LuxSensor(flat, "hall", "lux_hall"),
        PowerMeter(flat, "hall", "power_hall"),
        Light(flat, "living", "light_living"),
        Light(flat, "bedroom", "light_bedroom"),
        Heater(flat, "living", "heater_living"),
    ]


def main():
    ap = argparse.ArgumentParser(
        description="Симулятор розумної квартири (Варіант 1)",
        epilog="Сценарії:\n" + "\n".join(f"  {k:13s} {v}" for k, v in SCENARIOS.items()),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--scenario", choices=SCENARIOS, default="normal", help="початковий сценарій")
    ap.add_argument("--send", choices=SCENARIOS, help="надіслати сценарій у запущений симулятор і вийти")
    args = ap.parse_args()

    if args.send:
        sys.exit(send_scenario(args.send))

    flat = Flat()
    apply_scenario(flat, args.scenario)
    devices = build_devices(flat) + [ScenarioController(flat, "sim_ctl")]
    for d in devices:
        d.start()

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    last = time.time()
    while not stop.is_set():
        now = time.time()
        flat.step(now - last)
        last = now
        for d in devices:
            d.tick(now)
        stop.wait(0.5)

    log("Зупинка: коректно публікую offline (Last Will спрацьовує лише при аварійному обриві)")
    for d in devices:
        d.stop()


if __name__ == "__main__":
    main()

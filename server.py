#!/usr/bin/python3

import json
import logging
import signal
import time
from pathlib import Path

import serial
from ha_mqtt_discoverable import Settings, DeviceInfo
from ha_mqtt_discoverable.sensors import Sensor, SensorInfo
import paho.mqtt.client as paho
from paho.mqtt.enums import CallbackAPIVersion

DEFAULT_BAUD = 9600
DEFAULT_READ_INTERVAL = 10
SERIAL_TIMEOUT = 3
JSON_DECODER = json.JSONDecoder()
CONFIG_PATH = Path(__file__).parent / "config.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("visor")

SENSOR_DEFS = [
    # (key,           name,          device_class,                        unit)
    ("pm1",          "PM1.0",        "pm1",                              "µg/m³"),
    ("pm25",         "PM2.5",        "pm25",                             "µg/m³"),
    ("pm10",         "PM10.0",       "pm10",                             "µg/m³"),
    ("co2",          "CO2",          "carbon_dioxide",                   "ppm"),
    ("temperature",  "Temperature",  "temperature",                      "°C"),
    ("humidity",     "Humidity",     "humidity",                         "%"),
    ("voc",          "VOC",          "volatile_organic_compounds_parts", "ppm"),
    ("iaq",          "IAQ",          "aqi",                              None),
]


class SerialReader:
    """Persistent serial connection with buffered JSON extraction.

    Keeps the port open between read cycles and accumulates partial data
    in an internal buffer so that JSON objects fragmented across multiple
    serial reads are reassembled automatically.
    """

    def __init__(self, name: str, port: str, baud: int = DEFAULT_BAUD):
        self.name = name
        self.port = port
        self.baud = baud
        self._ser: serial.Serial | None = None
        self._buf = ""
        self._initialized = False

    @property
    def is_open(self) -> bool:
        return self._ser is not None and self._ser.is_open

    def _ensure_open(self) -> bool:
        if self.is_open:
            return True
        try:
            log.info("Opening %s (%s)", self.name, self.port)
            self._ser = serial.Serial(self.port, self.baud, timeout=SERIAL_TIMEOUT)
            self._ser.reset_input_buffer()
            self._buf = ""
            self._initialized = False
            return True
        except serial.SerialException as e:
            log.warning("Cannot open %s: %s", self.name, e)
            self._ser = None
            return False

    def close(self):
        if self._ser:
            self._ser.close()
        self._ser = None
        self._buf = ""

    def read_json(self, max_lines: int = 5) -> dict | None:
        """Read lines until a complete JSON object is extracted from the buffer."""
        if not self._ensure_open():
            return None

        if not self._initialized:
            self._ser.readline()
            self._initialized = True

        for _ in range(max_lines):
            try:
                raw = self._ser.readline()
            except serial.SerialException as e:
                log.warning("Read error on %s: %s", self.name, e)
                self.close()
                return None

            if not raw:
                continue

            self._buf += raw.decode(errors="replace")

            if len(self._buf) > 4096:
                self._buf = self._buf[-2048:]

            start = self._buf.find("{")
            if start == -1:
                self._buf = ""
                continue

            try:
                obj, end = JSON_DECODER.raw_decode(self._buf, start)
                self._buf = self._buf[end:]
                return obj
            except json.JSONDecodeError:
                self._buf = self._buf[start:]
                continue

        return None


def extract_particulate(data: dict) -> dict:
    return {
        "pm1": data["pm10"],
        "pm25": data["pm25"],
        "pm10": data["pm100"],
    }


def extract_gas(data: dict) -> dict | None:
    """Returns None when carbonDioxide is 0 so the caller retries."""
    if data.get("carbonDioxide", 0) == 0:
        return None
    return {
        "co2": round(data["carbonDioxide"]),
        "temperature": round(data["temperature"], 1),
        "humidity": round(data["relativeHumidity"], 1),
    }


def extract_iaq(data: dict) -> dict:
    return {
        "voc": round(data["breathVOC"], 2),
        "iaq": round(data["staticIaq"], 2),
    }


SENSOR_PORTS = [
    ("particulate", "smoke-path", extract_particulate),
    ("gas",         "co2-path",   extract_gas),
    ("iaq",         "air-path",   extract_iaq),
]


def get_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def make_mqtt_client(config):
    client = paho.Client(callback_api_version=CallbackAPIVersion.VERSION2)
    client.username_pw_set(config["mqtt_username"], config["mqtt_password"])
    client.connect(config["mqtt_host"], config.get("mqtt_port", 1883))
    client.loop_start()
    return client


def build_sensors(mqtt_settings, device):
    sensors = {}
    for key, name, device_class, unit in SENSOR_DEFS:
        kwargs = dict(name=name, unique_id=f"visor_{key}", device=device)
        if device_class:
            kwargs["device_class"] = device_class
        if unit:
            kwargs["unit_of_measurement"] = unit
        info = SensorInfo(**kwargs)
        sensors[key] = Sensor(Settings(mqtt=mqtt_settings, entity=info))
    return sensors


def read_sensor(reader: SerialReader, extractor, max_attempts: int = 5) -> dict:
    """Try to get one valid reading, retrying when the extractor rejects data."""
    for _ in range(max_attempts):
        data = reader.read_json()
        if data is None:
            return {}
        try:
            result = extractor(data)
            if result is not None:
                return result
        except (KeyError, TypeError) as e:
            log.warning("Unexpected data from %s: %s", reader.name, e)
    return {}


def main():
    config = get_config()
    baud = config.get("baudrate", DEFAULT_BAUD)
    interval = config.get("read_interval", DEFAULT_READ_INTERVAL)

    client = make_mqtt_client(config)
    mqtt_settings = Settings.MQTT(host=config["mqtt_host"], client=client)

    device = DeviceInfo(
        name="Visor",
        identifiers="visor_air_quality",
        manufacturer="Val",
        model="Visor",
    )

    sensors = build_sensors(mqtt_settings, device)

    readers = [
        (SerialReader(name, config[port_key], baud), extractor)
        for name, port_key, extractor in SENSOR_PORTS
    ]

    running = True

    def handle_signal(sig, _frame):
        nonlocal running
        log.info("Received %s, shutting down…", signal.Signals(sig).name)
        running = False

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    log.info("Visor started (interval=%ds, baud=%d)", interval, baud)

    try:
        while running:
            readings = {}
            for reader, extractor in readers:
                readings.update(read_sensor(reader, extractor))

            if readings:
                for key, value in readings.items():
                    sensors[key].set_state(value)
                log.info("Published: %s", readings)
            else:
                log.warning("No sensor data this cycle")

            for _ in range(interval * 10):
                if not running:
                    break
                time.sleep(0.1)
    finally:
        for reader, _ in readers:
            reader.close()
        client.disconnect()
        client.loop_stop()
        log.info("Visor stopped")


if __name__ == "__main__":
    main()

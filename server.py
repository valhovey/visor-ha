#!/usr/bin/python3

from ha_mqtt_discoverable import Settings, DeviceInfo
from ha_mqtt_discoverable.sensors import Sensor, SensorInfo
import paho.mqtt.client as paho
from paho.mqtt.enums import CallbackAPIVersion
import serial
import json

BAUD = 9600
SERIAL_TIMEOUT = 5
JSON_DECODER = json.JSONDecoder()

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


def get_config():
    with open("config.json", "r") as file:
        return json.load(file)


def make_mqtt_client(config):
    """Create and connect a single shared paho MQTT client."""
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


def parse_json_line(line):
    """Extract the first JSON object from a serial line, tolerating
    leading garbage or concatenated trailing data."""
    text = line.decode(errors="replace").strip()
    start = text.find("{")
    if start == -1:
        raise ValueError(f"No JSON object found in: {text!r}")
    obj, _ = JSON_DECODER.raw_decode(text, start)
    return obj


def read_serial_json(port, attempts=4):
    """Read lines from a serial port until one parses as valid JSON.
    The first line after a buffer flush is always discarded since it
    may be a partial mid-transmission fragment."""
    with serial.Serial(port, BAUD, timeout=SERIAL_TIMEOUT) as ser:
        ser.reset_input_buffer()
        ser.readline()
        for _ in range(attempts):
            line = ser.readline()
            if not line:
                raise TimeoutError(f"No data received from {port}")
            try:
                return parse_json_line(line)
            except (ValueError, json.JSONDecodeError):
                continue
    raise ValueError(f"No valid JSON from {port} after {attempts} lines")


def read_particulate(port):
    data = read_serial_json(port)
    return {
        "pm1": data["pm10"],
        "pm25": data["pm25"],
        "pm10": data["pm100"],
    }


def read_gas(port, attempts=5):
    """Read CO2/temperature/humidity.  Retries to skip both partial
    serial lines and the initial zero-value startup readings."""
    with serial.Serial(port, BAUD, timeout=SERIAL_TIMEOUT) as ser:
        ser.reset_input_buffer()
        ser.readline()
        for _ in range(attempts):
            line = ser.readline()
            if not line:
                raise TimeoutError(f"No data received from {port}")
            try:
                data = parse_json_line(line)
            except (ValueError, json.JSONDecodeError):
                continue
            if data.get("carbonDioxide", 0) != 0:
                return {
                    "co2": round(data["carbonDioxide"]),
                    "temperature": round(data["temperature"], 1),
                    "humidity": round(data["relativeHumidity"], 1),
                }
    raise ValueError("CO2 sensor returned no valid readings")


def read_iaq(port):
    data = read_serial_json(port)
    return {
        "voc": round(data["breathVOC"], 2),
        "iaq": round(data["staticIaq"], 2),
    }


def read_all_sensors(config):
    readings = {}
    for name, port_key, reader in [
        ("particulate", "smoke-path", read_particulate),
        ("gas",         "co2-path",   read_gas),
        ("iaq",         "air-path",   read_iaq),
    ]:
        try:
            readings.update(reader(config[port_key]))
        except Exception as e:
            print(f"Error reading {name} sensor: {e}")
    return readings


def publish(sensors, readings):
    for key, value in readings.items():
        sensors[key].set_state(value)


if __name__ == "__main__":
    config = get_config()

    client = make_mqtt_client(config)

    mqtt_settings = Settings.MQTT(
        host=config["mqtt_host"],
        client=client,
    )

    device = DeviceInfo(
        name="Visor",
        identifiers="visor_air_quality",
        manufacturer="Val",
        model="Visor",
    )

    sensors = build_sensors(mqtt_settings, device)
    readings = read_all_sensors(config)
    publish(sensors, readings)
    print(readings)

    client.disconnect()
    client.loop_stop()

#!/usr/bin/python3

from ha_mqtt_discoverable import Settings, DeviceInfo
from ha_mqtt_discoverable.sensors import Sensor, SensorInfo
from datetime import datetime, timezone
import serial
import json
import time

def get_config():
    with open('config.json', 'r') as file:
        return json.load(file)

config = get_config()
PORT = config["serial-path"]
BAUD = config["baudrate"]
LAT = config["lat"]
LON = config["lon"]
OFFSET = config["sqm_offset"]

AMBIENT_WEATHER_ENABLE = config["ambient_weather_enable"]
AMBIENT_API_KEY = config["ambient_weather"]["api_key"]
AMBIENT_APP_KEY = config["ambient_weather"]["app_key"]

INFLUXDB_ENABLE = config["influxdb_enable"]
INFLUXDB_HOST = config["influxdb_host"]
INFLUXDB_TOKEN = config["influxdb_token"]
INFLUXDB_ORG = config["influxdb_org"]
INFLUXDB_BUCKET = config["influxdb_bucket"]

PIRATE_WEATHER_ENABLE = config["pirate_weather_enable"]
PIRATE_WEATHER_API_KEY = config["pirate_weather_api_key"]

mqtt_settings = Settings.MQTT(
    host=config["mqtt_host"],
    username=config["mqtt_username"],
    password=config["mqtt_password"]
)

device = DeviceInfo(
    name="SQM Meter",
    identifiers="sqm_meter",
    manufacturer="Val",
    model="SQM-LU"
)

sqm = SensorInfo(
    name="Sky Quality",
    unit_of_measurement="mag/(arcsec^2)",
    unique_id="sqm_sensor_sqm",
    device=device
)

sensor_temp = SensorInfo(
    name="Sensor Temperature",
    device_class="temperature",
    unit_of_measurement="°C",
    unique_id="sqm_sensor_temp",
    device=device
)

air_temp = SensorInfo(
    name="Air Temperature",
    device_class="temperature",
    unit_of_measurement="°C",
    unique_id="sqm_air_temp",
    device=device
)

air_pressure = SensorInfo(
    name="Air Pressure",
    device_class="atmospheric_pressure",
    unit_of_measurement="inHg",
    unique_id="sqm_air_pressure",
    device=device
)

air_humidity = SensorInfo(
    name="Air Humidity",
    device_class="humidity",
    unit_of_measurement="%",
    unique_id="sqm_air_humidity",
    device=device
)

moon_alt = SensorInfo(
    name="Moon Altitude",
    unit_of_measurement="˚",
    unique_id="sqm_moon_alt",
    device=device
)

moon_az = SensorInfo(
    name="Moon Azimuth",
    unit_of_measurement="˚",
    unique_id="sqm_moon_az",
    device=device
)

moon_illum = SensorInfo(
    name="Moon Illumination",
    unit_of_measurement="%",
    unique_id="sqm_moon_illumination",
    device=device
)

cloud_coverage = SensorInfo(
    name="Cloud Coverage",
    unit_of_measurement="%",
    unique_id="sqm_cloud_coverage",
    device=device
)

settings_sqm = Settings(mqtt=mqtt_settings, entity=sqm)
settings_sensor_temp = Settings(mqtt=mqtt_settings, entity=sensor_temp)
settings_air_temp = Settings(mqtt=mqtt_settings, entity=air_temp)
settings_air_pressure = Settings(mqtt=mqtt_settings, entity=air_pressure)
settings_air_humidity = Settings(mqtt=mqtt_settings, entity=air_humidity)
settings_moon_alt = Settings(mqtt=mqtt_settings, entity=moon_alt)
settings_moon_az = Settings(mqtt=mqtt_settings, entity=moon_az)
settings_moon_illum = Settings(mqtt=mqtt_settings, entity=moon_illum)
settings_cloud_coverage = Settings(mqtt=mqtt_settings, entity=cloud_coverage)

sqm_sensor = Sensor(settings_sqm)
sensor_temp_sensor = Sensor(settings_sensor_temp)
air_temp_sensor = Sensor(settings_air_temp)
air_pressure_sensor = Sensor(settings_air_pressure)
air_humidity_sensor = Sensor(settings_air_humidity)
moon_alt_sensor = Sensor(settings_moon_alt)
moon_az_sensor = Sensor(settings_moon_az)
moon_illum_sensor = Sensor(settings_moon_illum)
cloud_coverage_sensor = Sensor(settings_cloud_coverage)

def get_ambient_weather():
    url = "https://api.ambientweather.net/v1/devices"
    params = {
        "apiKey": AMBIENT_API_KEY,
        "applicationKey": AMBIENT_APP_KEY
    }
    response = requests.get(url, params=params)
    response.raise_for_status()

    devices = response.json()

    if not devices:
        print("No stations found")
        exit()

    # Each device contains a list of data points
    device = devices[0]

    last_data = device["lastData"]

    temperature = last_data.get("tempf")
    humidity = last_data.get("humidity")
    pressure = last_data.get("baromrelin")
    tempc = round((temperature - 32) * (5/9), 2)

    ambient = {
        "air_temp_c": tempc,
        "humidity_rh": humidity,
        "pressure_inHg": pressure
    }

    return ambient

def get_cloud_cover():
    url = f"https://api.pirateweather.net/forecast/{PIRATE_WEATHER_API_KEY}/{LAT},{LON}"

    params = {
        "exclude": "minutely,hourly,daily,alerts,flags"
    }

    r = requests.get(url, params=params, timeout=5)
    r.raise_for_status()

    data = r.json()
    cloud_fraction = data["currently"]["cloudCover"]

    return { "cloud_coverage": round(cloud_fraction * 100, 2) }

def get_reading():
    with serial.Serial(
            port=PORT,
            baudrate=BAUD,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            bytesize=serial.EIGHTBITS,
            timeout=2
        ) as ser:
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        ser.write(b"rx\n")
        line = ser.readline()

        return line.decode(errors="replace").strip()

def get_moon_stats(now):
    eph = load('de421.bsp')
    moon = eph['moon']
    earth = eph['earth']
    observer = earth + Topos(latitude_degrees=LAT, longitude_degrees=LON)
    astrometric = observer.at(now).observe(moon)
    alt, az, _ = astrometric.apparent().altaz()
    illum = fraction_illuminated(eph, 'moon', now)

    moon = {
        "moon_alt_deg": round(alt.degrees, 2),
        "moon_az_deg": round(az.degrees, 2),
        "moon_illum": round(illum * 100, 2),
    }

    return moon

def parse_reading(reading):
    values = list(map(str.strip, reading.split(",")))
    sqm = float(values[1][:-1]) + OFFSET
    temp = float(values[5][:-1])

    parsed = {
        "sqm": sqm,
        "sensor_temp_c": temp
    }

    return parsed

def get_all_data(now_utc):
    ts = load.timescale()
    now = ts.from_datetime(now_utc)
    reading = get_reading()
    parsed = parse_reading(reading)
    moon = get_moon_stats(now)
    ambient = {}
    cloud_coverage = {}

    try:
        ambient = get_ambient_weather()
        cloud_coverage = get_cloud_cover()
    except:
        print("Error fetching net data")

    return {
        "now": now_utc.isoformat(),
        **parsed,
        **moon,
        **ambient,
        **cloud_coverage
    }

def publish_ha_data(data):
    sqm_sensor.set_state(data["sqm"])
    sensor_temp_sensor.set_state(data["sensor_temp_c"])
    moon_alt_sensor.set_state(float(data["moon_alt_deg"]))
    moon_az_sensor.set_state(float(data["moon_az_deg"]))
    moon_illum_sensor.set_state(float(data["moon_illum"]))

    if AMBIENT_WEATHER_ENABLE:
        air_temp_sensor.set_state(data["air_temp_c"])
        air_pressure_sensor.set_state(data["pressure_inHg"])
        air_humidity_sensor.set_state(data["humidity_rh"])

    if PIRATE_WEATHER_ENABLE:
        cloud_coverage_sensor.set_state(data["cloud_coverage"])

def publish_influxdb_data(data, now):
    client = InfluxDBClient(
        url=INFLUXDB_HOST,
        token=INFLUXDB_TOKEN,
        org=INFLUXDB_ORG
    )
    write_api = client.write_api(write_options=SYNCHRONOUS)

    point = (
        Point("sky_monitor")
        .tag("device", "sqm_meter")
        .field("sqm", float(data["sqm"]))
        .field("sensor_temp_c", float(data["sensor_temp_c"]))
        .field("moon_alt_deg", float(data["moon_alt_deg"]))
        .field("moon_az_deg", float(data["moon_az_deg"]))
        .field("moon_illum", float(data["moon_illum"]))
        .time(now)
    )

    if AMBIENT_WEATHER_ENABLE:
        point.field("air_temp_c", float(data["air_temp_c"]))
        point.field("pressure_inHg", float(data["pressure_inHg"]))
        point.field("humidity_rh", float(data["humidity_rh"]))

    if PIRATE_WEATHER_ENABLE:
        point.field("cloud_coverage", data["cloud_coverage"])

    write_api.write(
        bucket=INFLUXDB_BUCKET,
        org=INFLUXDB_ORG,
        record=point
    )

def get_mock_reading():
    return "r, 09.66m,0000012099Hz,0000000000c,0000000.000s, 024.8C"

if __name__ == "__main__":
    now_utc = datetime.now(timezone.utc)
    values = get_all_data(now_utc)
    publish_ha_data(values)

    if INFLUXDB_ENABLE:
        publish_influxdb_data(values, now_utc)
    
    print(values)

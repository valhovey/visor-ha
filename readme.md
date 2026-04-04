# Visor - Home Assistant

My first air quality project involved hooking up various sensors to a server over serial, combining the data, and making it available somehow. Over the years, this has morphed into a proxy to forward the data over MQTT to Home Assistant. This version of the project improves [on the original](https://github.com/valhovey/visor-mqtt) by using the Python Home Assistant MQTT library `ha-mqtt-discoverable` to add units and easier processing of device/entity relationships which previously were managed manually and did not fully conform to Home Assistant standards (air quality was not in a compatible unit with other sensors I have, for instance).

## Sensors

| Sensor | Keys | Serial Port Config |
|---|---|---|
| Particulate (PM1.0 / PM2.5 / PM10) | `pm1`, `pm25`, `pm10` | `smoke-path` |
| CO2 / Temperature / Humidity | `co2`, `temperature`, `humidity` | `co2-path` |
| VOC / IAQ | `voc`, `iaq` | `air-path` |

## Configuration

Copy `config.example.json` to `config.json` and fill in the values:

```json
{
  "smoke-path": "/dev/serial/by-id/...",
  "air-path": "/dev/serial/by-id/...",
  "co2-path": "/dev/serial/by-id/...",
  "baudrate": 9600,
  "read_interval": 10,
  "mqtt_host": "192.168.100.76",
  "mqtt_port": 1883,
  "mqtt_username": "homeassistant",
  "mqtt_password": "password"
}
```

- **`baudrate`** — Serial baud rate (default `9600`).
- **`read_interval`** — Seconds between sensor read cycles (default `10`).
- **`mqtt_port`** — MQTT broker port (default `1883`).

Serial paths under `/dev/serial/by-id/` are recommended over `/dev/ttyUSBx` because they are stable across reboots.

## Running

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (or pip)

### Install dependencies

```bash
uv sync
```

### Run directly

```bash
uv run server.py
```

The script runs continuously, reading sensors every `read_interval` seconds and publishing to MQTT. Press Ctrl+C to stop.

### Install as a systemd service

1. Create a dedicated user (must be in `dialout` for serial port access):

```bash
sudo useradd -r -s /usr/sbin/nologin -G dialout visor
```

2. Copy the project to `/opt/visor-ha` (or wherever you prefer):

```bash
sudo cp -r . /opt/visor-ha
sudo chown -R visor:visor /opt/visor-ha
```

3. Create the virtualenv and install dependencies:

```bash
cd /opt/visor-ha
sudo -u visor uv sync
```

4. Make sure `config.json` exists and is populated in the install directory.

5. Install the service unit:

```bash
sudo cp visor-ha.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now visor-ha
```

6. Check status and logs:

```bash
sudo systemctl status visor-ha
sudo journalctl -u visor-ha -f
```

### Customizing the service

If you installed to a different path, edit `WorkingDirectory` and `ExecStart` in the service file before copying it. If you want the service to run as your own user instead of a dedicated `visor` user, change the `User=` line accordingly (and ensure that user is in the `dialout` group).

### Uninstalling

```bash
sudo systemctl disable --now visor-ha
sudo rm /etc/systemd/system/visor-ha.service
sudo systemctl daemon-reload
```

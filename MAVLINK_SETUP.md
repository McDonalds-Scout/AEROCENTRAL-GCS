# MAVLink Telemetry Setup

This guide explains how AeroCentral connects to PX4/Pixhawk-compatible flight controllers through MAVLink.

## Data Link

Typical link:

```text
Flight Controller
  -> Telemetry radio or USB
  -> Computer serial port or UDP
  -> MAVLink connector
  -> Backend API
  -> Ground Control Interface
```

PX6C / Pixhawk 6C compatible workflows use `px6c_connector.py` through the backend connection manager.

Default UI address:

```text
http://127.0.0.1:8080/
```

## 1. Install MAVLink Dependencies

Dependencies are managed by `requirements.txt`. If they need to be reinstalled, run:

```powershell
python -m pip install -r requirements.txt
```

The recommended startup script also checks dependencies:

```powershell
.\start-ui.cmd
```

## 2. Serial MAVLink Connection

After plugging in a telemetry device or USB flight controller, start the UI and open Connection Settings.

Common serial settings:

```text
Connection type: USB serial
Port: COMx
Baud rate: 57600 or 115200
```

The selected baud rate must match the telemetry module or flight controller configuration.

For direct troubleshooting, the project also keeps compatibility helpers such as:

```powershell
.\start-px6c.cmd
```

These helpers are intended for development or low-level diagnostics. The normal operator path is still:

```powershell
.\start-ui.cmd
```

## 3. UDP MAVLink Connection

Common UDP listener mode:

```text
Listen address: 0.0.0.0
Listen port: 14550
```

This mode expects the flight controller, telemetry radio, or router to send MAVLink UDP packets to the computer.

Common UDP target mode:

```text
Target flight controller IP: 127.0.0.1 for simulator, or the configured aircraft link IP
Target port: 14550
```

Do not commit real aircraft IP addresses to the repository.

## 4. MAVLink Messages Used by the UI

Core messages:

- `HEARTBEAT`: vehicle online state, armed state, vehicle type, base mode, and custom mode.
- `ATTITUDE`: roll, pitch, and yaw.
- `GLOBAL_POSITION_INT`: latitude, longitude, altitude, relative altitude, and heading.
- `VFR_HUD`: ground speed, airspeed, heading, throttle, and climb rate.
- `SYS_STATUS`: battery voltage and remaining capacity.
- `BATTERY_STATUS`: battery voltage, current, and remaining capacity when available.
- `GPS_RAW_INT`: satellite count and fix type.
- `RC_CHANNELS`: real-time RC input channels.
- `STATUSTEXT`: PX4 warning, calibration, failsafe, and command rejection text.
- `COMMAND_ACK`: command result for Arm, Disarm, mode switching, calibration, mission, and actuator commands.

## 5. GCS Behavior

AeroCentral is designed to behave as a Ground Control Station, not only as a passive telemetry viewer.

Required GCS behavior:

- Send GCS Heartbeat at 1 Hz.
- Use `MAV_TYPE_GCS`.
- Use `MAV_AUTOPILOT_INVALID`.
- Wait for vehicle Heartbeat before identifying `target_system` and `target_component`.
- Send commands to the identified target.
- Display COMMAND_ACK and STATUSTEXT for command results.

## 6. VS Code Launch Options

The repository may include VS Code launch entries for local testing. Typical workflows include:

- UI plus MAVLink simulator for system validation.
- UI plus UDP telemetry connection.
- Serial MAVLink auto-selection for connected devices.
- PX6C / Pixhawk-compatible MAVLink connector.

Use QGroundControl as a reference when validating a new flight controller or RC setup.

## 7. Map Coordinates

PX4 GPS messages usually provide WGS-84 coordinates. The UI uses WGS-84 coordinates directly for map rendering.

The current map tiles require network access. For fully offline operation, replace the tile URL with an internal or local map tile server.

## 8. Troubleshooting

If telemetry is visible but commands fail, check:

- GCS Heartbeat is being sent.
- Vehicle Heartbeat is received.
- `target_system` and `target_component` are correct.
- COMMAND_ACK is visible.
- STATUSTEXT includes any PX4 rejection reason.
- No other program is holding the same serial port.

If QGroundControl works but AeroCentral does not, compare:

- Serial port and baud rate.
- UDP direction and firewall rules.
- MAVLink target identification.
- Requested message intervals.
- Whether RC_CHANNELS is being received.

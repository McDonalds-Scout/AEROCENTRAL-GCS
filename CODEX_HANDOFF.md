# Codex Handoff - Tianxun UAV Ground Station

Last updated: 2026-07-03

This file is the shared source of truth for any new Codex account continuing this project.
Read it before changing code. It is intentionally practical, not a marketing summary.

## Project Identity

- Project: Chinese web UAV ground station UI for PX6C / Pixhawk 6C / PX4.
- Runtime: local Python HTTP server plus native frontend. This is not React/Vite/Vue/Next.
- Main URL: `http://127.0.0.1:8080/`
- Main backend: `ground_station_server.py`
- MAVLink bridge/connector: `px6c_connector.py`
- Main frontend: `index.html`, `app.js`, `styles.css`
- Optional frontend module: `ui_modules/connection_diagnostics.js`
- Core state model: `core/vehicle_state.py`

## How To Run

Preferred:

```powershell
.\start-ui.cmd
```

Manual backend:

```powershell
C:\Users\19636\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe ground_station_server.py
```

Before starting manually, clean old backends:

```powershell
.\cleanup-ui.ps1
```

Important: do not leave multiple servers on port `8080`. Multiple old Python backends caused random 404s because requests hit different processes.

## Current Architecture

### Backend

- `ground_station_server.py`
  - Serves static frontend.
  - Provides `/api/*`.
  - Manages connection process lifecycle.
  - Owns safety manager, command queue, telemetry state, AI endpoints, reports, RC calibration, aircraft calibration.

- `px6c_connector.py`
  - Connects to PX4 using serial, `udpout`, or `udpin`.
  - Sends GCS heartbeat.
  - Receives MAVLink messages.
  - Parses telemetry, RC, ACK, STATUSTEXT, calibration progress.
  - Reads command queue `commands/px6c_commands.jsonl`.
  - Writes command status `commands/px6c_command_status.json`.

- `app/safety_manager.py`
  - Central safety gate.
  - Real commands require `real_command` mode.
  - Blocks unsafe actions when disconnected or armed.

### Frontend

- `index.html`
  - Static page shell.
  - Uses versioned `styles.css?v=gcs-58` and `app.js?v=gcs-62`.

- `app.js`
  - Main single-page frontend.
  - Dynamically injects RC Setup, GCS Diagnostic, Connection Self Check, Aircraft Calibration.
  - Uses fixed internal refresh intervals, no user-facing refresh-rate modes.

- `styles.css`
  - Dark engineering UI theme.

## MAVLink Ground Station Behavior

The backend is not only a telemetry viewer. It does send MAVLink messages.

Implemented:

- GCS HEARTBEAT in `px6c_connector.py`
  - `MAV_TYPE_GCS`
  - `MAV_AUTOPILOT_INVALID`
  - approximate 1 Hz
  - default `source_system=255`
  - default component `MAV_COMP_ID_MISSIONPLANNER` / 190

- Vehicle HEARTBEAT parsing
  - Saves `targetSystem`, `targetComponent`, vehicle type, autopilot, base mode, custom mode, MAVLink version.

- Message interval requests
  - `MAV_CMD_SET_MESSAGE_INTERVAL`
  - Requests `RC_CHANNELS`, `ATTITUDE`, `GLOBAL_POSITION_INT`, `GPS_RAW_INT`, `VFR_HUD`, `BATTERY_STATUS`, `SYS_STATUS`, `SERVO_OUTPUT_RAW`.

- COMMAND_ACK parsing
  - Keeps last 100 command ACKs.
  - Timeout is represented as `TIMEOUT`.

- STATUSTEXT parsing
  - Keeps last 100 STATUSTEXT items.
  - Highlights preflight/arming/failsafe/EKF/compass/GPS/battery/safety/RC related messages.

Do not add RC override/manual control by default.

Current code only parses `MANUAL_CONTROL`; it should not send `RC_CHANNELS_OVERRIDE` or `MANUAL_CONTROL` unless a future explicit test mode is designed with safety controls.

## Connection Modes

Supported connection modes in UI/backend:

- Demo mode
- USB serial
- UDP target mode via `udpout:<targetIp>:<targetPort>`
- UDP listen mode via `udpin:<listenAddress>:<listenPort>`

Important UDP note:

- `udpout` is the preferred bidirectional mode when target flight controller IP/port is known, e.g. `192.168.144.12`.
- Pure `udpin` listens passively and cannot proactively know the remote endpoint before packets arrive.

## Current Main Pages / UI Modules

Known pages and injected pages:

- `overview` - main dashboard with map, HUD, attitude, compass, telemetry panels, alerts.
- `history` - local flight records.
- `tuning` - PID, AI PID Advisor, report generation, parameter queue, servo/motor tests, old simple calibration panel.
- `aircraftCalibration` - new independent Aircraft Calibration Center.
- `mission` - mission route editor/planner and upload checks.
- `risk` - risk scoring based on real telemetry freshness and evidence, not a fixed fake value.
- `feasibility` - mission feasibility prediction.
- `geofence` - geofence check.
- `logCompare` - multi-log comparison.
- `flightLog` - flight controller log list/download, including USB log workflow.
- `sensorHealth` - uploaded ULG sensor health scoring.
- `connection` - serial/UDP/demo connection settings.
- `rcSetup` - RC Monitor and RC Calibration Wizard.
- `gcsDiagnostic` - GCS heartbeat, target, ACK, STATUSTEXT and RC link diagnostics.
- `connectionCheck` - connection self-check.

## Newly Added Aircraft Calibration Center

Do not confuse this with RC calibration.

New file:

- `services/aircraft_calibration_service.py`

Modified files:

- `ground_station_server.py`
- `px6c_connector.py`
- `app.js`
- `styles.css`
- `index.html`

New APIs:

- `GET /api/aircraft-calibration/overview`
- `GET /api/aircraft-calibration/status`
- `POST /api/aircraft-calibration/start`
- `POST /api/aircraft-calibration/cancel`
- `GET /api/aircraft-calibration/session/{session_id}`
- `GET /api/aircraft-calibration/messages/{session_id}`

Supported calibration cards:

- Gyroscope / `gyro`
- Accelerometer / `accel`
- Compass / `compass`
- Level Horizon / `level_horizon`
- Airspeed / `airspeed`
- Power/Battery diagnostics / `power`
- Actuator safety framework / `actuator`
- Motor safety framework / `motor`

Real MAVLink calibration commands currently implemented:

- `gyro` -> PX4 `MAV_CMD_PREFLIGHT_CALIBRATION` gyro parameter
- `accel` -> PX4 accelerometer parameter
- `compass` -> PX4 magnetometer parameter, plus MAG_CAL progress/report handling
- `level_horizon` -> PX4 level horizon parameter
- `airspeed` -> PX4 airspeed parameter

Read-only/framework only:

- `power` only diagnoses voltage/current/battery data. It does not modify power parameters.
- `actuator` and `motor` are safety framework/read-only in this page. They do not actively move outputs from this page.

Safety behavior:

- Real aircraft calibration requires connected flight controller.
- Requires vehicle heartbeat.
- Requires target identified.
- Requires GCS heartbeat sending.
- Requires Disarmed.
- Blocks likely flying state.
- Requires confirmation text in UI.
- Mock mode is explicitly marked as Mock and sends no MAVLink.

Verification already done:

- `python -m py_compile ground_station_server.py px6c_connector.py services\aircraft_calibration_service.py`
- `node --check app.js`
- `GET /api/aircraft-calibration/status` works.
- Mock compass/gyro session works.
- Real calibration correctly rejects when no flight controller is connected.

## RC Setup / RC Monitor

Do not delete or merge into Aircraft Calibration.

Important files:

- `services/rc_calibration_engine.py`
- `services/rc_link_analyzer.py`
- RC frontend is injected in `app.js`.

Current RC behavior:

- Uses MAVLink `RC_CHANNELS` as the real RC source.
- Does not treat `SERVO_OUTPUT_RAW` or actuator outputs as RC input.
- Reads PX4 `RC_MAP_ROLL`, `RC_MAP_PITCH`, `RC_MAP_THROTTLE`, `RC_MAP_YAW`, `RC_MAP_FLTMODE`, `RC_MAP_ARM_SW`.
- Shows CH1-CH18 raw channels and mapped channels.
- Displays RC Link Status, RSSI, update rate, last update age, channel count and warnings.

## Arm / Disarm

Implemented:

- UI topbar arm button.
- Backend endpoint `/api/arm`.
- Connector sends `MAV_CMD_COMPONENT_ARM_DISARM`.
- Requires safety gate and confirmation from frontend.
- Shows COMMAND_ACK result and recent STATUSTEXT.

Do not allow automatic Arm/Disarm.

## Servo / Motor Tests

Existing behavior:

- Servo/motor tests are in the `tuning` page.
- They are safety gated.
- They use command queue to PX4 connector.
- There are hold/stop controls.
- Must not be triggered by AI.

Known caution:

- Real output mapping depends on PX4 output functions. If one command moves two surfaces, inspect PX4 mixer/output function mapping rather than assuming UI channel numbers are wrong.

## AI PID Advisor

Files:

- `services/ai_pid_advisor.py`
- `services/llm_pid_advisor.py`
- `services/feature_extractor.py`
- `services/safety_gate.py`
- `services/rollback_manager.py`
- `config/pid_parameter_whitelist.json`
- `config/ai_models.json`
- `config/model_pricing.json`

Rules:

- AI only suggests PID changes.
- AI must not directly control the flight controller.
- AI must not change PID in flight.
- Parameter write uses whitelist, range checks, safety gate, confirmation, backup/rollback, command queue.
- OpenAI/ChatGPT integration sends structured features, not raw ULG by default.
- If OpenAI fails, local engineering rules can still provide conservative suggestions.

## Flight Reports / ULG Analysis

Files:

- `services/ulg_analyzer.py`
- `services/report_data_builder.py`
- `services/ai_report_service.py`
- `services/ai_report_exporter.py`
- `services/report_reliability.py`

Endpoints:

- `/api/ulg/report`
- `/api/ai-report/generate`
- `/api/ai-report/export`
- `/api/ulg/compare`
- `/api/ulg/sensor-health`

Important rule:

- Algorithmic report and AI report are separate. Do not overwrite the algorithmic report with AI logic.
- AI report should be based on verified structured analysis, not invented conclusions.

## Cache / Versioning

Current frontend versions:

- `styles.css?v=gcs-58`
- `app.js?v=gcs-62`

Backend version endpoints:

- `/api/version`
- `/version.json`

Cache policy:

- `index.html` and `/api/*` should be no-store/no-cache.
- Versioned JS/CSS can cache longer.
- Frontend unregisters stale local service workers on localhost.

If UI looks stale:

1. Run `.\cleanup-ui.ps1`.
2. Restart with `.\start-ui.cmd`.
3. Open `http://127.0.0.1:8080/?v=<timestamp>`.
4. Hard refresh browser.
5. Check `/api/version`.

## Important Safety Rules For Future Agents

Do not contradict these:

- Do not remove RC calibration.
- Do not merge RC calibration into Aircraft Calibration.
- Do not fake live telemetry, GPS, risk, link quality, RC, ACK, or STATUSTEXT.
- Do not mark a calibration successful unless PX4 returns evidence or Mock mode is explicitly enabled.
- Do not send `RC_CHANNELS_OVERRIDE` or `MANUAL_CONTROL` by default.
- Do not let AI send MAVLink commands.
- Do not auto-arm, auto-disarm, auto-land, or auto-change PID.
- Do not start multiple backend servers on 8080.
- Do not change PX4 parameters unless the safety gate and explicit confirmation path are preserved.

## Known Limitations

- Real aircraft calibration still needs real PX4/Pixhawk validation.
- `udpin` mode is passive until the flight controller sends packets.
- Power calibration is currently only diagnostics, not parameter writing.
- Actuator/Motor section inside Aircraft Calibration is only a safety framework. Actual tests remain under `tuning`.
- Some older files/logs/reports are generated artifacts and not authoritative for app behavior.
- README currently displays mojibake in PowerShell if not read as UTF-8; prefer this handoff and source code for current state.

## Recommended First Checks For New Codex

Run:

```powershell
.\cleanup-ui.ps1
.\start-ui.cmd
```

Verify:

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8080/api/version
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8080/api/aircraft-calibration/status
```

Syntax checks:

```powershell
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m py_compile ground_station_server.py px6c_connector.py services\aircraft_calibration_service.py
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe" --check app.js
```

If real flight controller testing is needed:

1. Start UI from `start-ui.cmd`.
2. Use Connection Settings, not a separate manual `px6c_connector.py`.
3. Start in real read-only mode.
4. Confirm GCS Diagnostic shows GCS heartbeat around 1 Hz.
5. Confirm vehicle target is identified.
6. Switch to real command mode only when the aircraft is safe and propellers are removed.


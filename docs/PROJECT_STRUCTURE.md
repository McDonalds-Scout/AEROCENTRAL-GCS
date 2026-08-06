# Project Structure

AeroCentral uses a local Web UI, a Python backend, a MAVLink connection process, and modular service layers. The frontend handles visualization and operator interaction. The backend handles APIs, telemetry delivery, command queues, reporting, AI services, flight controller connection, and safety controls.

## Top-Level Files

| File | Purpose |
| --- | --- |
| `index.html` | Frontend page structure for flight monitoring, connection settings, mission planning, reports, tuning, and tools. |
| `app.js` | Main frontend logic for page switching, API calls, map rendering, attitude display, compass, HUD, alerts, and real-time telemetry updates. |
| `styles.css` | Frontend visual styling, including the dark theme, panel layout, instruments, map, and controls. |
| `ground_station_server.py` | Main backend entrypoint. It serves static files, `/api/` endpoints, SSE telemetry, connection process management, reports, and command queues. |
| `px6c_connector.py` | Backward-compatible MAVLink connection entrypoint for USB / UDP, message parsing, command dispatch, mission upload, and flight controller target identification. |
| `requirements.txt` | Python dependency list. |
| `start-ui.cmd` / `start-ui.ps1` | Recommended startup entrypoints for local Web mode. |
| `cleanup-ui.ps1` | Cleans stale local backend processes and port conflicts. |
| `ensure-dependencies.ps1` / `ensure_dependencies.py` | Checks and installs Python dependencies. |

## Main Directories

| Directory | Purpose | Public Repository Status |
| --- | --- | --- |
| `backend/` | Modular MAVLink communication components introduced during backend refactoring. | Included |
| `services/` | Backend service modules for AI, reports, MAVLink helpers, calibration, safety gates, rollback, and log analysis. | Included |
| `core/` | Core state models, including vehicle and telemetry state. | Included |
| `app/` | Application-level safety helpers. | Included |
| `config/` | Public defaults for AI models, pricing, PID whitelists, and aircraft/report configuration. | Included |
| `prompts/` | Prompt templates for AI report review, AI PID tuning, and final verification. | Included |
| `tests/` | Automated tests. | Included |
| `docs/` | Engineering documentation and validation matrices. | Included |
| `assets/` | Logo and static UI assets. | Included |
| `vendor/` | Third-party frontend libraries such as Leaflet. | Included |
| `mock/` | Mock data used for development and demonstration. | Included if synthetic |
| `desktop/` | Electron desktop application layer. | Included |
| `packaging/` | Windows packaging scripts. | Included |
| `logs/` | Runtime telemetry and backend logs. | Ignored |
| `uploads/` | User-uploaded `.ulg` files. | Ignored |
| `downloads/` | Downloaded flight controller logs or temporary files. | Ignored |
| `reports/` | Generated Word / PDF / HTML / Markdown reports. | Ignored |
| `outputs/` | Generated artifacts and temporary outputs. | Ignored |
| `commands/` | Runtime MAVLink command queue and command status files. | Runtime state only |

## Backend Service Modules

| Module | Purpose |
| --- | --- |
| `services/mavlink_gcs.py` | GCS Heartbeat, vehicle Heartbeat filtering, source and target identity handling. |
| `services/mavlink_command_status.py` | Command status files, COMMAND_ACK classification, and command queue expiry protection. |
| `services/aircraft_calibration_service.py` | Aircraft and sensor calibration sessions, safety confirmation, and PX4 calibration text handling. |
| `services/rc_link_analyzer.py` | RC link state, RSSI, channel health, and update-rate diagnostics. |
| `services/rc_calibration_engine.py` | RC calibration sampling, mapping preview, and parameter recommendations. |
| `services/ulg_analyzer.py` | PX4 ULog parsing, deterministic engineering report data extraction, charts, and export support. |
| `services/report_reliability.py` | `verified_summary`, anomaly filtering, and report credibility checks. |
| `services/report_data_builder.py` | Structured report input, aircraft type detection, and flight phase analysis. |
| `services/ai_report_service.py` | AI Engineering Report generation from verified analysis results. |
| `services/ai_pid_advisor.py` | Local engineering-rule PID recommendations. |
| `services/llm_pid_advisor.py` | OpenAI-compatible PID advisory service. |
| `services/safety_gate.py` | Safety gates for parameter writes and dangerous operations. |
| `services/rollback_manager.py` | PID parameter rollback snapshots. |
| `services/session_logger.py` | Real-time telemetry recording. |
| `services/version_info.py` | Frontend/backend version, build hash, and cache diagnostics. |

## Modular Backend Layout

```text
backend/
|-- communication/
|   |-- mavlink_receiver.py
|   |-- message_bus.py
|   |-- connection_manager.py
|   |-- heartbeat_manager.py
|   |-- command_sender.py
|   |-- parameter_manager.py
|   |-- mission_manager.py
|   |-- calibration_manager.py
|   |-- actuator_test_manager.py
|   |-- flight_log_manager.py
|   `-- communication_monitor.py
|-- telemetry/
|-- mission/
|-- parameters/
|-- calibration/
|-- actuator/
|-- logs/
`-- reports/
```

## Data Flow

```text
PX4 / Pixhawk
  -> MAVLink serial or UDP link
  -> MAVLink Receiver
  -> Message Bus
  -> Telemetry / Command / Mission / Parameter / Calibration / Actuator / Log modules
  -> Python Backend API
  -> Ground Control Interface
```

Flight log analysis flow:

```text
User-supplied ULog
  -> ULog Parser
  -> Data Validation
  -> Feature Extraction
  -> Engineering Metrics
  -> Algorithm Engineering Report
  -> Optional AI Engineering Report
```

## Development Boundaries

- Frontend display and interaction currently remain in `app.js`; larger components should continue moving into `ui_modules/`.
- Backend API dispatch remains in `ground_station_server.py`.
- MAVLink protocol details should stay in backend communication modules or MAVLink-focused services.
- Flight log analysis and AI reporting must not block the MAVLink receive path.
- High-risk commands require safety gates, command status tracking, COMMAND_ACK, STATUSTEXT evidence, and timeout handling.

## Content That Must Not Be Committed

The following content belongs only to local runtime environments and must not be published:

- `.env`
- Real `.ulg` flight logs
- Downloaded flight controller logs
- Generated reports
- Runtime command queue files
- Runtime logs
- Real GPS coordinates
- Real aircraft serial numbers
- Real aircraft IP addresses
- Private API keys or access tokens

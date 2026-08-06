# AeroCentral Engineering Structure

This document defines development boundaries for AeroCentral and helps prevent new features from being concentrated in a single large file.

## Current Runtime Entrypoints

| File | Responsibility | Notes |
| --- | --- | --- |
| `ground_station_server.py` | Web backend, API routing, static UI, SSE telemetry, connection process management, report entrypoints. | Kept as the main local Web entrypoint. |
| `px6c_connector.py` | Backward-compatible MAVLink connection process, telemetry parsing, and command execution orchestration. | Kept as the compatibility entrypoint while protocol logic is migrated to modules. |
| `app.js` | Main frontend interface, real-time rendering, and page navigation. | Should remain stable for existing UI behavior. |

## Extracted Modules

| Module | Responsibility |
| --- | --- |
| `backend/communication/heartbeat_manager.py` | GCS Heartbeat and vehicle Heartbeat identity handling. |
| `backend/communication/command_queue.py` | Command queue state and compatibility helpers. |
| `backend/communication/command_sender.py` | COMMAND_LONG sending and ACK-oriented command workflow. |
| `backend/communication/parameter_manager.py` | Parameter read/write workflow and PARAM_VALUE handling. |
| `backend/communication/calibration_manager.py` | Sensor and aircraft calibration command workflow. |
| `backend/communication/mission_manager.py` | MAVLink Mission upload state and mission ACK handling. |
| `backend/communication/actuator_test_manager.py` | Servo and motor test commands. |
| `backend/communication/flight_log_manager.py` | Flight controller log listing, download state, and file management. |
| `backend/communication/connection_manager.py` | Serial/UDP connection lifecycle and connection state. |
| `backend/communication/mavlink_receiver.py` | Central MAVLink receive loop. |
| `backend/communication/message_bus.py` | Prioritized message dispatch to consumers. |
| `backend/communication/communication_monitor.py` | Communication performance diagnostics. |
| `services/aircraft_calibration_service.py` | Calibration sessions, local safety confirmation, and PX4 calibration text presentation. |
| `services/rc_link_analyzer.py` | RC link state, RSSI, channel health, and update-rate diagnostics. |
| `services/rc_calibration_engine.py` | RC calibration sampling, mapping preview, and parameter suggestions. |
| `services/ulg_analyzer.py` | ULog parsing and deterministic engineering report extraction. |
| `services/report_reliability.py` | `verified_summary`, anomaly filtering, and report credibility enforcement. |
| `services/ai_report_service.py` | AI Engineering Report generation. |
| `services/ai_pid_advisor.py` / `services/llm_pid_advisor.py` | Local and AI-assisted PID recommendations. |
| `core/vehicle_state.py` | Unified backend vehicle state model. |
| `ui_modules/connection_diagnostics.js` | Frontend connection diagnostics and GCS status display. |

## Refactoring Principles

1. `ground_station_server.py` should focus on HTTP/API dispatch and avoid embedding complex flight-control protocol logic.
2. `px6c_connector.py` should remain a compatibility facade for connection lifecycle and orchestration.
3. MAVLink protocol behavior should live in dedicated communication modules.
4. High-frequency flight display data must remain isolated from lower-priority background work such as reports, downloads, and parameter lists.
5. High-risk real-aircraft commands require local safety gates, command queue state, connection ownership, COMMAND_ACK, STATUSTEXT, and timeout handling.
6. Each new module must preserve existing API paths, JSON formats, startup scripts, and UI state structures unless a migration plan explicitly changes them.

## Real-Time Data Priority

The Ground Control Station must prioritize flight-critical display data:

Priority 0:

- ATTITUDE
- HIGHRES_IMU
- GPS
- VFR_HUD
- RC_CHANNELS

Priority 1:

- HEARTBEAT
- COMMAND_ACK
- STATUSTEXT

Priority 2:

- Mission messages
- Parameter messages

Priority 3:

- ULog download data
- File operations
- Report generation

Background operations must not block attitude, heading, altitude, speed, GPS, battery, or RC display updates.

## Future Module Direction

| Priority | Target Area | Scope |
| --- | --- | --- |
| P0 | Real-time telemetry rendering | Continue optimizing attitude, compass, HUD, speed, and GPS updates. |
| P0 | Mission GCS workflow | Expand mission upload, read-back verification, current waypoint progress, and fixed-wing/VTOL templates. |
| P1 | Command evidence binding | Tie every real-aircraft command to COMMAND_ACK, STATUSTEXT, timeout state, and operator-visible reason. |
| P1 | Parameter safety | Strengthen whitelist, rollback, confirmation, and write verification. |
| P2 | Flight log analysis | Improve report credibility, aircraft-type detection, and phase-specific engineering analysis. |
| P2 | Desktop release workflow | Improve installation, runtime diagnostics, and packaged smoke tests. |

## Change Control Rule

Any structural change must pass:

```powershell
python -m compileall -q ground_station_server.py px6c_connector.py services backend core app
python -m unittest discover -s tests -p "test_*.py"
```

Real-aircraft command changes must additionally be validated with the real-flight test matrix.

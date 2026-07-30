# Project cleanup notes

This project is a real-aircraft ground station first. The normal operator path is:

```powershell
.\start-ui.cmd
```

Then open:

```text
http://127.0.0.1:8080/
```

## Main runtime path

- `start-ui.cmd` starts the complete local ground station.
- `start-ui.ps1` checks dependencies, cleans old background processes, and runs `ground_station_server.py`.
- `ground_station_server.py` serves the UI, APIs, SSE telemetry stream, command queue, report generation, and connection management.
- `px6c_connector.py` is launched by the backend when the user starts a MAVLink connection from the UI.

Do not manually start `ground_station_server.py`, `px6c_connector.py`, `mavlink_bridge.py`, and old helper scripts at the same time. That can cause duplicated backend processes, serial port contention, command queue confusion, or stale telemetry.

## Legacy helpers

The following scripts are kept for development or troubleshooting only. They now print a LEGACY warning when started:

- `start-demo.cmd`
- `start-mavlink.cmd`
- `start-px6c.cmd`
- `start-serial.cmd`
- `run-ai-report-server.cmd`

They are not deleted because they can still help during low-level debugging, but they should not be presented as the normal startup method.

## Developer-only UI

Mock/demo controls are hidden by default in the operator UI:

- AI PID mock case selection
- AI PID example analysis button
- Aircraft calibration mock mode

To temporarily show these tools for development, open:

```text
http://127.0.0.1:8080/?dev=1
```

To hide them again:

```text
http://127.0.0.1:8080/?dev=0
```

## Runtime files that should stay out of Git

The following are local runtime artifacts and should remain ignored:

- `.env`
- `*.log`
- `commands/*.jsonl`
- `commands/*status*.json`
- `logs/`
- `downloads/`
- `uploads/`
- `reports/`
- `outputs/`
- `*.ulg`

## Cleanup rule

When adding a new feature, keep the operator path simple:

- Put real flight operation controls in the main UI.
- Put mock/demo/test controls behind developer mode.
- Keep one primary startup path: `start-ui.cmd`.
- Do not add fake telemetry values to the operator UI.
- If a value is not from MAVLink or a verified local calculation, label it clearly.

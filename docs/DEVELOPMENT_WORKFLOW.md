# Development and Contribution Workflow

This document explains how to modify, validate, and submit changes without destabilizing the Ground Control Station or introducing real-aircraft safety risks.

## 1. Before Making Changes

Check the current working tree first:

```powershell
git status --short
```

If there are uncommitted changes from another developer, do not overwrite them. Review whether they are related to the current task before editing.

## 2. Recommended Change Sequence

1. Read `README.md` and `docs/PROJECT_STRUCTURE.md`.
2. Identify whether the task affects frontend UI, backend API, MAVLink communication, reports, AI services, or packaging.
3. Keep changes narrowly scoped.
4. Run syntax checks after each meaningful change.
5. Start the UI and run a local smoke test.
6. For real-aircraft commands, record the result in `docs/REAL_FLIGHT_TEST_MATRIX.md`.

## 3. Common Validation Commands

Python compile check:

```powershell
python -m compileall -q ground_station_server.py px6c_connector.py services backend core app
```

Automated tests:

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

Start the Ground Control Station:

```powershell
.\start-ui.cmd
```

Check backend version endpoint:

```powershell
Invoke-WebRequest http://127.0.0.1:8080/api/version -UseBasicParsing
```

## 4. Git Workflow

Review changes before staging:

```powershell
git status --short
git diff --stat
```

Commit only the intended files:

```powershell
git add README.md docs/
git commit -m "Improve public documentation"
```

Do not commit:

- `.env`
- API keys or tokens
- Real flight logs
- Downloaded ULog files
- Generated reports
- Runtime command queues
- Local build caches
- IDE state
- Personal screenshots or private test notes

These files should be excluded by `.gitignore`. If they still appear in `git status`, stop and update the ignore rules before publishing.

## 5. Real-Aircraft Safety Rules

Any change related to the following areas requires real-aircraft validation under safe conditions:

- Arm / Disarm
- Flight mode switching
- Motor testing
- Servo testing
- Mission upload
- Parameter writing
- Sensor calibration
- RC and transmitter-related workflows

Minimum safety requirements:

- Propellers removed.
- Aircraft Disarmed.
- Throttle at minimum.
- QGroundControl comparison available.
- UI displays COMMAND_ACK and STATUSTEXT.
- Command failure reasons are visible to the operator.

## 6. AI Feature Boundaries

AI may be used for:

- Flight log explanation
- PID tuning recommendations
- Engineering report generation
- Risk highlighting
- Post-flight review suggestions

AI must not be used for:

- Direct flight controller control
- In-flight PID modification
- Automatic Arm / Disarm
- Automatic flight mode switching
- Bypassing safety gates for parameter writes

All AI output must pass through local engineering rules, safety checks, and human confirmation before it affects the aircraft.

## 7. Module Boundaries

Current large entrypoints remain compatible:

- `ground_station_server.py`
- `px6c_connector.py`
- `app.js`

Preferred direction for future changes:

- Keep frontend rendering utilities in `ui_modules/` when they grow large.
- Keep MAVLink mission logic in dedicated mission modules.
- Keep parameter read/write logic in parameter modules.
- Keep motor and servo testing logic in actuator modules.
- Keep COMMAND_ACK and STATUSTEXT evidence handling independent from UI rendering.

Module extraction must preserve existing startup scripts, API paths, JSON response formats, and UI navigation.

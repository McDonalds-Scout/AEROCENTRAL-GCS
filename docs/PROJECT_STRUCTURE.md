# Getting Started

This guide helps a new developer or reviewer clone the project, start the Ground Control Station, and understand the first validation steps.

## 1. Environment Requirements

Recommended environment:

| Item | Requirement |
| --- | --- |
| Operating system | Windows 10 / Windows 11 |
| Python | 3.10 or later; 3.11 or 3.12 recommended |
| Git | Required for clone, pull, commit, and push workflows |
| Browser | Chrome or Edge for Web development mode |
| Real-aircraft testing | PX4 / Pixhawk / PX6C compatible flight controller with USB or UDP MAVLink |

The startup script can use:

- Project virtual environment: `.venv\Scripts\python.exe`
- Bundled Codex Python runtime, when available
- System `python`
- Windows `py` launcher

## 2. Clone the Repository

```powershell
git clone https://github.com/McDonalds-Scout/AEROCENTRAL-GCS.git
cd AEROCENTRAL-GCS
```

Do not copy another machine's `logs/`, `uploads/`, `downloads/`, `reports/`, or `.env` files into the public project. These are local runtime data and private configuration.

## 3. Start the Ground Control Station

Recommended startup command:

```powershell
.\start-ui.cmd
```

The script checks dependencies, cleans stale local background processes, starts the backend, and opens the UI.

If the browser does not open automatically, visit:

```text
http://127.0.0.1:8080/
```

## 4. Manual Python Setup

If automatic startup fails because Python is missing, install Python 3.10 or later and rerun:

```powershell
.\start-ui.cmd
```

Manual virtual environment setup:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 5. Configure AI Features

AI PID Advisor and AI Engineering Report generation require a private `.env` file.

Create it from the public example:

```powershell
copy .env.example .env
```

Then configure:

```env
AI_PROVIDER=openai
OPENAI_API_BASE=https://api.openai.com/v1
OPENAI_API_KEY=YOUR_API_KEY_HERE
```

Without an API key, the local UI, MAVLink connection, deterministic Algorithm Engineering Report, and local engineering rules remain available. OpenAI-dependent features will report configuration errors or use local fallback behavior where implemented.

## 6. Validate with Demo Mode First

For a first run, use Demo mode before connecting real hardware:

1. Open the UI.
2. Go to Connection Settings.
3. Select Demo mode.
4. Click Start Connection.
5. Confirm that the map, attitude indicator, compass, HUD, trend charts, warnings, and MAVLink message panel update.

Demo data is synthetic and does not represent a real aircraft or real GPS position.

## 7. USB Hardware Connection

Recommended steps:

1. Close QGroundControl to avoid serial-port contention.
2. Connect the flight controller by USB.
3. Open Connection Settings.
4. Select USB serial connection.
5. Choose the correct COM port.
6. Select a baud rate, usually `57600` or `115200`.
7. Click Start Connection.

Confirm that the UI displays:

- Vehicle Heartbeat
- Target system and target component
- Changing attitude data
- Battery, GPS, and flight mode based on the actual flight controller state

## 8. UDP MAVLink Connection

Common UDP listener settings:

- UI listen address: `0.0.0.0`
- UI listen port: `14550`
- Flight controller or telemetry device sends MAVLink UDP packets to the computer

If using a target flight controller IP, confirm that the computer and flight controller are on the same subnet and that the firewall does not block UDP traffic.

## 9. Common Issues

### Browser Does Not Open

Run:

```powershell
.\start-ui.cmd
```

Confirm that the terminal prints a local URL such as:

```text
http://127.0.0.1:8080/
```

### Dependency Installation Fails

Check Python and pip:

```powershell
python --version
python -m pip --version
```

If the network blocks Python package downloads, configure a proxy or an internal package mirror.

### Serial Connection Fails

Check:

- QGroundControl is closed.
- USB cable is connected.
- COM port list has been refreshed.
- `57600` and `115200` have both been tested.
- Windows Device Manager shows the flight controller serial port.

### UI Receives Telemetry but Commands Fail

Telemetry receive and command acknowledgement are different paths. Check:

- GCS Heartbeat is being sent.
- `target_system` and `target_component` are identified.
- COMMAND_ACK is returned.
- STATUSTEXT shows any PX4 rejection reason.
- The UI is in a mode that allows real-aircraft commands.

## 10. Read Before Development

- [Project Structure](PROJECT_STRUCTURE.md)
- [Development and Contribution Workflow](DEVELOPMENT_WORKFLOW.md)
- [Real-Flight Test Matrix](REAL_FLIGHT_TEST_MATRIX.md)

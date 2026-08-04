# Open Source Release Checklist

This checklist documents the final GitHub preparation pass for AeroCentral.

## 1. Security Cleanup Completed

- `.env` remains local-only and is ignored by Git.
- `.env.example` contains placeholders only.
- MAVLink defaults are public-safe local defaults.
- Real OpenAI keys are not stored in tracked configuration.
- Real flight logs, downloaded logs, generated reports, command state, and runtime logs are ignored.
- The tracked flight case library was sanitized to an empty public seed file.
- A private backup of the previous local case library was placed under `data/private/`, which is ignored.
- Public examples were added under `examples/` using synthetic data only.

## 2. Removed Or Isolated Content

- Removed `CODEX_HANDOFF.md` from the public project tree.
- Removed `Safe-CDriveScan.ps1` from the public project tree.
- Removed root-level `mock_flight.csv`; replaced it with `examples/sample_mock_flight.csv`.
- Removed hardcoded private aircraft-link defaults from public UI defaults.
- Replaced the local absolute download path shown in the UI with a generic runtime-data path.

## 3. Content Kept

- MAVLink communication code and compatibility entrypoints.
- Telemetry, mission, parameter, calibration, actuator, and flight-log modules.
- Web UI functionality and existing API paths.
- Report generation and AI-assisted analysis services.
- Packaging scripts and public documentation.
- Synthetic examples suitable for public GitHub demonstration.

## 4. Required Pre-Publish Checks

Latest local verification:

- `python -m compileall -q ground_station_server.py px6c_connector.py services backend core app`: passed
- `python -m unittest discover -s tests -p "test_*.py"`: passed
- Public candidate scan found no real `sk-` API key, no local Windows user-profile path, no hardcoded private aircraft address, and no known private flight-log filename patterns.

Run again before any public push:

```powershell
git status --short
rg -n "api_key|apikey|secret|token|password|OPENAI_API_KEY|sk-|Bearer|192\\.168|C:\\\\Users|/home/|\\.ulg|\\.tlog|\\.bin|\\.bag" .
python -m unittest discover -s tests -p "test_*.py"
python -m compileall -q ground_station_server.py px6c_connector.py services backend core app
```

Review any matches from source files. Documentation references to supported file extensions are acceptable; real keys, real local paths, real flight logs, and real aircraft addresses are not.

## 5. Git Commit Recommendation

Do not use `git add .`.

Recommended staged scope:

```powershell
git add .gitignore .env.example README.md OPEN_SOURCE_RELEASE_CHECKLIST.md
git add config/default.json config/example_config.json
git add examples/
git add ground_station_server.py index.html app.js
git add data/flight_case_library.json
git add -u CODEX_HANDOFF.md Safe-CDriveScan.ps1 mock_flight.csv assets/yitong-logo.png
```

Then verify:

```powershell
git status --short
git diff --cached --stat
```

Suggested commit message:

```text
Prepare AeroCentral for open-source release
```

Do not push until the staged diff has been manually reviewed.

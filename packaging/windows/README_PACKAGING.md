# Windows Packaging Guide

This guide explains how to package the local Web-based Ground Control Station into a Windows application that can be launched by double-clicking an executable.

## Packaging Outputs

After a successful build, the Python packaging workflow generates:

- `dist/AEROCENTRAL/AEROCENTRAL.exe`: legacy Python-only user launcher.
- `dist/AEROCENTRAL/ground_station_server.exe`: local backend service.
- `dist/AEROCENTRAL/px6c_connector.exe`: MAVLink/PX4 connection process.
- `dist/AEROCENTRAL/mavlink_simulator.exe`: simulator used for demo mode.
- `dist/AEROCENTRAL-portable.zip`: portable backend package.

The Electron desktop workflow generates:

- `dist/desktop/AeroCentral Setup 0.1.0.exe`: Windows installer.
- `dist/desktop/AeroCentral 0.1.0.exe`: portable desktop application.
- `dist/desktop/win-unpacked/AeroCentral.exe`: unpacked test executable.

## Build Commands

Run from the project root.

Python backend package:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\windows\build_windows_package.ps1 -SkipInstaller
```

Electron desktop package:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\desktop\electron\build_desktop_windows.ps1 -Version 0.1.0
```

Specify a version:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\desktop\electron\build_desktop_windows.ps1 -Version 0.2.0
```

## Installer Requirements

The Electron workflow uses electron-builder to produce both an installer and a portable executable.

The legacy Python-only workflow can optionally use Inno Setup. If Inno Setup is required, install it and ensure `ISCC.exe` is available in the system PATH.

If Inno Setup is not installed, the legacy script still generates a portable directory:

```text
dist\AEROCENTRAL\
```

## Runtime Data Location

Packaged applications must not write logs, reports, uploaded ULog files, downloaded logs, or command queues into the installation directory.

Runtime data is written to the user's application data directory.

Typical runtime folders:

- `logs/`: telemetry and backend runtime logs.
- `reports/`: Algorithm Engineering Reports and AI Engineering Reports.
- `uploads/`: user-supplied `.ulg` files.
- `downloads/`: flight controller `.ulg` downloads.
- `commands/`: MAVLink command queue and COMMAND_ACK state.
- `.env`: optional private user configuration, including OpenAI API key.

## Delivery Checklist

Before sharing a Windows build:

1. Double-click `AeroCentral.exe` or install `AeroCentral Setup 0.1.0.exe`.
2. Confirm that the UI opens.
3. Confirm that `/api/version` returns a valid JSON response.
4. Confirm that Demo mode updates attitude, map, warnings, and MAVLink message display.
5. Confirm that USB serial ports can be listed.
6. Confirm that UDP connection settings can be saved and used.
7. Confirm that the Algorithm Engineering Report can export a Word document.
8. Confirm that AI features report a clear configuration error or fallback when no OpenAI API key is configured.
9. Confirm that AI Engineering Report generation works after configuring a valid private API key.
10. Confirm that reports, logs, downloads, and uploads are written to the runtime data directory.
11. Validate real-aircraft command features separately with propellers removed and under safe test conditions.

## Security Notes

- Do not package a real `.env` file.
- Do not package `uploads/`, `downloads/`, `reports/`, `logs/`, or `connection.log`.
- Do not package real aircraft IP addresses, GPS coordinates, mission files, or flight logs.
- Desktop packaging solves distribution and startup convenience. It does not certify real-aircraft safety.

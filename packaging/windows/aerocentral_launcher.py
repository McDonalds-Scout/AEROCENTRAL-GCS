from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


APP_DIR_NAME = "AEROCENTRAL"
PORT_CANDIDATES = (8080, 8094, 8095, 8096, 8097)


def app_data_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    root = Path(base) / APP_DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    for name in ("logs", "reports", "uploads", "downloads", "commands"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def url_for(port: int) -> str:
    return f"http://127.0.0.1:{port}/"


def probe(port: int, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/version", timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def existing_server_url() -> str | None:
    for port in PORT_CANDIDATES:
        if probe(port, timeout=0.35):
            return url_for(port)
    return None


def server_command(root: Path) -> list[str]:
    suffix = ".exe" if os.name == "nt" else ""
    for packaged_server in (
        root / "ground_station_server" / f"ground_station_server{suffix}",
        root / f"ground_station_server{suffix}",
    ):
        if packaged_server.exists():
            return [str(packaged_server)]
    return [sys.executable, str(root / "ground_station_server.py")]


def start_server(root: Path, data_root: Path) -> subprocess.Popen:
    env = os.environ.copy()
    env["AUTO_OPEN"] = "0"
    env["PORT"] = "8080"
    env["PYTHONUNBUFFERED"] = "1"
    env["GCS_DATA_DIR"] = str(data_root)
    log_path = data_root / "logs" / "launcher-server.log"
    err_path = data_root / "logs" / "launcher-server.err.log"
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    return subprocess.Popen(
        server_command(root),
        cwd=str(root),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log_path.open("ab", buffering=0),
        stderr=err_path.open("ab", buffering=0),
        creationflags=flags,
    )


def wait_for_server(timeout_s: float = 20.0) -> str | None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        url = existing_server_url()
        if url:
            return url
        time.sleep(0.25)
    return None


def main() -> int:
    data_root = app_data_root()
    url = existing_server_url()
    if not url:
        start_server(app_dir(), data_root)
        url = wait_for_server()
    if not url:
        print(f"AEROCENTRAL Ground Control Station failed to start. Check logs in: {data_root / 'logs'}")
        return 1
    webbrowser.open(url)
    print(f"AEROCENTRAL Ground Control Station opened: {url}")
    print(f"Runtime data: {data_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

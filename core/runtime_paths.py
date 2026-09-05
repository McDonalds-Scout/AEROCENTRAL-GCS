from __future__ import annotations

import os
import sys
from pathlib import Path


APP_DIR_NAME = "AEROCENTRAL"


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def is_packaged() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    override = os.environ.get("GCS_RESOURCE_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        return Path(bundle_root).resolve()
    return project_root()


def executable_dir() -> Path:
    if is_packaged():
        return Path(sys.executable).resolve().parent
    return project_root()


def runtime_data_root() -> Path:
    override = os.environ.get("GCS_DATA_DIR", "").strip()
    if override:
        root = Path(override).expanduser()
    elif is_packaged():
        local_app_data = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
        root = Path(local_app_data) / APP_DIR_NAME
    else:
        root = project_root()
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def runtime_data_path(*parts: str) -> Path:
    path = runtime_data_root().joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def packaged_entry_command(script_name: str, python_executable: str | None = None) -> list[str]:
    stem = Path(script_name).stem
    if is_packaged():
        suffix = ".exe" if os.name == "nt" else ""
        executable_dir = Path(sys.executable).resolve().parent
        candidates = [
            executable_dir / stem / f"{stem}{suffix}",
            executable_dir / f"{stem}{suffix}",
            executable_dir.parent / stem / f"{stem}{suffix}",
            executable_dir.parent / f"{stem}{suffix}",
        ]
        for executable in candidates:
            if executable.exists():
                return [str(executable)]
    python = python_executable or sys.executable
    return [python, str(resource_root() / script_name)]

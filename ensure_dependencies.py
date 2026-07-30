from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REQUIREMENTS = ROOT / "requirements.txt"

REQUIRED_IMPORTS = {
    "numpy": "numpy",
    "pandas": "pandas",
    "python-docx": "docx",
    "reportlab": "reportlab",
    "pyserial": "serial",
    "pymavlink": "pymavlink",
    "pyulog": "pyulog",
}

OPTIONAL_IMPORTS = {
    "matplotlib": "matplotlib",
}


def missing_packages(imports: dict[str, str]) -> list[str]:
    return [package for package, module in imports.items() if importlib.util.find_spec(module) is None]


def run_pip_install() -> int:
    if not REQUIREMENTS.exists():
        print(f"[deps] requirements.txt not found: {REQUIREMENTS}")
        return 2
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "-r",
        str(REQUIREMENTS),
    ]
    print("[deps] Installing missing Python dependencies in one step...")
    print("[deps] " + " ".join(cmd))
    return subprocess.call(cmd, cwd=str(ROOT))


def main() -> int:
    print(f"[deps] Python: {sys.executable}")
    missing_required = missing_packages(REQUIRED_IMPORTS)
    missing_optional = missing_packages(OPTIONAL_IMPORTS)
    if not missing_required and not missing_optional:
        print("[deps] All required ground station dependencies are available.")
        return 0

    print("[deps] Missing required: " + (", ".join(missing_required) if missing_required else "none"))
    print("[deps] Missing optional: " + (", ".join(missing_optional) if missing_optional else "none"))

    if os.environ.get("UAV_SKIP_DEP_INSTALL", "").strip() == "1":
        print("[deps] UAV_SKIP_DEP_INSTALL=1, skipping automatic installation.")
        return 2 if missing_required else 0

    exit_code = run_pip_install()
    if exit_code != 0:
        print(f"[deps] pip install failed with exit code {exit_code}.")
        return exit_code

    missing_required = missing_packages(REQUIRED_IMPORTS)
    missing_optional = missing_packages(OPTIONAL_IMPORTS)
    if missing_required:
        print("[deps] Still missing required packages after install: " + ", ".join(missing_required))
        return 2
    if missing_optional:
        print("[deps] Optional packages still missing: " + ", ".join(missing_optional))
        print("[deps] The UI can start, but chart rendering may be disabled.")
    print("[deps] Dependency check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

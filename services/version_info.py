from __future__ import annotations

import hashlib
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from core.runtime_paths import resource_root


ROOT = resource_root()
BUILD_TIME = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
VERSION_FILES = ("index.html", "app.js", "styles.css")


def _short_file_hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    except OSError:
        return "missing"


def frontend_hash() -> str:
    digest = hashlib.sha256()
    used_any = False
    for relative in VERSION_FILES:
        path = ROOT / relative
        if not path.is_file():
            continue
        used_any = True
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:12] if used_any else "unknown"


def git_commit() -> str:
    env_commit = os.environ.get("GCS_GIT_COMMIT", "").strip()
    if env_commit:
        return env_commit
    git_dir = ROOT / ".git"
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref:"):
            ref_path = git_dir / head.split(" ", 1)[1]
            return ref_path.read_text(encoding="utf-8").strip()[:8] or "unknown"
        return head[:8] or "unknown"
    except Exception:
        pass
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        ).strip() or "unknown"
    except Exception:
        return "unknown"


def version_payload() -> dict:
    version = os.environ.get("GCS_VERSION", "").strip() or frontend_hash()
    return {
        "app": "AEROCENTRAL Ground Control Station",
        "version": version,
        "buildTime": BUILD_TIME,
        "gitCommit": git_commit(),
        "frontendHash": frontend_hash(),
        "backend": "python-ground-station",
        "files": {
            relative: _short_file_hash(ROOT / relative)
            for relative in VERSION_FILES
        },
        "cachePolicy": {
            "html": "no-store, no-cache, must-revalidate, max-age=0",
            "api": "no-store, no-cache, must-revalidate, max-age=0",
            "versionedAssets": "public, max-age=31536000, immutable",
            "unversionedAssets": "no-store, no-cache, must-revalidate, max-age=0",
        },
    }

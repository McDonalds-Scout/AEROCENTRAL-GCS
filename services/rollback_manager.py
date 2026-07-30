from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HISTORY_PATH = ROOT / "logs" / "pid_parameter_rollbacks.json"


def _read_history(path: Path = HISTORY_PATH) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def _write_history(rows: list[dict[str, Any]], path: Path = HISTORY_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows[-200:], ensure_ascii=False, indent=2), encoding="utf-8")


def create_rollback_snapshot(
    recommendations: list[dict[str, Any]],
    state: Any,
    source: str = "AI PID Advisor",
    note: str = "",
) -> dict[str, Any]:
    state_dict = state.to_dict() if hasattr(state, "to_dict") else dict(state or {})
    parameters = dict(state_dict.get("parameters") or {})
    rows = []
    for item in recommendations:
        name = str(item.get("name", "")).strip().upper()
        if not name:
            continue
        old_value = item.get("current")
        if old_value is None:
            old_value = parameters.get(name)
        rows.append({
            "name": name,
            "oldValue": old_value,
            "newValue": item.get("suggested", item.get("value")),
            "reason": item.get("reason", ""),
        })
    snapshot = {
        "id": uuid.uuid4().hex,
        "createdAt": int(time.time() * 1000),
        "source": source,
        "note": note,
        "items": rows,
        "applied": False,
        "rolledBack": False,
    }
    history = _read_history()
    history.append(snapshot)
    _write_history(history)
    return snapshot


def mark_applied(snapshot_id: str, queued: list[dict[str, Any]]) -> None:
    history = _read_history()
    for item in history:
        if item.get("id") == snapshot_id:
            item["applied"] = True
            item["queued"] = queued
            item["appliedAt"] = int(time.time() * 1000)
            break
    _write_history(history)


def list_rollbacks(limit: int = 30) -> list[dict[str, Any]]:
    return list(reversed(_read_history()[-limit:]))


def get_snapshot(snapshot_id: str) -> dict[str, Any] | None:
    for item in _read_history():
        if item.get("id") == snapshot_id:
            return item
    return None


def rollback_recommendations(snapshot_id: str) -> list[dict[str, Any]]:
    snapshot = get_snapshot(snapshot_id)
    if not snapshot:
        raise ValueError("未找到回滚快照")
    return [
        {
            "name": item["name"],
            "current": item.get("newValue"),
            "suggested": item.get("oldValue"),
            "reason": "回滚到写入前参数值",
        }
        for item in snapshot.get("items", [])
        if item.get("oldValue") is not None
    ]


def mark_rolled_back(snapshot_id: str, queued: list[dict[str, Any]]) -> None:
    history = _read_history()
    for item in history:
        if item.get("id") == snapshot_id:
            item["rolledBack"] = True
            item["rollbackQueued"] = queued
            item["rolledBackAt"] = int(time.time() * 1000)
            break
    _write_history(history)

"""Command status helpers for the MAVLink connector.

This module is the new communication-layer owner for command state policy.
The current implementation intentionally preserves the existing JSON status
file contract so the UI and connector keep working during the refactor.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from core.runtime_paths import runtime_data_path


COMMAND_STATUS = runtime_data_path("commands", "px6c_command_status.json")

ACK_TIMEOUT_RESULTS = {"NO_ACK", "TIMEOUT"}
FINAL_COMMAND_STATUSES = {
    "accepted",
    "rejected",
    "failed",
    "timeout",
    "sent_no_ack",
    "expired",
    "unsupported",
    "interrupted",
    "partial",
}
PENDING_COMMAND_STATUSES = {"queued", "running"}
RECOVERABLE_QUEUE_WINDOW_MS = 10 * 60 * 1000
TRANSIENT_ACTUATOR_QUEUE_WINDOW_MS = 3000
SAFE_RECOVERABLE_COMMANDS = {
    "calibrate",
    "request_parameters",
    "set_parameter",
    "set_flight_mode",
    "arm_disarm",
    "list_flight_logs",
    "download_flight_log",
    "upload_mission",
    "clear_mission",
    "read_mission",
}
TRANSIENT_ACTUATOR_COMMANDS = {"set_servo", "test_motor"}


def write_command_status(command_id: str | None, **status: Any) -> None:
    if not command_id:
        return
    COMMAND_STATUS.parent.mkdir(exist_ok=True)
    try:
        statuses = json.loads(COMMAND_STATUS.read_text(encoding="utf-8"))
        if not isinstance(statuses, dict):
            statuses = {}
    except (FileNotFoundError, json.JSONDecodeError):
        statuses = {}

    statuses[command_id] = {
        **statuses.get(command_id, {}),
        **status,
        "updatedAt": int(time.time() * 1000),
    }
    payload = json.dumps(statuses, ensure_ascii=False, indent=2)
    for attempt in range(5):
        temporary = COMMAND_STATUS.with_name(f"{COMMAND_STATUS.stem}.{os.getpid()}.{time.time_ns()}.tmp")
        try:
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(COMMAND_STATUS)
            return
        except OSError as error:
            try:
                temporary.unlink()
            except OSError:
                pass
            if attempt == 4:
                print(f"Failed to write command status: {error}")
                return
            time.sleep(0.08 * (attempt + 1))


def load_command_statuses() -> dict[str, Any]:
    try:
        statuses = json.loads(COMMAND_STATUS.read_text(encoding="utf-8"))
        return statuses if isinstance(statuses, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def command_status_text(command_id: str | None, statuses: dict[str, Any] | None = None) -> str:
    if not command_id:
        return ""
    data = (statuses or load_command_statuses()).get(command_id) or {}
    return str(data.get("status") or "").strip().lower()


def command_result_status(ack: dict[str, Any] | None) -> str:
    result_text = str((ack or {}).get("resultText") or "").upper()
    if result_text in {"ACCEPTED", "IN_PROGRESS"}:
        return "accepted"
    if result_text == "TIMEOUT":
        return "timeout"
    if result_text == "UNSUPPORTED":
        return "unsupported"
    if result_text in {"DENIED", "TEMPORARILY_REJECTED"}:
        return "rejected"
    if result_text == "FAILED":
        return "failed"
    if result_text == "NO_ACK":
        return "sent_no_ack"
    return "rejected"


def is_ack_timeout(ack: dict[str, Any] | None) -> bool:
    return str((ack or {}).get("resultText", "")).upper() in ACK_TIMEOUT_RESULTS


def should_process_queued_command(command: dict[str, Any], statuses: dict[str, Any] | None = None) -> bool:
    command_id = command.get("id")
    status = command_status_text(command_id, statuses)
    if status in FINAL_COMMAND_STATUSES:
        return False
    if status and status not in PENDING_COMMAND_STATUSES:
        return False

    command_name = str(command.get("command") or "")
    if command_name not in SAFE_RECOVERABLE_COMMANDS and command_name not in TRANSIENT_ACTUATOR_COMMANDS:
        write_command_status(
            command_id,
            status="expired",
            message=f"Command {command_name or 'unknown'} was not replayed by the connector safety filter",
            results=[],
        )
        return False

    created_at = command.get("createdAt")
    try:
        age_ms = int(time.time() * 1000) - int(created_at)
    except (TypeError, ValueError):
        age_ms = RECOVERABLE_QUEUE_WINDOW_MS + 1
    queue_window_ms = (
        TRANSIENT_ACTUATOR_QUEUE_WINDOW_MS
        if command_name in TRANSIENT_ACTUATOR_COMMANDS
        else RECOVERABLE_QUEUE_WINDOW_MS
    )
    if age_ms > queue_window_ms:
        write_command_status(
            command_id,
            status="expired",
            message=(
                "Actuator test command expired before the connector could send it; click the UI action again"
                if command_name in TRANSIENT_ACTUATOR_COMMANDS
                else "Command expired before the PX6C connector could send it; please click the UI action again"
            ),
            results=[],
        )
        return False
    return True

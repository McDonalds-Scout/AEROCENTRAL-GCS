"""PX4 MAVLink calibration command helpers."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from pymavlink import mavutil

from backend.communication.command_queue import is_ack_timeout, write_command_status
from backend.communication.command_sender import mavlink_text, send_command_long, wait_for_command_ack
from backend.communication.heartbeat_manager import send_gcs_heartbeat
from backend.communication.message_bus import MessageBus, wait_for_message


CALIBRATION_PARAMS = {
    "gyro": (1, 0, 0, 0, 0, 0, 0),
    "magnetometer": (0, 1, 0, 0, 0, 0, 0),
    "radio": (0, 0, 0, 1, 0, 0, 0),
    "accelerometer": (0, 0, 0, 0, 1, 0, 0),
    "level": (0, 0, 0, 0, 2, 0, 0),
    "airspeed": (0, 0, 0, 0, 0, 1, 0),
    "esc": (0, 0, 0, 0, 0, 0, 1),
}

MAG_CAL_STATUS_NAMES = {
    0: "NOT_STARTED",
    1: "WAITING_TO_START",
    2: "RUNNING_STEP_ONE",
    3: "RUNNING_STEP_TWO",
    4: "SUCCESS",
    5: "FAILED",
    6: "BAD_ORIENTATION",
    7: "BAD_RADIUS",
}


def calibration_label_ascii(calibration_type: str) -> str:
    return {
        "gyro": "gyro",
        "magnetometer": "compass",
        "radio": "radio",
        "accelerometer": "accelerometer",
        "level": "level horizon",
        "airspeed": "airspeed",
        "esc": "ESC",
    }.get(calibration_type, str(calibration_type or "unknown"))


def calibration_evidence_text(
    message: Any,
    mag_cal_status_names: dict[int, str] | None = None,
) -> str:
    message_type = message.get_type()
    status_names = mag_cal_status_names or MAG_CAL_STATUS_NAMES
    if message_type == "STATUSTEXT":
        text = mavlink_text(message)
        lower = text.lower()
        if any(term in lower for term in ("[cal]", "calibration", "calibrate", "mag", "compass", "gyro", "accel")):
            return text
    if message_type == "MAG_CAL_PROGRESS":
        return f"MAG_CAL_PROGRESS {int(getattr(message, 'completion_pct', 0) or 0)}%"
    if message_type == "MAG_CAL_REPORT":
        status_code = int(getattr(message, "cal_status", 0) or 0)
        return f"MAG_CAL_REPORT {status_names.get(status_code, str(status_code))}"
    return ""


def monitor_magnetometer_calibration(
    master: Any,
    command_id: str | None = None,
    ack: dict[str, Any] | None = None,
    on_message: Callable[[Any], None] | None = None,
    *,
    mag_cal_status_names: dict[int, str] | None = None,
    timeout_s: float = 120.0,
) -> None:
    status_names = mag_cal_status_names or MAG_CAL_STATUS_NAMES
    write_command_status(
        command_id,
        status="running",
        message="Compass calibration accepted; follow PX4 rotate prompts",
        results=[{"type": "magnetometer", "ack": ack or {}, "progress": 0}],
    )
    deadline = time.monotonic() + timeout_s
    last_progress = None
    last_text = ""
    bus = MessageBus.for_master(master)
    cursor = bus.current_sequence() if bus is not None else None
    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        message, cursor = wait_for_message(
            master,
            message_types=("STATUSTEXT", "MAG_CAL_PROGRESS", "MAG_CAL_REPORT"),
            timeout=min(0.25, remaining),
            on_message=on_message,
            heartbeat=lambda: send_gcs_heartbeat(master),
            cursor=cursor,
            poll_interval=0.05,
        )
        if message is None:
            continue
        message_type = message.get_type()
        if message_type == "STATUSTEXT":
            text = mavlink_text(message)
            if any(term in text.lower() for term in ["cal", "mag", "compass", "rotate"]):
                last_text = text
                write_command_status(
                    command_id,
                    status="running",
                    message=f"Compass calibration prompt: {text}",
                    results=[{"type": "magnetometer", "ack": ack or {}, "progress": last_progress, "text": text}],
                )
        elif message_type == "MAG_CAL_PROGRESS":
            progress = int(getattr(message, "completion_pct", 0) or 0)
            compass_id = int(getattr(message, "compass_id", 0) or 0)
            status_code = int(getattr(message, "cal_status", 0) or 0)
            status_text = status_names.get(status_code, str(status_code))
            last_progress = progress
            write_command_status(
                command_id,
                status="running",
                message=f"Compass {compass_id} calibration progress: {progress}% / {status_text}",
                results=[{
                    "type": "magnetometer",
                    "ack": ack or {},
                    "compassId": compass_id,
                    "progress": progress,
                    "status": status_code,
                    "statusText": status_text,
                }],
            )
        elif message_type == "MAG_CAL_REPORT":
            compass_id = int(getattr(message, "compass_id", 0) or 0)
            status_code = int(getattr(message, "cal_status", 0) or 0)
            status_text = status_names.get(status_code, str(status_code))
            success = status_text == "SUCCESS" or status_code == 4
            write_command_status(
                command_id,
                status="accepted" if success else "rejected",
                message=(
                    f"Compass {compass_id} calibration completed: {status_text}"
                    if success
                    else f"Compass {compass_id} calibration failed: {status_text}"
                ),
                results=[{
                    "type": "magnetometer",
                    "ack": ack or {},
                    "compassId": compass_id,
                    "progress": 100 if success else last_progress,
                    "status": status_code,
                    "statusText": status_text,
                    "fitness": float(getattr(message, "fitness", 0.0) or 0.0),
                    "autosaved": bool(getattr(message, "autosaved", 0)),
                }],
            )
            return
    write_command_status(
        command_id,
        status="partial",
        message=(
            f"Compass calibration still running or no final report; last progress {last_progress}%"
            if last_progress is not None
            else f"Compass calibration did not report progress. Last PX4 text: {last_text or 'none'}"
        ),
        results=[{"type": "magnetometer", "ack": ack or {}, "progress": last_progress, "text": last_text}],
    )


def send_calibration(
    master: Any,
    calibration_type: str,
    command_id: str | None = None,
    on_message: Callable[[Any], None] | None = None,
    *,
    calibration_params: dict[str, tuple[int, int, int, int, int, int, int]] | None = None,
    mag_cal_status_names: dict[int, str] | None = None,
    ack_history: list[dict[str, Any]] | None = None,
    result_names: dict[int, str] | None = None,
) -> None:
    params = (calibration_params or CALIBRATION_PARAMS).get(calibration_type)
    status_names = mag_cal_status_names or MAG_CAL_STATUS_NAMES
    label = calibration_label_ascii(calibration_type)
    if params is None:
        print(f"Unknown calibration type ignored: {calibration_type}")
        write_command_status(
            command_id,
            status="rejected",
            message=f"Unknown calibration type: {calibration_type}",
            results=[],
        )
        return
    write_command_status(
        command_id,
        status="running",
        message=f"{label} calibration command sent to flight controller; waiting for COMMAND_ACK",
        results=[],
    )
    mav_command = mavutil.mavlink.MAV_CMD_PREFLIGHT_CALIBRATION
    bus = MessageBus.for_master(master)
    cursor = bus.current_sequence() if bus is not None else None
    send_command_long(master, mav_command, params)
    ack = wait_for_command_ack(
        master,
        mav_command,
        timeout=5.0,
        on_message=on_message,
        evidence_predicate=lambda message: calibration_evidence_text(message, status_names),
        ack_history=ack_history,
        result_names=result_names,
        cursor=cursor,
    )
    accepted = ack["resultText"] in {"ACCEPTED", "IN_PROGRESS"}
    if accepted and calibration_type == "magnetometer":
        monitor_magnetometer_calibration(
            master,
            command_id,
            ack,
            on_message=on_message,
            mag_cal_status_names=status_names,
        )
        return
    evidence_text = ack.get("evidenceText") if isinstance(ack, dict) else ""
    write_command_status(
        command_id,
        status="accepted" if accepted else ("sent_no_ack" if is_ack_timeout(ack) else "rejected"),
        message=(
            f"Flight controller accepted {label} calibration command; follow PX4 prompts"
            if accepted
            else f"{label} calibration command was not confirmed by flight controller: {ack['resultText']}"
            + (f"; PX4 text: {evidence_text}" if evidence_text else "")
        ),
        results=[{
            "type": calibration_type,
            "label": label,
            "ack": ack,
            "text": evidence_text or None,
        }],
    )

"""MAVLink command sending helpers.

This module owns generic COMMAND_LONG helpers and command acknowledgement
handling. Legacy entry points in px6c_connector.py delegate here during the
incremental refactor.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from pymavlink import mavutil

from backend.communication.command_queue import (
    command_result_status,
    is_ack_timeout,
    write_command_status,
)
from backend.communication.heartbeat_manager import (
    is_vehicle_heartbeat,
    send_gcs_heartbeat,
    vehicle_target,
)
from backend.communication.message_bus import MessageBus, wait_for_message


DEFAULT_MAV_RESULT_NAMES = {
    getattr(mavutil.mavlink, "MAV_RESULT_ACCEPTED", 0): "ACCEPTED",
    getattr(mavutil.mavlink, "MAV_RESULT_TEMPORARILY_REJECTED", 1): "TEMPORARILY_REJECTED",
    getattr(mavutil.mavlink, "MAV_RESULT_DENIED", 2): "DENIED",
    getattr(mavutil.mavlink, "MAV_RESULT_UNSUPPORTED", 3): "UNSUPPORTED",
    getattr(mavutil.mavlink, "MAV_RESULT_FAILED", 4): "FAILED",
    getattr(mavutil.mavlink, "MAV_RESULT_IN_PROGRESS", 5): "IN_PROGRESS",
}


def mavlink_text(message: Any) -> str:
    text = getattr(message, "text", "")
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    return str(text).rstrip("\x00")


def command_ack_entry(message: Any, result_names: dict[int, str] | None = None) -> dict[str, Any]:
    result = int(getattr(message, "result", -1))
    names = result_names or DEFAULT_MAV_RESULT_NAMES
    return {
        "timeMs": int(time.time() * 1000),
        "command": int(getattr(message, "command", -1)),
        "result": result,
        "resultText": names.get(result, str(result)),
        "progress": int(getattr(message, "progress", 0) or 0),
    }


def remember_command_ack(
    history: list[dict[str, Any]],
    message: Any,
    result_names: dict[int, str] | None = None,
) -> dict[str, Any]:
    entry = command_ack_entry(message, result_names)
    if history:
        last = history[-1]
        duplicate = (
            last.get("command") == entry.get("command")
            and last.get("result") == entry.get("result")
            and last.get("progress") == entry.get("progress")
            and entry["timeMs"] - int(last.get("timeMs") or 0) < 250
        )
        if duplicate:
            return last
    history.append(entry)
    del history[:-100]
    return entry


def send_command_long(master: Any, mav_command: int, params: list[float] | tuple[float, ...], confirmation: int = 0) -> None:
    padded = [*params, 0, 0, 0, 0, 0, 0, 0][:7]
    master.mav.command_long_send(
        master.target_system,
        master.target_component,
        int(mav_command),
        int(confirmation),
        *padded,
    )


def wait_for_command_ack(
    master: Any,
    mav_command: int,
    timeout: float = 1.0,
    on_message: Callable[[Any], None] | None = None,
    evidence_predicate: Callable[[Any], str] | None = None,
    ack_history: list[dict[str, Any]] | None = None,
    result_names: dict[int, str] | None = None,
    text_decoder: Callable[[Any], str] = mavlink_text,
    cursor: int | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    evidence = ""
    latest_statustext = ""
    names = result_names or DEFAULT_MAV_RESULT_NAMES
    bus = MessageBus.for_master(master)
    if cursor is None and bus is not None:
        cursor = bus.current_sequence()
    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        message, cursor = wait_for_message(
            master,
            message_types=("COMMAND_ACK", "STATUSTEXT", "MAG_CAL_PROGRESS", "MAG_CAL_REPORT"),
            timeout=min(0.1, remaining),
            on_message=on_message,
            heartbeat=lambda: send_gcs_heartbeat(master),
            cursor=cursor,
            poll_interval=0.05,
        )
        if message is None:
            continue
        if message.get_type() == "STATUSTEXT":
            latest_statustext = text_decoder(message)
            lower_text = latest_statustext.lower()
            if not evidence or any(
                term in lower_text
                for term in (
                    "fail",
                    "denied",
                    "reject",
                    "preflight",
                    "safety",
                    "cal",
                    "arm",
                    "mode",
                    "motor",
                    "servo",
                )
            ):
                evidence = latest_statustext
        if evidence_predicate:
            try:
                evidence = evidence or str(evidence_predicate(message) or "")
            except Exception:
                evidence = evidence or ""
        if message.get_type() == "COMMAND_ACK":
            if ack_history is not None:
                remember_command_ack(ack_history, message, names)
        if message.get_type() == "COMMAND_ACK" and int(getattr(message, "command", -1)) == int(mav_command):
            result = int(getattr(message, "result", -1))
            return {
                "command": int(mav_command),
                "result": result,
                "resultText": names.get(result, f"RESULT_{result}"),
                "evidenceText": evidence,
                "latestStatustext": latest_statustext,
                **vehicle_target(master),
            }
    if evidence:
        in_progress = getattr(mavutil.mavlink, "MAV_RESULT_IN_PROGRESS", 5)
        return {
            "command": int(mav_command),
            "result": in_progress,
            "resultText": names.get(in_progress, "IN_PROGRESS"),
            "evidenceText": evidence,
            "latestStatustext": latest_statustext,
            **vehicle_target(master),
        }
    return {
        "command": int(mav_command),
        "result": None,
        "resultText": "TIMEOUT",
        "evidenceText": evidence,
        "latestStatustext": latest_statustext,
        **vehicle_target(master),
    }


def send_flight_mode(
    master: Any,
    mode_key: str,
    command_id: str | None = None,
    on_message: Callable[[Any], None] | None = None,
    *,
    flight_mode_commands: dict[str, dict[str, Any]],
    mode_encoder: Callable[[int, int], int],
    mode_decoder: Callable[[int], str],
    ack_history: list[dict[str, Any]] | None = None,
    result_names: dict[int, str] | None = None,
) -> None:
    selected_mode = str(mode_key or "").strip().lower()
    preset = flight_mode_commands.get(selected_mode)
    if not preset:
        write_command_status(
            command_id,
            status="rejected",
            message=f"Unsupported PX4 flight mode: {selected_mode}",
            results=[],
        )
        return

    main_mode = int(preset["mainMode"])
    sub_mode = int(preset["subMode"])
    custom_mode = mode_encoder(main_mode, sub_mode)
    base_mode = getattr(mavutil.mavlink, "MAV_MODE_FLAG_CUSTOM_MODE_ENABLED", 1)
    mav_command = mavutil.mavlink.MAV_CMD_DO_SET_MODE

    write_command_status(
        command_id,
        status="running",
        message=f"Sending flight mode switch to {preset['label']} / {preset['px4']}",
        results=[],
    )
    bus = MessageBus.for_master(master)
    ack_cursor = bus.current_sequence() if bus is not None else None
    send_command_long(master, mav_command, (base_mode, main_mode, sub_mode, 0, 0, 0, 0))
    ack = wait_for_command_ack(
        master,
        mav_command,
        timeout=1.2,
        ack_history=ack_history,
        result_names=result_names,
        cursor=ack_cursor,
    )
    fallback_sent = False
    if ack["resultText"] not in {"ACCEPTED", "IN_PROGRESS"}:
        fallback_sent = True
        master.mav.set_mode_send(master.target_system, base_mode, custom_mode)

    confirmed = False
    observed_mode = None
    deadline = time.monotonic() + 2.5
    bus = MessageBus.for_master(master)
    cursor = bus.current_sequence() if bus is not None else None
    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        message, cursor = wait_for_message(
            master,
            message_types=("HEARTBEAT",),
            timeout=min(0.2, remaining),
            on_message=on_message,
            heartbeat=lambda: send_gcs_heartbeat(master),
            cursor=cursor,
            poll_interval=0.05,
        )
        if message is None:
            continue
        if message.get_type() == "HEARTBEAT" and is_vehicle_heartbeat(message):
            observed_mode = mode_decoder(getattr(message, "custom_mode", 0))
            if int(getattr(message, "custom_mode", -1)) == custom_mode:
                confirmed = True
                break

    accepted_ack = ack["resultText"] in {"ACCEPTED", "IN_PROGRESS"}
    if confirmed:
        status = "accepted"
        status_message = f"Flight controller switched to {preset['label']} / {preset['px4']}"
    elif accepted_ack:
        status = "accepted"
        status_message = f"Flight controller accepted mode switch: {preset['label']}; waiting for HEARTBEAT confirmation"
    else:
        status = "sent_no_ack" if is_ack_timeout(ack) or fallback_sent else command_result_status(ack)
        status_message = f"Mode switch was not confirmed by flight controller: {ack['resultText']}"

    write_command_status(
        command_id,
        status=status,
        message=status_message,
        results=[{
            "mode": selected_mode,
            "label": preset["label"],
            "px4Mode": preset["px4"],
            "mainMode": main_mode,
            "subMode": sub_mode,
            "customMode": custom_mode,
            "ack": ack,
            "fallbackSetModeSent": fallback_sent,
            "heartbeatConfirmed": confirmed,
            "observedMode": observed_mode,
        }],
    )


def send_arm_disarm(
    master: Any,
    arm: bool,
    command_id: str | None = None,
    on_message: Callable[[Any], None] | None = None,
    *,
    ack_history: list[dict[str, Any]] | None = None,
    result_names: dict[int, str] | None = None,
) -> None:
    mav_command = mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM
    arm = bool(arm)
    action_label = "arm" if arm else "disarm"
    write_command_status(
        command_id,
        status="running",
        message=f"Sending {action_label} command to flight controller",
        results=[],
    )
    bus = MessageBus.for_master(master)
    ack_cursor = bus.current_sequence() if bus is not None else None
    send_command_long(master, mav_command, (1 if arm else 0, 0, 0, 0, 0, 0, 0))
    ack = wait_for_command_ack(
        master,
        mav_command,
        timeout=1.5,
        ack_history=ack_history,
        result_names=result_names,
        cursor=ack_cursor,
    )
    accepted = ack["resultText"] in {"ACCEPTED", "IN_PROGRESS"}
    confirmed = False
    observed_armed = None
    deadline = time.monotonic() + 2.5
    bus = MessageBus.for_master(master)
    cursor = bus.current_sequence() if bus is not None else None
    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        message, cursor = wait_for_message(
            master,
            message_types=("HEARTBEAT",),
            timeout=min(0.2, remaining),
            on_message=on_message,
            heartbeat=lambda: send_gcs_heartbeat(master),
            cursor=cursor,
            poll_interval=0.05,
        )
        if message is None:
            continue
        if message.get_type() == "HEARTBEAT" and is_vehicle_heartbeat(message):
            observed_armed = bool(
                int(getattr(message, "base_mode", 0))
                & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED
            )
            if observed_armed == arm:
                confirmed = True
                break

    if confirmed:
        status = "accepted"
        message = f"Flight controller confirmed {action_label}"
    elif accepted:
        status = "accepted"
        message = f"Flight controller accepted {action_label}; waiting for HEARTBEAT confirmation"
    else:
        status = "sent_no_ack" if is_ack_timeout(ack) else command_result_status(ack)
        message = f"{action_label} was not confirmed by flight controller: {ack['resultText']}"

    write_command_status(
        command_id,
        status=status,
        message=message,
        results=[{
            "command": "MAV_CMD_COMPONENT_ARM_DISARM",
            "arm": arm,
            "ack": ack,
            "observedArmed": observed_armed,
            "confirmed": confirmed,
        }],
    )

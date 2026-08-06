"""PX4 MAVLink parameter read/write helpers."""

from __future__ import annotations

import struct
import time
from collections.abc import Callable
from typing import Any

from pymavlink import mavutil

from backend.communication.command_queue import write_command_status
from backend.communication.heartbeat_manager import send_gcs_heartbeat
from backend.communication.message_bus import MessageBus, wait_for_message


INTEGER_PARAM_TYPES = {
    mavutil.mavlink.MAV_PARAM_TYPE_UINT8,
    mavutil.mavlink.MAV_PARAM_TYPE_INT8,
    mavutil.mavlink.MAV_PARAM_TYPE_UINT16,
    mavutil.mavlink.MAV_PARAM_TYPE_INT16,
    mavutil.mavlink.MAV_PARAM_TYPE_UINT32,
    mavutil.mavlink.MAV_PARAM_TYPE_INT32,
}


def decode_param_value(message: Any) -> float | int:
    value = float(message.param_value)
    param_type = int(getattr(message, "param_type", 0) or 0)
    if param_type not in INTEGER_PARAM_TYPES:
        return value
    raw = struct.pack("<f", value)
    if param_type == mavutil.mavlink.MAV_PARAM_TYPE_UINT8:
        return struct.unpack("<B", raw[:1])[0]
    if param_type == mavutil.mavlink.MAV_PARAM_TYPE_INT8:
        return struct.unpack("<b", raw[:1])[0]
    if param_type == mavutil.mavlink.MAV_PARAM_TYPE_UINT16:
        return struct.unpack("<H", raw[:2])[0]
    if param_type == mavutil.mavlink.MAV_PARAM_TYPE_INT16:
        return struct.unpack("<h", raw[:2])[0]
    if param_type == mavutil.mavlink.MAV_PARAM_TYPE_UINT32:
        return struct.unpack("<I", raw)[0]
    if param_type == mavutil.mavlink.MAV_PARAM_TYPE_INT32:
        return struct.unpack("<i", raw)[0]
    return value


def request_output_function_params(
    master: Any,
    output_function_params: list[str],
    rc_map_parameter_names: list[str],
    rc_calibration_parameter_names: list[str],
    delay_s: float = 0.02,
) -> None:
    for name in [*output_function_params, *rc_map_parameter_names, *rc_calibration_parameter_names]:
        master.mav.param_request_read_send(
            master.target_system,
            master.target_component,
            name.encode("ascii"),
            -1,
        )
        time.sleep(delay_s)


def wait_for_param_value(
    master: Any,
    name: str,
    expected: float | int | None = None,
    timeout: float = 2.0,
    decoder: Callable[[Any], float | int] = decode_param_value,
    cursor: int | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    bus = MessageBus.for_master(master)
    if cursor is None and bus is not None:
        cursor = bus.current_sequence()

    def matches_param(message: Any) -> bool:
        param_id = message.param_id
        if isinstance(param_id, bytes):
            param_id = param_id.decode("ascii", errors="replace")
        return str(param_id).rstrip("\x00") == name

    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        message, cursor = wait_for_message(
            master,
            message_types=("PARAM_VALUE",),
            predicate=matches_param,
            timeout=min(0.1, remaining),
            heartbeat=lambda: send_gcs_heartbeat(master),
            cursor=cursor,
            poll_interval=0.05,
        )
        if message is None:
            continue
        value = decoder(message)
        if expected is None or int(round(float(value))) == int(round(float(expected))):
            return {"name": name, "value": value, "confirmed": True}
        return {"name": name, "value": value, "confirmed": False}
    return {"name": name, "value": None, "confirmed": False}


def request_parameter_values(
    master: Any,
    names: list[str] | None = None,
    command_id: str | None = None,
    on_message: Callable[[Any], None] | None = None,
    *,
    default_parameter_requests: list[str],
    decoder: Callable[[Any], float | int] = decode_param_value,
) -> None:
    requested = [
        str(name or "").strip().upper()
        for name in (names or default_parameter_requests)
        if str(name or "").strip()
    ]
    requested = list(dict.fromkeys(requested))
    if not requested:
        requested = list(default_parameter_requests)
    write_command_status(
        command_id,
        status="running",
        message=f"Reading flight controller parameters: 0/{len(requested)}",
        results=[],
    )
    bus = MessageBus.for_master(master)
    cursor = bus.current_sequence() if bus is not None else None
    for name in requested:
        master.mav.param_request_read_send(
            master.target_system,
            master.target_component,
            name.encode("ascii", errors="ignore"),
            -1,
        )
        time.sleep(0.015)

    pending = set(requested)
    results: dict[str, dict[str, Any]] = {}
    deadline = time.monotonic() + max(4.0, len(requested) * 0.16)
    while pending and time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        message, cursor = wait_for_message(
            master,
            message_types=("PARAM_VALUE",),
            timeout=min(0.12, remaining),
            on_message=on_message,
            heartbeat=lambda: send_gcs_heartbeat(master),
            cursor=cursor,
            poll_interval=0.05,
        )
        if message is None:
            continue
        param_id = message.param_id
        if isinstance(param_id, bytes):
            param_id = param_id.decode("ascii", errors="replace")
        name = str(param_id).rstrip("\x00")
        if name not in pending:
            continue
        value = decoder(message)
        results[name] = {
            "name": name,
            "value": value,
            "index": int(getattr(message, "param_index", -1)),
            "count": int(getattr(message, "param_count", -1)),
            "type": int(getattr(message, "param_type", 0) or 0),
        }
        pending.discard(name)
        write_command_status(
            command_id,
            status="running",
            message=f"Reading flight controller parameters: {len(results)}/{len(requested)}",
            results=list(results.values()),
        )

    status = "accepted" if results else "sent_no_ack"
    if pending and results:
        status = "partial"
    write_command_status(
        command_id,
        status=status,
        message=(
            f"Parameter read completed: {len(results)}/{len(requested)}"
            if results
            else "Parameter read timed out: no PARAM_VALUE received"
        ),
        results=list(results.values()),
        missing=sorted(pending),
    )


def encode_param_set_value(name: str, value: float) -> tuple[float, int]:
    numeric_value = float(value)
    if name.startswith(("PWM_MAIN_FUNC", "PWM_AUX_FUNC")):
        encoded_value = struct.unpack("<f", struct.pack("<i", int(numeric_value)))[0]
        return encoded_value, mavutil.mavlink.MAV_PARAM_TYPE_INT32
    return numeric_value, mavutil.mavlink.MAV_PARAM_TYPE_REAL32


def send_parameter(master: Any, name: str, value: float, command_id: str | None = None) -> None:
    normalized_name = str(name or "").strip().upper()
    numeric_value = float(value)
    encoded_value, param_type = encode_param_set_value(normalized_name, numeric_value)
    write_command_status(
        command_id,
        status="running",
        message=f"Writing parameter {normalized_name} to flight controller",
        results=[],
    )
    bus = MessageBus.for_master(master)
    cursor = bus.current_sequence() if bus is not None else None
    master.mav.param_set_send(
        master.target_system,
        master.target_component,
        normalized_name.encode("ascii"),
        encoded_value,
        param_type,
    )
    result = wait_for_param_value(
        master,
        normalized_name,
        expected=numeric_value,
        timeout=2.0,
        cursor=cursor,
    )
    status = "accepted" if result["confirmed"] else "sent_no_ack"
    message = (
        f"Parameter {normalized_name} confirmed as {result['value']}"
        if result["confirmed"]
        else f"Parameter {normalized_name} was sent but not confirmed"
    )
    write_command_status(
        command_id,
        status=status,
        message=message,
        results=[{"name": normalized_name, "value": numeric_value, "ack": result}],
    )

"""PX4 MAVLink mission upload/read helpers."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from pymavlink import mavutil

from backend.communication.command_queue import write_command_status
from backend.communication.heartbeat_manager import send_gcs_heartbeat
from backend.communication.message_bus import MessageBus, wait_for_message


MAV_MISSION_ACCEPTED = getattr(mavutil.mavlink, "MAV_MISSION_ACCEPTED", 0)
MAV_MISSION_TYPE_MISSION = getattr(mavutil.mavlink, "MAV_MISSION_TYPE_MISSION", 0)

MISSION_COMMANDS = {
    "TAKEOFF": getattr(mavutil.mavlink, "MAV_CMD_NAV_TAKEOFF", 22),
    "WAYPOINT": getattr(mavutil.mavlink, "MAV_CMD_NAV_WAYPOINT", 16),
    "LOITER": getattr(mavutil.mavlink, "MAV_CMD_NAV_LOITER_TIME", 19),
    "LAND": getattr(mavutil.mavlink, "MAV_CMD_NAV_LAND", 21),
    "RTL": getattr(mavutil.mavlink, "MAV_CMD_NAV_RETURN_TO_LAUNCH", 20),
    "DO_CHANGE_SPEED": getattr(mavutil.mavlink, "MAV_CMD_DO_CHANGE_SPEED", 178),
}


def mission_ack_text(ack_type: int) -> str:
    names = {
        0: "ACCEPTED",
        1: "ERROR",
        2: "UNSUPPORTED_FRAME",
        3: "UNSUPPORTED",
        4: "NO_SPACE",
        5: "INVALID",
        6: "INVALID_PARAM1",
        7: "INVALID_PARAM2",
        8: "INVALID_PARAM3",
        9: "INVALID_PARAM4",
        10: "INVALID_PARAM5_X",
        11: "INVALID_PARAM6_Y",
        12: "INVALID_PARAM7",
        13: "INVALID_SEQUENCE",
        14: "DENIED",
        15: "OPERATION_CANCELLED",
    }
    return names.get(int(ack_type), f"ACK_{ack_type}")


def _commands(mission_commands: dict[str, int] | None = None) -> dict[str, int]:
    return mission_commands or MISSION_COMMANDS


def send_mission_clear_all(master: Any, mission_type: int = MAV_MISSION_TYPE_MISSION) -> None:
    try:
        master.mav.mission_clear_all_send(
            master.target_system,
            master.target_component,
            mission_type,
        )
    except TypeError:
        master.mav.mission_clear_all_send(master.target_system, master.target_component)


def wait_mission_ack(
    master: Any,
    timeout: float = 5.0,
    on_message: Callable[[Any], None] | None = None,
    cursor: int | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    bus = MessageBus.for_master(master)
    if cursor is None and bus is not None:
        cursor = bus.current_sequence()
    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        message, cursor = wait_for_message(
            master,
            message_types=("MISSION_ACK",),
            timeout=min(0.2, remaining),
            on_message=on_message,
            heartbeat=lambda: send_gcs_heartbeat(master),
            cursor=cursor,
            poll_interval=0.05,
        )
        if message is None:
            continue
        if message.get_type() == "MISSION_ACK":
            ack_type = int(getattr(message, "type", -1))
            return {"type": ack_type, "resultText": mission_ack_text(ack_type)}
    return {"type": -1, "resultText": "TIMEOUT"}


def clear_mission(
    master: Any,
    command_id: str | None = None,
    on_message: Callable[[Any], None] | None = None,
    *,
    accepted_ack: int = MAV_MISSION_ACCEPTED,
    mission_type: int = MAV_MISSION_TYPE_MISSION,
) -> None:
    write_command_status(command_id, status="running", message="Clearing flight mission", results=[])
    bus = MessageBus.for_master(master)
    cursor = bus.current_sequence() if bus is not None else None
    send_mission_clear_all(master, mission_type=mission_type)
    ack = wait_mission_ack(master, timeout=5.0, on_message=on_message, cursor=cursor)
    accepted = ack["type"] == accepted_ack
    write_command_status(
        command_id,
        status="accepted" if accepted else "rejected",
        message="Flight mission cleared" if accepted else f"Mission clear failed: {ack['resultText']}",
        results=[{"ack": ack}],
    )


def mission_item_payload(
    item: dict[str, Any],
    seq: int,
    total: int,
    *,
    mission_commands: dict[str, int] | None = None,
) -> dict[str, Any]:
    commands = _commands(mission_commands)
    command_name = str(item.get("command", "WAYPOINT")).upper()
    command = commands.get(command_name, commands["WAYPOINT"])
    frame = getattr(mavutil.mavlink, "MAV_FRAME_GLOBAL_RELATIVE_ALT_INT", 6)
    lat = float(item.get("lat", 0) or 0)
    lon = float(item.get("lon", 0) or 0)
    altitude = float(item.get("altitude", 0) or 0)
    hold = float(item.get("hold", 0) or 0)
    param1 = hold if command_name in {"WAYPOINT", "LOITER"} else 0
    param2 = 0
    param3 = 0
    param4 = float("nan")
    if command_name == "DO_CHANGE_SPEED":
        frame = getattr(mavutil.mavlink, "MAV_FRAME_MISSION", 2)
        param1 = 1
        param2 = float(item.get("speed", 0) or 0)
        param3 = -1
        param4 = 0
        lat = lon = altitude = 0
    if command_name == "RTL":
        lat = lon = altitude = 0
    return {
        "seq": int(seq),
        "frame": frame,
        "command": int(command),
        "current": 1 if seq == 0 else 0,
        "autocontinue": 1 if seq < total - 1 else 0,
        "param1": param1,
        "param2": param2,
        "param3": param3,
        "param4": param4,
        "x": int(round(lat * 1e7)),
        "y": int(round(lon * 1e7)),
        "z": altitude,
        "name": command_name,
    }


def expand_mission_upload_items(waypoints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    expanded = []
    last_speed = None
    for item in waypoints:
        command_name = str(item.get("command", "WAYPOINT")).upper()
        speed = float(item.get("speed", 0) or 0)
        if speed > 0 and command_name not in {"RTL"} and speed != last_speed:
            expanded.append({"command": "DO_CHANGE_SPEED", "speed": speed})
            last_speed = speed
        expanded.append(item)
    return expanded


def send_mission_item(
    master: Any,
    item: dict[str, Any],
    seq: int,
    total: int,
    use_int: bool = True,
    *,
    mission_commands: dict[str, int] | None = None,
    mission_type: int = MAV_MISSION_TYPE_MISSION,
) -> dict[str, Any]:
    payload = mission_item_payload(item, seq, total, mission_commands=mission_commands)
    if use_int:
        try:
            master.mav.mission_item_int_send(
                master.target_system,
                master.target_component,
                payload["seq"],
                payload["frame"],
                payload["command"],
                payload["current"],
                payload["autocontinue"],
                payload["param1"],
                payload["param2"],
                payload["param3"],
                payload["param4"],
                payload["x"],
                payload["y"],
                payload["z"],
                mission_type,
            )
            return payload
        except TypeError:
            master.mav.mission_item_int_send(
                master.target_system,
                master.target_component,
                payload["seq"],
                payload["frame"],
                payload["command"],
                payload["current"],
                payload["autocontinue"],
                payload["param1"],
                payload["param2"],
                payload["param3"],
                payload["param4"],
                payload["x"],
                payload["y"],
                payload["z"],
            )
            return payload
    args = [
        master.target_system,
        master.target_component,
        payload["seq"],
        getattr(mavutil.mavlink, "MAV_FRAME_GLOBAL_RELATIVE_ALT", 3),
        payload["command"],
        payload["current"],
        payload["autocontinue"],
        payload["param1"],
        payload["param2"],
        payload["param3"],
        payload["param4"],
        payload["x"] / 1e7,
        payload["y"] / 1e7,
        payload["z"],
    ]
    try:
        master.mav.mission_item_send(*args, mission_type)
    except TypeError:
        master.mav.mission_item_send(*args)
    return payload


def send_mission_count(master: Any, count: int, mission_type: int = MAV_MISSION_TYPE_MISSION) -> None:
    try:
        master.mav.mission_count_send(
            master.target_system,
            master.target_component,
            int(count),
            mission_type,
        )
    except TypeError:
        master.mav.mission_count_send(master.target_system, master.target_component, int(count))


def upload_mission(
    master: Any,
    waypoints: list[dict[str, Any]],
    command_id: str | None = None,
    clear_existing: bool = True,
    on_message: Callable[[Any], None] | None = None,
    *,
    mission_commands: dict[str, int] | None = None,
    accepted_ack: int = MAV_MISSION_ACCEPTED,
    mission_type: int = MAV_MISSION_TYPE_MISSION,
) -> None:
    waypoints = list(waypoints or [])
    if not waypoints:
        raise RuntimeError("Mission has no waypoints")
    upload_items = expand_mission_upload_items(waypoints)
    write_command_status(
        command_id,
        status="running",
        message=f"Uploading Mission: 0/{len(upload_items)}",
        results=[{
            "count": len(upload_items),
            "sourceWaypointCount": len(waypoints),
            "uploaded": 0,
            "progress": 0,
        }],
    )
    if clear_existing:
        bus = MessageBus.for_master(master)
        clear_cursor = bus.current_sequence() if bus is not None else None
        send_mission_clear_all(master, mission_type=mission_type)
        wait_mission_ack(master, timeout=3.0, on_message=on_message, cursor=clear_cursor)
    bus = MessageBus.for_master(master)
    cursor = bus.current_sequence() if bus is not None else None
    send_mission_count(master, len(upload_items), mission_type=mission_type)
    sent = {}
    deadline = time.monotonic() + max(20.0, len(upload_items) * 4.0)
    last_activity = time.monotonic()
    count_retries = 0
    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        message, cursor = wait_for_message(
            master,
            message_types=("MISSION_REQUEST_INT", "MISSION_REQUEST", "MISSION_ACK"),
            timeout=min(0.3, remaining),
            on_message=on_message,
            heartbeat=lambda: send_gcs_heartbeat(master),
            cursor=cursor,
            poll_interval=0.05,
        )
        if message is None:
            if time.monotonic() - last_activity > 2.0 and count_retries < 3:
                count_retries += 1
                send_mission_count(master, len(upload_items), mission_type=mission_type)
                last_activity = time.monotonic()
                write_command_status(
                    command_id,
                    status="running",
                    message=f"Mission upload waiting for vehicle request; resent MISSION_COUNT {count_retries}/3",
                    results=[{
                        "count": len(upload_items),
                        "sourceWaypointCount": len(waypoints),
                        "uploaded": len(sent),
                        "progress": round(len(sent) * 100 / len(upload_items), 1),
                        "countRetries": count_retries,
                    }],
                )
            continue
        msg_type = message.get_type()
        last_activity = time.monotonic()
        if msg_type in {"MISSION_REQUEST_INT", "MISSION_REQUEST"}:
            seq = int(getattr(message, "seq", -1))
            if not 0 <= seq < len(upload_items):
                raise RuntimeError(f"Vehicle requested invalid mission seq {seq}")
            use_int = msg_type == "MISSION_REQUEST_INT"
            payload = send_mission_item(
                master,
                upload_items[seq],
                seq,
                len(upload_items),
                use_int=use_int,
                mission_commands=mission_commands,
                mission_type=mission_type,
            )
            sent[seq] = payload
            progress = round(len(sent) * 100 / len(upload_items), 1)
            write_command_status(
                command_id,
                status="running",
                message=f"Uploading Mission: {len(sent)}/{len(upload_items)}",
                results=[{
                    "count": len(upload_items),
                    "sourceWaypointCount": len(waypoints),
                    "uploaded": len(sent),
                    "progress": progress,
                    "lastSeq": seq,
                    "lastCommand": payload["name"],
                }],
            )
            deadline = time.monotonic() + 8.0
            continue
        if msg_type == "MISSION_ACK":
            ack_type = int(getattr(message, "type", -1))
            accepted = ack_type == accepted_ack
            verification = None
            if accepted:
                verification = verify_uploaded_mission(
                    master,
                    upload_items,
                    on_message=on_message,
                    mission_commands=mission_commands,
                    mission_type=mission_type,
                )
            result = {
                "count": len(upload_items),
                "sourceWaypointCount": len(waypoints),
                "uploaded": len(sent),
                "progress": 100 if accepted else round(len(sent) * 100 / len(upload_items), 1),
                "ack": {"type": ack_type, "resultText": mission_ack_text(ack_type)},
                "verified": bool(verification and verification.get("matched")),
                "verification": verification,
            }
            write_command_status(
                command_id,
                status="accepted" if accepted and result["verified"] else ("partial" if accepted else "rejected"),
                message=(
                    f"Mission upload completed and readback verification passed: {len(waypoints)} waypoints, {len(upload_items)} mission items"
                    if accepted and result["verified"]
                    else f"Mission accepted by vehicle but readback verification failed: {(verification or {}).get('reason', 'no verification')}"
                    if accepted
                    else f"Mission upload failed: {mission_ack_text(ack_type)}"
                ),
                results=[result],
            )
            return
    write_command_status(
        command_id,
        status="rejected",
        message="Mission upload timed out: no complete MISSION_REQUEST/MISSION_ACK flow",
        results=[{"count": len(upload_items), "uploaded": len(sent), "progress": round(len(sent) * 100 / len(upload_items), 1)}],
    )


def request_mission_item(master: Any, seq: int, mission_type: int = MAV_MISSION_TYPE_MISSION) -> None:
    try:
        master.mav.mission_request_int_send(
            master.target_system,
            master.target_component,
            int(seq),
            mission_type,
        )
    except (AttributeError, TypeError):
        try:
            master.mav.mission_request_send(
                master.target_system,
                master.target_component,
                int(seq),
                mission_type,
            )
        except TypeError:
            master.mav.mission_request_send(master.target_system, master.target_component, int(seq))


def request_mission_list(master: Any, mission_type: int = MAV_MISSION_TYPE_MISSION) -> None:
    try:
        master.mav.mission_request_list_send(
            master.target_system,
            master.target_component,
            mission_type,
        )
    except TypeError:
        master.mav.mission_request_list_send(master.target_system, master.target_component)


def mav_command_label(command: int, mission_commands: dict[str, int] | None = None) -> str:
    command = int(command)
    for name, value in _commands(mission_commands).items():
        if int(value) == command:
            return name
    return f"CMD_{command}"


def mission_message_to_dict(message: Any, mission_commands: dict[str, int] | None = None) -> dict[str, Any]:
    msg_type = message.get_type()
    command = int(getattr(message, "command", 0))
    if msg_type == "MISSION_ITEM_INT":
        lat = float(getattr(message, "x", 0) or 0) / 1e7
        lon = float(getattr(message, "y", 0) or 0) / 1e7
    else:
        lat = float(getattr(message, "x", 0) or 0)
        lon = float(getattr(message, "y", 0) or 0)
    item = {
        "seq": int(getattr(message, "seq", 0)),
        "frame": int(getattr(message, "frame", 0)),
        "command": command,
        "commandName": mav_command_label(command, mission_commands),
        "current": int(getattr(message, "current", 0)),
        "autocontinue": int(getattr(message, "autocontinue", 0)),
        "param1": float(getattr(message, "param1", 0) or 0),
        "param2": float(getattr(message, "param2", 0) or 0),
        "param3": float(getattr(message, "param3", 0) or 0),
        "param4": float(getattr(message, "param4", 0) or 0),
        "lat": lat,
        "lon": lon,
        "altitude": float(getattr(message, "z", 0) or 0),
    }
    if item["commandName"] == "DO_CHANGE_SPEED":
        item["speed"] = item["param2"]
    return item


def mission_items_match_upload(
    upload_items: list[dict[str, Any]],
    read_items: list[dict[str, Any]],
    *,
    mission_commands: dict[str, int] | None = None,
) -> tuple[bool, str]:
    commands = _commands(mission_commands)
    if len(read_items) != len(upload_items):
        return False, f"Mission item count mismatch: uploaded {len(upload_items)}, read {len(read_items)}"
    by_seq = {int(item.get("seq", -1)): item for item in read_items}
    for seq, upload in enumerate(upload_items):
        read_item = by_seq.get(seq)
        if not read_item:
            return False, f"Missing readback mission item seq={seq}"
        raw_command = upload.get("command", -1)
        if isinstance(raw_command, str):
            expected_command = int(commands.get(raw_command.upper(), -1))
        else:
            expected_command = int(raw_command)
        actual_command = int(read_item.get("command", -2))
        if expected_command != actual_command:
            return False, f"seq={seq} command mismatch: uploaded {expected_command}, read {actual_command}"
        if expected_command in {commands["WAYPOINT"], commands["TAKEOFF"], commands["LAND"], commands["LOITER"]}:
            upload_lat = upload.get("lat")
            upload_lon = upload.get("lon")
            upload_alt = upload.get("altitude", upload.get("z", 0))
            if upload_lat is None and "x" in upload:
                upload_lat = float(upload.get("x", 0) or 0) / 1e7
            if upload_lon is None and "y" in upload:
                upload_lon = float(upload.get("y", 0) or 0) / 1e7
            lat_diff = abs(float(upload_lat or 0) - float(read_item.get("lat", 0) or 0))
            lon_diff = abs(float(upload_lon or 0) - float(read_item.get("lon", 0) or 0))
            alt_diff = abs(float(upload_alt or 0) - float(read_item.get("altitude", 0) or 0))
            if lat_diff > 1e-6 or lon_diff > 1e-6 or alt_diff > 0.5:
                return False, f"seq={seq} position/altitude mismatch"
    return True, "Readback mission matches uploaded mission"


def read_mission_items_from_vehicle(
    master: Any,
    command_id: str | None = None,
    on_message: Callable[[Any], None] | None = None,
    label: str = "Reading flight mission",
    *,
    mission_commands: dict[str, int] | None = None,
    mission_type: int = MAV_MISSION_TYPE_MISSION,
) -> dict[str, Any]:
    bus = MessageBus.for_master(master)
    cursor = bus.current_sequence() if bus is not None else None
    request_mission_list(master, mission_type=mission_type)
    deadline = time.monotonic() + 6.0
    count = None
    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        message, cursor = wait_for_message(
            master,
            message_types=("MISSION_COUNT",),
            timeout=min(0.25, remaining),
            on_message=on_message,
            heartbeat=lambda: send_gcs_heartbeat(master),
            cursor=cursor,
            poll_interval=0.05,
        )
        if message is None:
            continue
        if message.get_type() != "MISSION_COUNT":
            continue
        count = int(getattr(message, "count", 0))
        break
    if count is None:
        return {"count": None, "items": [], "status": "timeout", "reason": "Mission read timed out: no MISSION_COUNT"}

    items = {}
    for seq in range(count):
        received = False
        for _attempt in range(3):
            bus = MessageBus.for_master(master)
            if bus is not None:
                cursor = bus.current_sequence()
            request_mission_item(master, seq, mission_type=mission_type)
            item_deadline = time.monotonic() + 2.0
            while time.monotonic() < item_deadline:
                remaining = max(0.0, item_deadline - time.monotonic())
                message, cursor = wait_for_message(
                    master,
                    message_types=("MISSION_ITEM", "MISSION_ITEM_INT"),
                    timeout=min(0.18, remaining),
                    on_message=on_message,
                    heartbeat=lambda: send_gcs_heartbeat(master),
                    cursor=cursor,
                    poll_interval=0.05,
                )
                if message is None:
                    continue
                if message.get_type() not in {"MISSION_ITEM", "MISSION_ITEM_INT"}:
                    continue
                item = mission_message_to_dict(message, mission_commands=mission_commands)
                if item["seq"] != seq:
                    continue
                items[seq] = item
                received = True
                write_command_status(
                    command_id,
                    status="running",
                    message=f"{label}: {len(items)}/{count}",
                    results=[{"count": count, "items": [items[index] for index in sorted(items)]}],
                )
                break
            if received:
                break
        if not received:
            return {
                "count": count,
                "items": [items[index] for index in sorted(items)],
                "status": "partial",
                "reason": f"Mission read missing seq={seq}",
            }
    return {
        "count": count,
        "items": [items[index] for index in sorted(items)],
        "status": "accepted",
        "reason": f"Flight mission read: {len(items)}/{count}",
    }


def verify_uploaded_mission(
    master: Any,
    upload_items: list[dict[str, Any]],
    on_message: Callable[[Any], None] | None = None,
    *,
    mission_commands: dict[str, int] | None = None,
    mission_type: int = MAV_MISSION_TYPE_MISSION,
) -> dict[str, Any]:
    readback = read_mission_items_from_vehicle(
        master,
        command_id=None,
        on_message=on_message,
        label="Verifying uploaded Mission",
        mission_commands=mission_commands,
        mission_type=mission_type,
    )
    if readback.get("status") != "accepted":
        return {"matched": False, "reason": readback.get("reason") or "Mission readback failed", "readback": readback}
    matched, reason = mission_items_match_upload(
        upload_items,
        readback.get("items") or [],
        mission_commands=mission_commands,
    )
    return {"matched": matched, "reason": reason, "readback": readback}


def read_flight_mission(
    master: Any,
    command_id: str | None = None,
    on_message: Callable[[Any], None] | None = None,
    *,
    mission_commands: dict[str, int] | None = None,
    mission_type: int = MAV_MISSION_TYPE_MISSION,
) -> None:
    write_command_status(command_id, status="running", message="Reading flight mission", results=[])
    readback = read_mission_items_from_vehicle(
        master,
        command_id=command_id,
        on_message=on_message,
        mission_commands=mission_commands,
        mission_type=mission_type,
    )
    ordered = readback.get("items") or []
    count = readback.get("count") or 0
    status = "accepted" if readback.get("status") == "accepted" else ("partial" if ordered else "sent_no_ack")
    write_command_status(
        command_id,
        status=status,
        message=readback.get("reason") or f"Flight mission read: {len(ordered)}/{count}",
        results=[{"count": count, "items": ordered}],
    )

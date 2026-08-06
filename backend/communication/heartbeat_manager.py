"""MAVLink GCS identity and vehicle heartbeat helpers."""

from __future__ import annotations

import time
from typing import Any

from pymavlink import mavutil

from backend.communication.mavlink_receiver import receive_match_once


DEFAULT_SOURCE_SYSTEM = 255
DEFAULT_SOURCE_COMPONENT = mavutil.mavlink.MAV_COMP_ID_MISSIONPLANNER


def vehicle_target(master: Any) -> dict[str, int]:
    """Return the active vehicle target and our GCS source identity."""
    return {
        "targetSystem": int(getattr(master, "target_system", 0) or 0),
        "targetComponent": int(getattr(master, "target_component", 0) or 0),
        "sourceSystem": int(getattr(master, "source_system", DEFAULT_SOURCE_SYSTEM) or DEFAULT_SOURCE_SYSTEM),
        "sourceComponent": int(getattr(master, "source_component", DEFAULT_SOURCE_COMPONENT) or DEFAULT_SOURCE_COMPONENT),
    }


def is_vehicle_heartbeat(message: Any) -> bool:
    """Only autopilot heartbeats identify a controllable vehicle target."""
    if message is None or message.get_type() != "HEARTBEAT":
        return False
    vehicle_type = int(getattr(message, "type", -1) or -1)
    autopilot = int(getattr(message, "autopilot", -1) or -1)
    if vehicle_type == mavutil.mavlink.MAV_TYPE_GCS:
        return False
    if autopilot == mavutil.mavlink.MAV_AUTOPILOT_INVALID:
        return False
    return True


def send_gcs_heartbeat(master: Any, state: dict[str, Any] | None = None, force: bool = False) -> bool:
    """Send a MAVLink GCS heartbeat at a fixed 1 Hz cadence."""
    now = time.monotonic()
    if state is not None and not force:
        last = float(state.get("_lastGcsHeartbeatMono") or 0.0)
        if now - last < 1.0:
            return False
    if state is None and not force:
        last = float(getattr(master, "_codex_last_gcs_heartbeat_mono", 0.0) or 0.0)
        if now - last < 1.0:
            return False

    master.mav.heartbeat_send(
        mavutil.mavlink.MAV_TYPE_GCS,
        mavutil.mavlink.MAV_AUTOPILOT_INVALID,
        0,
        0,
        mavutil.mavlink.MAV_STATE_ACTIVE,
    )
    setattr(master, "_codex_last_gcs_heartbeat_mono", now)

    if state is not None:
        count = int(state.get("_gcsHeartbeatCount") or 0) + 1
        first = float(state.get("_gcsHeartbeatFirstMono") or now)
        elapsed = max(0.001, now - first)
        state["_lastGcsHeartbeatMono"] = now
        state["_gcsHeartbeatCount"] = count
        state["_gcsHeartbeatFirstMono"] = first
        state["gcsHeartbeat"] = {
            "enabled": True,
            "sending": True,
            "rateHz": round(count / elapsed, 2) if count > 1 else 1.0,
            "lastSentMs": int(time.time() * 1000),
            "count": count,
            "ourSystemId": int(getattr(master, "source_system", DEFAULT_SOURCE_SYSTEM) or DEFAULT_SOURCE_SYSTEM),
            "ourComponentId": int(getattr(master, "source_component", DEFAULT_SOURCE_COMPONENT) or DEFAULT_SOURCE_COMPONENT),
        }
    return True


def wait_for_vehicle_heartbeat(master: Any, connection: str, timeout: float = 30.0) -> Any | None:
    """Wait for a vehicle heartbeat, keeping UDP links awake with GCS heartbeats."""
    active_udp = str(connection or "").startswith(("udpout:", "udp:"))
    deadline = time.monotonic() + timeout
    last_ping = 0.0
    while time.monotonic() < deadline:
        if active_udp and time.monotonic() - last_ping >= 1.0:
            send_gcs_heartbeat(master)
            last_ping = time.monotonic()
        heartbeat = receive_match_once(master, message_type="HEARTBEAT", timeout=0.5)
        if is_vehicle_heartbeat(heartbeat):
            return heartbeat
    return None

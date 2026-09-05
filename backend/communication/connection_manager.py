"""MAVLink connection lifecycle helpers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
import time

from pymavlink import mavutil
from serial.tools import list_ports

from backend.communication.heartbeat_manager import wait_for_vehicle_heartbeat


DEFAULT_SOURCE_COMPONENT = mavutil.mavlink.MAV_COMP_ID_MISSIONPLANNER


def is_physical_serial_port(port: Any) -> bool:
    description = str(getattr(port, "description", "") or "")
    return "bluetooth" not in description.lower() and "\u84dd\u7259" not in description


def detect_serial_port(
    *,
    ports_provider: Callable[[], list[Any]] = list_ports.comports,
    input_func: Callable[[str], str] = input,
) -> str:
    ports = list(ports_provider())
    physical = [port for port in ports if is_physical_serial_port(port)]
    if len(physical) == 1:
        return str(physical[0].device)
    if not physical:
        raise SystemExit("No USB telemetry serial port found. Please connect the radio/flight controller and retry.")

    print("Multiple physical serial ports detected:")
    for index, port in enumerate(physical, start=1):
        print(f"  {index}. {port.device} - {port.description}")
    while True:
        selected = input_func("Select PX6C telemetry serial port number: ").strip()
        if selected.isdigit() and 1 <= int(selected) <= len(physical):
            return str(physical[int(selected) - 1].device)


def seed_udp_target(master: Any, udp_target: str | tuple[str, int] | None) -> tuple[str, int] | None:
    """Pre-register a UDP peer so udpin links can send GCS heartbeats before first RX packet."""
    if not udp_target:
        return None
    if isinstance(udp_target, tuple):
        host, port = udp_target
    else:
        parts = str(udp_target).strip().split(":")
        if len(parts) != 2 or not parts[0].strip():
            raise ValueError("UDP target must be host:port")
        host, port = parts[0].strip(), parts[1].strip()
    endpoint = (str(host), int(port))
    clients = getattr(master, "clients", None)
    clients_last_alive = getattr(master, "clients_last_alive", None)
    if isinstance(clients, set):
        clients.add(endpoint)
    if isinstance(clients_last_alive, dict):
        clients_last_alive[endpoint] = time.time()
    setattr(master, "_codex_udp_target", endpoint)
    return endpoint


def connect(
    connection: str,
    baud: int,
    source_system: int = 255,
    source_component: int | None = None,
    *,
    connection_factory: Callable[..., Any] = mavutil.mavlink_connection,
    heartbeat_waiter: Callable[[Any, str, float], Any | None] = wait_for_vehicle_heartbeat,
    heartbeat_timeout: float = 30.0,
    udp_target: str | tuple[str, int] | None = None,
) -> tuple[Any, Any]:
    source_component = source_component or DEFAULT_SOURCE_COMPONENT
    master = connection_factory(
        connection,
        baud=baud,
        autoreconnect=True,
        source_system=source_system,
        source_component=source_component,
    )
    seed_udp_target(master, udp_target)
    heartbeat = heartbeat_waiter(master, connection, heartbeat_timeout)
    if heartbeat is None:
        raise TimeoutError("No PX4 HEARTBEAT received within timeout")
    master.target_system = heartbeat.get_srcSystem()
    master.target_component = heartbeat.get_srcComponent()
    master._codex_vehicle_type = int(getattr(heartbeat, "type", -1) or -1)
    master._codex_vehicle_autopilot = int(getattr(heartbeat, "autopilot", -1) or -1)
    master._codex_vehicle_base_mode = int(getattr(heartbeat, "base_mode", 0) or 0)
    master._codex_vehicle_custom_mode = int(getattr(heartbeat, "custom_mode", 0) or 0)
    return master, heartbeat


def disconnect(master: Any) -> bool:
    if master is None:
        return False
    close = getattr(master, "close", None)
    if callable(close):
        close()
        return True
    port = getattr(master, "port", None)
    close_port = getattr(port, "close", None)
    if callable(close_port):
        close_port()
        return True
    return False


def reconnect(
    connection: str,
    baud: int,
    previous_master: Any | None = None,
    source_system: int = 255,
    source_component: int | None = None,
    **connect_kwargs: Any,
) -> tuple[Any, Any]:
    disconnect(previous_master)
    return connect(
        connection,
        baud,
        source_system=source_system,
        source_component=source_component,
        **connect_kwargs,
    )

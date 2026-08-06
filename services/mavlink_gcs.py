"""Compatibility import for the MAVLink heartbeat manager.

New code should import from ``backend.communication.heartbeat_manager``.
This module is kept so existing imports continue to work during the
incremental backend refactor.
"""

from backend.communication.heartbeat_manager import (  # noqa: F401
    DEFAULT_SOURCE_COMPONENT,
    DEFAULT_SOURCE_SYSTEM,
    is_vehicle_heartbeat,
    send_gcs_heartbeat,
    vehicle_target,
    wait_for_vehicle_heartbeat,
)

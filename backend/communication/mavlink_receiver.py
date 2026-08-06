"""Dedicated MAVLink receive loop."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from backend.communication.message_bus import MessageBus


def receive_match_once(master: Any, *, message_type: str | None = None, timeout: float = 0.1) -> Any | None:
    kwargs: dict[str, Any] = {"blocking": True, "timeout": timeout}
    if message_type is not None:
        kwargs["type"] = message_type
    return master.recv_match(**kwargs)


class MavlinkReceiver:
    """Own the raw recv_match loop and publish every MAVLink message to a bus."""

    def __init__(
        self,
        master: Any,
        message_bus: MessageBus,
        *,
        heartbeat_filter: Callable[[Any], bool] | None = None,
        timeout_s: float = 0.02,
    ) -> None:
        self.master = master
        self.message_bus = message_bus
        self.heartbeat_filter = heartbeat_filter
        self.timeout_s = max(0.001, float(timeout_s))
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="mavlink-receiver", daemon=True)
        self.last_message_mono: float | None = None
        self.last_vehicle_heartbeat_mono: float | None = None
        self.last_message_type: str | None = None
        self.error: Exception | None = None

    def start(self) -> None:
        self.message_bus.attach_master(self.master)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=1.0)

    def last_vehicle_heartbeat_age_s(self) -> float | None:
        if self.last_vehicle_heartbeat_mono is None:
            return None
        return time.monotonic() - self.last_vehicle_heartbeat_mono

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                message = receive_match_once(self.master, timeout=self.timeout_s)
            except Exception as error:
                self.error = error
                break
            if message is None:
                continue
            now = time.monotonic()
            self.last_message_mono = now
            self.last_message_type = str(message.get_type())
            if self.heartbeat_filter and self.heartbeat_filter(message):
                self.last_vehicle_heartbeat_mono = now
            self.message_bus.publish(message, received_mono=now)

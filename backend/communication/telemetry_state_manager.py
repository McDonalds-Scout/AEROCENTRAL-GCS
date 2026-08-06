"""Realtime telemetry state consumer for the MAVLink message bus."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from backend.communication.communication_monitor import CommunicationMonitor
from backend.communication.message_bus import MessageBus, MessageSubscription
from backend.communication.telemetry_state_cache import TelemetryStateCache


class TelemetryStateManager:
    """Consume MAVLink messages, update shared telemetry state, and publish at 50 Hz."""

    def __init__(
        self,
        *,
        state: dict[str, Any],
        message_bus: MessageBus,
        update_state: Callable[[dict[str, Any], Any], bool],
        publisher: Any,
        full_publish_message_types: set[str] | frozenset[str],
        heartbeat_filter: Callable[[Any], bool] | None = None,
        monitor: CommunicationMonitor | None = None,
        state_cache: TelemetryStateCache | None = None,
        publish_hz: float = 50.0,
    ) -> None:
        self.state = state
        self.message_bus = message_bus
        self.update_state = update_state
        self.publisher = publisher
        self.full_publish_message_types = {str(item) for item in full_publish_message_types}
        self.heartbeat_filter = heartbeat_filter
        self.monitor = monitor
        self.state_cache = state_cache
        self.publish_interval = 1.0 / max(1.0, float(publish_hz))
        self.state_lock = threading.RLock()
        self.stop_event = threading.Event()
        self.subscription: MessageSubscription | None = None
        self.publish_thread = threading.Thread(target=self._publish_loop, name="telemetry-state-publisher", daemon=True)
        self._dirty = False
        self._full_dirty = False
        self._last_vehicle_heartbeat_mono: float | None = None

    def start(self) -> None:
        self.subscription = self.message_bus.subscribe(
            None,
            self.handle_message,
            maxsize=1000,
            name="telemetry-state",
        )
        self.publish_thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.subscription is not None:
            self.subscription.close()
            self.subscription = None
        self.publish_thread.join(timeout=1.0)

    def publish_once(self, full: bool = False) -> None:
        with self.state_lock:
            if self.state_cache is not None:
                self.state_cache.update_from_state(self.state, full=full)
                payload, _revision = self.state_cache.snapshot()
            else:
                payload = None
        if payload is not None and hasattr(self.publisher, "publish_payload"):
            self.publisher.publish_payload(payload)
        elif payload is None:
            with self.state_lock:
                self.publisher.publish(self.state, full=full)

    def last_vehicle_heartbeat_age_s(self) -> float | None:
        with self.state_lock:
            if self._last_vehicle_heartbeat_mono is None:
                return None
            return time.monotonic() - self._last_vehicle_heartbeat_mono

    def handle_message(self, message: Any) -> None:
        message_type = str(message.get_type())
        now = time.monotonic()
        with self.state_lock:
            if self.heartbeat_filter and self.heartbeat_filter(message):
                self._last_vehicle_heartbeat_mono = now
            changed = self.update_state(self.state, message)
            if changed:
                self._dirty = True
                if message_type in self.full_publish_message_types:
                    self._full_dirty = True
        if self.monitor is not None:
            self.monitor.record_latency(message_type, now)

    def _publish_loop(self) -> None:
        next_tick = time.monotonic()
        while not self.stop_event.is_set():
            now = time.monotonic()
            wait = next_tick - now
            if wait > 0:
                self.stop_event.wait(wait)
                continue
            next_tick = max(next_tick + self.publish_interval, time.monotonic())
            with self.state_lock:
                if not self._dirty:
                    continue
                full = self._full_dirty
                self._dirty = False
                self._full_dirty = False
                if self.state_cache is not None:
                    self.state_cache.update_from_state(self.state, full=full)
                    payload, _revision = self.state_cache.snapshot()
                else:
                    payload = None
            if payload is not None and hasattr(self.publisher, "publish_payload"):
                self.publisher.publish_payload(payload)
            elif payload is None:
                with self.state_lock:
                    self.publisher.publish(self.state, full=full)

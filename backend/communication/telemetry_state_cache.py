"""Thread-safe latest telemetry payload cache."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any


class TelemetryStateCache:
    """Store the latest prebuilt UI telemetry payload."""

    def __init__(self, payload_builder: Callable[[dict[str, Any], bool], dict[str, Any]]) -> None:
        self.payload_builder = payload_builder
        self._lock = threading.Lock()
        self._payload: dict[str, Any] | None = None
        self._revision = 0
        self._full_revision = 0

    def update_from_state(self, state: dict[str, Any], *, full: bool = False) -> int:
        payload = self.payload_builder(state, bool(full))
        with self._lock:
            self._payload = payload
            self._revision += 1
            if full:
                self._full_revision = self._revision
            return self._revision

    def snapshot(self) -> tuple[dict[str, Any] | None, int]:
        with self._lock:
            return self._payload, self._revision

    def revision(self) -> int:
        with self._lock:
            return self._revision

    def full_revision(self) -> int:
        with self._lock:
            return self._full_revision

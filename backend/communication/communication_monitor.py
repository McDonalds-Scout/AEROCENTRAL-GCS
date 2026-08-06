"""Runtime diagnostics for the MAVLink communication pipeline."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Any


class CommunicationMonitor:
    """Track receive rates, latency, queue depth, and command wait timing."""

    def __init__(self, window_s: float = 5.0) -> None:
        self.window_s = max(1.0, float(window_s))
        self._lock = threading.Lock()
        self._received_at: deque[float] = deque()
        self._type_received_at: dict[str, deque[float]] = defaultdict(deque)
        self._latencies_ms: dict[str, float] = {}
        self._queue_depth = 0
        self._dropped_messages = 0
        self._command_waits_ms: deque[float] = deque(maxlen=200)

    def _trim(self, now: float) -> None:
        cutoff = now - self.window_s
        while self._received_at and self._received_at[0] < cutoff:
            self._received_at.popleft()
        for values in list(self._type_received_at.values()):
            while values and values[0] < cutoff:
                values.popleft()

    def record_message(self, message: Any, received_mono: float | None = None) -> None:
        now = received_mono if received_mono is not None else time.monotonic()
        message_type = message.get_type() if message is not None else "UNKNOWN"
        with self._lock:
            self._received_at.append(now)
            self._type_received_at[str(message_type)].append(now)
            self._latencies_ms[str(message_type)] = 0.0
            self._trim(now)

    def record_latency(self, message_type: str, received_mono: float) -> None:
        latency_ms = max(0.0, (time.monotonic() - float(received_mono)) * 1000.0)
        with self._lock:
            self._latencies_ms[str(message_type)] = latency_ms

    def record_queue_depth(self, depth: int) -> None:
        with self._lock:
            self._queue_depth = max(0, int(depth))

    def record_drop(self, count: int = 1) -> None:
        with self._lock:
            self._dropped_messages += max(0, int(count))

    def record_command_wait(self, elapsed_s: float) -> None:
        with self._lock:
            self._command_waits_ms.append(max(0.0, float(elapsed_s) * 1000.0))

    def snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            self._trim(now)
            recv_rate = len(self._received_at) / self.window_s
            type_rates = {
                message_type: round(len(values) / self.window_s, 2)
                for message_type, values in self._type_received_at.items()
            }
            command_waits = list(self._command_waits_ms)
            avg_command_wait = (
                sum(command_waits) / len(command_waits)
                if command_waits
                else 0.0
            )
            return {
                "recv_rate": round(recv_rate, 2),
                "message_queue_depth": self._queue_depth,
                "message_rates": type_rates,
                "attitude_latency_ms": round(self._latencies_ms.get("ATTITUDE", 0.0), 1),
                "gps_latency_ms": round(
                    max(
                        self._latencies_ms.get("GPS_RAW_INT", 0.0),
                        self._latencies_ms.get("GLOBAL_POSITION_INT", 0.0),
                    ),
                    1,
                ),
                "command_waiting_time_ms": round(avg_command_wait, 1),
                "dropped_messages": self._dropped_messages,
            }

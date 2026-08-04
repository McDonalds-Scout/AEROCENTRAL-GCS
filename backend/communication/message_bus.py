"""Thread-safe MAVLink message bus and compatibility wait helpers."""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from backend.communication.communication_monitor import CommunicationMonitor


PRIORITY_REALTIME = 0
PRIORITY_SAFETY = 1
PRIORITY_TRANSACTION = 2
PRIORITY_BACKGROUND = 3

PRIORITY_BY_TYPE = {
    "ATTITUDE": PRIORITY_REALTIME,
    "HIGHRES_IMU": PRIORITY_REALTIME,
    "GLOBAL_POSITION_INT": PRIORITY_REALTIME,
    "GPS_RAW_INT": PRIORITY_REALTIME,
    "VFR_HUD": PRIORITY_REALTIME,
    "RC_CHANNELS": PRIORITY_REALTIME,
    "RC_CHANNELS_RAW": PRIORITY_REALTIME,
    "MANUAL_CONTROL": PRIORITY_REALTIME,
    "SERVO_OUTPUT_RAW": PRIORITY_REALTIME,
    "BATTERY_STATUS": PRIORITY_REALTIME,
    "SYS_STATUS": PRIORITY_REALTIME,
    "HEARTBEAT": PRIORITY_SAFETY,
    "COMMAND_ACK": PRIORITY_SAFETY,
    "STATUSTEXT": PRIORITY_SAFETY,
    "PARAM_VALUE": PRIORITY_TRANSACTION,
    "MISSION_COUNT": PRIORITY_TRANSACTION,
    "MISSION_REQUEST": PRIORITY_TRANSACTION,
    "MISSION_REQUEST_INT": PRIORITY_TRANSACTION,
    "MISSION_ITEM": PRIORITY_TRANSACTION,
    "MISSION_ITEM_INT": PRIORITY_TRANSACTION,
    "MISSION_ACK": PRIORITY_TRANSACTION,
    "MISSION_CURRENT": PRIORITY_TRANSACTION,
    "MISSION_ITEM_REACHED": PRIORITY_TRANSACTION,
    "LOG_ENTRY": PRIORITY_BACKGROUND,
    "LOG_DATA": PRIORITY_BACKGROUND,
}


@dataclass(frozen=True)
class MessageEnvelope:
    sequence: int
    message: Any
    message_type: str
    priority: int
    received_mono: float
    received_wall_ms: int


class MessageSubscription:
    def __init__(
        self,
        bus: "MessageBus",
        message_types: set[str] | None,
        callback: Callable[[Any], None],
        *,
        maxsize: int,
        name: str,
    ) -> None:
        self.bus = bus
        self.message_types = message_types
        self.callback = callback
        self.queue: queue.PriorityQueue[tuple[int, int, MessageEnvelope]] = queue.PriorityQueue(maxsize=max(1, int(maxsize)))
        self.stop_event = threading.Event()
        self.name = name
        self.thread = threading.Thread(target=self._run, name=f"mavlink-bus-{name}", daemon=True)
        self.thread.start()

    def accepts(self, message_type: str) -> bool:
        return self.message_types is None or message_type in self.message_types

    def push(self, envelope: MessageEnvelope) -> None:
        item = (envelope.priority, envelope.sequence, envelope)
        try:
            self.queue.put_nowait(item)
        except queue.Full:
            if envelope.priority == PRIORITY_BACKGROUND:
                self.bus.record_drop()
                return
            self._drop_one_for_priority(envelope.priority)
            try:
                self.queue.put_nowait(item)
            except queue.Full:
                self.bus.record_drop()

    def _drop_one_for_priority(self, incoming_priority: int) -> None:
        retained: list[tuple[int, int, MessageEnvelope]] = []
        dropped = False
        while True:
            try:
                item = self.queue.get_nowait()
            except queue.Empty:
                break
            if not dropped and item[0] >= incoming_priority:
                dropped = True
                continue
            retained.append(item)
        for item in retained:
            try:
                self.queue.put_nowait(item)
            except queue.Full:
                self.bus.record_drop()
                break
        if dropped:
            self.bus.record_drop()

    def close(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=1.0)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                _priority, _sequence, envelope = self.queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                self.callback(envelope.message)
            except Exception:
                self.bus.record_drop()


class MessageBus:
    """Fan-out raw MAVLink messages without letting consumers call recv_match."""

    def __init__(
        self,
        *,
        history_size: int = 2000,
        monitor: CommunicationMonitor | None = None,
    ) -> None:
        self.monitor = monitor or CommunicationMonitor()
        self._history: deque[MessageEnvelope] = deque(maxlen=max(100, int(history_size)))
        self._sequence = 0
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._subscriptions: list[MessageSubscription] = []
        self._dropped_messages = 0

    def attach_master(self, master: Any) -> None:
        setattr(master, "_codex_message_bus", self)

    @staticmethod
    def for_master(master: Any) -> "MessageBus | None":
        return getattr(master, "_codex_message_bus", None)

    def current_sequence(self) -> int:
        with self._lock:
            return self._sequence

    def record_drop(self, count: int = 1) -> None:
        count = max(1, int(count))
        with self._lock:
            self._dropped_messages += count
        self.monitor.record_drop(count)

    def publish(self, message: Any, received_mono: float | None = None) -> MessageEnvelope | None:
        if message is None:
            return None
        message_type = str(message.get_type())
        now = received_mono if received_mono is not None else time.monotonic()
        envelope = MessageEnvelope(
            sequence=0,
            message=message,
            message_type=message_type,
            priority=PRIORITY_BY_TYPE.get(message_type, PRIORITY_TRANSACTION),
            received_mono=now,
            received_wall_ms=int(time.time() * 1000),
        )
        with self._condition:
            self._sequence += 1
            envelope = MessageEnvelope(
                sequence=self._sequence,
                message=envelope.message,
                message_type=envelope.message_type,
                priority=envelope.priority,
                received_mono=envelope.received_mono,
                received_wall_ms=envelope.received_wall_ms,
            )
            self._history.append(envelope)
            self.monitor.record_queue_depth(len(self._history))
            self._condition.notify_all()
            subscriptions = list(self._subscriptions)
        self.monitor.record_message(message, now)
        for subscription in subscriptions:
            if subscription.accepts(message_type):
                subscription.push(envelope)
        return envelope

    def subscribe(
        self,
        message_types: list[str] | tuple[str, ...] | set[str] | None,
        callback: Callable[[Any], None],
        *,
        maxsize: int = 500,
        name: str = "subscriber",
    ) -> MessageSubscription:
        normalized = {str(item) for item in message_types} if message_types is not None else None
        subscription = MessageSubscription(
            self,
            normalized,
            callback,
            maxsize=maxsize,
            name=name,
        )
        with self._lock:
            self._subscriptions.append(subscription)
        return subscription

    def close(self) -> None:
        with self._lock:
            subscriptions = list(self._subscriptions)
            self._subscriptions.clear()
        for subscription in subscriptions:
            subscription.close()

    def wait_for(
        self,
        *,
        message_types: list[str] | tuple[str, ...] | set[str] | None = None,
        predicate: Callable[[Any], bool] | None = None,
        timeout: float = 1.0,
        cursor: int | None = None,
        inspect: Callable[[Any], None] | None = None,
        heartbeat: Callable[[], None] | None = None,
        poll_interval: float = 0.1,
    ) -> tuple[Any | None, int]:
        wanted = {str(item) for item in message_types} if message_types is not None else None
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            seen = self._sequence if cursor is None else int(cursor)
        while time.monotonic() < deadline:
            if heartbeat:
                heartbeat()
            with self._condition:
                envelopes = [item for item in self._history if item.sequence > seen]
                if not envelopes:
                    remaining = max(0.0, deadline - time.monotonic())
                    self._condition.wait(min(float(poll_interval), remaining))
                    envelopes = [item for item in self._history if item.sequence > seen]
                for envelope in envelopes:
                    seen = max(seen, envelope.sequence)
                    if wanted is not None and envelope.message_type not in wanted:
                        continue
                    message = envelope.message
                    if inspect:
                        inspect(message)
                    if predicate is None or predicate(message):
                        elapsed = max(0.0, time.monotonic() - (deadline - float(timeout)))
                        self.monitor.record_command_wait(elapsed)
                        return message, seen
        self.monitor.record_command_wait(float(timeout))
        return None, seen

    def snapshot(self) -> dict[str, Any]:
        data = self.monitor.snapshot()
        with self._lock:
            data["message_queue_depth"] = len(self._history)
            data["dropped_messages"] = max(int(data.get("dropped_messages") or 0), self._dropped_messages)
            data["last_sequence"] = self._sequence
        return data


def attach_message_bus(master: Any, bus: MessageBus) -> MessageBus:
    bus.attach_master(master)
    return bus


def wait_for_message(
    master: Any,
    *,
    message_types: list[str] | tuple[str, ...] | set[str] | str | None = None,
    predicate: Callable[[Any], bool] | None = None,
    timeout: float = 1.0,
    on_message: Callable[[Any], None] | None = None,
    heartbeat: Callable[[], None] | None = None,
    cursor: int | None = None,
    poll_interval: float = 0.1,
) -> tuple[Any | None, int | None]:
    if isinstance(message_types, str):
        normalized = [message_types]
    else:
        normalized = message_types
    bus = MessageBus.for_master(master)
    if bus is not None:
        return bus.wait_for(
            message_types=normalized,
            predicate=predicate,
            timeout=timeout,
            cursor=cursor,
            inspect=None,
            heartbeat=heartbeat,
            poll_interval=poll_interval,
        )

    from backend.communication.mavlink_receiver import receive_match_once

    deadline = time.monotonic() + max(0.0, float(timeout))
    wanted = {str(item) for item in normalized} if normalized is not None else None
    seen = cursor
    while time.monotonic() < deadline:
        if heartbeat:
            heartbeat()
        message = receive_match_once(master, timeout=min(float(poll_interval), max(0.0, deadline - time.monotonic())))
        if message is None:
            continue
        msg_type = str(message.get_type())
        if wanted is not None and msg_type not in wanted:
            if on_message:
                on_message(message)
            continue
        if on_message:
            on_message(message)
        if predicate is None or predicate(message):
            return message, seen
    return None, seen

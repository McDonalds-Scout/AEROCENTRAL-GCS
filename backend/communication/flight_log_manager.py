"""PX4 MAVLink flight log list and download helpers."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from backend.communication.command_queue import write_command_status
from backend.communication.heartbeat_manager import send_gcs_heartbeat
from backend.communication.message_bus import MessageBus, wait_for_message


DEFAULT_CHUNK_SIZE = 90


def request_flight_log_entries(
    master: Any,
    command_id: str | None = None,
    on_message: Callable[[Any], None] | None = None,
) -> None:
    write_command_status(
        command_id,
        status="running",
        message="Reading flight controller SD card .ulg log list",
        results=[],
    )
    bus = MessageBus.for_master(master)
    cursor = bus.current_sequence() if bus is not None else None
    master.mav.log_request_list_send(master.target_system, master.target_component, 0, 0xFFFF)
    deadline = time.monotonic() + 5.0
    logs = {}
    expected_count = None
    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        message, cursor = wait_for_message(
            master,
            message_types=("LOG_ENTRY",),
            timeout=min(0.25, remaining),
            on_message=on_message,
            heartbeat=lambda: send_gcs_heartbeat(master),
            cursor=cursor,
            poll_interval=0.05,
        )
        if message is None:
            continue
        item = {
            "id": int(message.id),
            "numLogs": int(message.num_logs),
            "lastLogNum": int(message.last_log_num),
            "timeUtc": int(getattr(message, "time_utc", 0) or 0),
            "size": int(getattr(message, "size", 0) or 0),
        }
        logs[item["id"]] = item
        expected_count = item["numLogs"]
        if expected_count is not None and len(logs) >= expected_count:
            break
    ordered = [logs[key] for key in sorted(logs)]
    status = "accepted" if ordered else "rejected"
    message = (
        f"Read {len(ordered)} flight controller .ulg logs"
        if ordered
        else "No flight logs were read; confirm the SD card is inserted and PX4 logging is available"
    )
    write_command_status(command_id, status=status, message=message, results=ordered)
    print(message)


def safe_ulg_filename(log_id: int, time_utc: int = 0) -> str:
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(time_utc)) if time_utc else time.strftime("%Y%m%d_%H%M%S")
    return re.sub(r"[^A-Za-z0-9_.-]", "_", f"px4_log_{log_id}_{stamp}.ulg")


def request_log_data_chunk(
    master: Any,
    log_id: int,
    offset: int,
    count: int,
    timeout: float = 1.2,
    on_message: Callable[[Any], None] | None = None,
) -> bytes | None:
    bus = MessageBus.for_master(master)
    cursor = bus.current_sequence() if bus is not None else None
    master.mav.log_request_data_send(
        master.target_system,
        master.target_component,
        int(log_id),
        int(offset),
        int(count),
    )
    deadline = time.monotonic() + timeout

    def matches_chunk(message: Any) -> bool:
        return (
            int(message.id) == int(log_id)
            and int(message.ofs) == int(offset)
        )

    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        message, cursor = wait_for_message(
            master,
            message_types=("LOG_DATA",),
            predicate=matches_chunk,
            timeout=min(0.1, remaining),
            on_message=on_message,
            heartbeat=lambda: send_gcs_heartbeat(master),
            cursor=cursor,
            poll_interval=0.05,
        )
        if message is None:
            continue
        data = bytes(message.data[: int(message.count)])
        return data
    return None


def validate_downloaded_log(path: Path, expected_size: int = 0) -> dict[str, Any]:
    exists = path.exists()
    actual_size = path.stat().st_size if exists else 0
    expected_size = max(0, int(expected_size or 0))
    size_ok = exists and (expected_size <= 0 or actual_size >= expected_size)
    return {
        "exists": exists,
        "size": actual_size,
        "expectedSize": expected_size,
        "sizeOk": size_ok,
    }


def _request_log_end(master: Any) -> None:
    try:
        master.mav.log_request_end_send(master.target_system, master.target_component)
    except AttributeError:
        pass


def download_flight_log(
    master: Any,
    log_id: int,
    size: int = 0,
    time_utc: int = 0,
    output_dir: str | Path = "downloads/ulg",
    command_id: str | None = None,
    on_message: Callable[[Any], None] | None = None,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> None:
    log_id = int(log_id)
    size = max(0, int(size or 0))
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    filename = safe_ulg_filename(log_id, time_utc)
    output_path = output_root / filename
    partial_path = output_root / f"{filename}.part"
    existing_validation = validate_downloaded_log(output_path, size)
    if existing_validation["sizeOk"]:
        final_size = int(existing_validation["size"])
        result = {
            "id": log_id,
            "size": size or final_size,
            "downloaded": final_size,
            "progress": 100,
            "path": str(output_path),
            "url": f"/downloads/ulg/{output_path.name}",
            "filename": output_path.name,
            "bytesPerSecond": 0,
            "etaSeconds": 0,
            "resumed": True,
        }
        write_command_status(
            command_id,
            status="accepted",
            message=f"Flight log {log_id} already exists: {output_path.name}",
            results=[result],
        )
        return
    offset = partial_path.stat().st_size if partial_path.exists() else 0
    if size > 0 and offset > size:
        partial_path.unlink()
        offset = 0
    write_command_status(
        command_id,
        status="running",
        message=f"Downloading flight log {log_id}" + (f" from byte {offset}" if offset else ""),
        results=[{"id": log_id, "size": size, "downloaded": offset, "progress": round(offset * 100 / size, 1) if size else 0, "resumed": bool(offset)}],
    )
    chunk_size = max(1, int(chunk_size))
    started_at = time.monotonic()
    last_status_at = 0.0
    initial_offset = offset
    with partial_path.open("ab") as handle:
        while True:
            request_count = chunk_size if size <= 0 else min(chunk_size, size - offset)
            if request_count <= 0:
                break
            chunk = None
            for _attempt in range(4):
                chunk = request_log_data_chunk(
                    master,
                    log_id,
                    offset,
                    request_count,
                    on_message=on_message,
                )
                if chunk is not None:
                    break
            if chunk is None:
                progress = round(offset * 100 / size, 1) if size else 0
                write_command_status(
                    command_id,
                    status="interrupted",
                    message=f"Flight log {log_id} download timed out at byte {offset}; partial file retained for resume",
                    results=[{
                        "id": log_id,
                        "size": size,
                        "downloaded": offset,
                        "progress": progress,
                        "partialPath": str(partial_path),
                        "filename": output_path.name,
                        "resumable": True,
                    }],
                )
                _request_log_end(master)
                return
            handle.write(chunk)
            handle.flush()
            offset += len(chunk)
            now = time.monotonic()
            if now - last_status_at > 0.8:
                progress = round(offset * 100 / size, 1) if size else 0
                elapsed = max(0.1, now - started_at)
                speed = round((offset - initial_offset) / elapsed, 1)
                eta = round((size - offset) / speed, 1) if size and speed > 0 else None
                write_command_status(
                    command_id,
                    status="running",
                    message=f"Downloading flight log {log_id}: {progress}%",
                    results=[{
                        "id": log_id,
                        "size": size,
                        "downloaded": offset,
                        "progress": progress,
                        "bytesPerSecond": speed,
                        "etaSeconds": eta,
                        "resumed": bool(initial_offset),
                    }],
                )
                last_status_at = now
            if size <= 0 and len(chunk) < chunk_size:
                break
    _request_log_end(master)
    partial_path.replace(output_path)
    final_validation = validate_downloaded_log(output_path, size)
    if not final_validation["sizeOk"]:
        write_command_status(
            command_id,
            status="interrupted",
            message=f"Flight log {log_id} file validation failed after download",
            results=[{
                "id": log_id,
                "size": size,
                "downloaded": final_validation["size"],
                "progress": round(final_validation["size"] * 100 / size, 1) if size else 0,
                "path": str(output_path),
                "filename": output_path.name,
                "resumable": True,
            }],
        )
        return
    final_size = int(final_validation["size"])
    result = {
        "id": log_id,
        "size": size or final_size,
        "downloaded": final_size,
        "progress": 100,
        "path": str(output_path),
        "url": f"/downloads/ulg/{output_path.name}",
        "filename": output_path.name,
        "bytesPerSecond": round((final_size - initial_offset) / max(0.1, time.monotonic() - started_at), 1),
        "etaSeconds": 0,
        "resumed": bool(initial_offset),
    }
    write_command_status(
        command_id,
        status="accepted",
        message=f"Flight log {log_id} downloaded: {output_path.name}",
        results=[result],
    )
    print(f"Flight log {log_id} downloaded: {output_path}")

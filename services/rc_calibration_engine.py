from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from statistics import mean, pstdev
from typing import Any


RC_MAP_NAMES = {
    "roll": "RC_MAP_ROLL",
    "pitch": "RC_MAP_PITCH",
    "throttle": "RC_MAP_THROTTLE",
    "yaw": "RC_MAP_YAW",
    "flightMode": "RC_MAP_FLTMODE",
    "armSwitch": "RC_MAP_ARM_SW",
}

RC_CAL_PARAMS = [
    *(f"RC{index}_{suffix}" for index in range(1, 19) for suffix in ("MIN", "MAX", "TRIM", "REV", "DZ")),
]
RC_PARAMETER_NAMES = [*RC_MAP_NAMES.values(), *RC_CAL_PARAMS]


def finite_pwm(value: Any) -> bool:
    try:
        numeric = int(round(float(value)))
    except (TypeError, ValueError):
        return False
    return 800 <= numeric <= 2200


def normalize_channels(values: list[Any] | None) -> list[int | None]:
    values = values or []
    output: list[int | None] = []
    for index in range(18):
        value = values[index] if index < len(values) else None
        output.append(int(round(float(value))) if finite_pwm(value) else None)
    return output


def pwm_percent(value: Any, minimum: Any = 1000, maximum: Any = 2000) -> float | None:
    if not finite_pwm(value):
        return None
    try:
        low = float(minimum)
        high = float(maximum)
    except (TypeError, ValueError):
        low, high = 1000, 2000
    span = max(1.0, high - low)
    return round(max(0.0, min(100.0, (float(value) - low) / span * 100.0)), 1)


def summarize_samples(samples: list[list[Any]]) -> list[dict[str, Any]]:
    normalized = [normalize_channels(sample) for sample in samples]
    summaries = []
    for index in range(18):
        values = [sample[index] for sample in normalized if sample[index] is not None]
        if not values:
            summaries.append({
                "channel": index + 1,
                "used": False,
                "min": None,
                "max": None,
                "trim": None,
                "range": 0,
                "stddev": 0,
                "movementScore": 0,
                "stabilityScore": 0,
            })
            continue
        low = min(values)
        high = max(values)
        span = high - low
        std = pstdev(values) if len(values) > 1 else 0
        summaries.append({
            "channel": index + 1,
            "used": span >= 30 or len(values) >= 2,
            "min": low,
            "max": high,
            "trim": int(round(mean(values))),
            "range": span,
            "stddev": round(std, 2),
            "movementScore": round(span + std * 2, 2),
            "stabilityScore": round(max(0, 100 - std), 2),
        })
    return summaries


def channel_snapshot(channels: list[Any], parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    parameters = parameters or {}
    raw = normalize_channels(channels)
    mapped = {}
    for key, name in RC_MAP_NAMES.items():
        try:
            channel = int(round(float(parameters.get(name, 0) or 0)))
        except (TypeError, ValueError):
            channel = 0
        pwm = raw[channel - 1] if 1 <= channel <= 18 else None
        mapped[key] = {
            "param": name,
            "channel": channel,
            "pwm": pwm,
            "percent": pwm_percent(
                pwm,
                parameters.get(f"RC{channel}_MIN", 1000) if channel else 1000,
                parameters.get(f"RC{channel}_MAX", 2000) if channel else 2000,
            ),
            "available": pwm is not None,
        }
    return {
        "raw": [
            {
                "channel": index + 1,
                "pwm": value,
                "percent": pwm_percent(
                    value,
                    parameters.get(f"RC{index + 1}_MIN", 1000),
                    parameters.get(f"RC{index + 1}_MAX", 2000),
                ),
                "used": value is not None,
                "min": parameters.get(f"RC{index + 1}_MIN"),
                "max": parameters.get(f"RC{index + 1}_MAX"),
                "trim": parameters.get(f"RC{index + 1}_TRIM"),
                "rev": parameters.get(f"RC{index + 1}_REV"),
                "dz": parameters.get(f"RC{index + 1}_DZ"),
            }
            for index, value in enumerate(raw)
        ],
        "mapped": mapped,
        "rcMapAvailable": all(parameters.get(name) is not None for name in RC_MAP_NAMES.values()),
    }


def detect_moving_channel(samples: list[list[Any]], used_channels: set[int] | None = None) -> dict[str, Any]:
    used_channels = used_channels or set()
    summaries = summarize_samples(samples)
    candidates = [
        item for item in summaries
        if item["channel"] not in used_channels and item["range"] >= 120 and item["movementScore"] > 130
    ]
    candidates.sort(key=lambda item: item["movementScore"], reverse=True)
    if not candidates:
        return {"ok": False, "reason": "未检测到足够明显的通道变化", "candidates": summaries}
    top = candidates[0]
    ambiguous = len(candidates) > 1 and candidates[1]["movementScore"] > top["movementScore"] * 0.82
    return {
        "ok": not ambiguous,
        "reason": "检测结果不唯一，请手动选择或重新执行该步骤" if ambiguous else "检测到主要变化通道",
        "channel": top["channel"],
        "summary": top,
        "candidates": candidates[:4],
    }


@dataclass
class RcCalibrationSession:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    step: str = "safety"
    samples: dict[str, list[list[Any]]] = field(default_factory=dict)
    detected: dict[str, Any] = field(default_factory=dict)
    backup: dict[str, Any] = field(default_factory=dict)
    preview: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "createdAt": int(self.created_at * 1000),
            "updatedAt": int(self.updated_at * 1000),
            "step": self.step,
            "detected": self.detected,
            "backup": self.backup,
            "preview": self.preview,
            "warnings": self.warnings,
        }


def build_preview(session: RcCalibrationSession, current_parameters: dict[str, Any]) -> list[dict[str, Any]]:
    proposed: dict[str, Any] = {}
    for control, result in session.detected.items():
        channel = int(result.get("channel") or 0)
        if not channel:
            continue
        if control in RC_MAP_NAMES:
            proposed[RC_MAP_NAMES[control]] = channel
        summary = result.get("summary") or {}
        low = summary.get("min")
        high = summary.get("max")
        trim = summary.get("trim")
        if finite_pwm(low):
            proposed[f"RC{channel}_MIN"] = int(low)
        if finite_pwm(high):
            proposed[f"RC{channel}_MAX"] = int(high)
        if control == "throttle" and finite_pwm(low):
            proposed[f"RC{channel}_TRIM"] = int(low)
        elif finite_pwm(trim):
            proposed[f"RC{channel}_TRIM"] = int(trim)
        if result.get("reversed") is not None:
            proposed[f"RC{channel}_REV"] = -1 if result["reversed"] else 1
    preview = []
    for name in sorted(proposed):
        old = current_parameters.get(name)
        new = proposed[name]
        preview.append({
            "name": name,
            "current": old,
            "value": new,
            "changed": old is None or float(old) != float(new),
            "risk": "warning" if old is None else "normal",
        })
    session.preview = preview
    session.updated_at = time.time()
    return preview

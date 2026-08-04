from __future__ import annotations

import math
from typing import Any

import numpy as np

from services.ulg_analyzer import (
    THRESHOLDS,
    dataset_field,
    error_metrics,
    finite_array,
    get_topic,
    load_flight_log,
    safe_stats,
    zero_crossings,
)


PID_AXES = ("roll", "pitch", "yaw")


def _num(value, digits=3):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits)


def _field_stats(dataset, field):
    if not dataset or field not in dataset.fields:
        return None
    return safe_stats(dataset.fields[field])


def _rms_error(actual_topic, actual_key, setpoint_topic, setpoint_key):
    metrics = error_metrics(actual_topic, actual_key, setpoint_topic, setpoint_key)
    if not metrics:
        return None
    return {
        "rms": _num(metrics.get("rms")),
        "peak": _num(metrics.get("max")),
        "mean_abs": _num(metrics.get("mean")),
    }


def _oscillation(actual_topic, axis):
    if not actual_topic or axis not in actual_topic.fields:
        return {"detected": None, "frequency_hz": None, "zero_crossings": None}
    values = np.asarray(actual_topic.fields[axis], dtype=float)
    time_s = np.asarray(actual_topic.time_s, dtype=float)
    count = min(len(values), len(time_s))
    if count < 5:
        return {"detected": None, "frequency_hz": None, "zero_crossings": None}
    values = values[:count]
    time_s = time_s[:count]
    valid = np.isfinite(values) & np.isfinite(time_s)
    values = values[valid]
    time_s = time_s[valid]
    if len(values) < 5:
        return {"detected": None, "frequency_hz": None, "zero_crossings": None}
    crossings = zero_crossings(values)
    duration = max(1e-6, float(time_s[-1] - time_s[0]))
    frequency = crossings / 2 / duration
    detected = crossings >= THRESHOLDS["oscillation_zero_crossings"] and frequency > 0.4
    return {
        "detected": bool(detected),
        "frequency_hz": _num(frequency),
        "zero_crossings": int(crossings),
    }


def _saturation_percent(dataset, low=0.05, high=0.95):
    if not dataset:
        return None
    values = []
    for key, series in dataset.fields.items():
        if not (key.startswith("output") or key.startswith("control") or key.startswith("servo") or key.startswith("motor")):
            continue
        arr = finite_array(series)
        if len(arr):
            values.append(arr)
    if not values:
        return None
    merged = np.concatenate(values)
    if len(merged) == 0:
        return None
    high_threshold = high
    low_threshold = low
    if np.nanmax(np.abs(merged)) > 2:
        high_threshold = 1900
        low_threshold = 1100
    saturation = np.mean((merged >= high_threshold) | (merged <= low_threshold)) * 100
    return _num(saturation, 2)


def _battery_drop_rate(battery):
    if not battery or "voltage" not in battery.fields or len(battery.time_s) < 2:
        return None
    voltage = np.asarray(battery.fields["voltage"], dtype=float)
    time_s = np.asarray(battery.time_s, dtype=float)
    count = min(len(voltage), len(time_s))
    if count < 2:
        return None
    valid = np.isfinite(voltage[:count]) & np.isfinite(time_s[:count])
    voltage = voltage[:count][valid]
    time_s = time_s[:count][valid]
    if len(voltage) < 2:
        return None
    duration = max(1e-6, float(time_s[-1] - time_s[0]))
    return _num((float(voltage[0]) - float(voltage[-1])) / duration, 4)


def extract_features_from_log(path) -> dict[str, Any]:
    log = load_flight_log(path)
    return extract_features_from_loaded_log(log)


def extract_features_from_loaded_log(log) -> dict[str, Any]:
    attitude = get_topic(log, "mapped_attitude")
    attitude_sp = get_topic(log, "mapped_attitude_setpoint")
    gps = get_topic(log, "mapped_gps")
    battery = get_topic(log, "mapped_battery")
    actuator = get_topic(log, "mapped_actuator")
    sensor = get_topic(log, "mapped_sensor")

    missing = []
    if not attitude:
        missing.append("attitude_actual")
    if not attitude_sp:
        missing.append("attitude_setpoint")
    if not gps:
        missing.append("gps")
    if not battery:
        missing.append("battery")
    if not actuator:
        missing.append("actuator_outputs")
    if not sensor:
        missing.append("imu_vibration")

    axis_features = {}
    for axis in PID_AXES:
        error = _rms_error(attitude, axis, attitude_sp, f"{axis}_sp")
        stats = _field_stats(attitude, axis)
        oscillation = _oscillation(attitude, axis)
        axis_features[axis] = {
            "actual_stats": stats,
            "setpoint_available": bool(attitude_sp and f"{axis}_sp" in attitude_sp.fields),
            "error_rms": error["rms"] if error else None,
            "error_peak": error["peak"] if error else None,
            "error_mean_abs": error["mean_abs"] if error else None,
            "oscillation_detected": oscillation["detected"],
            "oscillation_frequency_hz": oscillation["frequency_hz"],
            "zero_crossings": oscillation["zero_crossings"],
        }

    voltage_stats = _field_stats(battery, "voltage")
    current_stats = _field_stats(battery, "current")
    gps_sat_stats = _field_stats(gps, "satellites")
    gps_hdop_stats = _field_stats(gps, "hdop") or _field_stats(gps, "eph")
    gyro_stats = _field_stats(sensor, "gyro_norm")
    accel_stats = _field_stats(sensor, "accel_norm")

    return {
        "source": log.path.name,
        "duration_s": _num(log.duration_s, 2),
        "flight_phase": "unknown",
        "axes": axis_features,
        "roll_error_rms": axis_features["roll"]["error_rms"],
        "pitch_error_rms": axis_features["pitch"]["error_rms"],
        "yaw_error_rms": axis_features["yaw"]["error_rms"],
        "roll_error_peak": axis_features["roll"]["error_peak"],
        "pitch_error_peak": axis_features["pitch"]["error_peak"],
        "yaw_error_peak": axis_features["yaw"]["error_peak"],
        "overshoot_percent": None,
        "settling_time_estimate": None,
        "oscillation_detected": any(item["oscillation_detected"] for item in axis_features.values() if item["oscillation_detected"] is not None),
        "oscillation_frequency_hz": max([item["oscillation_frequency_hz"] for item in axis_features.values() if item["oscillation_frequency_hz"] is not None] or [None]),
        "servo_output_saturation_percent": _saturation_percent(actuator),
        "motor_output_saturation_percent": _saturation_percent(actuator),
        "battery_voltage_min": _num(voltage_stats["min"]) if voltage_stats else None,
        "battery_voltage_drop_rate": _battery_drop_rate(battery),
        "battery_current_max": _num(current_stats["max"]) if current_stats else None,
        "gps_satellite_min": _num(gps_sat_stats["min"], 0) if gps_sat_stats else None,
        "gps_hdop_max": _num(gps_hdop_stats["max"]) if gps_hdop_stats else None,
        "ekf_warning_count": sum(1 for item in log.messages if "ekf" in str(item.get("text", "")).lower()),
        "vibration_level": _num(max(
            gyro_stats["std"] if gyro_stats else 0,
            accel_stats["std"] if accel_stats else 0,
        )),
        "messages": log.messages[:80],
        "parameters": dict(log.parameters or {}),
        "missing": missing,
    }


def extract_features_from_telemetry(history, state=None) -> dict[str, Any]:
    rows = list(history or [])[-600:]
    state = state or {}

    def values(name):
        result = []
        for row in rows:
            value = row.get(name) if isinstance(row, dict) else None
            if value is None:
                value = row.get(name.replace("_deg", "")) if isinstance(row, dict) else None
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number):
                result.append(number)
        return np.asarray(result, dtype=float)

    missing = []
    axes = {}
    for axis in PID_AXES:
        arr = values(axis) if axis != "yaw" else values("yaw")
        if len(arr) == 0:
            missing.append(f"{axis}_actual")
            axes[axis] = {"actual_stats": None, "setpoint_available": False, "error_rms": None, "error_peak": None, "oscillation_detected": None}
            continue
        stats = safe_stats(arr)
        oscillation = _oscillation(type("TelemetryTopic", (), {"fields": {axis: arr}, "time_s": np.arange(len(arr), dtype=float) / 20})(), axis)
        axes[axis] = {
            "actual_stats": stats,
            "setpoint_available": False,
            "error_rms": None,
            "error_peak": None,
            "oscillation_detected": oscillation["detected"],
            "oscillation_frequency_hz": oscillation["frequency_hz"],
            "zero_crossings": oscillation["zero_crossings"],
        }

    battery = values("battery")
    voltage = values("voltage")
    satellites = values("satellites")
    if len(voltage) == 0 and len(battery) == 0:
        missing.append("battery")
    if len(satellites) == 0:
        missing.append("gps")

    return {
        "source": "live_telemetry",
        "duration_s": _num(len(rows) / 20 if rows else 0, 2),
        "flight_phase": "unknown",
        "axes": axes,
        "roll_error_rms": None,
        "pitch_error_rms": None,
        "yaw_error_rms": None,
        "roll_error_peak": None,
        "pitch_error_peak": None,
        "yaw_error_peak": None,
        "overshoot_percent": None,
        "settling_time_estimate": None,
        "oscillation_detected": any(item.get("oscillation_detected") for item in axes.values() if item.get("oscillation_detected") is not None),
        "oscillation_frequency_hz": max([item.get("oscillation_frequency_hz") for item in axes.values() if item.get("oscillation_frequency_hz") is not None] or [None]),
        "servo_output_saturation_percent": None,
        "motor_output_saturation_percent": None,
        "battery_voltage_min": _num(float(np.min(voltage))) if len(voltage) else None,
        "battery_voltage_drop_rate": None,
        "battery_current_max": None,
        "gps_satellite_min": _num(float(np.min(satellites)), 0) if len(satellites) else None,
        "gps_hdop_max": None,
        "ekf_warning_count": sum(1 for item in state.get("warnings", []) if "ekf" in str(item).lower()),
        "vibration_level": None,
        "messages": [{"text": item, "level": "WARNING"} for item in state.get("warnings", [])],
        "parameters": dict(state.get("parameters", {}) or {}),
        "missing": missing + ["setpoint"],
    }

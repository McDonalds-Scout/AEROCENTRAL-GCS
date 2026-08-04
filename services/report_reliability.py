from __future__ import annotations

import math
import re
from typing import Any

import numpy as np


FIX_TYPE_LABELS = {
    0: "No GPS",
    1: "No Fix",
    2: "2D Fix",
    3: "3D Fix",
    4: "DGPS",
    5: "RTK Float",
    6: "RTK Fixed",
    7: "Static",
    8: "PPP",
}

INFO_PATTERNS = (
    "armed by", "disarmed", "takeoff detected", "landing detected",
    "start file log", "executing mission", "mission", "home position",
)

SEVERITY_RANK = {
    "EMERGENCY": 5,
    "ALERT": 5,
    "CRITICAL": 4,
    "ERROR": 3,
    "WARNING": 2,
    "NOTICE": 1,
    "INFO": 0,
    "DEBUG": 0,
}

OPERATION_PATTERNS = (
    "armed by rc",
    "armed by rc switch",
    "armed by",
    "disarmed by landing",
    "disarmed by user",
    "disarmed",
    "takeoff detected",
    "landing detected",
)

ABNORMAL_PATTERNS = (
    "preflight fail",
    "arming denied",
    "failsafe",
    "rc lost",
    "manual control lost",
    "data link lost",
    "datalink lost",
    "ekf",
    "estimator",
    "gps lost",
    "gps degraded",
    "battery warning",
    "battery critical",
    "compass",
    "magnetometer",
    "actuator failure",
    "motor failure",
)


def _finite_array(values) -> np.ndarray:
    if values is None:
        return np.array([], dtype=float)
    arr = np.asarray(values, dtype=float)
    return arr[np.isfinite(arr)]


def _stats(values) -> dict[str, Any] | None:
    arr = _finite_array(values)
    if len(arr) == 0:
        return None
    return {
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
        "mean": float(np.mean(arr)),
        "std": float(np.std(arr)),
        "span": float(np.max(arr) - np.min(arr)),
        "count": int(len(arr)),
    }


def _level_from_score(score: float) -> str:
    if score >= 0.78:
        return "High"
    if score >= 0.52:
        return "Medium"
    return "Low"


CONFIDENCE_SCORE = {"High": 1.0, "Medium-High": 0.82, "Medium": 0.65, "Low": 0.3}
CONFIDENCE_RANK = {"Low": 0, "Medium": 1, "Medium-High": 2, "High": 3}


def _min_level(*levels: str) -> str:
    clean = [level for level in levels if level in CONFIDENCE_RANK]
    if not clean:
        return "Low"
    return min(clean, key=lambda item: CONFIDENCE_RANK[item])


def _confidence_from_evidence(evidence: list[str], strong_evidence: list[str] | tuple[str, ...] = ()) -> str:
    if not evidence:
        return "Low"
    strong = sum(1 for item in evidence if item in strong_evidence)
    if len(evidence) >= 3 and strong >= 1:
        return "High"
    if len(evidence) >= 2:
        return "Medium-High"
    return "Medium"


def _fmt_time(value) -> str:
    if value is None or not isinstance(value, (int, float)) or not math.isfinite(value):
        return "N/A"
    return f"{value:.2f}s"


def _num(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def event_scope(time_s: float | None, flight_events: dict[str, Any] | None) -> tuple[str, bool]:
    value = _num(time_s)
    if value is None:
        return "unknown", False
    flight_events = flight_events or {}
    start = _num(flight_events.get("effective_flight_start_time"))
    end = _num(flight_events.get("effective_flight_end_time"))
    if start is None or end is None:
        return "unknown", False
    if value < start:
        return "preflight", False
    if value > end:
        return "postflight", False
    return "effective_flight", True


def interval_scope(start_s, end_s, flight_events: dict[str, Any] | None) -> tuple[str, bool]:
    start = _num(start_s)
    end = _num(end_s)
    if start is None and end is None:
        return "unknown", False
    mid = start if end is None else end if start is None else (start + end) / 2
    return event_scope(mid, flight_events)


def clip_interval_to_effective_range(event: dict[str, Any], flight_events: dict[str, Any] | None) -> dict[str, Any]:
    start = _num(event.get("start_s"))
    end = _num(event.get("end_s"))
    flight_events = flight_events or {}
    range_start = _num(flight_events.get("effective_flight_start_time"))
    range_end = _num(flight_events.get("effective_flight_end_time"))
    result = dict(event)
    scope, in_range = interval_scope(start, end, flight_events)
    result["flight_scope"] = scope
    result["in_effective_flight"] = in_range
    if start is None or end is None or range_start is None or range_end is None:
        return result
    overlap_start = max(start, range_start)
    overlap_end = min(end, range_end)
    if overlap_end >= overlap_start:
        result["original_start_s"] = start
        result["original_end_s"] = end
        result["start_s"] = overlap_start
        result["end_s"] = overlap_end
        result["duration_s"] = max(0.0, overlap_end - overlap_start)
        result["flight_scope"] = "effective_flight"
        result["in_effective_flight"] = True
        if overlap_start > start or overlap_end < end:
            result["boundary_clipped"] = True
            result["confidence"] = _min_level(str(result.get("confidence", "Medium")), "Medium")
    else:
        result["is_abnormal"] = False
        result["excluded_reason"] = "outside_effective_flight"
        result["confidence"] = "Low"
        result["risk"] = "低"
    return result


def _format_parameter_value(value: Any) -> str:
    if value is None:
        return "日志未包含"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(number):
        return "日志未包含"
    abs_value = abs(number)
    if abs_value != 0 and abs_value < 0.01:
        return f"{number:.6f}".rstrip("0").rstrip(".")
    return f"{number:.3f}"


def _sample_rate_hz(dataset) -> float | None:
    if not dataset or len(getattr(dataset, "time_s", [])) < 2:
        return None
    time_s = np.asarray(dataset.time_s, dtype=float)
    time_s = time_s[np.isfinite(time_s)]
    if len(time_s) < 2:
        return None
    duration = float(time_s[-1] - time_s[0])
    if duration <= 0:
        return None
    return round((len(time_s) - 1) / duration, 2)


def _topic_profile(dataset, expected_fields: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
    if not dataset:
        return {
            "available": False,
            "sample_count": 0,
            "sample_rate_hz": None,
            "time_coverage_s": 0.0,
            "fields": [],
            "missing_fields": list(expected_fields),
        }
    fields = sorted(str(key) for key in getattr(dataset, "fields", {}).keys())
    time_s = np.asarray(getattr(dataset, "time_s", []), dtype=float)
    time_s = time_s[np.isfinite(time_s)]
    coverage = float(time_s[-1] - time_s[0]) if len(time_s) > 1 else 0.0
    return {
        "available": True,
        "sample_count": int(len(time_s)),
        "sample_rate_hz": _sample_rate_hz(dataset),
        "time_coverage_s": round(max(0.0, coverage), 3),
        "fields": fields,
        "missing_fields": [field for field in expected_fields if field not in fields],
    }


def topic_availability_summary(analysis) -> dict[str, Any]:
    topics = analysis.get("topics", {})
    expected = {
        "attitude": ("roll", "pitch", "yaw"),
        "attitude_sp": ("roll_sp", "pitch_sp", "yaw_sp"),
        "rates": ("roll_rate", "pitch_rate", "yaw_rate"),
        "rates_sp": ("roll_rate_sp", "pitch_rate_sp", "yaw_rate_sp"),
        "position": ("altitude", "horizontal_speed"),
        "gps": ("lat", "lon", "satellites", "fix_type"),
        "battery": ("voltage", "current"),
        "actuator": (),
        "sensor": ("gyro_norm", "accel_norm"),
    }
    profiles = {name: _topic_profile(topics.get(name), fields) for name, fields in expected.items()}
    available_count = sum(1 for item in profiles.values() if item["available"])
    return {
        "expected_topic_count": len(expected),
        "available_topic_count": available_count,
        "missing_topics": [name for name, item in profiles.items() if not item["available"]],
        "profiles": profiles,
    }


def circular_error_deg(actual_deg, setpoint_deg):
    return circular_delta_deg(setpoint_deg, actual_deg)


def circular_delta_deg(target_deg, actual_deg):
    return ((np.asarray(target_deg, dtype=float) - np.asarray(actual_deg, dtype=float) + 540.0) % 360.0) - 180.0


def circular_range_deg(values) -> float | None:
    arr = _finite_array(values)
    if len(arr) == 0:
        return None
    radians = np.radians(arr)
    unwrapped = np.degrees(np.unwrap(radians))
    return float(np.nanmax(unwrapped) - np.nanmin(unwrapped))


def yaw_wrap_warning(values) -> dict[str, Any]:
    arr = _finite_array(values)
    if len(arr) < 2:
        return {"wrap_detected": False, "warning": ""}
    raw_span = float(np.nanmax(arr) - np.nanmin(arr))
    circular_span = circular_range_deg(arr)
    wrap_detected = bool(raw_span > 300 and circular_span is not None and circular_span < 120)
    warning = (
        "存在航向角跨零/wrap 风险，不能使用 max-min 原始范围作为 360 度振荡证据。"
        if wrap_detected else ""
    )
    return {"wrap_detected": wrap_detected, "raw_span_deg": raw_span, "circular_span_deg": circular_span, "warning": warning}


def aligned_error_stats(actual_dataset, actual_field, setpoint_dataset, setpoint_field, circular=False, max_dt=0.25):
    if not actual_dataset or not setpoint_dataset:
        return None
    if actual_field not in actual_dataset.fields or setpoint_field not in setpoint_dataset.fields:
        return None
    actual_count = min(len(actual_dataset.time_s), len(actual_dataset.fields[actual_field]))
    setpoint_count = min(len(setpoint_dataset.time_s), len(setpoint_dataset.fields[setpoint_field]))
    if actual_count < 2 or setpoint_count < 2:
        return None
    actual_t = np.asarray(actual_dataset.time_s[:actual_count], dtype=float)
    actual_v = np.asarray(actual_dataset.fields[actual_field][:actual_count], dtype=float)
    sp_t = np.asarray(setpoint_dataset.time_s[:setpoint_count], dtype=float)
    sp_v = np.asarray(setpoint_dataset.fields[setpoint_field][:setpoint_count], dtype=float)
    actual_mask = np.isfinite(actual_t) & np.isfinite(actual_v)
    sp_mask = np.isfinite(sp_t) & np.isfinite(sp_v)
    actual_t, actual_v = actual_t[actual_mask], actual_v[actual_mask]
    sp_t, sp_v = sp_t[sp_mask], sp_v[sp_mask]
    if len(actual_t) < 2 or len(sp_t) < 2:
        return None
    within = (actual_t >= sp_t[0]) & (actual_t <= sp_t[-1])
    actual_t, actual_v = actual_t[within], actual_v[within]
    if len(actual_t) < 2:
        return None
    indices = np.searchsorted(sp_t, actual_t)
    left = np.clip(indices - 1, 0, len(sp_t) - 1)
    right = np.clip(indices, 0, len(sp_t) - 1)
    nearest_dt = np.minimum(np.abs(actual_t - sp_t[left]), np.abs(actual_t - sp_t[right]))
    valid = nearest_dt <= max_dt
    if np.count_nonzero(valid) < 2:
        return {
            "available": False,
            "reason": f"setpoint 与 actual 时间差超过 {max_dt:.2f}s，不能计算 tracking error",
            "valid_count": int(np.count_nonzero(valid)),
        }
    actual_t, actual_v = actual_t[valid], actual_v[valid]
    sp_interp = np.interp(actual_t, sp_t, sp_v)
    error = circular_error_deg(actual_v, sp_interp) if circular else actual_v - sp_interp
    error = error[np.isfinite(error)]
    if len(error) == 0:
        return None
    return {
        "available": True,
        "max": float(np.max(np.abs(error))),
        "mean": float(np.mean(np.abs(error))),
        "bias": float(np.mean(error)),
        "rms": float(np.sqrt(np.mean(error ** 2))),
        "valid_count": int(len(error)),
        "max_time_delta_s": float(np.max(nearest_dt[valid])) if len(nearest_dt[valid]) else None,
        "method": "circular_error_deg" if circular else "time_aligned_linear_interp",
    }


def detect_flight_events(log) -> dict[str, Any]:
    result = {
        "log_start_time": 0.0,
        "log_end_time": float(log.duration_s or 0.0),
        "log_start_s": 0.0,
        "log_end_s": float(log.duration_s or 0.0),
        "armed_time": None,
        "armed_time_s": None,
        "takeoff_detected_time": None,
        "takeoff_time_s": None,
        "mission_start_time": None,
        "landing_detected_time": None,
        "landing_time_s": None,
        "disarmed_time": None,
        "disarm_time_s": None,
        "effective_flight_start_time": None,
        "effective_flight_end_time": None,
        "effective_flight_start_s": None,
        "effective_flight_end_s": None,
        "effective_airborne_time": None,
        "effective_airborne_duration_s": None,
        "detection_method": [],
        "confidence": "Low",
        "fallback_used": False,
        "fallbacks": [],
        "timeline": [],
    }
    for message in log.messages or []:
        text = str(message.get("text", ""))
        lower = text.lower()
        time_s = float(message.get("time_s", 0.0) or 0.0)
        event_name = None
        if "armed by" in lower and result["armed_time"] is None:
            result["armed_time"] = time_s
            result["armed_time_s"] = time_s
            event_name = "Armed"
        elif ("takeoff detected" in lower or "takeoff" in lower) and result["takeoff_detected_time"] is None:
            result["takeoff_detected_time"] = time_s
            result["takeoff_time_s"] = time_s
            event_name = "Takeoff detected"
        elif "executing mission" in lower and result["mission_start_time"] is None:
            result["mission_start_time"] = time_s
            event_name = "Mission start"
        elif "landing detected" in lower and result["landing_detected_time"] is None:
            result["landing_detected_time"] = time_s
            result["landing_time_s"] = time_s
            event_name = "Landing detected"
        elif "disarmed" in lower and result["disarmed_time"] is None:
            result["disarmed_time"] = time_s
            result["disarm_time_s"] = time_s
            event_name = "Disarmed"
        if event_name:
            result["timeline"].append({"time_s": time_s, "event": event_name, "source": text})
            result["detection_method"].append(f"log_message:{event_name}")

    start = result["takeoff_detected_time"]
    if start is None:
        start = result["armed_time"]
        result["fallbacks"].append("缺少 Takeoff detected，使用 Armed 作为有效飞行段起点。")
    if start is None:
        start = 0.0
        result["fallbacks"].append("缺少 Armed/Takeoff detected，使用日志起点作为有效飞行段起点，置信度较低。")
    end = result["disarmed_time"]
    if end is None:
        end = result["landing_detected_time"]
        result["fallbacks"].append("缺少 Disarmed，使用 Landing detected 作为有效飞行段终点。")
    if end is None:
        end = float(log.duration_s or 0.0)
        result["fallbacks"].append("缺少 Landing/Disarmed，使用日志终点作为有效飞行段终点，置信度较低。")
    result["effective_flight_start_time"] = start
    result["effective_flight_end_time"] = end
    result["effective_flight_start_s"] = start
    result["effective_flight_end_s"] = end
    result["effective_airborne_time"] = max(0.0, end - start) if end is not None and start is not None else None
    result["effective_airborne_duration_s"] = result["effective_airborne_time"]
    result["fallback_used"] = bool(result["fallbacks"])
    direct_markers = sum(1 for key in ("armed_time", "takeoff_detected_time", "landing_detected_time", "disarmed_time") if result.get(key) is not None)
    if direct_markers >= 3 and not result["fallbacks"]:
        result["confidence"] = "High"
    elif direct_markers >= 2:
        result["confidence"] = "Medium-High" if not result["fallbacks"] else "Medium"
    elif direct_markers >= 1:
        result["confidence"] = "Medium"
    else:
        result["confidence"] = "Low"
    if not result["detection_method"]:
        result["detection_method"].append("fallback_log_bounds")
    return result


def message_category(text: str, severity: str, is_operation: bool) -> str:
    lower = text.lower()
    if is_operation:
        return "operation_info"
    if "preflight fail" in lower:
        return "preflight_fail"
    if "arming denied" in lower:
        return "arming_denied"
    if "failsafe" in lower:
        return "failsafe"
    if "rc lost" in lower or "manual control lost" in lower:
        return "rc_fault"
    if "data link lost" in lower or "datalink lost" in lower or "no connection to the ground control" in lower:
        return "datalink_fault"
    if "gps" in lower:
        return "gps_fault"
    if "ekf" in lower or "estimator" in lower:
        return "ekf_fault"
    if "compass" in lower or "magnetometer" in lower or "mag " in lower:
        return "compass_fault"
    if "battery" in lower or "low voltage" in lower:
        return "battery_warning"
    if "actuator" in lower or "motor" in lower or "servo" in lower:
        return "actuator_saturation" if "saturat" in lower else "sensor_fault"
    if severity == "WARNING":
        return "system_warning"
    if severity in {"ERROR", "CRITICAL", "ALERT", "EMERGENCY"}:
        return "unknown"
    return "flight_event"


def classify_log_messages(log, phases=None, flight_events=None) -> dict[str, Any]:
    rows = []
    counts = {"total_messages": 0, "info_events": 0, "warning_count": 0, "error_count": 0, "failsafe_count": 0}
    for message in log.messages or []:
        text = str(message.get("text", ""))
        level = str(message.get("level", "INFO") or "INFO").upper()
        lower = text.lower()
        normalized = re.sub(r"\s+", " ", lower).strip()
        time_s = float(message.get("time_s", 0.0) or 0.0)
        rank = SEVERITY_RANK.get(level, 0)
        is_operation = any(pattern in lower for pattern in OPERATION_PATTERNS)
        is_failsafe = "failsafe" in lower or "preflight fail" in lower
        is_abnormal_keyword = any(pattern in lower for pattern in ABNORMAL_PATTERNS)
        is_info_event = is_operation or rank <= 1 or any(pattern in lower for pattern in INFO_PATTERNS)
        if is_failsafe:
            counts["failsafe_count"] += 1
        if is_operation:
            counts["info_events"] += 1
            impact = "操作/飞行事件，只进入时间线，不计入异常"
            severity = "INFO"
            category = "operation_info"
            is_abnormal = False
        elif is_info_event and not is_failsafe and rank < 2:
            counts["info_events"] += 1
            impact = "信息事件，不默认计入异常"
            severity = "INFO"
            category = "flight_info"
            is_abnormal = False
        elif rank >= 3 or is_failsafe or is_abnormal_keyword:
            counts["error_count"] += 1
            impact = "可能影响飞行，需要复核"
            severity = "ERROR" if rank < 4 else level
            category = message_category(text, severity, is_operation)
            is_abnormal = True
        elif rank == 2:
            counts["warning_count"] += 1
            impact = "告警，需要结合阶段和数据复核"
            severity = "WARNING"
            category = message_category(text, severity, is_operation)
            is_abnormal = True
        else:
            counts["info_events"] += 1
            impact = "普通日志事件"
            severity = level
            category = message_category(text, severity, is_operation)
            is_abnormal = False
        counts["total_messages"] += 1
        scope, in_effective = event_scope(time_s, flight_events)
        if is_abnormal and not in_effective and category not in {"preflight_fail", "arming_denied", "failsafe"}:
            impact = "发生在有效飞行段之外，不能默认写成飞行中故障"
        rows.append({
            "time_s": time_s,
            "time": _fmt_time(time_s),
            "level": level,
            "severity": severity,
            "raw_message": text,
            "normalized_message": normalized,
            "raw": text,
            "meaning": translate_message(text),
            "impact": impact,
            "flight_phase": phase_at_time(phases or [], time_s),
            "phase": phase_at_time(phases or [], time_s),
            "flight_scope": scope,
            "in_effective_flight": in_effective,
            "advice": message_advice(text, severity),
            "category": category,
            "is_abnormal": is_abnormal,
            "is_anomaly": is_abnormal,
            "confidence": "High" if category in {"preflight_fail", "arming_denied", "failsafe"} else "Medium" if is_abnormal else "High",
            "recommended_action": message_advice(text, severity),
        })
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key_text = re.sub(r"\s+", " ", str(row.get("raw", "")).strip().lower())
        key = (str(row.get("severity", "INFO")), key_text)
        item = grouped.setdefault(key, {
            "severity": row.get("severity"),
            "category": row.get("category"),
            "raw": row.get("raw"),
            "meaning": row.get("meaning"),
            "first_time_s": row.get("time_s"),
            "last_time_s": row.get("time_s"),
            "count": 0,
            "is_anomaly": bool(row.get("is_anomaly")),
        })
        item["count"] += 1
        item["last_time_s"] = row.get("time_s")
        item["is_anomaly"] = bool(item["is_anomaly"] or row.get("is_anomaly"))
    groups = sorted(grouped.values(), key=lambda item: (not item["is_anomaly"], item.get("first_time_s") or 0))
    return {"counts": counts, "rows": rows, "groups": groups}


def translate_message(text: str) -> str:
    lower = text.lower()
    if "armed by" in lower:
        return "通过遥控器开关解锁"
    if "takeoff detected" in lower:
        return "检测到起飞事件"
    if "landing detected" in lower:
        return "检测到着陆事件"
    if "start file log" in lower:
        return "开始记录飞行日志"
    if "executing mission" in lower:
        return "开始执行任务"
    if "preflight fail" in lower and "yaw" in lower:
        return "飞前检查失败：航向估计误差"
    if "failsafe" in lower:
        return "Failsafe 保护触发"
    if "ekf" in lower:
        return "EKF/估计器相关消息"
    return "飞控日志消息"


def message_advice(text: str, severity: str) -> str:
    lower = text.lower()
    if "preflight fail" in lower and "yaw" in lower:
        return "重点检查磁罗盘、GPS 航向/双天线、EKF yaw innovation 和起飞前姿态初始化。"
    if "failsafe" in lower:
        return "复盘触发条件，检查遥控、数传、电池、电源和导航状态。"
    if severity == "INFO":
        return "作为时间线事件记录，不作为异常结论。"
    return "结合发生阶段、传感器状态和执行器余量人工复核。"


def phase_at_time(phases: list[dict[str, Any]], time_s: float | None) -> str:
    if time_s is None:
        return "N/A"
    for phase in phases:
        if phase.get("start_s") is None or phase.get("end_s") is None:
            continue
        if phase["start_s"] <= time_s <= phase["end_s"]:
            return phase["name"]
    return "未定位阶段"


def _series(topic, *fields):
    if not topic:
        return None, None
    for field in fields:
        if field in topic.fields:
            count = min(len(topic.time_s), len(topic.fields[field]))
            if count <= 0:
                continue
            time_s = np.asarray(topic.time_s[:count], dtype=float)
            values = np.asarray(topic.fields[field][:count], dtype=float)
            mask = np.isfinite(time_s) & np.isfinite(values)
            if np.count_nonzero(mask) >= 2:
                return time_s[mask], values[mask]
    return None, None


def _first_time(time_s, values, predicate, start=None, end=None):
    if time_s is None or values is None:
        return None
    mask = np.ones(len(time_s), dtype=bool)
    if start is not None:
        mask &= time_s >= start
    if end is not None:
        mask &= time_s <= end
    index = np.where(mask & predicate(values))[0]
    return float(time_s[index[0]]) if len(index) else None


def _last_time(time_s, values, predicate, start=None, end=None):
    if time_s is None or values is None:
        return None
    mask = np.ones(len(time_s), dtype=bool)
    if start is not None:
        mask &= time_s >= start
    if end is not None:
        mask &= time_s <= end
    index = np.where(mask & predicate(values))[0]
    return float(time_s[index[-1]]) if len(index) else None


def _transition_intervals(log, start, end) -> list[dict[str, Any]]:
    status = getattr(log, "topics", {}).get("vehicle_status")
    if not status:
        return []
    time_s, transition = _series(status, "in_transition_mode", "transition_mode")
    if time_s is None:
        return []
    to_fw = None
    if "in_transition_to_fw" in status.fields:
        count = min(len(status.time_s), len(status.fields["in_transition_to_fw"]))
        to_fw = np.asarray(status.fields["in_transition_to_fw"][:count], dtype=float)
    mask = (transition > 0.5) & (time_s >= start) & (time_s <= end)
    intervals = []
    active_start = None
    active_to_fw = []
    for index, active in enumerate(mask):
        if active and active_start is None:
            active_start = float(time_s[index])
            active_to_fw = []
        if active and to_fw is not None and index < len(to_fw):
            active_to_fw.append(float(to_fw[index]))
        if (not active or index == len(mask) - 1) and active_start is not None:
            stop_index = index if active else max(0, index - 1)
            active_end = float(time_s[stop_index])
            if active_end - active_start >= 0.2:
                direction = "front" if (active_to_fw and np.nanmean(active_to_fw) > 0.5) else "back"
                intervals.append({"start_s": active_start, "end_s": active_end, "direction": direction})
            active_start = None
    if len(intervals) >= 2 and all(item["direction"] == "back" for item in intervals):
        intervals[0]["direction"] = "front"
    return intervals


def _segment(name, start_s, end_s, basis, confidence):
    if start_s is None or end_s is None:
        return None
    start_s = float(start_s)
    end_s = float(end_s)
    if not math.isfinite(start_s) or not math.isfinite(end_s) or end_s <= start_s:
        return None
    return {
        "name": name,
        "start_s": start_s,
        "end_s": end_s,
        "duration_s": max(0.0, end_s - start_s),
        "basis": basis,
        "evidence": basis,
        "method": "event_altitude_transition_detector",
        "confidence": confidence,
    }


def _altitude_points(position, start, end):
    time_s, altitude = _series(position, "altitude")
    if time_s is None:
        return {}
    max_alt = float(np.nanmax(altitude)) if len(altitude) else 0.0
    if max_alt <= 0.8:
        return {"time": time_s, "altitude": altitude, "max_altitude": max_alt}
    low = max(1.0, max_alt * 0.12)
    high = max(low + 0.5, max_alt * 0.72)
    return {
        "time": time_s,
        "altitude": altitude,
        "max_altitude": max_alt,
        "airborne_start": _first_time(time_s, altitude, lambda arr: arr >= low, start=start, end=end),
        "airborne_end": _last_time(time_s, altitude, lambda arr: arr >= low, start=start, end=end),
        "high_alt_start": _first_time(time_s, altitude, lambda arr: arr >= high, start=start, end=end),
        "high_alt_end": _last_time(time_s, altitude, lambda arr: arr >= high, start=start, end=end),
    }


def build_phase_segments(analysis, flight_events) -> dict[str, Any]:
    log = analysis["log"]
    topics = analysis.get("topics", {})
    airframe = analysis.get("airframe") or {}
    start = flight_events.get("effective_flight_start_time") or 0.0
    end = flight_events.get("effective_flight_end_time") or float(log.duration_s or 0.0)
    duration = max(0.0, end - start)
    if duration <= 0:
        return {"confidence": "Low", "segments": [], "basis": "有效飞行段时长不可用"}

    position = topics.get("position")
    altitude = _altitude_points(position, start, end)
    takeoff = flight_events.get("takeoff_detected_time") or altitude.get("airborne_start") or start
    landing = flight_events.get("landing_detected_time") or altitude.get("airborne_end") or end
    transitions = _transition_intervals(log, start, end)
    kind = airframe.get("kind", "unknown")
    evidence = []
    if flight_events.get("timeline"):
        evidence.append("PX4 log messages")
    if altitude.get("max_altitude") is not None:
        evidence.append("vehicle_local_position altitude profile")
    if transitions:
        evidence.append("vehicle_status transition flags")

    direct_markers = sum(
        1
        for key in ("armed_time_s", "takeoff_time_s", "landing_time_s", "disarm_time_s")
        if flight_events.get(key) is not None
    )
    if transitions and direct_markers >= 2:
        confidence = "High"
    elif len(evidence) >= 2:
        confidence = "Medium-High"
    elif evidence:
        confidence = "Medium"
    else:
        confidence = "Low"
    if kind == "fixed_wing" and confidence == "High":
        confidence = "Medium-High"
    basis = " / ".join(evidence) if evidence else "缺少可验证阶段证据，仅保留有效飞行段"
    segments = []

    if kind == "compound_vtol" and transitions:
        first = transitions[0]
        last = transitions[-1]
        segments.extend(filter(None, [
            _segment("旋翼起飞/MC Takeoff", start, first["start_s"], basis, confidence),
            _segment("前转换/Front transition", first["start_s"], first["end_s"], "vehicle_status.in_transition_mode", "High"),
            _segment("固定翼巡航/FW Cruise", first["end_s"], last["start_s"] if len(transitions) > 1 else landing, basis, confidence),
            _segment("后转换/Back transition", last["start_s"], last["end_s"], "vehicle_status.in_transition_mode", "High") if len(transitions) > 1 else None,
            _segment("旋翼降落/MC Landing", last["end_s"] if len(transitions) > 1 else landing, end, basis, confidence),
        ]))
    elif kind in {"compound_vtol", "multicopter"}:
        climb_end = altitude.get("high_alt_start") or takeoff
        descent_start = altitude.get("high_alt_end") or landing
        segments.extend(filter(None, [
            _segment("旋翼起飞/Takeoff", start, climb_end, basis, confidence),
            _segment("悬停/任务/Hover or Mission", climb_end, descent_start, basis, confidence),
            _segment("旋翼降落/Landing", descent_start, end, basis, confidence),
        ]))
    elif kind == "fixed_wing":
        climb_end = altitude.get("high_alt_start")
        descent_start = altitude.get("high_alt_end")
        if climb_end and descent_start and descent_start > climb_end:
            approach_start = descent_start + max(0.0, landing - descent_start) * 0.6 if landing and landing > descent_start else descent_start
            landing_end = landing if landing and landing > approach_start else end
            segments.extend(filter(None, [
                _segment("Ground / Taxi", start, takeoff, basis, confidence),
                _segment("Takeoff", takeoff, min(climb_end, takeoff + max(1.0, (climb_end - takeoff) * 0.35)), basis, confidence),
                _segment("Climb", min(climb_end, takeoff + max(1.0, (climb_end - takeoff) * 0.35)), climb_end, basis, confidence),
                _segment("Cruise", climb_end, descent_start, basis, confidence),
                _segment("Descent", descent_start, approach_start, basis, confidence),
                _segment("Approach", approach_start, landing_end, basis, confidence),
                _segment("Landing", landing_end, min(end, landing_end + max(1.0, (end - landing_end) * 0.35)), basis, confidence),
                _segment("Postflight", min(end, landing_end + max(1.0, (end - landing_end) * 0.35)), end, basis, confidence),
            ]))
        else:
            low_basis = basis + "；固定翼阶段证据不足，不按固定比例硬切阶段，仅输出 verified flight window。"
            segments.append(_segment("Fixed-wing verified flight window", start, end, low_basis, "Low" if not evidence else "Medium"))
    else:
        segments.append(_segment("有效飞行段/Verified flight window", start, end, basis, confidence))

    segments = [item for item in segments if item]
    for segment in segments:
        if segment.get("duration_s", 0.0) < 2.0 and segment.get("confidence") in {"High", "Medium-High"}:
            segment["confidence"] = "Medium"
            segment["basis"] = str(segment.get("basis", "")) + "；阶段时长很短，置信度降级。"
        if segment.get("confidence") != "High":
            segment["note"] = "阶段划分仅作为趋势参考，不能作为确定异常边界。"
    if not segments:
        segments = [_segment("有效飞行段/Verified flight window", start, end, basis, "Low")]
    if not transitions and kind == "compound_vtol":
        basis += "；未检测到 transition 标记，因此不生成前转换/后转换阶段"
    return {"confidence": confidence, "segments": segments, "basis": basis}


def actuator_channel_metrics(actuator, flight_events=None, airframe=None) -> dict[str, Any]:
    aircraft_kind = (airframe or {}).get("kind") or (airframe or {}).get("selected_aircraft_type") or "unknown"
    if not actuator:
        return {
            "available": False,
            "valid_channels": [],
            "unused_channels": [],
            "excluded_channels": [],
            "risk_channels": [],
            "saturation_events": [],
            "mapping_confidence": "Unavailable",
            "channel_stats": [],
            "summary": "日志未包含执行器输出数据",
        }
    valid, unused, risk, saturation_events = [], [], [], []
    for key, values in actuator.fields.items():
        count = min(len(getattr(actuator, "time_s", [])), len(values))
        arr_raw = np.asarray(values[:count], dtype=float) if count else np.array([], dtype=float)
        time_s = np.asarray(actuator.time_s[:count], dtype=float) if count else np.array([], dtype=float)
        mask = np.isfinite(arr_raw) & np.isfinite(time_s)
        arr = arr_raw[mask]
        channel_time = time_s[mask]
        if len(arr) < 5:
            unused.append({"channel": key, "reason": "insufficient_samples", "label": "有效样本不足", "excluded_from_abnormal_events": True})
            continue
        span = float(np.max(arr) - np.min(arr))
        std = float(np.std(arr))
        fixed_values = (0.0, 1000.0, 1500.0, 2000.0, 65535.0)
        fixed_like = any(np.allclose(arr, value, atol=1e-6) for value in fixed_values)
        if span < 1e-6 or std < 1e-6 or fixed_like:
            unused.append({"channel": key, "reason": "fixed_or_unused", "label": "通道长期固定/未使用/无效，不参与风险判断", "excluded_from_abnormal_events": True})
            continue
        is_pwm = float(np.nanmax(np.abs(arr))) > 2
        if is_pwm:
            high = arr >= 1950
            low = arr <= 1050
            midpoint_note = "PWM 1500us 视为舵机中位，不计饱和"
            possible_role = "servo/control_surface" if aircraft_kind == "fixed_wing" else "pwm_actuator"
        else:
            high = arr >= 0.95
            low = arr <= -0.95
            midpoint_note = "normalized 输出接近 0 视为中位，不计饱和"
            possible_role = "motor_or_normalized_actuator"
        high_ratio = float(np.mean(high) * 100)
        low_ratio = float(np.mean(low) * 100)
        sustained = sustained_boolean(high | low, min_run=8)
        row = {
            "channel": key,
            "min": float(np.min(arr)),
            "max": float(np.max(arr)),
            "mean": float(np.mean(arr)),
            "std": std,
            "high_saturation_percent": round(high_ratio, 2),
            "low_saturation_percent": round(low_ratio, 2),
            "sustained_saturation": bool(sustained),
            "participates_in_risk": bool(sustained and (high_ratio + low_ratio) > 3),
            "note": midpoint_note,
            "possible_role": possible_role,
            "mapping_confidence": "Medium",
        }
        valid.append(row)
        if row["participates_in_risk"]:
            risk.append(row)
            saturation_events.extend(clip_interval_to_effective_range(event, flight_events) for event in _saturation_intervals(
                key,
                channel_time,
                high,
                low,
                high_ratio,
                low_ratio,
            ))
    saturation_events = [event for event in saturation_events if event]
    mapping_confidence = "Medium-High" if len(valid) >= 4 else "Medium" if valid else "Low"
    return {
        "available": True,
        "valid_channels": valid,
        "unused_channels": unused,
        "excluded_channels": unused,
        "risk_channels": risk,
        "saturation_events": saturation_events,
        "mapping_confidence": mapping_confidence,
        "channel_stats": valid,
        "summary": "执行器数据已按有效通道过滤，未使用/固定通道不参与饱和结论",
    }


def _saturation_intervals(channel: str, time_s, high_mask, low_mask, high_ratio: float, low_ratio: float) -> list[dict[str, Any]]:
    events = []
    for saturation_type, mask, ratio in (("high", high_mask, high_ratio), ("low", low_mask, low_ratio)):
        if not np.any(mask):
            continue
        active_start = None
        last_index = None
        for index, active in enumerate(mask):
            if active and active_start is None:
                active_start = index
            if active:
                last_index = index
            if (not active or index == len(mask) - 1) and active_start is not None:
                stop = last_index if last_index is not None else active_start
                start_s = float(time_s[active_start]) if active_start < len(time_s) else None
                end_s = float(time_s[stop]) if stop < len(time_s) else None
                duration = (end_s - start_s) if start_s is not None and end_s is not None else None
                if duration is not None and duration < 0.15:
                    active_start = None
                    last_index = None
                    continue
                events.append({
                    "channel": channel,
                    "start_s": start_s,
                    "end_s": end_s,
                    "duration_s": duration,
                    "saturation_type": saturation_type,
                    "phase": "未定位阶段",
                    "confidence": "Medium" if duration is not None else "Low",
                    "evidence": f"{saturation_type} saturation ratio {ratio:.2f}% within valid channel",
                    "category": "actuator_saturation",
                    "is_abnormal": True,
                })
                active_start = None
                last_index = None
    return events


def sustained_boolean(mask, min_run=8) -> bool:
    run = 0
    for value in mask:
        run = run + 1 if bool(value) else 0
        if run >= min_run:
            return True
    return False


def battery_quality(battery) -> dict[str, Any]:
    if not battery:
        return {"quality": "unavailable", "reason": "日志未包含 battery_status/mapped_battery", "current_reliable": False}
    voltage = _stats(battery.fields.get("voltage"))
    current = _stats(battery.fields.get("current"))
    consumed = _stats(battery.fields.get("consumed_mah"))
    reasons = []
    current_reliable = True
    if not current:
        current_reliable = False
        reasons.append("日志未包含有效电流字段")
    elif current["max"] < 1.0 and current["mean"] < 0.3:
        current_reliable = False
        reasons.append("电流长期接近 0，疑似电源模块未校准或字段无效")
    if consumed and consumed["max"] < 5 and battery and len(getattr(battery, "time_s", [])) > 60:
        current_reliable = False
        reasons.append("消耗容量与日志时长不匹配，consumed_mah 可疑")
    if not voltage:
        reasons.append("日志未包含有效电压字段")
    quality = "reliable" if voltage and current_reliable else "suspicious" if voltage else "unavailable"
    return {
        "quality": quality,
        "voltage": voltage,
        "current": current,
        "consumed_mah": consumed,
        "current_reliable": current_reliable,
        "reason": "；".join(reasons) if reasons else "电压/电流字段通过基本合理性检查",
    }


def gps_quality(gps) -> dict[str, Any]:
    if not gps:
        return {"quality": "unavailable", "reason": "日志未包含 GPS 数据", "fix_type_label": "N/A"}
    sat = _stats(gps.fields.get("satellites"))
    fix = _stats(gps.fields.get("fix_type"))
    eph = _stats(gps.fields.get("eph"))
    min_fix = int(fix["min"]) if fix else None
    label = FIX_TYPE_LABELS.get(min_fix, f"Fix {min_fix}") if min_fix is not None else "N/A"
    reasons = []
    if sat and sat["min"] < 8:
        reasons.append(f"最低卫星数 {sat['min']:.0f}，存在定位质量下降")
    if min_fix is not None and min_fix < 3:
        reasons.append(f"最低 Fix Type 为 {label}")
    quality = "reliable" if not reasons and (sat or fix) else "suspicious" if (sat or fix) else "unavailable"
    return {"quality": quality, "satellites": sat, "fix_type": fix, "eph_or_hdop": eph, "fix_type_label": label, "reason": "；".join(reasons) if reasons else "GPS 基本质量未见明显异常"}


def pid_parameters_for_airframe(airframe: dict[str, Any], params: dict[str, Any]) -> list[dict[str, Any]]:
    kind = airframe.get("kind")
    if kind == "multicopter":
        names = ["MC_ROLLRATE_P", "MC_ROLLRATE_I", "MC_ROLLRATE_D", "MC_PITCHRATE_P", "MC_PITCHRATE_I", "MC_PITCHRATE_D", "MC_YAWRATE_P", "MC_YAWRATE_I", "MC_YAWRATE_D", "MC_ROLL_P", "MC_PITCH_P", "MC_YAW_P", "MPC_XY_P", "MPC_Z_VEL_P_ACC"]
    elif kind == "compound_vtol":
        names = ["MC_ROLLRATE_P", "MC_PITCHRATE_P", "MC_YAWRATE_P", "FW_RR_P", "FW_PR_P", "FW_YR_P", "VT_ARSP_TRANS", "VT_F_TR_OL_TM", "VT_B_TRANS_DUR"]
    else:
        names = ["FW_RR_P", "FW_RR_I", "FW_RR_D", "FW_PR_P", "FW_PR_I", "FW_PR_D", "FW_YR_P", "FW_YR_I", "FW_YR_D", "FW_RR_FF", "FW_PR_FF", "FW_YR_FF", "FW_R_TC", "FW_P_TC", "FW_YR_MAX", "FW_R_RMAX", "FW_P_RMAX", "TECS_SPDWEIGHT", "TECS_TIME_CONST"]
    return [
        {
            "name": name,
            "value": _format_parameter_value(params.get(name)) if name in params else None,
            "raw_value": params.get(name) if name in params else None,
            "source": "log_parameter" if name in params else "日志未包含",
        }
        for name in names
    ]


def data_quality_score(analysis, flight_events, phase, actuator, battery, gps, messages) -> dict[str, Any]:
    topics = analysis.get("topics", {})
    missing = list(analysis.get("missing") or [])
    availability = topic_availability_summary(analysis)
    dimensions = {
        "required_topics_available": availability["available_topic_count"] / max(1, availability["expected_topic_count"]),
        "timestamp_alignment_quality": 0.8,
        "setpoint_availability": 1.0 if topics.get("attitude_sp") else 0.2,
        "actuator_data_reliability": 1.0 if actuator.get("available") and actuator.get("valid_channels") else 0.4,
        "actuator_mapping_confidence": CONFIDENCE_SCORE.get(str(actuator.get("mapping_confidence", "Low")), 0.3),
        "battery_data_reliability": {"reliable": 1.0, "suspicious": 0.5, "unavailable": 0.1}.get(battery.get("quality"), 0.4),
        "gps_data_reliability": {"reliable": 1.0, "suspicious": 0.55, "unavailable": 0.2}.get(gps.get("quality"), 0.4),
        "estimator_data_availability": 1.0 if any("estimator" in name for name in analysis["log"].topics) else 0.35,
        "parameter_availability": 1.0 if analysis["log"].parameters else 0.25,
        "aircraft_type_confidence": float((analysis.get("airframe") or {}).get("confidence", 0.4)),
        "phase_segmentation_confidence": CONFIDENCE_SCORE.get(str(phase.get("confidence", "Low")), 0.3),
    }
    completeness_score = dimensions["required_topics_available"]
    engineering_dimensions = [
        dimensions["timestamp_alignment_quality"],
        dimensions["setpoint_availability"],
        dimensions["actuator_data_reliability"],
        dimensions["battery_data_reliability"],
        dimensions["gps_data_reliability"],
        dimensions["estimator_data_availability"],
        dimensions["aircraft_type_confidence"],
        dimensions["phase_segmentation_confidence"],
        dimensions["actuator_mapping_confidence"],
    ]
    engineering_score = sum(engineering_dimensions) / len(engineering_dimensions)
    if battery.get("quality") != "reliable":
        engineering_score = min(engineering_score, 0.74)
    if phase.get("confidence") != "High":
        engineering_score = min(engineering_score, 0.74)
    if float((analysis.get("airframe") or {}).get("confidence", 0.4)) < 0.8:
        engineering_score = min(engineering_score, 0.74)
    average = (completeness_score * 0.45) + (engineering_score * 0.55)
    engineering_level = _level_from_score(engineering_score)
    level = _min_level(_level_from_score(average), engineering_level)
    limitations = []
    if missing:
        limitations.append("缺失 topic：" + "、".join(missing))
    if battery.get("quality") != "reliable":
        limitations.append("电流/电源数据不可直接用于动力负载结论")
    if not topics.get("attitude_sp"):
        limitations.append("缺少姿态 setpoint，不能输出 tracking-based PID 结论")
    if phase.get("confidence") == "Low":
        limitations.append("阶段划分置信度低，需要人工复核")
    elif phase.get("confidence") in {"Medium", "Medium-High"}:
        limitations.append("阶段划分未达到 High，报告中的阶段结论仅作为趋势参考")
    if actuator.get("mapping_confidence") in {"Low", "Medium"}:
        limitations.append("执行器通道映射未达到 High，不能直接将通道限幅写成确定机械故障")
    if messages.get("counts", {}).get("failsafe_count", 0):
        limitations.append("日志包含 failsafe/preflight fail，相关结论需要结合现场现象复核")
    return {
        "overall": level,
        "level": level,
        "score": round(average * 100, 1),
        "data_completeness": _level_from_score(completeness_score),
        "engineering_confidence": engineering_level,
        "overall_report_confidence": level,
        "data_completeness_score": round(completeness_score * 100, 1),
        "engineering_confidence_score": round(engineering_score * 100, 1),
        "dimensions": dimensions,
        "dimension_scores": {key: round(value * 100, 1) for key, value in dimensions.items()},
        "topic_availability": availability,
        "limitations": limitations,
    }


def merged_voltage_drop_events(
    battery,
    battery_metrics: dict[str, Any],
    phases=None,
    rate_threshold=0.15,
    merge_gap_s=5.0,
    flight_events=None,
) -> list[dict[str, Any]]:
    if not battery or "voltage" not in battery.fields or len(getattr(battery, "time_s", [])) < 2:
        return []
    count = min(len(battery.time_s), len(battery.fields["voltage"]))
    time_s = np.asarray(battery.time_s[:count], dtype=float)
    voltage = np.asarray(battery.fields["voltage"][:count], dtype=float)
    mask = np.isfinite(time_s) & np.isfinite(voltage)
    time_s, voltage = time_s[mask], voltage[mask]
    if len(time_s) < 2:
        return []
    dt = np.maximum(np.diff(time_s), 1e-3)
    drop_rate = -np.diff(voltage) / dt
    indices = np.where(drop_rate > rate_threshold)[0]
    if len(indices) == 0:
        return []
    intervals = []
    start_idx = int(indices[0])
    last_idx = int(indices[0])
    for index in indices[1:]:
        index = int(index)
        if float(time_s[index] - time_s[last_idx]) <= merge_gap_s:
            last_idx = index
            continue
        intervals.append((start_idx, last_idx))
        start_idx = last_idx = index
    intervals.append((start_idx, last_idx))

    events = []
    current_reliable = bool(battery_metrics.get("current_reliable"))
    for start_idx, end_idx in intervals:
        end_sample = min(end_idx + 1, len(voltage) - 1)
        start_s = float(time_s[start_idx])
        end_s = float(time_s[end_sample])
        start_v = float(voltage[start_idx])
        end_v = float(voltage[end_sample])
        max_rate = float(np.nanmax(drop_rate[start_idx:end_idx + 1]))
        total_drop = start_v - end_v
        duration_s = max(0.0, end_s - start_s)
        is_transient = bool(duration_s <= 3.0 and max_rate >= 0.5 and total_drop >= 0.1)
        advice = "检查电池内阻、电源线和接插件。"
        confidence = "Medium"
        if not current_reliable:
            confidence = "Low"
            advice = "电压存在下降趋势，但电流数据可信度低，不能仅凭该日志判断动力负载。"
        event = {
            "name": "瞬态电压下降" if is_transient else "电压下降趋势",
            "category": "battery_voltage_transient_drop" if is_transient else "voltage_trend",
            "event_type": "voltage_transient_drop" if is_transient else "voltage_trend",
            "severity": "WARNING",
            "start_s": start_s,
            "end_s": end_s,
            "duration_s": duration_s,
            "voltage_start": start_v,
            "voltage_end": end_v,
            "total_drop_v": total_drop,
            "max_drop_rate": max_rate,
            "drop_rate": max_rate,
            "phase": phase_at_time(phases or [], start_s),
            "confidence": confidence,
            "evidence": f"voltage {start_v:.2f}V -> {end_v:.2f}V, max drop rate {max_rate:.2f} V/s",
            "risk": "中" if is_transient else "低",
            "advice": advice,
            "is_abnormal": bool(is_transient),
            "use_for_power_load_conclusion": bool(current_reliable and is_transient),
        }
        event = clip_interval_to_effective_range(event, flight_events)
        if event.get("event_type") == "voltage_trend":
            event["is_abnormal"] = False
            event["advice"] = "作为电源趋势观察项记录；不得单独写成动力负载异常或高风险事件。"
        events.append(event)
    return events


def build_missing_data_statement(analysis, availability: dict[str, Any]) -> dict[str, Any]:
    missing = list(dict.fromkeys(analysis.get("missing") or []))
    unavailable_topics = [
        item.get("topic")
        for item in availability.get("details", [])
        if not item.get("available") and item.get("topic")
    ]
    if unavailable_topics:
        missing.extend(unavailable_topics)
    missing = list(dict.fromkeys(str(item) for item in missing if item))
    return {
        "items": missing,
        "statement": "；".join(missing) if missing else "关键解析字段未发现明显缺失",
        "do_not_conclude": [
            "缺失字段不得被 AI 或报告补全为具体数值。",
            "缺失 setpoint 时不得输出 tracking-based PID 确定结论。",
            "缺失执行器映射时不得把通道编号写成确定舵面/电机名称。",
        ],
    }


def _verified_algorithm_summary(core: dict[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": "verified-algorithm-summary.v1",
        "aircraft_type": core.get("aircraft_type"),
        "selected_aircraft_type": core.get("selected_aircraft_type"),
        "aircraft_type_source": core.get("aircraft_type_source"),
        "aircraft_type_override": core.get("aircraft_type_override"),
        "aircraft_type_candidates": core.get("aircraft_type_candidates"),
        "aircraft_type_confidence": core.get("aircraft_type_confidence"),
        "aircraft_type_evidence": core.get("aircraft_type_evidence"),
        "ignored_evidence": core.get("ignored_evidence"),
        "data_quality": core.get("data_quality"),
        "topic_availability": core.get("topic_availability"),
        "flight_events": core.get("flight_events"),
        "verified_flight_timeline": core.get("verified_flight_timeline"),
        "phase_segments": core.get("phase_segments"),
        "verified_flight_phases": core.get("verified_flight_phases"),
        "attitude_metrics": core.get("attitude_metrics"),
        "actuator_metrics": core.get("actuator_metrics"),
        "actuator_channel_analysis": core.get("actuator_channel_analysis"),
        "battery_metrics": core.get("battery_metrics"),
        "gps_metrics": core.get("gps_metrics"),
        "warning_events": core.get("warning_events"),
        "abnormal_events": core.get("abnormal_events"),
        "voltage_drop_events": core.get("voltage_drop_events"),
        "root_cause_analysis": core.get("root_cause_analysis"),
        "risk_assessment": core.get("risk_assessment"),
        "pid_parameters": core.get("pid_parameters"),
        "missing_data": core.get("missing_data"),
        "do_not_conclude": core.get("do_not_conclude"),
        "battery_events": core.get("battery_events"),
        "unreliable_data": core.get("unreliable_data"),
    }


def _verified_ai_input_summary(core: dict[str, Any]) -> dict[str, Any]:
    warnings = core.get("warning_events") or {}
    return {
        "schemaVersion": "verified-ai-input-summary.v1",
        "aircraft_type": core.get("aircraft_type"),
        "selected_aircraft_type": core.get("selected_aircraft_type"),
        "aircraft_type_source": core.get("aircraft_type_source"),
        "aircraft_type_override": core.get("aircraft_type_override"),
        "aircraft_type_candidates": core.get("aircraft_type_candidates"),
        "aircraft_type_confidence": core.get("aircraft_type_confidence"),
        "aircraft_type_evidence": core.get("aircraft_type_evidence"),
        "ignored_evidence": core.get("ignored_evidence"),
        "data_quality": core.get("data_quality"),
        "topic_availability": core.get("topic_availability"),
        "flight_events": core.get("flight_events"),
        "verified_flight_timeline": core.get("verified_flight_timeline"),
        "phase_segments": core.get("phase_segments"),
        "attitude_metrics": core.get("attitude_metrics"),
        "actuator_metrics": core.get("actuator_metrics"),
        "actuator_channel_analysis": core.get("actuator_channel_analysis"),
        "battery_metrics": core.get("battery_metrics"),
        "gps_metrics": core.get("gps_metrics"),
        "warning_events": {
            "counts": warnings.get("counts") or {},
            "groups": (warnings.get("groups") or [])[:30],
            "rows": (warnings.get("rows") or [])[:40],
        },
        "pid_parameters": (core.get("pid_parameters") or [])[:40],
        "abnormal_events": (core.get("abnormal_events") or [])[:60],
        "root_cause_analysis": (core.get("root_cause_analysis") or [])[:20],
        "risk_assessment": core.get("risk_assessment"),
        "missing_data": core.get("missing_data") or [],
        "do_not_conclude": core.get("do_not_conclude") or [],
        "battery_events": core.get("battery_events") or [],
        "unreliable_data": core.get("unreliable_data") or [],
        "ai_boundaries": [
            "AI receives verified summary only; raw ULog samples are not sent to the model.",
            "AI cannot change verified numbers or create missing data.",
            "AI cannot send MAVLink commands or modify PID in flight.",
            "Parameter changes require local safety gate, disarmed state, human confirmation, and rollback.",
        ],
    }


def build_verified_flight_timeline(flight_events, phase, messages) -> dict[str, Any]:
    rows = [["时间段/时间", "类型", "内容", "依据", "置信度"]]
    events = []
    start = flight_events.get("effective_flight_start_time")
    end = flight_events.get("effective_flight_end_time")
    if start is not None and end is not None:
        rows.append([
            f"{start:.2f}s - {end:.2f}s",
            "effective_flight_range",
            "统一有效飞行段",
            "verified flight events",
            phase.get("confidence", "Low"),
        ])
    for segment in phase.get("segments", []):
        rows.append([
            f"{segment.get('start_s', 0):.2f}s - {segment.get('end_s', 0):.2f}s",
            "flight_phase",
            segment.get("name"),
            segment.get("basis", ""),
            segment.get("confidence", "Low"),
        ])
        events.append({
            "type": "flight_phase",
            "name": segment.get("name"),
            "start_s": segment.get("start_s"),
            "end_s": segment.get("end_s"),
            "duration_s": segment.get("duration_s"),
            "confidence": segment.get("confidence"),
            "evidence": segment.get("basis"),
            "method": segment.get("method", "verified_phase_detector"),
        })
    for row in messages.get("rows", []):
        if row.get("category") != "operation_info":
            continue
        rows.append([
            row.get("time", "N/A"),
            "operation_info",
            row.get("meaning") or row.get("raw"),
            row.get("raw"),
            "High",
        ])
        events.append({
            "type": "operation_info",
            "time_s": row.get("time_s"),
            "name": row.get("meaning") or row.get("raw"),
            "raw": row.get("raw"),
            "is_abnormal": False,
            "phase": row.get("flight_phase"),
        })
    return {"rows": rows, "events": events}


def build_verified_abnormal_events(messages, actuator, voltage_events, attitude, flight_events=None) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for row in messages.get("rows", []):
        if not row.get("is_abnormal"):
            continue
        if row.get("category") == "operation_info":
            continue
        high_severity = row.get("severity") in {"ERROR", "CRITICAL", "ALERT", "EMERGENCY"}
        high_category = row.get("category") in {"preflight_fail", "arming_denied", "failsafe"}
        risk = "高" if high_severity or high_category else "中"
        if row.get("flight_scope") == "postflight" and high_category:
            risk = "中"
        elif row.get("in_effective_flight") is False and not high_category:
            risk = "低"
        events.append({
            "name": row.get("meaning") or row.get("raw"),
            "category": row.get("category", "mavlink_status"),
            "severity": row.get("severity", "WARNING"),
            "time": row.get("time_s"),
            "start_s": row.get("time_s"),
            "end_s": row.get("time_s"),
            "duration_s": 0.0,
            "phase": row.get("flight_phase"),
            "evidence": row.get("raw"),
            "risk": risk,
            "confidence": row.get("confidence", "Medium"),
            "advice": row.get("advice"),
            "raw_message": row.get("raw_message") or row.get("raw"),
            "normalized_message": row.get("normalized_message"),
            "flight_scope": row.get("flight_scope"),
            "in_effective_flight": row.get("in_effective_flight"),
            "is_abnormal": True,
        })
    for event in actuator.get("saturation_events", []):
        if not event.get("is_abnormal", True) or event.get("in_effective_flight") is False:
            continue
        events.append({
            "name": f"{event.get('channel')} 执行器{event.get('saturation_type')}端持续限幅",
            "category": "actuator_saturation",
            "severity": "WARNING",
            "time": event.get("start_s"),
            "start_s": event.get("start_s"),
            "end_s": event.get("end_s"),
            "duration_s": event.get("duration_s"),
            "phase": event.get("phase"),
            "evidence": event.get("evidence"),
            "risk": "中",
            "confidence": event.get("confidence", "Medium"),
            "advice": "仅对有效执行器通道生成该事件；请结合实机通道映射、机械限位和电源状态复核。",
            "flight_scope": event.get("flight_scope"),
            "in_effective_flight": event.get("in_effective_flight"),
            "is_abnormal": True,
        })
    for event in voltage_events:
        if not event.get("is_abnormal"):
            continue
        events.append(event)
    for axis, row in (attitude or {}).items():
        if not isinstance(row, dict):
            continue
        warning = row.get("wrap_warning") or {}
        if warning.get("wrap_detected"):
            events.append({
                "name": "航向角跨零/wrap 复核提示",
                "category": "attitude_wrap_review",
                "severity": "NOTICE",
                "time": None,
                "start_s": None,
                "end_s": None,
                "duration_s": None,
                "phase": "全局",
                "evidence": warning.get("warning"),
                "risk": "低",
                "confidence": "Low",
                "advice": "不要将 raw yaw range=max-min 作为真实 360° 振荡证据；请结合 circular error 和 setpoint 连续性复核。",
                "is_abnormal": False,
            })
    return sorted(events, key=lambda item: float(item.get("start_s") if item.get("start_s") is not None else 1e12))


def conservative_root_causes(abnormal_events, quality, battery, actuator) -> list[dict[str, Any]]:
    causes = []
    categories = {str(item.get("category")) for item in abnormal_events if item.get("is_abnormal")}
    if "actuator_saturation" in categories:
        causes.append({
            "level": "possible",
            "candidate": "控制余量、重心、舵面/电机映射或机械限位待复核",
            "evidence": "仅有效执行器通道出现持续限幅事件；被排除通道不参与该判断。",
            "next_check": "先核对实机执行器映射、机械限位、重心和电源，再讨论控制参数。",
        })
    if "battery_voltage_drop" in categories or "battery_voltage_transient_drop" in categories:
        level = "possible" if battery.get("current_reliable") else "insufficient_evidence"
        causes.append({
            "level": level,
            "candidate": "电源电压下降趋势",
            "evidence": "日志存在合并后的电压下降区间。",
            "next_check": "若电流数据 suspicious，不能直接归因为动力负载；需检查电源模块校准、电池内阻和接插件。",
        })
    if any(item.get("category") in {"abnormal_event", "warning"} for item in abnormal_events):
        causes.append({
            "level": "possible",
            "candidate": "飞控告警或 failsafe 相关风险",
            "evidence": "STATUSTEXT/日志消息中存在 warning/error/failsafe 类事件。",
            "next_check": "按告警原文逐项复核 EKF、GPS、Compass、Battery、Safety 状态。",
        })
    if quality.get("engineering_confidence") != "High":
        causes.append({
            "level": "insufficient_evidence",
            "candidate": "PID 作为根因证据不足",
            "evidence": f"工程结论可信度为 {quality.get('engineering_confidence')}，不能直接将姿态误差归因为 PID。",
            "next_check": "PID 调整只应在机械、电源、传感器和执行器映射确认正常后进行小幅对比测试。",
        })
    if not causes:
        causes.append({
            "level": "insufficient_evidence",
            "candidate": "未确认明确根因",
            "evidence": "verified abnormal_events 未显示足够强的单一根因证据。",
            "next_check": "结合现场现象和下一次完整日志继续复核。",
        })
    return causes


def verified_risk_assessment(abnormal_events, quality) -> dict[str, Any]:
    confirmed_events = [item for item in abnormal_events if item.get("is_abnormal")]
    if not confirmed_events:
        return {
            "level": "Low",
            "level_cn": "低",
            "score": 15,
            "summary": "verified abnormal_events 未检测到可确认异常。",
            "basis": ["无 confirmed abnormal event"],
        }
    severe_tokens = {"CRITICAL", "ALERT", "EMERGENCY"}
    high_categories = {"failsafe", "preflight_fail", "arming_denied", "motor_failure", "actuator_failure"}
    has_high = False
    basis = []
    for event in confirmed_events:
        severity = str(event.get("severity", "")).upper()
        category = str(event.get("category", "")).lower()
        evidence = str(event.get("evidence", "")).lower()
        scope = str(event.get("flight_scope", "")).lower()
        postflight_only = scope == "postflight"
        if (severity in severe_tokens or category in high_categories) and not postflight_only:
            has_high = True
        if ("failsafe" in evidence or "preflight fail" in evidence or "arming denied" in evidence) and not postflight_only:
            has_high = True
        basis.append(f"{event.get('name', 'event')}[{event.get('category', 'unknown')}/{event.get('confidence', 'Low')}]")
    if has_high:
        return {
            "level": "High",
            "level_cn": "高",
            "score": 85,
            "summary": "存在 confirmed 高严重度飞控告警或 failsafe/preflight 类事件。",
            "basis": basis[:12],
        }
    if len(confirmed_events) >= 3:
        return {
            "level": "Medium",
            "level_cn": "中",
            "score": 62,
            "summary": "存在多项待复核异常，但未达到 confirmed 高风险条件。",
            "basis": basis[:12],
        }
    if quality.get("engineering_confidence") == "Low":
        return {
            "level": "Medium",
            "level_cn": "中",
            "score": 55,
            "summary": "异常数量有限，但工程结论可信度较低，按中风险保守处理。",
            "basis": basis[:12],
        }
    return {
        "level": "Medium",
        "level_cn": "中",
        "score": 45,
        "summary": "存在待复核异常事件，但没有 confirmed 高风险证据。",
        "basis": basis[:12],
    }


def build_verified_analysis_summary(analysis) -> dict[str, Any]:
    log = analysis["log"]
    topics = analysis["topics"]
    flight_events = detect_flight_events(log)
    phase = build_phase_segments(analysis, flight_events)
    actuator = actuator_channel_metrics(topics.get("actuator"), flight_events, analysis.get("airframe") or {})
    for event in actuator.get("saturation_events", []):
        event["phase"] = phase_at_time(phase.get("segments", []), event.get("start_s"))
    battery = battery_quality(topics.get("battery"))
    gps = gps_quality(topics.get("gps"))
    messages = classify_log_messages(log, phase.get("segments", []), flight_events)
    attitude = {}
    for axis in ("roll", "pitch", "yaw"):
        row = aligned_error_stats(
            topics.get("attitude"),
            axis,
            topics.get("attitude_sp"),
            f"{axis}_sp",
            circular=(axis == "yaw"),
        )
        if axis == "yaw" and topics.get("attitude") and "yaw" in topics["attitude"].fields:
            if row is None:
                row = {"available": False, "reason": "日志未包含可对齐 yaw setpoint/actual"}
            row["wrap_warning"] = yaw_wrap_warning(topics["attitude"].fields.get("yaw"))
            row["raw_range_policy"] = "raw max-min range is not used for yaw abnormal evidence"
        attitude[axis] = row
    quality = data_quality_score(analysis, flight_events, phase, actuator, battery, gps, messages)
    if (attitude.get("yaw") or {}).get("wrap_warning", {}).get("wrap_detected"):
        quality["engineering_confidence"] = "Medium" if quality.get("engineering_confidence") == "High" else quality.get("engineering_confidence")
        quality["overall_report_confidence"] = "Medium" if quality.get("overall_report_confidence") == "High" else quality.get("overall_report_confidence")
        quality["level"] = quality["overall_report_confidence"]
        quality["overall"] = quality["overall_report_confidence"]
        quality.setdefault("limitations", []).append("航向角存在跨零/wrap 风险，航向异常结论需要人工复核")
    voltage_events = merged_voltage_drop_events(topics.get("battery"), battery, phase.get("segments", []), flight_events=flight_events)
    unreliable = []
    if battery.get("quality") != "reliable":
        unreliable.append({"name": "battery_current", "reason": battery.get("reason")})
    if gps.get("quality") == "unavailable":
        unreliable.append({"name": "gps", "reason": gps.get("reason")})
    timeline = build_verified_flight_timeline(flight_events, phase, messages)
    abnormal_events = build_verified_abnormal_events(messages, actuator, voltage_events, attitude, flight_events)
    root_causes = conservative_root_causes(abnormal_events, quality, battery, actuator)
    risk_assessment = verified_risk_assessment(abnormal_events, quality)
    airframe = analysis.get("airframe") or {}
    missing_statement = build_missing_data_statement(analysis, quality.get("topic_availability") or {})
    core = {
        "schemaVersion": "verified-flight-analysis.v2",
        "data_quality": quality,
        "topic_availability": quality.get("topic_availability"),
        "aircraft_type": airframe,
        "selected_aircraft_type": airframe.get("selected_aircraft_type") or airframe.get("kind"),
        "aircraft_type_source": airframe.get("aircraft_type_source") or airframe.get("source") or "auto_detection",
        "aircraft_type_override": airframe.get("aircraft_type_override"),
        "aircraft_type_candidates": airframe.get("aircraft_type_candidates", []),
        "aircraft_type_confidence": airframe.get("confidence_label") or ("high" if float(airframe.get("confidence", 0.0)) >= 0.85 else "medium" if float(airframe.get("confidence", 0.0)) >= 0.6 else "low"),
        "aircraft_type_evidence": airframe.get("evidence", []),
        "ignored_evidence": airframe.get("ignored_evidence", []),
        "flight_events": flight_events,
        "verified_flight_timeline": timeline,
        "phase_segments": phase,
        "verified_flight_phases": phase,
        "attitude_metrics": attitude,
        "actuator_metrics": actuator,
        "actuator_channel_analysis": actuator,
        "battery_metrics": battery,
        "gps_metrics": gps,
        "warning_events": messages,
        "abnormal_events": abnormal_events,
        "voltage_drop_events": voltage_events,
        "battery_events": voltage_events,
        "root_cause_analysis": root_causes,
        "risk_assessment": risk_assessment,
        "pid_parameters": pid_parameters_for_airframe(analysis.get("airframe") or {}, log.parameters or {}),
        "missing_data": missing_statement,
        "do_not_conclude": missing_statement.get("do_not_conclude", []),
        "unreliable_data": unreliable,
    }
    core["verified_algorithm_summary"] = _verified_algorithm_summary(core)
    core["verified_ai_input_summary"] = _verified_ai_input_summary(core)
    return core

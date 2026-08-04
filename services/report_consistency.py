from __future__ import annotations

import re
from typing import Any


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _excluded_channels(verified: dict[str, Any]) -> set[str]:
    actuator = verified.get("actuator_channel_analysis") or verified.get("actuator_metrics") or {}
    return {
        str(item.get("channel"))
        for item in actuator.get("excluded_channels", []) or actuator.get("unused_channels", [])
        if item.get("channel") is not None
    }


CONFIDENCE_RANK = {"Low": 0, "Medium": 1, "Medium-High": 2, "High": 3}


def check_report_consistency(verified: dict[str, Any], markdown_text: str = "") -> dict[str, Any]:
    warnings: list[str] = []
    blocking_errors: list[str] = []

    aircraft = verified.get("aircraft_type") or {}
    kind = verified.get("selected_aircraft_type") or aircraft.get("selected_aircraft_type") or aircraft.get("kind")
    phase = verified.get("phase_segments") or {}
    flight = verified.get("flight_events") or {}
    actuator = verified.get("actuator_channel_analysis") or verified.get("actuator_metrics") or {}
    abnormal_events = verified.get("abnormal_events") or []
    quality = verified.get("data_quality") or {}
    battery = verified.get("battery_metrics") or {}
    voltage_events = verified.get("battery_events") or verified.get("voltage_drop_events") or []

    start = flight.get("effective_flight_start_time")
    for segment in phase.get("segments", []):
        if start is not None and segment.get("start_s") is not None:
            if float(segment["start_s"]) + 1e-6 < float(start) and "preflight" not in _text(segment.get("name")).lower():
                blocking_errors.append("飞行阶段早于 verified effective flight start，且未标注为 preflight。")

    if kind == "fixed_wing":
        forbidden_terms = ("多旋翼", "旋翼起飞", "旋翼降落", "悬停/任务", "Hover or Mission", "悬停")
        for term in forbidden_terms:
            if term in markdown_text:
                blocking_errors.append(f"固定翼报告正文出现“{term}”，疑似旧模板残留。")
        pid_section = markdown_text
        match = re.search(r"##\s*6\.\s*PID.*?(?=\n##\s*7\.|\Z)", markdown_text, flags=re.DOTALL | re.IGNORECASE)
        if match:
            pid_section = match.group(0)
        if re.search(r"\bMC_[A-Z0-9_]+\b|\bMPC_[A-Z0-9_]+\b", pid_section):
            blocking_errors.append("固定翼报告 PID 主分析表出现 MC_*/MPC_* 参数。")
    if kind == "multicopter":
        match = re.search(r"##\s*6\.\s*PID.*?(?=\n##\s*7\.|\Z)", markdown_text, flags=re.DOTALL | re.IGNORECASE)
        pid_section = match.group(0) if match else markdown_text
        if re.search(r"\bFW_[A-Z0-9_]+\b|\bTECS_[A-Z0-9_]+\b", pid_section):
            blocking_errors.append("多旋翼报告 PID 主分析表出现 FW_*/TECS_* 参数。")

    excluded = _excluded_channels(verified)
    for event in abnormal_events:
        body = " ".join(_text(event.get(key)) for key in ("name", "channel", "evidence", "category"))
        if event.get("category") == "operation_info" or event.get("is_abnormal") is False and event.get("category") == "operation_info":
            blocking_errors.append("operation_info 进入 abnormal_events。")
        if event.get("event_type") == "voltage_trend" or event.get("category") == "voltage_trend":
            blocking_errors.append("voltage_trend 进入 abnormal_events。")
        if event.get("in_effective_flight") is False:
            marker = _text(event.get("name") or event.get("evidence"))
            if marker and marker in markdown_text and any(term in markdown_text for term in ("飞行中故障", "飞行中异常", "in-flight failure")):
                blocking_errors.append("有效飞行段外事件被写成飞行中故障。")
        for channel in excluded:
            if channel and channel in body and "saturation" in body.lower():
                blocking_errors.append(f"{channel} 已被排除，但仍出现在饱和异常事件中。")

    for event in actuator.get("saturation_events", []):
        if str(event.get("channel")) in excluded:
            blocking_errors.append(f"{event.get('channel')} 已被排除，但仍出现在 saturation_events。")

    if battery.get("current_reliable") is False:
        forbidden = ("动力负载过大", "电机负载过大", "动力系统负载过大", "负载过大")
        if any(item in markdown_text for item in forbidden):
            blocking_errors.append("电流数据 suspicious 时，报告中出现动力负载强结论。")

    for event in voltage_events:
        if event.get("event_type") == "voltage_trend":
            if event.get("is_abnormal"):
                blocking_errors.append("voltage_trend 被标记为异常。")
            if any(term in markdown_text for term in ("电压下降趋势为高风险", "电压下降趋势属于高风险", "动力负载过大", "动力系统负载过大")):
                blocking_errors.append("报告把 voltage_trend 写成高风险或动力负载强结论。")

    if re.search(r"航向.*(359|360)\s*deg.*(振荡|异常)", markdown_text, flags=re.IGNORECASE):
        blocking_errors.append("报告疑似使用 raw yaw range 作为 360° 振荡证据。")

    if phase.get("confidence") in {"Medium", "Low"}:
        warnings.append("阶段划分置信度不是 High，报告必须保持趋势参考口径。")
    if phase.get("confidence") == "High":
        strong_basis = any("transition" in _text(item.get("basis")).lower() or "vehicle_status" in _text(item.get("basis")).lower() for item in phase.get("segments", []))
        if not strong_basis and kind != "compound_vtol":
            blocking_errors.append("阶段划分缺少强证据但被标为 High。")

    if quality.get("engineering_confidence") != "High":
        warnings.append("工程结论可信度不是 High，风险和根因不得写成确定结论。")
    overall = quality.get("overall_report_confidence") or quality.get("overall") or quality.get("level")
    engineering = quality.get("engineering_confidence")
    if CONFIDENCE_RANK.get(str(overall), -1) > CONFIDENCE_RANK.get(str(engineering), -1):
        blocking_errors.append("overall_report_confidence 高于 engineering_confidence。")
    if actuator.get("mapping_confidence") in {"Low", "Medium"} and re.search(r"(确定|已确认).*(舵机|电机|执行器).*(故障|映射错误|限幅)", markdown_text):
        blocking_errors.append("执行器映射置信度不足时，报告出现确定执行器故障结论。")

    return {
        "passed": not blocking_errors,
        "warnings": warnings,
        "blocking_errors": blocking_errors,
    }

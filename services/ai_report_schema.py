from __future__ import annotations

import copy
from typing import Any


REQUIRED_REPORT_SECTIONS = [
    "报告标题",
    "日志基本信息",
    "数据质量声明",
    "执行摘要",
    "飞行过程概述",
    "关键事件时间线",
    "姿态控制表现分析",
    "高度/速度/航迹分析",
    "电池与电源分析",
    "GPS / EKF / 传感器状态分析",
    "执行器输出与控制余量分析",
    "告警与 failsafe 分析",
    "可能根因分析",
    "是否建议 PID 调参",
    "下一次试飞建议",
    "人工复核清单",
    "数据缺失与不确定性说明",
]

FORBIDDEN_RAW_KEYS = {"raw_samples", "samples", "tables", "dataframe", "ulog", "raw_log", "raw"}


def clamp_list(values: Any, limit: int) -> list[Any]:
    if not isinstance(values, list):
        return []
    return values[:limit]


def safe_text(value: Any, limit: int = 500) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\x00", "").strip()
    return text[:limit]


def strip_for_ai(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if str(key).lower() in FORBIDDEN_RAW_KEYS:
                continue
            result[key] = strip_for_ai(item)
        return result
    if isinstance(value, list):
        return [strip_for_ai(item) for item in value]
    return value


def build_ai_input_summary(report_data: dict[str, Any]) -> dict[str, Any]:
    """Build a compact, verified AI input. Never include raw ULG samples here."""
    verified = copy.deepcopy(
        report_data.get("verified_ai_input_summary")
        or (report_data.get("verified_analysis_summary") or {}).get("verified_ai_input_summary")
        or report_data.get("verified_analysis_summary")
        or {}
    )
    metadata = report_data.get("metadata") or {}
    flight_summary = report_data.get("flight_summary") or {}
    pid_features = report_data.get("pid_input_features") or {}

    data_quality = verified.get("data_quality") or metadata.get("data_quality") or {}
    warning_events = verified.get("warning_events") or {}
    phase_segments = verified.get("phase_segments") or {}
    verified_missing = verified.get("missing_data") or report_data.get("missing_data") or []
    if isinstance(verified_missing, dict):
        missing_items = list(verified_missing.get("items") or [])
        do_not_conclude = list(verified_missing.get("do_not_conclude") or [])
    else:
        missing_items = list(verified_missing or [])
        do_not_conclude = []

    summary = {
        "schemaVersion": "ai-report-input.v1",
        "inputBoundary": "verified_ai_input_summary_only",
        "source": safe_text(report_data.get("source") or metadata.get("source") or "unknown", 240),
        "metadata": {
            "duration_s": metadata.get("duration_s"),
            "start_time": metadata.get("start_time"),
            "end_time": metadata.get("end_time"),
            "topic_count": metadata.get("topic_count"),
            "risk": metadata.get("risk"),
            "success": metadata.get("success"),
            "airframe": metadata.get("airframe"),
        },
        "aircraft_type": verified.get("aircraft_type") or metadata.get("airframe") or {},
        "selected_aircraft_type": verified.get("selected_aircraft_type"),
        "aircraft_type_source": verified.get("aircraft_type_source"),
        "aircraft_type_override": verified.get("aircraft_type_override"),
        "aircraft_type_candidates": verified.get("aircraft_type_candidates") or [],
        "aircraft_type_evidence": verified.get("aircraft_type_evidence") or [],
        "ignored_evidence": verified.get("ignored_evidence") or [],
        "data_quality": data_quality,
        "topic_availability": verified.get("topic_availability") or (data_quality.get("topic_availability") if isinstance(data_quality, dict) else {}),
        "flight_events": verified.get("flight_events") or metadata.get("effective_flight") or {},
        "phase_segments": {
            "confidence": phase_segments.get("confidence"),
            "basis": phase_segments.get("basis"),
            "segments": clamp_list(phase_segments.get("segments"), 12),
        },
        "flight_summary": flight_summary,
        "attitude_metrics": verified.get("attitude_metrics") or {},
        "rate_metrics": pid_features.get("axes") or {},
        "actuator_metrics": verified.get("actuator_metrics") or {},
        "actuator_channel_analysis": verified.get("actuator_channel_analysis") or verified.get("actuator_metrics") or {},
        "battery_metrics": verified.get("battery_metrics") or {},
        "battery_events": clamp_list(verified.get("battery_events"), 30),
        "gps_metrics": verified.get("gps_metrics") or {},
        "ekf_metrics": verified.get("ekf_metrics") or {"available": False, "reason": "日志未包含或当前解析器未提取 EKF 指标"},
        "warning_events": {
            "counts": warning_events.get("counts") or {},
            "rows": clamp_list(warning_events.get("rows"), 25),
        },
        "failsafe_events": [
            item for item in clamp_list(warning_events.get("rows"), 25)
            if "failsafe" in safe_text(item.get("raw", ""), 240).lower()
        ],
        "pid_parameters": clamp_list(verified.get("pid_parameters"), 40),
        "abnormal_events": clamp_list(verified.get("abnormal_events"), 60),
        "root_cause_analysis": clamp_list(verified.get("root_cause_analysis"), 20),
        "root_cause_candidates": [],
        "missing_data": clamp_list(missing_items, 60),
        "do_not_conclude": clamp_list((verified.get("do_not_conclude") or do_not_conclude), 30),
        "unreliable_data": clamp_list(verified.get("unreliable_data"), 30),
        "ai_boundaries": verified.get("ai_boundaries") or [
            "AI only receives verified summary fields.",
            "AI must not create missing data.",
            "AI must obey do_not_conclude and event_type/in_effective_flight fields.",
            "AI must not send MAVLink commands or modify PID in flight.",
        ],
        "algorithm_report_key_findings": {
            "events": [],
            "warnings": [],
            "phase_conclusions": [],
            "attitude_phase_rows": [],
            "note": "AI input intentionally excludes unverified legacy event/root-cause lists.",
        },
    }
    return strip_for_ai(summary)


def _contains_forbidden_raw_data(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in FORBIDDEN_RAW_KEYS:
                return True
            if _contains_forbidden_raw_data(item):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden_raw_data(item) for item in value)
    return False


def validate_ai_input_summary(summary: dict[str, Any]) -> None:
    if not isinstance(summary, dict) or not summary:
        raise ValueError("verified_analysis_summary 为空，无法生成 AI 报告")
    if not summary.get("metadata"):
        raise ValueError("缺少日志元数据，无法生成 AI 报告")
    if summary.get("inputBoundary") != "verified_ai_input_summary_only":
        raise ValueError("AI 输入边界不正确，拒绝生成 AI 报告")
    if _contains_forbidden_raw_data(summary):
        raise ValueError("AI 输入中包含原始样本或未验证数据，拒绝生成 AI 报告")
    quality = summary.get("data_quality") or {}
    if quality.get("score") == 0:
        raise ValueError("数据质量为 0，AI 报告会失去依据，请先检查日志解析结果")
    if not summary.get("flight_events") and not summary.get("attitude_metrics"):
        raise ValueError("缺少可验证飞行事件和姿态指标，AI 报告证据不足")


def normalize_ai_report(raw: dict[str, Any], summary: dict[str, Any], model: str, provider: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("AI 返回内容不是 JSON 对象")
    markdown = safe_text(raw.get("report_markdown") or raw.get("reportMarkdown"), 70000)
    if len(markdown) < 200:
        raise ValueError("AI 返回内容过短，未形成有效报告")
    warnings = [safe_text(item, 260) for item in clamp_list(raw.get("warnings"), 20)]
    missing = summary.get("missing_data") or []
    safety = safe_text(raw.get("safety_note"), 1000) or (
        "AI 报告基于算法分析结果生成，仅用于辅助分析，不能替代人工工程判断；"
        "AI 不直接控制飞控，不在飞行中自动修改 PID 或发送 MAVLink 指令。"
    )
    if "AI" not in safety or "PID" not in safety:
        raise ValueError("AI 报告缺少明确安全边界声明")
    evidence_map = raw.get("evidence_map") or raw.get("evidenceMap") or []
    if evidence_map and not isinstance(evidence_map, list):
        raise ValueError("AI evidence_map 格式错误")
    if not evidence_map:
        raise ValueError("AI 报告缺少 evidence_map，拒绝作为可信 AI 报告输出")
    _validate_ai_report_against_summary(markdown, summary)
    return {
        "schemaVersion": "ai-flight-report.v1",
        "reportType": "ai",
        "provider": provider,
        "model": model,
        "reportMarkdown": markdown,
        "executiveSummary": safe_text(raw.get("executive_summary"), 1400),
        "dataQuality": summary.get("data_quality") or {},
        "missingData": missing,
        "warnings": warnings,
        "safetyNote": safety,
        "evidenceMap": clamp_list(evidence_map, 40),
        "inputSummary": summary,
        "sections": REQUIRED_REPORT_SECTIONS,
    }


def _validate_ai_report_against_summary(markdown: str, summary: dict[str, Any]) -> None:
    actuator = summary.get("actuator_channel_analysis") or summary.get("actuator_metrics") or {}
    excluded = {
        str(item.get("channel"))
        for item in actuator.get("excluded_channels", []) or actuator.get("unused_channels", [])
        if item.get("channel") is not None
    }
    for channel in excluded:
        if not channel or channel not in markdown:
            continue
        for match in _channel_contexts(markdown, channel):
            if any(token in match for token in ("排除", "未使用", "固定通道", "无效通道", "excluded", "unused", "not used")):
                continue
            if any(token in match for token in ("饱和", "异常", "故障", "saturation", "abnormal", "failure")):
                raise ValueError(f"AI 报告引用被排除执行器通道 {channel} 作为异常")
    if "Armed by RC" in markdown and any(token in markdown for token in ("遥控链路异常", "RC 故障", "RC fault", "链路故障")):
        raise ValueError("AI 报告把 Armed by RC 误判为遥控链路故障")
    if any(token in markdown for token in ("航向变化幅度 360", "航向实际范围 360", "yaw range 360", "360° 振荡")):
        raise ValueError("AI 报告把 yaw wrap 写成真实 360° 振荡")
    battery = summary.get("battery_metrics") or {}
    if battery.get("current_reliable") is False and any(token in markdown for token in ("动力负载过大", "电机负载过大", "动力系统负载过大")):
        raise ValueError("AI 报告在电流不可信时输出动力负载强结论")
    if "PID 是根因" in markdown or "PID为根因" in markdown:
        raise ValueError("AI 报告直接将 PID 写成确定根因")
    for event in summary.get("battery_events") or []:
        if event.get("event_type") == "voltage_trend" and any(token in markdown for token in ("电压下降趋势为高风险", "电压下降趋势属于高风险", "动力负载过大", "动力系统负载过大")):
            raise ValueError("AI 报告把 voltage_trend 写成高风险或动力负载强结论")
    for event in summary.get("abnormal_events") or []:
        if event.get("in_effective_flight") is False:
            marker = safe_text(event.get("name") or event.get("evidence"), 80)
            if marker and marker in markdown and any(token in markdown for token in ("飞行中故障", "飞行中异常", "in-flight failure")):
                raise ValueError("AI 报告把有效飞行段外事件写成飞行中故障")


def _channel_contexts(markdown: str, channel: str, radius: int = 80) -> list[str]:
    contexts = []
    start = 0
    while True:
        index = markdown.find(channel, start)
        if index < 0:
            break
        contexts.append(markdown[max(0, index - radius): index + len(channel) + radius])
        start = index + len(channel)
    return contexts

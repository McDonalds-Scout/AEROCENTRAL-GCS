from __future__ import annotations

from typing import Any

from services.feature_extractor import extract_features_from_loaded_log
from services.report_reliability import build_verified_analysis_summary
from services.ulg_analyzer import analyze_log, attitude_phase_analysis, detect_airframe_type, load_flight_log, metric


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    if hasattr(value, "tolist"):
        try:
            return value.tolist()
        except Exception:
            pass
    return value


def _metric(stats: dict[str, Any] | None, key: str, unit: str = "") -> str:
    if not stats:
        return "日志未包含"
    return metric(stats.get(key), unit)


def build_report_data(path, options: dict[str, Any] | None = None) -> dict[str, Any]:
    log = load_flight_log(path)
    analysis = analyze_log(log, options)
    analysis["verified"] = build_verified_analysis_summary(analysis)
    airframe = analysis.get("airframe") or detect_airframe_type(log)
    phase = attitude_phase_analysis(analysis)
    features = extract_features_from_loaded_log(log)
    metrics = analysis["metrics"]

    phase_rows = []
    for row in phase.get("axisRows", [])[1:]:
        if len(row) < 8:
            continue
        phase_rows.append({
            "phase": row[0],
            "axis": row[1],
            "mean": row[2],
            "span": row[3],
            "peak_error": row[4],
            "rms_error": row[5],
            "reversals": row[6],
            "verdict": row[7],
        })

    verified = analysis["verified"]
    verified_algorithm_summary = verified.get("verified_algorithm_summary") or verified
    verified_ai_input_summary = verified.get("verified_ai_input_summary") or verified
    risk_assessment = verified.get("risk_assessment") or {}
    missing_verified = verified.get("missing_data") or {}
    missing_items = list(missing_verified.get("items") or []) if isinstance(missing_verified, dict) else list(missing_verified or [])
    missing = list(dict.fromkeys(missing_items + (features.get("missing") or [])))
    verified_warnings = verified.get("warning_events") or {}
    verified_warning_rows = verified_warnings.get("rows") or []
    verified_warning_count = len([
        item for item in verified_warning_rows
        if item.get("severity") not in {"INFO", "DEBUG"} or item.get("is_anomaly") or item.get("is_abnormal")
    ])
    return _json_safe({
        "schemaVersion": "flight-report-data.v1",
        "source": log.path.name,
        "metadata": {
            "duration_s": round(log.duration_s, 2),
            "start_time": log.start_time,
            "end_time": log.end_time,
            "topic_count": len(log.topics),
            "airframe": airframe,
            "risk": risk_assessment.get("level_cn", "低"),
            "risk_assessment": risk_assessment,
            "success": "存在待复核风险" if risk_assessment.get("level_cn") in {"中", "高"} else "基本正常",
            "data_quality": verified["data_quality"],
            "effective_flight": verified["flight_events"],
        },
        "flight_summary": {
            "max_altitude": _metric(metrics["position"]["altitude"], "max", " m"),
            "max_speed": _metric(metrics["position"]["horizontal_speed"], "max", " m/s"),
            "min_voltage": _metric(metrics["power"]["voltage"], "min", " V"),
            "max_current": _metric(metrics["power"]["current"], "max", " A"),
            "min_satellites": _metric(metrics["gps"]["satellites"], "min", ""),
            "events": len([item for item in verified.get("abnormal_events", []) if item.get("is_abnormal")]),
            "warnings": verified_warning_count,
        },
        "attitude_phase_analysis": {
            "phase_table": phase.get("phaseRows", []),
            "axis_metrics": phase_rows,
            "conclusions": phase.get("conclusions", []),
        },
        "pid_input_features": features,
        "events": [item for item in verified.get("abnormal_events", []) if item.get("is_abnormal")][:40],
        "warnings": verified_warning_rows[:80],
        "root_causes": verified.get("root_cause_analysis", []),
        "missing_data": missing,
        "verified_analysis_summary": verified,
        "verified_algorithm_summary": verified_algorithm_summary,
        "verified_ai_input_summary": verified_ai_input_summary,
    })

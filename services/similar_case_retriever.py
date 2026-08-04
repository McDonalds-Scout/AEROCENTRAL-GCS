from __future__ import annotations

import math
from typing import Any

from services.flight_case_library import list_cases


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _close_score(a: Any, b: Any, scale: float) -> float:
    left = _num(a)
    right = _num(b)
    if left is None or right is None:
        return 0.0
    return max(0.0, 1.0 - abs(left - right) / max(scale, 1e-6))


def _axis_metric(metrics: dict[str, Any], axis: str, key: str) -> Any:
    value = metrics.get(axis) if isinstance(metrics, dict) else None
    if isinstance(value, dict):
        return value.get(key)
    return metrics.get(f"{axis}_{key}") if isinstance(metrics, dict) else None


def retrieve_similar_cases(current: dict[str, Any], limit: int = 5, confirmed_first: bool = True) -> list[dict[str, Any]]:
    records = list_cases({}).get("cases") or []
    current_type = str(current.get("aircraft_type") or ((current.get("metadata") or {}).get("airframe") or {}).get("type") or "").lower()
    current_issues = " ".join(str(item) for item in current.get("main_issues") or current.get("root_cause_candidates") or []).lower()
    current_attitude = current.get("attitude_metrics") or current.get("rate_metrics") or {}
    current_actuator = current.get("actuator_metrics") or {}
    current_gps = current.get("gps_metrics") or {}
    current_ekf = current.get("ekf_metrics") or {}
    scored = []
    for case in records:
        score = 0.0
        evidence = []
        review = case.get("human_review") or {}
        if current_type and current_type == str(case.get("aircraft_type", "")).lower():
            score += 2.0
            evidence.append("aircraft_type matched")
        case_issues = " ".join(str(item) for item in case.get("main_issues") or []).lower()
        if current_issues and case_issues and any(token and token in case_issues for token in current_issues.split()[:8]):
            score += 2.0
            evidence.append("main_issue similar")
        case_attitude = case.get("attitude_metrics") or {}
        for axis in ("roll", "pitch", "yaw"):
            score += _close_score(_axis_metric(current_attitude, axis, "rms"), _axis_metric(case_attitude, axis, "rms"), 8.0) * 0.7
            score += _close_score(_axis_metric(current_attitude, axis, "error_rms"), _axis_metric(case_attitude, axis, "error_rms"), 8.0) * 0.7
        score += _close_score(current_actuator.get("saturation_percent"), (case.get("actuator_metrics") or {}).get("saturation_percent"), 40.0)
        score += _close_score(current_gps.get("quality_score"), (case.get("gps_metrics") or {}).get("quality_score"), 40.0)
        if str(current_ekf.get("warning_type") or "") and str(current_ekf.get("warning_type")) == str((case.get("ekf_metrics") or {}).get("warning_type")):
            score += 1.0
            evidence.append("ekf warning type matched")
        if review.get("status") == "confirmed":
            score += 0.75
            evidence.append("human confirmed")
        if score <= 0:
            continue
        scored.append((score, case, evidence))
    scored.sort(key=lambda item: (item[1].get("human_review", {}).get("status") == "confirmed" if confirmed_first else False, item[0]), reverse=True)
    result = []
    for score, case, evidence in scored[: max(1, min(limit, 8))]:
        review = case.get("human_review") or {}
        result.append({
            "case_id": case.get("case_id"),
            "aircraft_type": case.get("aircraft_type"),
            "main_issue": ", ".join(str(item) for item in (case.get("main_issues") or [])[:3]),
            "similarity_score": round(score, 2),
            "evidence": evidence,
            "human_review_status": review.get("status", "unreviewed"),
            "human_confirmed_root_cause": review.get("confirmed_root_cause", ""),
            "actual_parameter_change": review.get("actual_parameter_changes", []),
            "next_flight_result": review.get("next_flight_result", ""),
            "lessons_learned": review.get("engineer_notes", ""),
        })
    return result

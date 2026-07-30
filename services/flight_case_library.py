from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CASE_PATH = DATA_DIR / "flight_case_library.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_cases() -> list[dict[str, Any]]:
    try:
        data = json.loads(CASE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write_cases(cases: list[dict[str, Any]]) -> None:
    CASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CASE_PATH.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")


def list_cases(filters: dict[str, Any] | None = None) -> dict[str, Any]:
    filters = filters or {}
    cases = _read_cases()
    aircraft_type = str(filters.get("aircraft_type") or "").strip().lower()
    issue = str(filters.get("issue") or "").strip().lower()
    reviewed = str(filters.get("reviewed") or "").strip().lower()
    query = str(filters.get("q") or "").strip().lower()
    result = []
    for case in cases:
        if aircraft_type and str(case.get("aircraft_type", "")).lower() != aircraft_type:
            continue
        if issue:
            issues = " ".join(str(item) for item in case.get("main_issues") or []).lower()
            if issue not in issues:
                continue
        if reviewed and str((case.get("human_review") or {}).get("status", "")).lower() != reviewed:
            continue
        if query:
            haystack = json.dumps({
                "case_id": case.get("case_id"),
                "log_file_name": case.get("log_file_name"),
                "main_issues": case.get("main_issues"),
                "tags": case.get("tags"),
                "ai_report_summary": case.get("ai_report_summary"),
            }, ensure_ascii=False).lower()
            if query not in haystack:
                continue
        result.append(case)
    return {"count": len(result), "cases": result[-200:]}


def _default_human_review() -> dict[str, Any]:
    return {
        "status": "unreviewed",
        "confirmed_root_cause": "",
        "engineer_notes": "",
        "actual_parameter_changes": [],
        "next_flight_result": "",
    }


def normalize_case_record(record: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    case_id = str(record.get("case_id") or f"case_{uuid.uuid4().hex[:10]}")
    return {
        "case_id": case_id,
        "created_at": record.get("created_at") or now,
        "updated_at": now,
        "log_file_name": str(record.get("log_file_name") or ""),
        "aircraft_type": str(record.get("aircraft_type") or "unknown"),
        "flight_summary": record.get("flight_summary") or {},
        "data_quality": record.get("data_quality") or {},
        "main_issues": list(record.get("main_issues") or []),
        "attitude_metrics": record.get("attitude_metrics") or {},
        "actuator_metrics": record.get("actuator_metrics") or {},
        "battery_metrics": record.get("battery_metrics") or {},
        "gps_metrics": record.get("gps_metrics") or {},
        "ekf_metrics": record.get("ekf_metrics") or {},
        "warnings": list(record.get("warnings") or []),
        "pid_parameters_before": record.get("pid_parameters_before") or {},
        "ai_pid_recommendations": list(record.get("ai_pid_recommendations") or []),
        "ai_report_summary": str(record.get("ai_report_summary") or "")[:4000],
        "human_review": {**_default_human_review(), **(record.get("human_review") or {})},
        "tags": list(record.get("tags") or []),
    }


def save_case(record: dict[str, Any]) -> dict[str, Any]:
    case = normalize_case_record(record)
    cases = _read_cases()
    cases = [item for item in cases if item.get("case_id") != case["case_id"]]
    cases.append(case)
    _write_cases(cases[-1000:])
    return case


def update_human_review(case_id: str, human_review: dict[str, Any]) -> dict[str, Any]:
    cases = _read_cases()
    for case in cases:
        if case.get("case_id") == case_id:
            case["human_review"] = {**_default_human_review(), **(case.get("human_review") or {}), **(human_review or {})}
            case["updated_at"] = _now()
            _write_cases(cases)
            return case
    raise ValueError("Case not found.")


def case_from_summary(summary: dict[str, Any], *, ai_report: dict[str, Any] | None = None, pid_advice: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata = summary.get("metadata") or {}
    airframe = metadata.get("airframe") or summary.get("aircraft_type") or {}
    warnings = (summary.get("warning_events") or {}).get("rows") or []
    main_issues = []
    for item in summary.get("root_cause_candidates") or []:
        name = item.get("name") if isinstance(item, dict) else str(item)
        if name:
            main_issues.append(str(name)[:120])
    if not main_issues and warnings:
        main_issues = [str((warnings[0] or {}).get("raw") or (warnings[0] or {}).get("text") or "warning")[:120]]
    return normalize_case_record({
        "log_file_name": summary.get("source") or metadata.get("source") or "",
        "aircraft_type": (airframe.get("type") or airframe.get("class") or airframe.get("label") or "unknown") if isinstance(airframe, dict) else "unknown",
        "flight_summary": summary.get("flight_summary") or {},
        "data_quality": summary.get("data_quality") or {},
        "main_issues": main_issues,
        "attitude_metrics": summary.get("attitude_metrics") or summary.get("rate_metrics") or {},
        "actuator_metrics": summary.get("actuator_metrics") or {},
        "battery_metrics": summary.get("battery_metrics") or {},
        "gps_metrics": summary.get("gps_metrics") or {},
        "ekf_metrics": summary.get("ekf_metrics") or {},
        "warnings": warnings[:20],
        "pid_parameters_before": {item.get("name"): item.get("value") for item in summary.get("pid_parameters") or [] if isinstance(item, dict) and item.get("name")},
        "ai_pid_recommendations": (pid_advice or {}).get("recommendations") or [],
        "ai_report_summary": (ai_report or {}).get("executiveSummary") or (ai_report or {}).get("executive_summary") or "",
        "tags": ["ai_report" if ai_report else "", "ai_pid" if pid_advice else ""],
    })

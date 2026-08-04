from __future__ import annotations

import json
from typing import Any


def build_ai_pid_user_prompt(
    *,
    features: dict[str, Any],
    current_state: dict[str, Any],
    similar_cases: list[dict[str, Any]],
    model_mode: str,
    axis: str,
    symptom: str,
    aggressiveness: int,
    candidate_parameters: dict[str, Any],
) -> str:
    payload = {
        "current_verified_summary": features,
        "current_state": current_state,
        "similar_cases": similar_cases,
        "model_mode": model_mode,
        "axis": axis,
        "symptom": symptom,
        "aggressiveness": aggressiveness,
        "candidate_parameters": candidate_parameters,
        "output_language": "zh-CN",
        "required_schema": {
            "status": "normal | warning | critical",
            "summary": "string",
            "data_quality": "high | medium | low",
            "similar_cases_used": [],
            "diagnosis": [],
            "pid_recommendations": [
                {
                    "parameter": "string",
                    "current_value": 0.0,
                    "suggested_value": 0.0,
                    "change_percent": 0.0,
                    "reason": "string",
                    "evidence": [],
                    "risk_level": "low | medium | high",
                    "apply_allowed": False,
                    "requires_human_confirmation": True,
                    "requires_disarmed": True
                }
            ],
            "do_not_tune_reason": "string | null",
            "pre_tuning_checks": [],
            "next_flight_test_plan": []
        },
        "safety_constraints": [
            "No in-flight PID changes.",
            "No direct MAVLink commands.",
            "Safety gate and human confirmation are mandatory.",
        ],
    }
    return json.dumps(payload, ensure_ascii=False)

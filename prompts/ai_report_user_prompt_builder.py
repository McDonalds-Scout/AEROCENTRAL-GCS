from __future__ import annotations

import json
from typing import Any


def build_ai_report_user_prompt(
    *,
    current_verified_summary: dict[str, Any],
    similar_cases: list[dict[str, Any]],
    model_mode: str,
    detail_level: str,
    audience: str,
    include_pid_advice: bool,
) -> str:
    payload = {
        "verified_ai_input_summary": current_verified_summary,
        "current_verified_summary": current_verified_summary,
        "similar_cases": similar_cases,
        "model_mode": model_mode,
        "detail_level": detail_level,
        "audience": audience,
        "include_pid_advice": bool(include_pid_advice),
        "data_quality": current_verified_summary.get("data_quality") or {},
        "safety_constraints": [
            "AI only provides analysis and recommendations.",
            "AI does not directly control the flight controller.",
            "AI does not modify PID in flight.",
            "Parameter write requires local safety gate and human confirmation.",
            "Unreviewed similar cases are references only, not confirmed facts.",
        ],
        "required_output": {
            "report_markdown": "完整中文 Markdown 报告正文",
            "executive_summary": "3-6 句摘要",
            "warnings": ["人工复核限制和数据限制"],
            "safety_note": "AI 安全边界声明",
            "evidence_map": [
                {
                    "claim": "报告中的主要判断",
                    "source_field": "current_verified_summary 中对应字段路径",
                    "confidence": "high / medium / low",
                }
            ],
        },
        "anti_hallucination_rules": [
            "Do not infer missing topics.",
            "Do not convert Unknown/N/A into a numeric value.",
            "Do not claim front/back transition unless phase_segments contains transition evidence.",
            "Do not claim fixed-wing phases for a multicopter log.",
            "When data_quality.score is low, make conclusions conservative.",
            "Do not cite excluded actuator channels as abnormal saturation events.",
            "Do not classify Armed by RC / Takeoff detected / Landing detected / Disarmed as faults.",
            "Do not describe yaw wrap as real 360 degree oscillation.",
            "Do not classify voltage_trend as high-risk abnormal or power-load evidence.",
            "Do not call an event an in-flight failure when in_effective_flight is false.",
            "When phase_segments.confidence is not High, describe phase results as trend reference only.",
        ],
    }
    return json.dumps(payload, ensure_ascii=False)

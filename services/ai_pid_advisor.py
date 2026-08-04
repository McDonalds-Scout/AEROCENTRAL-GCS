from __future__ import annotations

import math
from typing import Any

from services.safety_gate import load_pid_whitelist


AXIS_TO_PARAMS = {
    "roll": ("MC_ROLLRATE_P", "MC_ROLLRATE_I", "MC_ROLLRATE_D", "FW_RR_P", "FW_RR_I", "FW_RR_D"),
    "pitch": ("MC_PITCHRATE_P", "MC_PITCHRATE_I", "MC_PITCHRATE_D", "FW_PR_P", "FW_PR_I", "FW_PR_D"),
    "yaw": ("MC_YAWRATE_P", "MC_YAWRATE_I", "MC_YAWRATE_D", "FW_YR_P", "FW_YR_I", "FW_YR_D"),
}

AXIS_LABELS = {"roll": "横滚", "pitch": "俯仰", "yaw": "航向"}
DEFAULTS = {
    "MC_ROLLRATE_P": 0.15, "MC_ROLLRATE_I": 0.20, "MC_ROLLRATE_D": 0.003,
    "MC_PITCHRATE_P": 0.15, "MC_PITCHRATE_I": 0.20, "MC_PITCHRATE_D": 0.003,
    "MC_YAWRATE_P": 0.20, "MC_YAWRATE_I": 0.10, "MC_YAWRATE_D": 0.0,
    "FW_RR_P": 0.05, "FW_RR_I": 0.05, "FW_RR_D": 0.001,
    "FW_PR_P": 0.05, "FW_PR_I": 0.05, "FW_PR_D": 0.001,
    "FW_YR_P": 0.08, "FW_YR_I": 0.03, "FW_YR_D": 0.0,
}


def _finite(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _parameter_current(name: str, features: dict[str, Any], state: dict[str, Any] | None) -> float:
    params = {}
    params.update(features.get("parameters") or {})
    if state:
        params.update(state.get("parameters") or {})
    value = params.get(name)
    return float(value) if _finite(value) else DEFAULTS.get(name, 0.01)


def _axis_score(axis_features: dict[str, Any]) -> tuple[float, list[str]]:
    evidence = []
    score = 0.0
    error_rms = axis_features.get("error_rms")
    error_peak = axis_features.get("error_peak")
    if _finite(error_rms):
        score += min(1.0, float(error_rms) / 8.0)
        evidence.append(f"RMS 跟踪误差 {float(error_rms):.2f} deg")
    if _finite(error_peak):
        score += min(0.6, float(error_peak) / 30.0)
        evidence.append(f"峰值误差 {float(error_peak):.2f} deg")
    if axis_features.get("oscillation_detected"):
        score += 0.8
        evidence.append("检测到振荡/反向次数偏多")
    stats = axis_features.get("actual_stats") or {}
    if _finite(stats.get("span")) and float(stats["span"]) > 25:
        score += 0.3
        evidence.append(f"姿态变化范围 {float(stats['span']):.1f} deg")
    return score, evidence


def build_pid_advice(
    features: dict[str, Any],
    axis: str = "roll",
    symptom: str = "balanced",
    aggressiveness: int = 2,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    axis = axis if axis in AXIS_TO_PARAMS else "roll"
    aggressiveness = max(1, min(5, int(aggressiveness or 2)))
    whitelist = load_pid_whitelist()
    axis_features = (features.get("axes") or {}).get(axis, {})
    score, evidence = _axis_score(axis_features)
    missing = list(dict.fromkeys(features.get("missing") or []))
    data_quality = max(0.15, min(1.0, 1.0 - len(missing) * 0.08))
    confidence = round(max(0.2, min(0.92, 0.35 + score * 0.18 + data_quality * 0.25)), 2)

    reasons = []
    risks = []
    if evidence:
        reasons.extend(evidence)
    else:
        reasons.append("有效误差数据不足，采用保守微调建议")
    if missing:
        risks.append("缺失数据：" + "、".join(missing[:8]))
    if features.get("servo_output_saturation_percent") is not None and features["servo_output_saturation_percent"] > 8:
        risks.append("舵机/执行器输出接近饱和，优先检查机械行程和混控")
    if features.get("battery_voltage_min") is not None and features["battery_voltage_min"] < 10.5:
        risks.append("电压偏低会影响控制响应，调参前建议排除电源因素")
    if features.get("vibration_level") is not None and features["vibration_level"] > 3:
        risks.append("振动水平偏高，调 PID 前建议先处理振动源")

    base_step = 0.015 + aggressiveness * 0.007
    p_scale = i_scale = d_scale = 1.0
    if symptom == "sluggish":
        p_scale += base_step
        reasons.append("选择了响应慢，优先小幅提高 P")
    elif symptom == "oscillation" or axis_features.get("oscillation_detected"):
        p_scale -= base_step
        d_scale += base_step * 0.6
        reasons.append("存在振荡迹象，降低 P 并谨慎增加 D 阻尼")
    elif symptom == "overshoot":
        p_scale -= base_step * 0.7
        d_scale += base_step
        reasons.append("存在超调迹象，降低 P 并增加 D 抑制过冲")
    elif symptom == "drift":
        i_scale += base_step
        reasons.append("存在稳态误差/漂移，优先小幅提高 I")
    else:
        if score > 1.0:
            p_scale -= base_step * 0.5
            d_scale += base_step * 0.6
            reasons.append("综合指标显示姿态波动偏大，采用偏稳的阻尼方案")
        else:
            p_scale += base_step * 0.3
            reasons.append("未见强异常，采用非常保守的响应提升")

    recommendations = []
    for name in AXIS_TO_PARAMS[axis]:
        if name not in whitelist:
            continue
        current = _parameter_current(name, features, state)
        factor = p_scale if name.endswith("_P") else i_scale if name.endswith("_I") else d_scale
        rule = whitelist[name]
        suggested = round(_clamp(current * factor, rule["min_value"], rule["max_value"]), 6)
        delta = round(((suggested - current) / current * 100), 2) if abs(current) > 1e-9 else 0.0
        if abs(delta) < 0.05:
            continue
        recommendations.append({
            "name": name,
            "current": round(current, 6),
            "suggested": suggested,
            "deltaPercent": delta,
            "confidence": confidence,
            "reason": "、".join(reasons[-2:]),
        })

    return {
        "schemaVersion": "pid-advisor.v1",
        "source": features.get("source", "unknown"),
        "axis": axis,
        "axisLabel": AXIS_LABELS[axis],
        "symptom": symptom,
        "confidence": confidence,
        "dataQuality": round(data_quality, 2),
        "featureSummary": {
            "duration_s": features.get("duration_s"),
            "oscillation_detected": features.get("oscillation_detected"),
            "servo_output_saturation_percent": features.get("servo_output_saturation_percent"),
            "motor_output_saturation_percent": features.get("motor_output_saturation_percent"),
            "battery_voltage_min": features.get("battery_voltage_min"),
            "gps_satellite_min": features.get("gps_satellite_min"),
            "vibration_level": features.get("vibration_level"),
            "axis": axis_features,
        },
        "recommendations": recommendations,
        "reasons": reasons,
        "risks": risks,
        "missing": missing,
        "warning": "AI PID Advisor 只输出建议；写入飞控前必须通过白名单、幅度、安全状态和人工确认。",
    }


def ai_status() -> dict[str, Any]:
    whitelist = load_pid_whitelist()
    try:
        from services.llm_pid_advisor import provider_status
        llm = provider_status()
    except Exception:
        llm = {
            "provider": "local",
            "openaiConfigured": False,
            "openaiModel": "",
            "openaiBase": "",
            "openaiEnabled": False,
        }
    return {
        "available": True,
        "mode": "local-deterministic-advisor",
        "provider": "local",
        "message": "默认使用本地工程规则和日志特征生成结构化建议；配置 OPENAI_API_KEY 后可选择 ChatGPT/OpenAI 辅助分析。",
        "whitelistCount": len(whitelist),
        "llm": llm,
    }

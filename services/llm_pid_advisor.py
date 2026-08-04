from __future__ import annotations

import json
import math
import os
import socket
import urllib.error
import urllib.request
from typing import Any

from services.ai_pid_advisor import AXIS_LABELS
from services.ai_model_config import DEFAULT_BASE_URL, resolve_model
from services.flight_case_library import case_from_summary, save_case
from services.safety_gate import load_pid_whitelist
from services.similar_case_retriever import retrieve_similar_cases
from services.token_usage import record_ai_usage
from prompts.ai_pid_system_prompt import SYSTEM_PROMPT as PID_SYSTEM_PROMPT
from prompts.ai_pid_user_prompt_builder import build_ai_pid_user_prompt


DEFAULT_MODEL = ""
ALLOWED_PROVIDERS = {"openai", "chatgpt"}
DEFAULT_LOCAL_PROXY_PORTS = "7890,7897,7899,10809,10808,1087,1080"


def _finite(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number)


def _supports_custom_temperature(model: str) -> bool:
    return not model.strip().lower().startswith("gpt-5")


def _is_local_port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def _auto_proxy_candidates() -> list[str]:
    ports = os.environ.get("OPENAI_PROXY_PORTS", DEFAULT_LOCAL_PROXY_PORTS)
    candidates = []
    for raw in ports.split(","):
        raw = raw.strip()
        if not raw.isdigit():
            continue
        port = int(raw)
        if _is_local_port_open(port):
            candidates.append(f"http://127.0.0.1:{port}")
    return candidates


def _proxy_candidates() -> list[str | None]:
    proxy = os.environ.get("OPENAI_PROXY", "").strip()
    if proxy and proxy.lower() != "auto":
        return [proxy, None]

    candidates: list[str | None] = [None]
    if os.environ.get("OPENAI_AUTO_PROXY", "1").strip() != "0":
        candidates.extend(_auto_proxy_candidates())
    return list(dict.fromkeys(candidates))


def provider_status() -> dict[str, Any]:
    runtime = resolve_model("ai_pid_advisor", os.environ.get("AI_MODE"))
    provider = runtime["provider"]
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    model = runtime["model"]
    base_url = runtime["base_url"]
    proxy = os.environ.get("OPENAI_PROXY", "").strip()
    auto_proxies = _auto_proxy_candidates() if os.environ.get("OPENAI_AUTO_PROXY", "1").strip() != "0" else []
    return {
        "provider": provider,
        "openaiConfigured": bool(api_key),
        "openaiModel": model,
        "mode": runtime["mode"],
        "modeLabel": runtime["mode_label"],
        "costLevel": runtime["cost_level"],
        "openaiBase": base_url,
        "openaiProxy": proxy or ("auto" if auto_proxies else ""),
        "autoProxyCandidates": auto_proxies,
        "openaiEnabled": provider in ALLOWED_PROVIDERS and bool(api_key) and bool(model),
    }


def _safe_feature_payload(features: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "source",
        "duration_s",
        "flight_phase",
        "axes",
        "roll_error_rms",
        "pitch_error_rms",
        "yaw_error_rms",
        "roll_error_peak",
        "pitch_error_peak",
        "yaw_error_peak",
        "overshoot_percent",
        "settling_time_estimate",
        "oscillation_detected",
        "oscillation_frequency_hz",
        "servo_output_saturation_percent",
        "motor_output_saturation_percent",
        "battery_voltage_min",
        "battery_voltage_drop_rate",
        "battery_current_max",
        "gps_satellite_min",
        "gps_hdop_max",
        "ekf_warning_count",
        "vibration_level",
        "parameters",
        "missing",
    }
    payload = {key: features.get(key) for key in allowed if key in features}
    messages = []
    for item in list(features.get("messages") or [])[:12]:
        text = str(item.get("text", ""))[:180] if isinstance(item, dict) else str(item)[:180]
        if text:
            messages.append({"text": text, "level": item.get("level", "") if isinstance(item, dict) else ""})
    if messages:
        payload["messages_sample"] = messages
    return payload


def _combined_parameters(features: dict[str, Any], state: dict[str, Any] | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {}
    params.update(features.get("parameters") or {})
    if state:
        params.update(state.get("parameters") or {})
    return {str(key).strip().upper(): value for key, value in params.items()}


def _axis_parameter_prefixes(axis: str) -> tuple[str, ...]:
    return {
        "roll": ("MC_ROLLRATE_", "FW_RR_"),
        "pitch": ("MC_PITCHRATE_", "FW_PR_"),
        "yaw": ("MC_YAWRATE_", "FW_YR_"),
    }.get(axis, tuple())


def _candidate_whitelist(features: dict[str, Any], state: dict[str, Any] | None, axis: str) -> dict[str, Any]:
    whitelist = load_pid_whitelist()
    params = _combined_parameters(features, state)
    prefixes = _axis_parameter_prefixes(axis)
    candidates = {
        name: rule
        for name, rule in whitelist.items()
        if name in params and (not prefixes or name.startswith(prefixes))
    }
    if candidates:
        return candidates
    axis_whitelist = {
        name: rule
        for name, rule in whitelist.items()
        if not prefixes or name.startswith(prefixes)
    }
    return axis_whitelist or whitelist


def _candidate_parameter_payload(whitelist: dict[str, Any], features: dict[str, Any], state: dict[str, Any] | None) -> dict[str, Any]:
    params = _combined_parameters(features, state)
    result = {}
    for name, rule in whitelist.items():
        value = params.get(name)
        if _finite(value):
            result[name] = {
                "current": float(value),
                "min": rule.get("min_value"),
                "max": rule.get("max_value"),
                "aircraftType": rule.get("aircraft_type"),
                "description": rule.get("description", ""),
            }
    return result


def _schema_instruction(axis: str, symptom: str, aggressiveness: int, whitelist: dict[str, Any]) -> str:
    allowed_params = ", ".join(sorted(whitelist))
    return (
        "你是 PX4 无人机 PID 调参工程助手。只使用给定的结构化特征 JSON，不得编造缺失数据。"
        "你的输出必须是严格 JSON，不要 Markdown，不要解释性前后缀。"
        "AI 只提供建议，不直接控制飞控，不允许飞行中自动改参。"
        f"调参轴：{axis}，症状：{symptom}，调节强度：{aggressiveness}/5。"
        f"只能建议以下白名单参数：{allowed_params}。"
        "单次建议必须保守，优先 3%-8% 的小幅变化；数据质量不足时可以返回空 recommendations。"
        "返回字段：schemaVersion, source, axis, axisLabel, symptom, confidence, dataQuality, "
        "recommendations, reasons, risks, missing, warning。"
        "recommendations 每项字段：name, current, suggested, deltaPercent, confidence, reason。"
    )


def _request_body(model: str, system: str, user: str) -> dict[str, Any]:
    body = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    configured_temperature = os.environ.get("OPENAI_TEMPERATURE", "").strip()
    if configured_temperature:
        body["temperature"] = float(configured_temperature)
    elif _supports_custom_temperature(model):
        body["temperature"] = 0.1
    return body


def _openai_opener(proxy: str | None):
    if proxy:
        return urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener()


def _post_chat_completion(api_key: str, base_url: str, model: str, system: str, user: str, timeout: float) -> tuple[dict[str, Any], dict[str, Any] | None]:
    body = _request_body(model, system, user)
    url = f"{base_url.rstrip('/')}/chat/completions"
    errors = []

    for proxy in _proxy_candidates():
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with _openai_opener(proxy).open(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:1000]
            raise RuntimeError(f"OpenAI API 返回 {error.code}: {detail}") from error
        except urllib.error.URLError as error:
            label = proxy or "直连"
            errors.append(f"{label}: {error.reason}")
    else:
        hint = "；如果你在国内网络，请打开 Clash/V2Ray，并在 .env 设置 OPENAI_PROXY=http://127.0.0.1:代理端口"
        raise RuntimeError(f"OpenAI API 网络连接失败，已尝试 {len(errors)} 种连接方式：{' | '.join(errors)}{hint}")

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise RuntimeError("OpenAI API 返回格式不符合 Chat Completions 预期") from error
    try:
        return json.loads(content), data.get("usage")
    except json.JSONDecodeError as error:
        raise RuntimeError("OpenAI API 未返回有效 JSON") from error


def _current_value(name: str, features: dict[str, Any], state: dict[str, Any] | None, fallback: Any = None) -> float | None:
    params = _combined_parameters(features, state)
    value = params.get(name, fallback)
    return float(value) if _finite(value) else None


def _normalize_advice(raw: dict[str, Any], features: dict[str, Any], axis: str, symptom: str, state: dict[str, Any] | None, model: str, whitelist: dict[str, Any] | None = None) -> dict[str, Any]:
    whitelist = whitelist or load_pid_whitelist()
    recommendations = []
    for item in raw.get("recommendations") or []:
        name = str(item.get("name", "")).strip().upper()
        if name not in whitelist:
            continue
        current = _current_value(name, features, state, item.get("current"))
        suggested = item.get("suggested")
        if current is None or not _finite(suggested):
            continue
        suggested = float(suggested)
        rule = whitelist[name]
        suggested = max(float(rule["min_value"]), min(float(rule["max_value"]), suggested))
        delta = ((suggested - current) / current * 100) if abs(current) > 1e-9 else 0.0
        item_confidence = item.get("confidence", raw.get("confidence", 0.45))
        recommendations.append({
            "name": name,
            "current": round(current, 6),
            "suggested": round(suggested, 6),
            "deltaPercent": round(delta, 2),
            "confidence": round(float(item_confidence), 2) if _finite(item_confidence) else 0.45,
            "reason": str(item.get("reason", ""))[:220],
        })

    feature_missing = list(dict.fromkeys(features.get("missing") or []))
    feature_quality = max(0.15, 1.0 - len(feature_missing) * 0.08)
    raw_missing = [str(item)[:240] for item in (raw.get("missing") or []) if item]
    confidence = raw.get("confidence", 0.45)
    data_quality = max(raw.get("dataQuality", 0) if _finite(raw.get("dataQuality")) else 0, feature_quality)
    risks = [str(item)[:240] for item in (raw.get("risks") or [])[:8]]
    if raw_missing and not feature_missing:
        risks.append("AI 额外希望补充的证据：" + "、".join(raw_missing[:6]))
    return {
        "schemaVersion": "pid-advisor.v1",
        "source": features.get("source", raw.get("source", "unknown")),
        "axis": axis,
        "axisLabel": AXIS_LABELS.get(axis, axis),
        "symptom": symptom,
        "confidence": round(float(confidence), 2) if _finite(confidence) else 0.45,
        "dataQuality": round(float(data_quality), 2) if _finite(data_quality) else 0.5,
        "featureSummary": {
            "duration_s": features.get("duration_s"),
            "oscillation_detected": features.get("oscillation_detected"),
            "servo_output_saturation_percent": features.get("servo_output_saturation_percent"),
            "motor_output_saturation_percent": features.get("motor_output_saturation_percent"),
            "battery_voltage_min": features.get("battery_voltage_min"),
            "gps_satellite_min": features.get("gps_satellite_min"),
            "vibration_level": features.get("vibration_level"),
            "axis": (features.get("axes") or {}).get(axis, {}),
        },
        "recommendations": recommendations,
        "reasons": [str(item)[:240] for item in (raw.get("reasons") or [])[:8]],
        "risks": risks,
        "missing": feature_missing,
        "warning": "ChatGPT/OpenAI 只输出 PID 建议；写入飞控前仍必须通过本地白名单、安全门、人工确认和回滚机制。",
        "provider": "openai",
        "providerLabel": f"ChatGPT / OpenAI - {model}",
        "llmAssisted": True,
    }


def build_openai_pid_advice(
    features: dict[str, Any],
    axis: str,
    symptom: str,
    aggressiveness: int,
    state: dict[str, Any] | None = None,
    mode: str | None = None,
    use_similar_cases: bool = True,
) -> dict[str, Any]:
    runtime = resolve_model("ai_pid_advisor", mode)
    status = provider_status()
    if not status["openaiConfigured"]:
        raise RuntimeError("尚未配置 OPENAI_API_KEY，无法调用 ChatGPT/OpenAI。")
    if not runtime["model"]:
        raise RuntimeError("AI PID model is not configured. Please set AI_PID_MODEL or AI_MODEL_DEFAULT.")
    model = runtime["model"]
    base_url = runtime["base_url"]
    api_key = os.environ["OPENAI_API_KEY"].strip()
    whitelist = _candidate_whitelist(features, state, axis)
    safe_features = _safe_feature_payload(features)
    safe_features["candidateParameters"] = _candidate_parameter_payload(whitelist, features, state)
    similar_cases = retrieve_similar_cases(safe_features, limit=5) if use_similar_cases else []
    system = PID_SYSTEM_PROMPT + "\n\n" + _schema_instruction(axis, symptom, aggressiveness, whitelist)
    user = build_ai_pid_user_prompt(
        features=safe_features,
        current_state={"parameters": (state or {}).get("parameters", {})},
        similar_cases=similar_cases,
        model_mode=runtime["mode"],
        axis=axis,
        symptom=symptom,
        aggressiveness=aggressiveness,
        candidate_parameters=safe_features["candidateParameters"],
    )
    raw, usage = _post_chat_completion(
        api_key=api_key,
        base_url=base_url,
        model=model,
        system=system,
        user=user,
        timeout=float(os.environ.get("OPENAI_TIMEOUT", "45") or 45),
    )
    advice = _normalize_advice(raw, features, axis, symptom, state, model, whitelist=whitelist)
    usage_record = record_ai_usage(
        analysis_id=str(features.get("source") or ""),
        log_file_name=str(features.get("source") or ""),
        task_type="ai_pid_advisor",
        mode=runtime["mode"],
        model=model,
        provider=runtime["provider"],
        usage=usage,
        prompt={"system": system, "user": user},
        output=raw,
        success=True,
    )
    if not advice.get("recommendations"):
        from services.ai_pid_advisor import build_pid_advice

        local = build_pid_advice(features, axis=axis, symptom=symptom, aggressiveness=aggressiveness, state=state)
        advice["recommendations"] = [
            item for item in (local.get("recommendations") or [])
            if str(item.get("name", "")).strip().upper() in whitelist
        ]
        advice["reasons"] = list(dict.fromkeys((advice.get("reasons") or []) + (local.get("reasons") or [])))
        if advice["recommendations"]:
            advice["risks"] = list(dict.fromkeys((advice.get("risks") or []) + ["OpenAI 未返回可写入的真实机型参数，已采用本地日志特征兜底建议。"]))
    advice["modelMode"] = runtime["mode"]
    advice["modelModeLabel"] = runtime["mode_label"]
    advice["costLevel"] = runtime["cost_level"]
    advice["similarCases"] = similar_cases
    advice["tokenUsage"] = usage_record
    advice["providerLabel"] = f"ChatGPT / OpenAI - {model} ({runtime['mode_label']})"
    try:
        saved_case = save_case(case_from_summary(safe_features, pid_advice=advice))
        advice["caseId"] = saved_case.get("case_id")
    except Exception as error:
        advice.setdefault("risks", []).append(f"案例库保存失败：{error}")
    return advice

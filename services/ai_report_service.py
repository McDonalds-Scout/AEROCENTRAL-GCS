from __future__ import annotations

import json
import os
import socket
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
import urllib.error
import urllib.request

from services.ai_report_cache import AiReportCache
from services.ai_report_schema import build_ai_input_summary, normalize_ai_report, validate_ai_input_summary
from services.ai_model_config import DEFAULT_BASE_URL, public_ai_status, resolve_model
from services.flight_case_library import case_from_summary, save_case
from services.llm_pid_advisor import _openai_opener, _proxy_candidates, _request_body
from services.report_data_builder import build_report_data
from services.similar_case_retriever import retrieve_similar_cases
from services.token_usage import record_ai_usage
from services.token_usage import usage_summary
from prompts.ai_report_system_prompt import SYSTEM_PROMPT
from prompts.ai_report_user_prompt_builder import build_ai_report_user_prompt


DEFAULT_AI_REPORT_PROVIDER = "openai"
DEFAULT_AI_REPORT_MODEL = ""
DEFAULT_AI_REPORT_FALLBACK_MODELS = ""
DEFAULT_AI_REPORT_TIMEOUT = "120"
DEFAULT_AI_REPORT_MAX_INPUT_CHARS = 18000
DEFAULT_AI_REPORT_VALIDATION_RETRIES = "3"


def _ai_cache(output_dir: Path) -> AiReportCache:
    cache_dir = Path(output_dir) / "ai"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return AiReportCache(cache_dir / "ai_report_cache.json")


def _legacy_ai_cache(output_dir: Path) -> AiReportCache:
    return AiReportCache(Path(output_dir) / "ai_report_cache.json")


def _compact_for_openai(value: Any, depth: int = 0, key: str = "") -> Any:
    """Keep the verified summary small enough for a stable API call."""
    max_string = 900 if depth < 4 else 420
    if isinstance(value, str):
        text = value.replace("\x00", "").strip()
        return text if len(text) <= max_string else text[:max_string] + "...[truncated]"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        list_limits = {
            "rows": 12,
            "events": 12,
            "warnings": 12,
            "attitude_phase_rows": 24,
            "pid_parameters": 18,
            "root_cause_candidates": 8,
            "missing_data": 24,
            "unreliable_data": 12,
        }
        limit = list_limits.get(key, 10 if depth >= 3 else 16)
        return [_compact_for_openai(item, depth + 1, key) for item in value[:limit]]
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        for child_key, child_value in value.items():
            if child_key in {"inputSummary", "raw", "raw_samples", "samples", "tables"}:
                continue
            compact[str(child_key)] = _compact_for_openai(child_value, depth + 1, str(child_key))
        return compact
    return str(value)[:300]


def _openai_summary(summary: dict[str, Any]) -> dict[str, Any]:
    compact = _compact_for_openai(summary)
    text = json.dumps(compact, ensure_ascii=False)
    max_chars = int(os.environ.get("AI_REPORT_MAX_INPUT_CHARS", DEFAULT_AI_REPORT_MAX_INPUT_CHARS) or DEFAULT_AI_REPORT_MAX_INPUT_CHARS)
    if len(text) <= max_chars:
        return compact
    smaller = {
        "schemaVersion": summary.get("schemaVersion"),
        "source": summary.get("source"),
        "metadata": summary.get("metadata"),
        "aircraft_type": summary.get("aircraft_type"),
        "data_quality": summary.get("data_quality"),
        "flight_events": _compact_for_openai(summary.get("flight_events"), key="flight_events"),
        "phase_segments": _compact_for_openai(summary.get("phase_segments"), key="phase_segments"),
        "flight_summary": summary.get("flight_summary"),
        "attitude_metrics": summary.get("attitude_metrics"),
        "battery_metrics": summary.get("battery_metrics"),
        "gps_metrics": summary.get("gps_metrics"),
        "ekf_metrics": summary.get("ekf_metrics"),
        "warning_events": _compact_for_openai(summary.get("warning_events"), key="warning_events"),
        "pid_parameters": _compact_for_openai(summary.get("pid_parameters"), key="pid_parameters"),
        "missing_data": _compact_for_openai(summary.get("missing_data"), key="missing_data"),
        "algorithm_report_key_findings": _compact_for_openai(summary.get("algorithm_report_key_findings"), key="algorithm_report_key_findings"),
    }
    return smaller


def ai_report_status() -> dict[str, Any]:
    runtime = resolve_model(
        "ai_report",
        os.environ.get("AI_REPORT_MODE") or os.environ.get("AI_MODE") or "standard_analysis",
    )
    provider = runtime["provider"] or DEFAULT_AI_REPORT_PROVIDER
    model = runtime["model"]
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    base_url = runtime["base_url"] or DEFAULT_BASE_URL
    fallback_models = _fallback_models(model)
    configuration_warnings = _configuration_warnings(provider, model, fallback_models, bool(api_key))
    return {
        "ai_enabled": provider in {"openai", "chatgpt"},
        "api_key_configured": bool(api_key),
        "model": model,
        "mode": runtime["mode"],
        "mode_label": runtime["mode_label"],
        "cost_level": runtime["cost_level"],
        "modes": public_ai_status("ai_report").get("modes", []),
        "provider": provider,
        "base_url": base_url,
        "engine": "openai_chat_completions",
        "fallback_models": fallback_models,
        "configuration_warnings": configuration_warnings,
        "timeout_s": float(os.environ.get("OPENAI_TIMEOUT", DEFAULT_AI_REPORT_TIMEOUT) or DEFAULT_AI_REPORT_TIMEOUT),
        "max_input_chars": int(os.environ.get("AI_REPORT_MAX_INPUT_CHARS", DEFAULT_AI_REPORT_MAX_INPUT_CHARS) or DEFAULT_AI_REPORT_MAX_INPUT_CHARS),
        "available": provider in {"openai", "chatgpt"} and bool(api_key) and bool(model),
    }


def _fallback_models(primary_model: str) -> list[str]:
    raw = os.environ.get("AI_REPORT_FALLBACK_MODELS", DEFAULT_AI_REPORT_FALLBACK_MODELS)
    models = [item.strip() for item in raw.split(",") if item.strip()]
    return [item for item in dict.fromkeys([primary_model, *models]) if item]


def _configuration_warnings(provider: str, model: str, fallback_models: list[str], has_api_key: bool) -> list[str]:
    warnings = []
    if provider not in {"openai", "chatgpt"}:
        warnings.append("AI_REPORT_PROVIDER/AI_PROVIDER 不是 openai，AI 报告会走本地回退。")
    if not has_api_key:
        warnings.append("OPENAI_API_KEY 未配置，无法调用 OpenAI，只能生成本地 verified summary 回退报告。")
    if not model:
        warnings.append("AI_REPORT_MODEL 未配置，无法确定 OpenAI 模型。")
    if model and len(fallback_models) <= 1:
        warnings.append("AI_REPORT_FALLBACK_MODELS 没有配置不同的备用模型；主模型不可用时只能本地回退。")
    if model and model.lower().startswith(("gpt-5.4", "gpt-5.5")):
        warnings.append("当前模型需要你的 OpenAI 账号具备访问权限；如果生成失败，请查看返回的 model_not_found/permission 错误。")
    return warnings


def _model_error_should_retry(message: str) -> bool:
    lower = message.lower()
    return any(token in lower for token in (
        "model_not_found",
        "does not exist",
        "invalid model",
        "unsupported",
        "not found",
    ))


def _post_openai_report_json(api_key: str, base_url: str, model: str, system: str, user: str, timeout: float) -> tuple[dict[str, Any], dict[str, Any] | None]:
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
            detail = error.read().decode("utf-8", errors="replace")[:1200]
            raise RuntimeError(f"OpenAI API 返回 {error.code}: {detail}") from error
        except (urllib.error.URLError, socket.timeout, TimeoutError) as error:
            label = proxy or "direct"
            reason = getattr(error, "reason", None) or str(error)
            errors.append(f"{label}: {reason}")
    else:
        raise RuntimeError("OpenAI API 网络连接失败：" + " | ".join(errors))

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise RuntimeError("OpenAI API 返回格式不符合 Chat Completions 预期") from error
    try:
        return json.loads(content), data.get("usage")
    except json.JSONDecodeError as error:
        raise RuntimeError("OpenAI API 未返回有效 JSON") from error


def _merge_usage(primary: dict[str, Any] | None, extra: dict[str, Any] | None) -> dict[str, Any] | None:
    if not primary:
        return extra
    if not extra:
        return primary
    merged = dict(primary)
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if isinstance(primary.get(key), (int, float)) or isinstance(extra.get(key), (int, float)):
            merged[key] = int(primary.get(key) or 0) + int(extra.get(key) or 0)
    return merged


def _validation_repair_prompt(
    *,
    raw_summary: dict[str, Any],
    rejected_report: dict[str, Any],
    validation_error: str,
    validation_history: list[str],
    detail_level: str,
    audience: str,
    include_pid_advice: bool,
) -> str:
    """Ask the model to repair an already generated report instead of failing outright."""
    payload = {
        "task": "repair_ai_report_json_after_local_validation_failed",
        "validation_error": validation_error,
        "all_validation_errors_so_far": validation_history,
        "verified_ai_input_summary": _openai_summary(raw_summary),
        "rejected_report_json": _compact_for_openai(rejected_report),
        "detail_level": detail_level,
        "audience": audience,
        "include_pid_advice": bool(include_pid_advice),
        "strict_repair_rules": [
            "Return strict JSON only.",
            "Keep the same required keys: report_markdown, executive_summary, warnings, safety_note, evidence_map.",
            "Remove or weaken any conclusion named in validation_error.",
            "If current_reliable is false or suspicious, do not write strong power-load, motor-load, or over-current conclusions.",
            "Do not convert excluded/unused actuator channels into actuator faults.",
            "Do not describe yaw/heading wrap as a real 360 degree oscillation or heading oscillation.",
            "Avoid the phrases: 360° 振荡, 航向变化幅度 360, 航向实际范围 360, yaw range 360.",
            "Do not classify operation_info events as faults.",
            "When evidence is weak, write it as trend reference and require manual review.",
            "AI only provides analysis; it does not control the flight controller or modify PID in flight.",
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def _normalize_with_repair(
    *,
    raw: dict[str, Any],
    raw_summary: dict[str, Any],
    candidate_model: str,
    provider: str,
    base_url: str,
    options: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    try:
        return normalize_ai_report(raw, raw_summary, candidate_model, provider), None
    except ValueError as first_error:
        max_retries = int(os.environ.get("AI_REPORT_VALIDATION_RETRIES", DEFAULT_AI_REPORT_VALIDATION_RETRIES) or 0)
        if max_retries <= 0:
            raise RuntimeError(f"OpenAI 返回内容未通过可信度校验：{first_error}") from first_error
        last_error: Exception = first_error
        validation_history = [str(first_error)]
        repair_usage: dict[str, Any] | None = None
        repair_system = (
            SYSTEM_PROMPT
            + "\n\n你正在修正一份未通过本地可信度校验的 AI 报告。"
            + "必须优先满足 validation_error，删除或弱化不被 verified summary 支持的结论。"
        )
        for _attempt in range(max_retries):
            repair_user = _validation_repair_prompt(
                raw_summary=raw_summary,
                rejected_report=raw,
                validation_error=str(last_error),
                validation_history=validation_history,
                detail_level=options.get("detailLevel", "standard"),
                audience=options.get("audience", "engineering"),
                include_pid_advice=options.get("includePidAdvice", True),
            )
            repaired_raw, usage = _post_openai_report_json(
                api_key=os.environ["OPENAI_API_KEY"].strip(),
                base_url=base_url,
                model=candidate_model,
                system=repair_system,
                user=repair_user,
                timeout=float(os.environ.get("OPENAI_TIMEOUT", DEFAULT_AI_REPORT_TIMEOUT) or DEFAULT_AI_REPORT_TIMEOUT),
            )
            repair_usage = _merge_usage(repair_usage, usage)
            try:
                repaired = normalize_ai_report(repaired_raw, raw_summary, candidate_model, provider)
                repaired.setdefault("warnings", []).append(
                    f"首轮 AI 输出未通过可信度校验，系统已自动修正后通过校验。首轮问题：{first_error}"
                )
                return repaired, repair_usage
            except ValueError as repair_error:
                last_error = repair_error
                validation_history.append(str(repair_error))
                raw = repaired_raw
        raise RuntimeError(
            f"OpenAI 返回内容未通过可信度校验：{first_error}；自动修正仍失败：{last_error}"
        ) from last_error


def _openai_report(raw_summary: dict[str, Any], options: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    runtime = resolve_model("ai_report", options.get("modelMode") or options.get("mode"))
    status = ai_report_status()
    if not status["api_key_configured"]:
        raise RuntimeError("OPENAI_API_KEY is not configured.")
    if runtime["provider"] not in {"openai", "chatgpt"} or not runtime["model"]:
        raise RuntimeError("AI provider is not enabled. Please set AI_PROVIDER=openai.")

    model = runtime["model"]
    base_url = runtime["base_url"]
    ai_summary = _openai_summary(raw_summary)
    similar_cases = retrieve_similar_cases(ai_summary, limit=5) if options.get("useSimilarCases", True) else []
    user_prompt = build_ai_report_user_prompt(
        current_verified_summary=ai_summary,
        similar_cases=similar_cases,
        model_mode=runtime["mode"],
        detail_level=options.get("detailLevel", "standard"),
        audience=options.get("audience", "engineering"),
        include_pid_advice=options.get("includePidAdvice", True),
    )
    errors = []
    for candidate_model in _fallback_models(model):
        try:
            raw, usage = _post_openai_report_json(
                api_key=os.environ["OPENAI_API_KEY"].strip(),
                base_url=base_url,
                model=candidate_model,
                system=SYSTEM_PROMPT,
                user=user_prompt,
                timeout=float(os.environ.get("OPENAI_TIMEOUT", DEFAULT_AI_REPORT_TIMEOUT) or DEFAULT_AI_REPORT_TIMEOUT),
            )
            report, repair_usage = _normalize_with_repair(
                raw=raw,
                raw_summary=raw_summary,
                candidate_model=candidate_model,
                provider=runtime["provider"],
                base_url=base_url,
                options=options,
            )
            usage = _merge_usage(usage, repair_usage)
            report["modelMode"] = runtime["mode"]
            report["modelModeLabel"] = runtime["mode_label"]
            report["costLevel"] = runtime["cost_level"]
            report["similarCases"] = similar_cases
            report["promptUser"] = user_prompt
            if candidate_model != model:
                report.setdefault("warnings", []).append(f"配置模型 {model} 不可用，已自动改用 {candidate_model} 生成 AI 报告。")
            return report, usage
        except RuntimeError as error:
            message = str(error)
            errors.append(f"{candidate_model}: {message}")
            if not _model_error_should_retry(message):
                raise
    raise RuntimeError("OpenAI 报告生成失败，已尝试模型：" + " | ".join(errors))


def _fmt(value: Any, suffix: str = "") -> str:
    if value is None or value == "":
        return "日志未包含该数据"
    return f"{value}{suffix}"


def _local_fallback_report(summary: dict[str, Any], reason: str, options: dict[str, Any]) -> dict[str, Any]:
    metadata = summary.get("metadata") or {}
    quality = summary.get("data_quality") or {}
    flight = summary.get("flight_summary") or {}
    findings = summary.get("algorithm_report_key_findings") or {}
    events = findings.get("events") or []
    warnings = (summary.get("warning_events") or {}).get("rows") or []
    missing = summary.get("missing_data") or []
    airframe = metadata.get("airframe") or {}
    phase = summary.get("phase_segments") or {}
    attitude = summary.get("attitude_metrics") or {}
    battery = summary.get("battery_metrics") or {}
    gps = summary.get("gps_metrics") or {}
    actuator = summary.get("actuator_metrics") or {}

    event_lines = "\n".join(
        f"- {item.get('time', item.get('time_s', 'N/A'))}: {item.get('name') or item.get('raw') or item.get('text') or '事件'}，证据：{item.get('evidence') or item.get('impact') or '日志事件'}"
        for item in events[:10]
    ) or "- 日志未检测到可确认的高风险事件。"
    warning_lines = "\n".join(
        f"- {item.get('time', 'N/A')} [{item.get('severity', item.get('level', 'INFO'))}] {item.get('raw', item.get('text', ''))}"
        for item in warnings[:12]
    ) or "- 日志未包含 WARNING/ERROR/CRITICAL 级别告警，或当前解析器未提取到告警。"
    missing_lines = "\n".join(f"- {item}" for item in missing[:20]) or "- 未发现关键缺失项。"
    phase_lines = "\n".join(
        f"- {item.get('name')}: {_fmt(item.get('start_s'), 's')} - {_fmt(item.get('end_s'), 's')}，依据：{item.get('basis', '日志阶段规则')}"
        for item in (phase.get("segments") or [])[:10]
    ) or "- 阶段划分置信度不足，报告仅按全局飞行统计描述。"
    attitude_lines = "\n".join(
        f"- {axis}: RMS={_fmt(metrics.get('rms'), 'deg')}，峰值误差={_fmt(metrics.get('max'), 'deg')}，可用={metrics.get('available', False)}"
        for axis, metrics in attitude.items()
        if isinstance(metrics, dict)
    ) or "- 日志未包含完整姿态 setpoint/actual 对齐数据。"

    markdown = f"""# AI 飞行日志分析报告

## 日志基本信息
- 日志文件：{summary.get('source', 'unknown')}
- 机型识别：{airframe.get('label', '日志未包含该数据')}
- 机型置信度：{airframe.get('confidence', '日志未包含该数据')}
- 日志时长：{_fmt(metadata.get('duration_s'), 's')}
- Topic 数量：{_fmt(metadata.get('topic_count'))}
- 系统风险：{metadata.get('risk', '日志未包含该数据')}

## 数据质量声明
- 数据质量等级：{quality.get('level', '日志未包含该数据')}
- 数据质量评分：{quality.get('score', '日志未包含该数据')}
- 限制说明：{"；".join(quality.get('limitations') or []) or "未发现明确限制项"}

## 执行摘要
本报告基于本地算法生成的 verified summary 自动整理。系统已尝试调用 OpenAI 生成自然语言报告，但调用失败，因此当前正文采用本地工程规则回退生成。该报告仍只使用日志中已经解析出的字段，不编造缺失数据。

## 飞行过程概述
- 最大高度：{flight.get('max_altitude', '日志未包含该数据')}
- 最大速度：{flight.get('max_speed', '日志未包含该数据')}
- 最低电压：{flight.get('min_voltage', '日志未包含该数据')}
- 最大电流：{flight.get('max_current', '日志未包含该数据')}
- 最低卫星数：{flight.get('min_satellites', '日志未包含该数据')}

## 关键事件时间线
{event_lines}

## 姿态控制表现分析
{attitude_lines}

## 阶段分析
{phase_lines}

## 电池与电源分析
- 电池数据质量：{battery.get('quality', '日志未包含该数据')}
- 说明：{battery.get('reason', '日志未包含该数据')}

## GPS / EKF / 传感器状态分析
- GPS 数据质量：{gps.get('quality', '日志未包含该数据')}
- GPS 说明：{gps.get('reason', '日志未包含该数据')}
- EKF：{(summary.get('ekf_metrics') or {}).get('reason', '日志未包含该数据')}

## 执行器输出与控制余量分析
- 有效通道数：{len(actuator.get('valid_channels') or [])}
- 风险通道数：{len(actuator.get('risk_channels') or [])}
- 说明：如存在持续饱和，需要结合实机舵面/电机映射人工复核，不能仅凭日志直接归因为 PID。

## 告警与 failsafe 分析
{warning_lines}

## 是否建议 PID 调参
当前报告不建议直接自动改 PID。若姿态误差、执行器输出、电池、电机振动和舵面映射均经过人工复核后仍指向控制响应问题，才建议做小幅、可回滚的地面调参验证。

## 下一次试飞建议
- 先确认日志记录完整性，尤其是姿态 setpoint、实际姿态、执行器输出、电池、GPS、EKF 相关 topic。
- 若存在执行器饱和，先检查舵机/电机映射、机械限位、电源和结构振动。
- 若要调参，必须人工确认参数变化，并保留回滚快照。

## 人工复核清单
- 飞控日志是否包含完整参数快照。
- 姿态 setpoint 与 actual 是否时间对齐。
- 电池电压、电流是否可信。
- GPS/EKF 是否有明显异常。
- 舵机/电机映射是否与实机一致。

## 数据缺失与不确定性说明
{missing_lines}

## AI 引擎状态
OpenAI/ChatGPT 调用失败，已按 AI 调参模块相同思路回退到本地 verified summary 报告生成。失败原因：{reason}
"""
    return {
        "schemaVersion": "ai-flight-report.v1",
        "reportType": "ai",
        "provider": "local",
        "model": "local-fallback-after-openai-failure",
        "reportMarkdown": markdown,
        "executiveSummary": "OpenAI 调用失败，已基于 verified summary 生成本地工程回退报告。",
        "dataQuality": summary.get("data_quality") or {},
        "missingData": missing,
        "warnings": [f"OpenAI/ChatGPT 调用失败，已回退本地报告：{reason}"],
        "safetyNote": "AI 报告仅用于辅助分析，不能替代人工工程判断；AI 不直接控制飞控，不在飞行中自动修改 PID。",
        "inputSummary": summary,
        "sections": [],
    }


def generate_ai_report(upload_path: Path, output_dir: Path, options: dict[str, Any] | None = None) -> dict[str, Any]:
    from services.ai_report_exporter import export_ai_report_files

    options = options or {}
    report_data = build_report_data(upload_path)
    summary = build_ai_input_summary(report_data)
    validate_ai_input_summary(summary)

    try:
        ai_report, usage = _openai_report(summary, options)
        engine = "openai_chat_completions"
    except Exception as error:
        ai_report = _local_fallback_report(summary, str(error), options)
        usage = None
        engine = "local_fallback_after_openai_failure"
    report_id = uuid.uuid4().hex[:12]
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stem = f"flight_report_ai_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}_{report_id}"
    files = export_ai_report_files(ai_report, Path(output_dir), stem)
    usage_record = record_ai_usage(
        report_id=report_id,
        log_file_name=str(summary.get("source") or Path(upload_path).name),
        task_type="ai_report",
        mode=ai_report.get("modelMode") or options.get("modelMode") or "",
        model=ai_report.get("model", ""),
        provider=ai_report.get("provider", ""),
        usage=usage,
        prompt=ai_report.get("inputSummary") or summary,
        output=ai_report.get("reportMarkdown"),
        success=engine == "openai_chat_completions",
        error_message="" if engine == "openai_chat_completions" else "; ".join(ai_report.get("warnings") or []),
    )
    try:
        saved_case = save_case(case_from_summary(summary, ai_report=ai_report))
        case_id = saved_case.get("case_id")
    except Exception as error:
        case_id = ""
        ai_report.setdefault("warnings", []).append(f"案例库保存失败：{error}")

    result = {
        "report_id": report_id,
        "report_type": "ai",
        "model": ai_report["model"],
        "provider": ai_report["provider"],
        "model_mode": ai_report.get("modelMode") or options.get("modelMode") or "",
        "model_mode_label": ai_report.get("modelModeLabel") or "",
        "cost_level": ai_report.get("costLevel") or "",
        "engine": engine,
        "openai_success": engine == "openai_chat_completions",
        "ai_engine_status": "openai_generated" if engine == "openai_chat_completions" else "openai_failed_local_verified_fallback",
        "generated_at": generated_at,
        "source": summary.get("source"),
        "report_markdown": ai_report["reportMarkdown"],
        "preview": ai_report["reportMarkdown"][:18000],
        "data_quality": ai_report["dataQuality"],
        "warnings": ai_report["warnings"],
        "missing_data": ai_report["missingData"],
        "safety_note": ai_report["safetyNote"],
        "token_usage": usage_record,
        "usage_summary": usage_summary(),
        "similar_cases": ai_report.get("similarCases") or [],
        "case_id": case_id,
        "input_summary": summary,
        "files": files,
    }
    _ai_cache(Path(output_dir)).put(report_id, result)
    return result


def get_ai_report(output_dir: Path, report_id: str) -> dict[str, Any]:
    report = _ai_cache(Path(output_dir)).get(report_id) or _legacy_ai_cache(Path(output_dir)).get(report_id)
    if not report:
        raise ValueError("AI report not found.")
    return report


def export_existing_ai_report(output_dir: Path, report_id: str, file_format: str = "docx") -> dict[str, Any]:
    report = get_ai_report(output_dir, report_id)
    files = report.get("files") or {}
    key = {
        "docx": "docxUrl",
        "word": "docxUrl",
        "markdown": "markdownUrl",
        "md": "markdownUrl",
        "html": "htmlUrl",
    }.get(str(file_format).lower(), "docxUrl")
    if key not in files:
        raise ValueError("Requested AI report export format is unavailable.")
    return {
        "report_id": report_id,
        "format": file_format,
        "url": files[key],
        "files": files,
    }

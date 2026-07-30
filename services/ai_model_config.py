from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "ai_models.json"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
ALLOWED_OPENAI_PROVIDERS = {"openai", "chatgpt"}


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def ai_model_config() -> dict[str, Any]:
    return _read_json(CONFIG_PATH, {"provider": "openai", "modes": {}, "tasks": {}})


def normalize_ai_mode(mode: str | None, task_type: str = "ai_report") -> str:
    config = ai_model_config()
    modes = config.get("modes") or {}
    tasks = config.get("tasks") or {}
    requested = (mode or "").strip()
    if requested in modes:
        return requested
    task_default = (tasks.get(task_type) or {}).get("default_mode")
    if task_default in modes:
        return task_default
    return "standard_analysis" if "standard_analysis" in modes else next(iter(modes), "standard_analysis")


def resolve_model(task_type: str, mode: str | None = None) -> dict[str, Any]:
    config = ai_model_config()
    modes = config.get("modes") or {}
    tasks = config.get("tasks") or {}
    requested_mode = (mode or "").strip()
    explicit_mode = requested_mode in modes
    mode_key = normalize_ai_mode(mode, task_type)
    mode_config = modes.get(mode_key) or {}
    task_config = tasks.get(task_type) or {}
    env_order = []
    if not explicit_mode:
        env_order.append(task_config.get("env"))
    env_order.append(mode_config.get("env"))
    model = ""
    for env_name in env_order:
        if not env_name:
            continue
        model = os.environ.get(str(env_name), "").strip()
        if model:
            break
    if not model:
        model = str(mode_config.get("default_model") or "").strip()
    provider = os.environ.get("AI_PROVIDER", str(config.get("provider") or "openai")).strip().lower() or "openai"
    report_provider = os.environ.get("AI_REPORT_PROVIDER", "").strip().lower()
    if task_type == "ai_report" and report_provider:
        provider = report_provider
    base_url = os.environ.get("OPENAI_API_BASE", DEFAULT_BASE_URL).strip().rstrip("/") or DEFAULT_BASE_URL
    return {
        "task_type": task_type,
        "mode": mode_key,
        "mode_label": mode_config.get("label", mode_key),
        "description": mode_config.get("description", ""),
        "cost_level": mode_config.get("cost_level", "medium"),
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "api_key_configured": bool(os.environ.get("OPENAI_API_KEY", "").strip()),
        "available": provider in ALLOWED_OPENAI_PROVIDERS and bool(os.environ.get("OPENAI_API_KEY", "").strip()) and bool(model),
    }


def public_ai_status() -> dict[str, Any]:
    config = ai_model_config()
    modes = []
    for key, value in (config.get("modes") or {}).items():
        resolved = resolve_model("ai_report", key)
        modes.append({
            "mode": key,
            "label": value.get("label", key),
            "description": value.get("description", ""),
            "cost_level": value.get("cost_level", "medium"),
            "model": resolved.get("model"),
        })
    return {
        "provider": os.environ.get("AI_PROVIDER", str(config.get("provider") or "openai")).strip().lower() or "openai",
        "openai_configured": bool(os.environ.get("OPENAI_API_KEY", "").strip()),
        "base_url": os.environ.get("OPENAI_API_BASE", DEFAULT_BASE_URL).strip().rstrip("/") or DEFAULT_BASE_URL,
        "modes": modes,
        "tasks": {
            "ai_report": resolve_model("ai_report", "standard_analysis"),
            "ai_pid_advisor": resolve_model("ai_pid_advisor", "standard_analysis"),
            "fast_check": resolve_model("fast_check"),
            "final_review": resolve_model("final_review"),
        },
        "safety": [
            "AI 只做分析和建议，不直接控制飞控。",
            "AI 不发送 MAVLink command，不自动 Arm/Disarm/RTL/Land。",
            "AI 不在飞行中自动修改 PID；参数写入必须安全门和人工确认。",
            "历史案例只用于本地检索辅助分析，不做 fine-tuning。",
        ],
    }

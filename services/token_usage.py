from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
USAGE_PATH = DATA_DIR / "token_usage.json"
PRICING_PATH = ROOT / "config" / "model_pricing.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def estimate_tokens(text: str | dict[str, Any] | list[Any] | None) -> int:
    if text is None:
        return 0
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False)
    if not text:
        return 0
    ascii_count = sum(1 for char in text if ord(char) < 128)
    non_ascii_count = len(text) - ascii_count
    return max(1, math.ceil(ascii_count / 4 + non_ascii_count / 1.8))


def pricing_for(model: str) -> dict[str, float]:
    pricing = _read_json(PRICING_PATH, {})
    if model in pricing:
        return pricing[model]
    for key, value in pricing.items():
        if model.startswith(key):
            return value
    return {"input_per_1m": 0.0, "output_per_1m": 0.0}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = pricing_for(model)
    return round((input_tokens / 1_000_000) * float(pricing.get("input_per_1m", 0)) + (output_tokens / 1_000_000) * float(pricing.get("output_per_1m", 0)), 6)


def normalize_usage(usage: dict[str, Any] | None, model: str, prompt: Any = None, output: Any = None) -> dict[str, Any]:
    usage = usage or {}
    input_tokens = usage.get("prompt_tokens") or usage.get("input_tokens")
    output_tokens = usage.get("completion_tokens") or usage.get("output_tokens")
    estimated = False
    if input_tokens is None:
        input_tokens = estimate_tokens(prompt)
        estimated = True
    if output_tokens is None:
        output_tokens = estimate_tokens(output)
        estimated = True
    input_tokens = int(input_tokens or 0)
    output_tokens = int(output_tokens or 0)
    total_tokens = int(usage.get("total_tokens") or input_tokens + output_tokens)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "estimated": bool(estimated),
        "estimated_cost_usd": estimate_cost(model, input_tokens, output_tokens),
    }


def record_ai_usage(
    *,
    analysis_id: str | None = None,
    report_id: str | None = None,
    log_file_name: str = "",
    task_type: str,
    model: str,
    provider: str,
    usage: dict[str, Any] | None,
    prompt: Any = None,
    output: Any = None,
    success: bool,
    error_message: str = "",
    mode: str = "",
) -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    normalized = normalize_usage(usage, model, prompt=prompt, output=output)
    record = {
        "id": uuid.uuid4().hex[:12],
        "analysis_id": analysis_id or report_id or uuid.uuid4().hex[:12],
        "report_id": report_id or "",
        "log_file_name": log_file_name,
        "task_type": task_type,
        "mode": mode,
        "model": model,
        "provider": provider,
        **normalized,
        "created_at": _now(),
        "success": bool(success),
        "error_message": str(error_message or "")[:1000],
    }
    records = _read_json(USAGE_PATH, [])
    if not isinstance(records, list):
        records = []
    records.append(record)
    _write_json(USAGE_PATH, records[-2000:])
    return record


def usage_summary() -> dict[str, Any]:
    records = _read_json(USAGE_PATH, [])
    if not isinstance(records, list):
        records = []
    now = datetime.now(timezone.utc)
    today_prefix = now.strftime("%Y-%m-%d")
    month_prefix = now.strftime("%Y-%m")

    def cost_of(items: list[dict[str, Any]]) -> float:
        return round(sum(float(item.get("estimated_cost_usd") or 0) for item in items), 6)

    today = [item for item in records if str(item.get("created_at", "")).startswith(today_prefix)]
    month = [item for item in records if str(item.get("created_at", "")).startswith(month_prefix)]
    return {
        "records": records[-50:],
        "today_cost_usd": cost_of(today),
        "month_cost_usd": cost_of(month),
        "today_calls": len(today),
        "month_calls": len(month),
        "note": "最终成本以 API 平台账单为准，此处为估算值。",
    }

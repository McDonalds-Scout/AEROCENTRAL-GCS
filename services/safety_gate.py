from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
WHITELIST_PATH = ROOT / "config" / "pid_parameter_whitelist.json"
MAX_BATCH_SIZE = 12
CONFIRMATION_TEXT = "确认写入参数"


def _finite(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number)


def load_pid_whitelist(path: Path | None = None) -> dict[str, dict[str, Any]]:
    path = path or WHITELIST_PATH
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if isinstance(raw, dict):
        items = raw.get("parameters", [])
    else:
        items = raw
    result = {}
    for item in items:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        name = str(item["name"]).strip().upper()
        result[name] = {
            "name": name,
            "min_value": float(item.get("min_value", item.get("min", 0))),
            "max_value": float(item.get("max_value", item.get("max", 1))),
            "max_change_percent": float(item.get("max_change_percent", 10)),
            "recommended_change_percent": float(item.get("recommended_change_percent", 5)),
            "aircraft_type": item.get("aircraft_type", "any"),
            "description": item.get("description", ""),
        }
    return result


def normalize_recommendations(recommendations: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    normalized = []
    for item in recommendations or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("parameter") or "").strip().upper()
        if not name:
            continue
        current = item.get("current")
        suggested = item.get("suggested", item.get("value"))
        normalized.append({
            **item,
            "name": name,
            "current": float(current) if _finite(current) else None,
            "suggested": float(suggested) if _finite(suggested) else None,
        })
    return normalized


def safety_gate(
    recommendations: list[dict[str, Any]] | None,
    state: Any,
    confirmation: str = "",
    require_confirmation: bool = True,
    whitelist: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    whitelist = whitelist or load_pid_whitelist()
    state_dict = state.to_dict() if hasattr(state, "to_dict") else dict(state or {})
    recs = normalize_recommendations(recommendations)
    checks = []
    accepted = []
    rejected = []

    def check(check_id: str, passed: bool, message: str, blocking: bool = True):
        checks.append({"id": check_id, "passed": bool(passed), "blocking": bool(blocking), "message": message})

    check("batch_size", len(recs) <= MAX_BATCH_SIZE, f"一次最多允许写入 {MAX_BATCH_SIZE} 个 PID 参数")
    check("has_recommendations", bool(recs), "必须先生成 PID 建议")
    check("disarmed", not state_dict.get("armed"), "飞控必须处于未解锁状态")
    if require_confirmation:
        check("confirmation", confirmation == CONFIRMATION_TEXT, f"确认文本必须为：{CONFIRMATION_TEXT}")

    for item in recs:
        name = item["name"]
        suggested = item.get("suggested")
        current = item.get("current")
        rule = whitelist.get(name)
        item_checks = []

        if not rule:
            item_checks.append({"passed": False, "message": f"{name} 不在 PID 白名单内"})
        if suggested is None or not _finite(suggested):
            item_checks.append({"passed": False, "message": f"{name} 缺少有效建议值"})
        if current is None or not _finite(current):
            item_checks.append({"passed": False, "message": f"{name} 缺少当前值，不能计算调整幅度"})

        if rule and suggested is not None and _finite(suggested):
            if not (rule["min_value"] <= float(suggested) <= rule["max_value"]):
                item_checks.append({
                    "passed": False,
                    "message": f"{name}={suggested:g} 超出白名单范围 {rule['min_value']:g}-{rule['max_value']:g}",
                })

        if rule and current is not None and suggested is not None and _finite(current) and _finite(suggested):
            base = abs(float(current)) if abs(float(current)) > 1e-9 else max(abs(float(suggested)), 1e-9)
            delta_percent = abs(float(suggested) - float(current)) / base * 100
            item["deltaPercent"] = round((float(suggested) - float(current)) / base * 100, 2)
            if delta_percent > rule["max_change_percent"]:
                item_checks.append({
                    "passed": False,
                    "message": f"{name} 单次调整 {delta_percent:.1f}% 超过安全上限 {rule['max_change_percent']:.1f}%",
                })
            elif delta_percent > rule["recommended_change_percent"]:
                item_checks.append({
                    "passed": True,
                    "message": f"{name} 调整 {delta_percent:.1f}% 高于建议微调幅度，建议先小范围试飞",
                    "warning": True,
                })

        if not item_checks:
            item_checks.append({"passed": True, "message": f"{name} 通过白名单、范围和幅度检查"})

        enriched = {**item, "checks": item_checks, "rule": rule}
        if all(check_item.get("passed") for check_item in item_checks if not check_item.get("warning")):
            accepted.append(enriched)
        else:
            rejected.append(enriched)

    blocking_failed = [item for item in checks if item["blocking"] and not item["passed"]]
    allowed = not blocking_failed and bool(accepted) and not rejected
    return {
        "allowed": allowed,
        "accepted": accepted,
        "rejected": rejected,
        "checks": checks,
        "summary": "安全门通过，可以进入参数写入队列" if allowed else "安全门未通过，禁止自动写入参数",
        "confirmationText": CONFIRMATION_TEXT,
        "whitelistCount": len(whitelist),
    }

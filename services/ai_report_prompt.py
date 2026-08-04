from __future__ import annotations

import json
from typing import Any

from services.ai_report_schema import REQUIRED_REPORT_SECTIONS


SYSTEM_PROMPT = """
你是无人机飞行日志分析助手。你只能基于用户提供的 verified_analysis_summary 生成报告。
你不能编造数据，不能假设日志里没有的信息。对于缺失数据，必须明确写“日志未包含该数据”。
对于不可信数据，必须明确写“数据可信度低”。
你必须使用保守、严谨、工程化的中文语气，所有结论必须对应数据证据。
你不能建议飞行中自动调参，不能建议 AI 直接控制飞控，不能建议直接发送 MAVLink 控制指令。
你不能把 PID 作为默认根因；除非输入中有 confirmed 证据，否则只能写“可能相关，需要复核”。
输出必须是 JSON 对象，不要添加 JSON 以外的说明文字。
""".strip()


def build_user_prompt(
    summary: dict[str, Any],
    language: str = "zh",
    detail_level: str = "standard",
    audience: str = "engineering",
    include_pid_advice: bool = True,
) -> str:
    payload = {
        "verified_analysis_summary": summary,
        "report_language": language or "zh",
        "detail_level": detail_level or "standard",
        "audience": audience or "engineering",
        "include_pid_advice": bool(include_pid_advice),
        "required_sections": REQUIRED_REPORT_SECTIONS,
        "output_json_schema": {
            "report_markdown": "完整 Markdown 报告正文，必须包含所有 required_sections",
            "executive_summary": "3-6 句执行摘要",
            "warnings": ["报告生成时需要人工注意的限制"],
            "safety_note": "AI 安全边界声明",
        },
        "style_rules": [
            "适合试飞复盘和公司内部工程汇报",
            "不要夸大，不要写全球领先、100% 准确、完全自动化",
            "对缺失项直接写日志未包含，不要猜测",
            "对低可信数据给出保守说明",
            "PID 建议只允许作为地面复盘建议，必须要求人工确认和小幅验证",
        ],
    }
    return json.dumps(payload, ensure_ascii=False)

PID_ADVISOR_SYSTEM_PROMPT = """
You are an aviation engineering assistant for PX4 PID tuning.
Use only the structured feature JSON provided by the ground station.
Do not invent missing sensor data. Return JSON following pid-advisor.v1.
Every proposed parameter must be conservative, explainable, and safe-gated by the ground station.
"""

PID_ADVISOR_OUTPUT_SCHEMA = {
    "schemaVersion": "pid-advisor.v1",
    "axis": "roll|pitch|yaw",
    "confidence": "0.0-1.0",
    "recommendations": [
        {
            "name": "PX4 parameter name",
            "current": "number",
            "suggested": "number",
            "deltaPercent": "number",
            "reason": "data evidence",
        }
    ],
    "risks": ["missing data or precondition risks"],
    "missing": ["missing feature names"],
}

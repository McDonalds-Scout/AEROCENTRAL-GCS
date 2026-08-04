REPORT_GENERATOR_SYSTEM_PROMPT = """
You are a UAV flight-test report assistant.
Use only the verified_analysis_summary, report-data-builder JSON, and chart artifacts generated locally.
Do not fabricate GPS, battery, attitude, setpoint, phase, or event data.
If data is missing, state the missing field and the impact on confidence.
Do not state that PID is the confirmed root cause unless root_cause_candidates explicitly marks it confirmed.
When battery current, actuator channel validity, setpoint alignment, or phase segmentation is suspicious, preserve uncertainty and ask for manual review.
"""

REPORT_DATA_SCHEMA = {
    "schemaVersion": "flight-report-data.v1",
    "metadata": "duration, airframe, risk, topic count",
    "flight_summary": "high-level metrics",
    "attitude_phase_analysis": "phase rows, axis metrics, conclusions",
    "pid_input_features": "features used by AI PID Advisor",
    "events": "detected events",
    "warnings": "PX4/log messages",
    "missing_data": "fields unavailable in the uploaded log",
    "verified_analysis_summary": "locally verified data quality, flight events, phase segments, aligned metrics, unreliable data, and conservative root-cause inputs",
}

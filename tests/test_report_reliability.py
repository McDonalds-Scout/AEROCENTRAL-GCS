from __future__ import annotations

import numpy as np

from services.report_reliability import (
    actuator_channel_metrics,
    battery_quality,
    build_verified_abnormal_events,
    build_phase_segments,
    build_verified_analysis_summary,
    circular_range_deg,
    circular_error_deg,
    classify_log_messages,
    merged_voltage_drop_events,
    topic_availability_summary,
    verified_risk_assessment,
)
from services.ai_report_schema import build_ai_input_summary, normalize_ai_report, validate_ai_input_summary
from services.report_consistency import check_report_consistency
from services.ulg_analyzer import detect_airframe_type


class Topic:
    def __init__(self, time_s, fields):
        self.time_s = np.asarray(time_s, dtype=float)
        self.fields = {key: np.asarray(value, dtype=float) for key, value in fields.items()}


class Log:
    def __init__(self, messages=None, duration_s=100.0, topics=None, parameters=None):
        self.messages = messages or []
        self.duration_s = duration_s
        self.topics = topics or {}
        self.parameters = parameters or {}


def test_circular_yaw_error_wraps_short_way():
    error = circular_error_deg(np.array([359.0, 1.0]), np.array([1.0, 359.0]))
    assert np.max(np.abs(error)) <= 2.1


def test_low_current_is_suspicious():
    topic = Topic(np.arange(120), {"voltage": np.linspace(24.8, 23.9, 120), "current": np.full(120, 0.02)})
    result = battery_quality(topic)
    assert result["quality"] == "suspicious"
    assert result["current_reliable"] is False


def test_info_messages_are_not_anomalies():
    result = classify_log_messages(Log([
        {"time_s": 12.0, "level": "INFO", "text": "Armed by RC (switch)"},
        {"time_s": 20.0, "level": "INFO", "text": "Takeoff detected"},
    ]))
    assert result["counts"]["warning_count"] == 0
    assert result["counts"]["error_count"] == 0
    assert all(not item["is_anomaly"] for item in result["rows"])


def test_armed_by_rc_switch_is_operation_info_not_rc_fault():
    result = classify_log_messages(Log([
        {"time_s": 12.0, "level": "INFO", "text": "[commander] Armed by RC (switch)"},
    ]))
    row = result["rows"][0]
    assert row["category"] == "operation_info"
    assert row["is_abnormal"] is False
    assert "遥控链路异常" not in row["meaning"]


def test_unused_actuator_channel_not_saturation():
    topic = Topic(
        np.arange(50),
        {
            "output[0]": np.linspace(1200, 1800, 50),
            "output[5]": np.full(50, 0.0),
            "output[6]": np.full(50, 1500.0),
        },
    )
    result = actuator_channel_metrics(topic)
    valid_names = {item["channel"] for item in result["valid_channels"]}
    unused_names = {item["channel"] for item in result["unused_channels"]}
    assert "output[0]" in valid_names
    assert "output[5]" in unused_names
    assert "output[6]" in unused_names
    assert not result["risk_channels"]
    assert "output[5]" in {item["channel"] for item in result["excluded_channels"]}
    assert not result["saturation_events"]


def test_excluded_actuator_channel_does_not_enter_abnormal_events():
    topic = Topic(
        np.arange(80),
        {
            "output[0]": np.r_[np.full(30, 1000.0), np.linspace(1200, 1800, 50)],
            "output[5]": np.full(80, 0.0),
        },
    )
    log = Log(duration_s=8.0, topics={}, parameters={})
    analysis = {
        "log": log,
        "topics": {
            "attitude": None,
            "attitude_sp": None,
            "position": None,
            "gps": None,
            "battery": None,
            "actuator": topic,
            "sensor": None,
            "rates": None,
            "rates_sp": None,
        },
        "airframe": {"kind": "multicopter", "confidence": 0.85},
        "missing": [],
    }
    verified = build_verified_analysis_summary(analysis)
    assert "output[5]" in {item["channel"] for item in verified["actuator_channel_analysis"]["excluded_channels"]}
    assert all("output[5]" not in str(item) for item in verified["abnormal_events"])


def test_topic_availability_reports_missing_fields():
    analysis = {
        "topics": {
            "attitude": Topic([0, 0.05, 0.1], {"roll": [0, 1, 2], "pitch": [0, 0, 0]}),
        }
    }
    result = topic_availability_summary(analysis)
    assert result["profiles"]["attitude"]["available"] is True
    assert "yaw" in result["profiles"]["attitude"]["missing_fields"]
    assert "gps" in result["missing_topics"]


def test_vtol_without_transition_does_not_create_transition_phases():
    position = Topic(
        np.arange(0, 20, dtype=float),
        {"altitude": [0, 0, 1, 3, 5, 5, 5, 5, 4, 3, 2, 1, 0, 0, 0, 0, 0, 0, 0, 0]},
    )
    analysis = {
        "log": Log(duration_s=20.0, topics={}),
        "topics": {"position": position},
        "airframe": {"kind": "compound_vtol", "confidence": 0.9},
    }
    phases = build_phase_segments(
        analysis,
        {
            "effective_flight_start_time": 0.0,
            "effective_flight_end_time": 19.0,
            "takeoff_detected_time": None,
            "disarmed_time": None,
            "timeline": [],
        },
    )
    names = " ".join(item["name"] for item in phases["segments"])
    assert "Front transition" not in names
    assert "Back transition" not in names


def test_fixed_wing_phase_does_not_contain_hover():
    position = Topic(
        np.arange(0, 20, dtype=float),
        {"altitude": [0, 0, 1, 3, 5, 8, 10, 10, 10, 9, 8, 5, 2, 1, 0, 0, 0, 0, 0, 0]},
    )
    analysis = {
        "log": Log(duration_s=20.0, topics={}),
        "topics": {"position": position},
        "airframe": {"kind": "fixed_wing", "confidence": 0.9},
    }
    phases = build_phase_segments(
        analysis,
        {"effective_flight_start_time": 0.0, "effective_flight_end_time": 19.0, "timeline": [{"event": "Takeoff"}]},
    )
    assert "悬停" not in " ".join(item["name"] for item in phases["segments"])


def test_fixed_wing_override_has_priority():
    log = Log(
        duration_s=10.0,
        topics={"vehicle_status": Topic([0, 1], {"vehicle_type": [2, 2]})},
        parameters={"MC_ROLLRATE_P": 0.1, "FW_RR_P": 0.08, "TECS_TIME_CONST": 5.0},
    )
    result = detect_airframe_type(log, {"aircraft_type": "fixed_wing"})
    assert result["kind"] == "fixed_wing"
    assert result["aircraft_type_override"] == "fixed_wing"
    assert result["override_source"] == "report_options"
    assert "fixed_wing" in " ".join(result["evidence"])


def test_vehicle_type_2_does_not_force_multicopter_when_override_fixed_wing():
    log = Log(
        duration_s=10.0,
        topics={"vehicle_status": Topic([0, 1], {"vehicle_type": [2, 2]})},
        parameters={"MC_ROLLRATE_P": 0.1},
    )
    result = detect_airframe_type(log, {"aircraftType": "fixed-wing"})
    assert result["kind"] == "fixed_wing"
    assert result["ignored_evidence"]


def test_mixed_fw_mc_vt_params_do_not_force_multicopter():
    log = Log(
        duration_s=10.0,
        topics={},
        parameters={"FW_RR_P": 0.08, "MC_ROLLRATE_P": 0.1, "VT_TYPE": 2, "TECS_SPDWEIGHT": 1.0},
    )
    result = detect_airframe_type(log, {"aircraft_type": "auto"})
    assert result["kind"] != "multicopter"
    assert result["aircraft_type_candidates"]


def test_fixed_wing_report_no_multicopter_terms():
    position = Topic(
        np.arange(0, 30, dtype=float),
        {"altitude": [0, 0, 1, 3, 8, 12, 15, 15, 15, 14, 13, 10, 6, 3, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]},
    )
    analysis = {
        "log": Log(duration_s=30.0, topics={}),
        "topics": {"position": position},
        "airframe": {"kind": "fixed_wing", "confidence": 0.99},
    }
    phases = build_phase_segments(
        analysis,
        {"effective_flight_start_time": 0.0, "effective_flight_end_time": 29.0, "timeline": [{"event": "Takeoff"}]},
    )
    text = " ".join(item["name"] for item in phases["segments"])
    assert "Ground / Taxi" in text
    assert "Cruise" in text
    assert "多旋翼" not in text
    assert "悬停" not in text
    assert "Hover or Mission" not in text


def test_fixed_wing_report_uses_fw_parameters():
    log = Log(duration_s=10.0, topics={}, parameters={"FW_RR_P": 0.08, "TECS_TIME_CONST": 5.0, "MC_ROLLRATE_P": 0.1})
    analysis = {
        "log": log,
        "topics": {
            "attitude": None,
            "attitude_sp": None,
            "position": None,
            "gps": None,
            "battery": None,
            "actuator": None,
            "sensor": None,
            "rates": None,
            "rates_sp": None,
        },
        "airframe": {"kind": "fixed_wing", "confidence": 0.99},
        "missing": [],
    }
    verified = build_verified_analysis_summary(analysis)
    names = [item["name"] for item in verified["pid_parameters"]]
    assert "FW_RR_P" in names
    assert "TECS_TIME_CONST" in names
    assert "MC_ROLLRATE_P" not in names


def test_voltage_drop_events_are_merged():
    topic = Topic(np.arange(10, dtype=float), {"voltage": [24, 23.7, 23.4, 23.1, 22.9, 22.8, 22.7, 22.69, 22.68, 22.67], "current": np.full(10, 0.02)})
    battery = battery_quality(topic)
    events = merged_voltage_drop_events(topic, battery, [])
    assert len(events) == 1
    assert events[0]["start_s"] == 0.0
    assert events[0]["end_s"] >= 4.0
    assert events[0]["confidence"] == "Low"
    assert events[0]["event_type"] == "voltage_trend"
    assert events[0]["is_abnormal"] is False


def test_verified_risk_not_high_for_single_medium_event():
    risk = verified_risk_assessment(
        [{"name": "电压下降趋势", "category": "battery_voltage_drop", "severity": "WARNING", "is_abnormal": True, "confidence": "Low"}],
        {"engineering_confidence": "Medium"},
    )
    assert risk["level_cn"] == "中"


def test_verified_risk_high_for_preflight_fail():
    risk = verified_risk_assessment(
        [{"name": "Preflight Fail", "category": "abnormal_event", "severity": "ERROR", "is_abnormal": True, "evidence": "Preflight Fail: compass"}],
        {"engineering_confidence": "Medium"},
    )
    assert risk["level_cn"] == "高"


def test_postflight_preflight_fail_does_not_force_high_risk():
    risk = verified_risk_assessment(
        [{"name": "Preflight Fail", "category": "preflight_fail", "severity": "ERROR", "is_abnormal": True, "evidence": "Preflight Fail: compass", "flight_scope": "postflight"}],
        {"engineering_confidence": "Medium"},
    )
    assert risk["level_cn"] != "高"


def test_yaw_wrap_range_is_not_360_degree():
    values = np.array([358.0, 359.0, 0.0, 1.0, 2.0])
    assert circular_range_deg(values) < 6.0


def test_verified_summary_exposes_separate_algorithm_and_ai_summaries():
    log = Log(duration_s=10.0, topics={}, parameters={"MC_ROLLRATE_P": 0.15})
    analysis = {
        "log": log,
        "topics": {
            "attitude": Topic([0, 1, 2], {"roll": [0, 1, 2], "pitch": [0, 0, 0], "yaw": [1, 359, 1]}),
            "attitude_sp": Topic([0, 1, 2], {"roll_sp": [0, 1, 1], "pitch_sp": [0, 0, 0], "yaw_sp": [359, 1, 359]}),
            "position": None,
            "gps": None,
            "battery": None,
            "actuator": None,
            "sensor": None,
            "rates": None,
            "rates_sp": None,
        },
        "airframe": {"kind": "multicopter", "confidence": 0.8},
        "missing": ["GPS 数据"],
    }
    result = build_verified_analysis_summary(analysis)
    assert result["schemaVersion"] == "verified-flight-analysis.v2"
    assert result["verified_algorithm_summary"]["schemaVersion"] == "verified-algorithm-summary.v1"
    assert result["verified_ai_input_summary"]["schemaVersion"] == "verified-ai-input-summary.v1"
    assert "data_completeness" in result["data_quality"]
    assert "engineering_confidence" in result["data_quality"]


def test_ai_input_summary_rejects_raw_samples():
    report_data = {
        "source": "flight.ulg",
        "metadata": {"duration_s": 10, "topic_count": 3},
        "verified_ai_input_summary": {
            "data_quality": {"score": 80},
            "flight_events": {"effective_airborne_time": 5},
            "raw_samples": [1, 2, 3],
        },
    }
    summary = build_ai_input_summary(report_data)
    assert "raw_samples" not in summary
    validate_ai_input_summary(summary)
    summary["raw_samples"] = [1, 2, 3]
    try:
        validate_ai_input_summary(summary)
    except ValueError as error:
        assert "原始样本" in str(error) or "未验证数据" in str(error)
    else:
        raise AssertionError("raw_samples must be rejected")


def test_ai_report_requires_evidence_map():
    summary = {
        "data_quality": {"score": 80},
        "missing_data": [],
    }
    raw = {
        "report_markdown": "# 报告\n\n" + "工程分析内容。" * 80,
        "safety_note": "AI 不直接控制飞控，不在飞行中自动修改 PID。",
    }
    try:
        normalize_ai_report(raw, summary, "test-model", "openai")
    except ValueError as error:
        assert "evidence_map" in str(error)
    else:
        raise AssertionError("AI report without evidence_map must be rejected")


def test_ai_report_rejects_excluded_channel_hallucination():
    summary = {
        "data_quality": {"score": 80},
        "missing_data": [],
        "actuator_channel_analysis": {
            "excluded_channels": [{"channel": "output[5]", "reason": "fixed_or_unused"}],
        },
    }
    raw = {
        "report_markdown": "# 报告\n\noutput[5] 出现饱和异常。" + "工程分析内容。" * 80,
        "safety_note": "AI 不直接控制飞控，不在飞行中自动修改 PID。",
        "evidence_map": [{"claim": "x", "source_field": "verified_ai_input_summary.actuator_channel_analysis", "confidence": "low"}],
    }
    try:
        normalize_ai_report(raw, summary, "test-model", "openai")
    except ValueError as error:
        assert "output[5]" in str(error)
    else:
        raise AssertionError("AI report must reject excluded actuator channel hallucination")


def test_ai_report_allows_excluded_channel_limitations_text():
    summary = {
        "data_quality": {"score": 80},
        "missing_data": [],
        "actuator_channel_analysis": {
            "excluded_channels": [{"channel": "output[15]", "reason": "fixed_or_unused"}],
        },
    }
    raw = {
        "report_markdown": "# 报告\n\n异常事件需按 verified summary 复核。output[15] 为未使用/被排除通道，不作为饱和异常证据。" + "工程分析内容。" * 80,
        "safety_note": "AI 不直接控制飞控，不在飞行中自动修改 PID。",
        "evidence_map": [{"claim": "x", "source_field": "verified_ai_input_summary.actuator_channel_analysis", "confidence": "low"}],
    }
    report = normalize_ai_report(raw, summary, "test-model", "openai")
    assert report["model"] == "test-model"


def test_consistency_checker_blocks_fixed_wing_hover():
    verified = {
        "aircraft_type": {"kind": "fixed_wing"},
        "phase_segments": {"segments": [], "confidence": "High"},
        "flight_events": {"effective_flight_start_time": 10.0},
        "actuator_channel_analysis": {},
        "abnormal_events": [],
        "data_quality": {"engineering_confidence": "High"},
        "battery_metrics": {"current_reliable": True},
    }
    result = check_report_consistency(verified, "固定翼 悬停")
    assert result["passed"] is False


def test_consistency_checker_blocks_hover_text_in_fixed_wing_report():
    verified = {
        "aircraft_type": {"kind": "fixed_wing"},
        "phase_segments": {"segments": [], "confidence": "High"},
        "flight_events": {"effective_flight_start_time": 10.0},
        "actuator_channel_analysis": {},
        "abnormal_events": [],
        "data_quality": {"engineering_confidence": "High"},
        "battery_metrics": {"current_reliable": True},
    }
    result = check_report_consistency(verified, "## 6. PID / 控制参数分析\n| 参数 | 当前值 |\n| MC_ROLLRATE_P | 0.1 |\n固定翼 Hover or Mission")
    assert result["passed"] is False
    assert any("Hover or Mission" in item for item in result["blocking_errors"])
    assert any("MC_" in item for item in result["blocking_errors"])


def test_fixed_wing_insufficient_phase_evidence_does_not_use_ratio_template():
    analysis = {
        "log": Log(duration_s=40.0, topics={}),
        "topics": {"position": None},
        "airframe": {"kind": "fixed_wing", "confidence": 0.9},
    }
    phases = build_phase_segments(
        analysis,
        {"effective_flight_start_time": 2.0, "effective_flight_end_time": 32.0, "timeline": []},
    )
    names = [item["name"] for item in phases["segments"]]
    assert names == ["Fixed-wing verified flight window"]
    assert phases["segments"][0]["confidence"] == "Low"


def test_voltage_trend_does_not_enter_abnormal_events():
    voltage_events = [{
        "name": "电压下降趋势",
        "category": "voltage_trend",
        "event_type": "voltage_trend",
        "is_abnormal": False,
        "start_s": 1.0,
        "end_s": 10.0,
    }]
    events = build_verified_abnormal_events({"rows": []}, {"saturation_events": []}, voltage_events, {})
    assert events == []


def test_consistency_checker_blocks_voltage_trend_high_risk():
    verified = {
        "aircraft_type": {"kind": "fixed_wing"},
        "phase_segments": {"segments": [{"basis": "vehicle_status transition flags"}], "confidence": "High"},
        "flight_events": {"effective_flight_start_time": 10.0},
        "actuator_channel_analysis": {},
        "abnormal_events": [],
        "battery_events": [{"event_type": "voltage_trend", "is_abnormal": False}],
        "data_quality": {"engineering_confidence": "Medium", "overall_report_confidence": "High"},
        "battery_metrics": {"current_reliable": False},
    }
    result = check_report_consistency(verified, "电压下降趋势为高风险，动力负载过大。")
    assert result["passed"] is False
    assert any("voltage_trend" in item or "overall_report_confidence" in item for item in result["blocking_errors"])


def test_build_verified_summary_uses_missing_data_policy_dict():
    analysis = {
        "log": Log(duration_s=10.0, topics={}),
        "topics": {
            "attitude": None,
            "attitude_sp": None,
            "position": None,
            "gps": None,
            "battery": None,
            "actuator": None,
            "sensor": None,
            "rates": None,
            "rates_sp": None,
        },
        "airframe": {"kind": "fixed_wing", "confidence": 0.9},
        "missing": ["attitude setpoint"],
    }
    verified = build_verified_analysis_summary(analysis)
    assert isinstance(verified["missing_data"], dict)
    assert "attitude setpoint" in verified["missing_data"]["items"]
    assert verified["do_not_conclude"]
    assert verified["data_quality"]["overall_report_confidence"] in {"Low", "Medium"}

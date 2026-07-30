from __future__ import annotations

import csv
import html
import json
import math
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt, RGBColor
try:
    from pyulog import ULog
except ImportError:
    ULog = None

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except Exception:
    matplotlib = None
    plt = None

from services.report_fonts import configure_plot_fonts, html_font_css, set_docx_fonts
from services.report_reliability import build_verified_analysis_summary, circular_error_deg, circular_range_deg
from services.report_consistency import check_report_consistency

if plt is not None:
    configure_plot_fonts()

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase import pdfmetrics
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


THRESHOLDS = {
    "attitude_error_deg": 8.0,
    "attitude_rms_deg": 4.0,
    "rate_error_dps": 80.0,
    "oscillation_zero_crossings": 8,
    "voltage_drop_v_per_s": 0.15,
    "current_spike_factor": 2.0,
    "gps_min_satellites": 8,
    "gps_min_fix_type": 3,
    "gps_jump_m": 12.0,
    "vibration_gyro_norm_std": 0.8,
    "vibration_accel_norm_std": 5.0,
    "actuator_saturation_high": 0.95,
    "actuator_saturation_low": 0.05,
    "actuator_imbalance_percent": 18.0,
}

PID_BASELINES = {
    "roll": {"MC_ROLLRATE_P": 0.15, "MC_ROLLRATE_I": 0.20, "MC_ROLLRATE_D": 0.003},
    "pitch": {"MC_PITCHRATE_P": 0.15, "MC_PITCHRATE_I": 0.20, "MC_PITCHRATE_D": 0.003},
    "yaw": {"MC_YAWRATE_P": 0.20, "MC_YAWRATE_I": 0.10, "MC_YAWRATE_D": 0.0},
    "altitude": {"MPC_Z_VEL_P_ACC": 4.0, "MPC_Z_VEL_I_ACC": 2.0, "MPC_Z_VEL_D_ACC": 0.0},
}

AXIS_LABELS = {"roll": "横滚", "pitch": "俯仰", "yaw": "航向", "altitude": "高度"}
MISSING = "该日志中未找到相关数据，无法分析。"
PROJECT_AIRCRAFT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "report_aircraft.json"


@dataclass
class Dataset:
    name: str
    time_s: np.ndarray
    fields: dict[str, np.ndarray]


@dataclass
class FlightLog:
    path: Path
    kind: str
    topics: dict[str, Dataset] = field(default_factory=dict)
    parameters: dict[str, Any] = field(default_factory=dict)
    messages: list[dict[str, Any]] = field(default_factory=list)
    start_time: str = "日志未包含"
    end_time: str = "日志未包含"
    duration_s: float = 0.0


def finite_array(values):
    if values is None:
        return np.array([], dtype=float)
    arr = np.asarray(values, dtype=float)
    return arr[np.isfinite(arr)]


def safe_stats(values):
    arr = finite_array(values)
    if len(arr) == 0:
        return None
    return {
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
        "mean": float(np.mean(arr)),
        "std": float(np.std(arr)),
        "span": float(np.max(arr) - np.min(arr)),
        "count": int(len(arr)),
    }


def metric(value, unit="", digits=2):
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "日志未包含"
    return f"{float(value):.{digits}f}{unit}"


def normalize_name(name):
    return str(name).lower().replace(" ", "_").replace("-", "_").replace(".", "_")


def dataset_time(dataset, start_us=None):
    timestamp = dataset.data.get("timestamp")
    if timestamp is None or len(timestamp) == 0:
        return np.array([], dtype=float)
    base = int(timestamp[0] if start_us is None else start_us)
    return (np.asarray(timestamp, dtype=float) - base) / 1_000_000


def q_field(data, index):
    return data.get(f"q[{index}]")


def quaternion_to_euler_deg(q0, q1, q2, q3):
    w = np.asarray(q0, dtype=float)
    x = np.asarray(q1, dtype=float)
    y = np.asarray(q2, dtype=float)
    z = np.asarray(q3, dtype=float)
    roll = np.degrees(np.arctan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y)))
    sinp = 2 * (w * y - z * x)
    pitch = np.degrees(np.arcsin(np.clip(sinp, -1, 1)))
    yaw = np.degrees(np.arctan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)))
    return roll, pitch, (yaw + 360) % 360


def load_flight_log(path: str | Path) -> FlightLog:
    path = Path(path)
    if path.suffix.lower() == ".ulg":
        return load_ulg(path)
    return load_generic_log(path)


def load_ulg(path: str | Path) -> FlightLog:
    if ULog is None:
        raise RuntimeError("当前 Python 环境缺少 pyulog，无法解析 .ulg 文件。请先安装 pyulog，或上传 .csv/.json 日志。")
    path = Path(path)
    ulog = ULog(str(path))
    start_us = None
    end_us = None
    raw_topics = {}
    for item in ulog.data_list:
        timestamp = item.data.get("timestamp")
        if timestamp is not None and len(timestamp):
            start_us = int(timestamp[0]) if start_us is None else min(start_us, int(timestamp[0]))
            end_us = int(timestamp[-1]) if end_us is None else max(end_us, int(timestamp[-1]))
        raw_topics.setdefault(item.name, item)

    log = FlightLog(
        path=path,
        kind="ULG",
        parameters=getattr(ulog, "initial_parameters", {}) or {},
        start_time="日志时间 0.0s",
        end_time=f"日志时间 {(end_us - start_us) / 1_000_000:.1f}s" if start_us is not None and end_us is not None else "日志未包含",
        duration_s=max(0.0, (end_us - start_us) / 1_000_000) if start_us is not None and end_us is not None else 0.0,
    )

    for name, item in raw_topics.items():
        fields = {}
        for key, value in item.data.items():
            if key == "timestamp":
                continue
            try:
                fields[key] = np.asarray(value, dtype=float)
            except (TypeError, ValueError):
                continue
        log.topics[name] = Dataset(name=name, time_s=dataset_time(item, start_us), fields=fields)

    extract_ulg_derived_topics(log)
    for msg in getattr(ulog, "logged_messages", []) or []:
        text = getattr(msg, "message", "")
        level = getattr(msg, "log_level_str", None)
        level = level() if callable(level) else level
        level = level if level is not None else getattr(msg, "log_level", "INFO")
        timestamp = getattr(msg, "timestamp", 0)
        log.messages.append({"time_s": (timestamp - start_us) / 1_000_000 if start_us is not None else 0, "level": str(level), "text": str(text)})
    return log


def load_generic_log(path: Path) -> FlightLog:
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        frame = pd.DataFrame(data if isinstance(data, list) else data.get("data", data))
    elif path.suffix.lower() == ".csv":
        frame = pd.read_csv(path)
    else:
        try:
            frame = pd.read_csv(path)
        except Exception:
            frame = pd.DataFrame({"raw_text": path.read_text(encoding="utf-8", errors="replace").splitlines()})
    fields = {}
    time_s = np.arange(len(frame), dtype=float)
    for column in frame.columns:
        values = pd.to_numeric(frame[column], errors="coerce").to_numpy()
        if normalize_name(column) in {"timestamp", "time", "time_s", "t"}:
            finite = finite_array(values)
            if len(finite):
                time_s = values.astype(float)
                if np.nanmax(time_s) > 1_000_000:
                    time_s = (time_s - np.nanmin(time_s)) / 1_000_000
                else:
                    time_s = time_s - np.nanmin(time_s)
        if np.isfinite(values).any():
            fields[str(column)] = values
    duration = float(np.nanmax(time_s) - np.nanmin(time_s)) if len(time_s) else 0.0
    log = FlightLog(path=path, kind=path.suffix.lower().lstrip(".").upper() or "TEXT", duration_s=duration, end_time=f"日志时间 {duration:.1f}s")
    log.topics["generic"] = Dataset("generic", time_s, fields)
    map_generic_fields(log)
    return log


def extract_ulg_derived_topics(log: FlightLog):
    attitude = log.topics.get("vehicle_attitude")
    if attitude and all(f"q[{i}]" in attitude.fields for i in range(4)):
        roll, pitch, yaw = quaternion_to_euler_deg(*(attitude.fields[f"q[{i}]"] for i in range(4)))
        log.topics["mapped_attitude"] = Dataset("mapped_attitude", attitude.time_s, {"roll": roll, "pitch": pitch, "yaw": yaw})

    setpoint = log.topics.get("vehicle_attitude_setpoint")
    if setpoint:
        fields = {}
        if all(f"q_d[{i}]" in setpoint.fields for i in range(4)):
            roll_sp, pitch_sp, yaw_sp = quaternion_to_euler_deg(*(setpoint.fields[f"q_d[{i}]"] for i in range(4)))
            fields.update({"roll_sp": roll_sp, "pitch_sp": pitch_sp, "yaw_sp": yaw_sp})
        aliases = {"roll_body": "roll_sp", "pitch_body": "pitch_sp", "yaw_body": "yaw_sp"}
        for source, target in aliases.items():
            if source in setpoint.fields:
                values = setpoint.fields[source]
                fields[target] = np.degrees(values) if np.nanmax(np.abs(finite_array(values))) < 7 else values
        if fields:
            log.topics["mapped_attitude_setpoint"] = Dataset("mapped_attitude_setpoint", setpoint.time_s, fields)

    angular = log.topics.get("vehicle_angular_velocity") or log.topics.get("vehicle_rates_setpoint")
    if angular:
        fields = {}
        for source, target in {"xyz[0]": "roll_rate", "xyz[1]": "pitch_rate", "xyz[2]": "yaw_rate", "roll": "roll_rate", "pitch": "pitch_rate", "yaw": "yaw_rate"}.items():
            if source in angular.fields:
                values = angular.fields[source]
                fields[target] = np.degrees(values) if np.nanmax(np.abs(finite_array(values))) < 20 else values
        if fields:
            log.topics["mapped_rates"] = Dataset("mapped_rates", angular.time_s, fields)

    rates_sp = log.topics.get("vehicle_rates_setpoint")
    if rates_sp:
        fields = {}
        for source, target in {"roll": "roll_rate_sp", "pitch": "pitch_rate_sp", "yaw": "yaw_rate_sp"}.items():
            if source in rates_sp.fields:
                values = rates_sp.fields[source]
                fields[target] = np.degrees(values) if np.nanmax(np.abs(finite_array(values))) < 20 else values
        if fields:
            log.topics["mapped_rates_setpoint"] = Dataset("mapped_rates_setpoint", rates_sp.time_s, fields)

    local = log.topics.get("vehicle_local_position")
    if local:
        fields = {}
        if "z" in local.fields:
            fields["altitude"] = -local.fields["z"]
        if "vz" in local.fields:
            fields["vertical_velocity"] = -local.fields["vz"]
        if "vx" in local.fields and "vy" in local.fields:
            fields["horizontal_speed"] = np.sqrt(local.fields["vx"] ** 2 + local.fields["vy"] ** 2)
        for key in ("x", "y"):
            if key in local.fields:
                fields[key] = local.fields[key]
        log.topics["mapped_position"] = Dataset("mapped_position", local.time_s, fields)

    gps = log.topics.get("vehicle_gps_position")
    if gps:
        fields = {}
        for source, target in {"lat": "lat", "lon": "lon", "alt": "gps_alt", "vel_m_s": "gps_speed", "satellites_used": "satellites", "fix_type": "fix_type", "eph": "eph", "epv": "epv", "hdop": "hdop"}.items():
            if source in gps.fields:
                values = gps.fields[source].copy()
                if source in {"lat", "lon"} and len(finite_array(values)) and np.nanmax(np.abs(values)) > 1000:
                    values = values / 1e7
                if source == "alt" and len(finite_array(values)) and np.nanmax(np.abs(values)) > 10000:
                    values = values / 1000
                fields[target] = values
        log.topics["mapped_gps"] = Dataset("mapped_gps", gps.time_s, fields)

    battery = log.topics.get("battery_status")
    if battery:
        fields = {}
        for source, target in {"voltage_v": "voltage", "current_a": "current", "remaining": "remaining", "discharged_mah": "consumed_mah"}.items():
            if source in battery.fields:
                values = battery.fields[source].copy()
                if source == "remaining" and len(finite_array(values)) and np.nanmax(values) <= 1.1:
                    values = values * 100
                fields[target] = values
        log.topics["mapped_battery"] = Dataset("mapped_battery", battery.time_s, fields)

    actuator = log.topics.get("actuator_outputs") or log.topics.get("actuator_controls_0")
    if actuator:
        fields = {key: value for key, value in actuator.fields.items() if key.startswith("output") or key.startswith("control")}
        if fields:
            log.topics["mapped_actuator"] = Dataset("mapped_actuator", actuator.time_s, fields)

    sensor = log.topics.get("sensor_combined")
    if sensor:
        fields = {}
        gyro_keys = ["gyro_rad[0]", "gyro_rad[1]", "gyro_rad[2]"]
        accel_keys = ["accelerometer_m_s2[0]", "accelerometer_m_s2[1]", "accelerometer_m_s2[2]"]
        if all(key in sensor.fields for key in gyro_keys):
            fields["gyro_norm"] = np.sqrt(sum(sensor.fields[key] ** 2 for key in gyro_keys))
        if all(key in sensor.fields for key in accel_keys):
            fields["accel_norm"] = np.sqrt(sum(sensor.fields[key] ** 2 for key in accel_keys))
        if fields:
            log.topics["mapped_sensor"] = Dataset("mapped_sensor", sensor.time_s, fields)


def map_generic_fields(log: FlightLog):
    dataset = log.topics["generic"]
    normalized = {normalize_name(key): key for key in dataset.fields}

    def pick(*names):
        for name in names:
            for normalized_name, original in normalized.items():
                if name in normalized_name:
                    return dataset.fields[original]
        return None

    attitude_fields = {
        "roll": pick("roll"),
        "pitch": pick("pitch"),
        "yaw": pick("yaw", "heading"),
        "roll_sp": pick("roll_setpoint", "roll_sp"),
        "pitch_sp": pick("pitch_setpoint", "pitch_sp"),
        "yaw_sp": pick("yaw_setpoint", "yaw_sp"),
    }
    attitude_fields = {key: value for key, value in attitude_fields.items() if value is not None}
    if attitude_fields:
        log.topics["mapped_attitude"] = Dataset("mapped_attitude", dataset.time_s, attitude_fields)

    position_fields = {
        "altitude": pick("altitude", "relative_alt", "height"),
        "vertical_velocity": pick("vertical_velocity", "climb", "vz"),
        "horizontal_speed": pick("ground_speed", "speed"),
        "x": pick("local_x", "x"),
        "y": pick("local_y", "y"),
    }
    position_fields = {key: value for key, value in position_fields.items() if value is not None}
    if position_fields:
        log.topics["mapped_position"] = Dataset("mapped_position", dataset.time_s, position_fields)

    battery_fields = {
        "voltage": pick("voltage"),
        "current": pick("current"),
        "remaining": pick("battery_remaining", "remaining"),
        "consumed_mah": pick("mah", "consumed"),
    }
    battery_fields = {key: value for key, value in battery_fields.items() if value is not None}
    if battery_fields:
        log.topics["mapped_battery"] = Dataset("mapped_battery", dataset.time_s, battery_fields)

    gps_fields = {
        "lat": pick("lat"),
        "lon": pick("lon", "lng"),
        "satellites": pick("satellite"),
        "fix_type": pick("fix"),
        "eph": pick("eph", "hdop"),
    }
    gps_fields = {key: value for key, value in gps_fields.items() if value is not None}
    if gps_fields:
        log.topics["mapped_gps"] = Dataset("mapped_gps", dataset.time_s, gps_fields)


def get_topic(log: FlightLog, name):
    return log.topics.get(name)


def interp_to(target_t, source_t, values, max_dt=None):
    if target_t is None or source_t is None or values is None:
        return None
    count = min(len(source_t), len(values))
    if count < 2 or len(target_t) == 0:
        return None
    source_time = np.asarray(source_t[:count], dtype=float)
    source_values = np.asarray(values[:count], dtype=float)
    target_time = np.asarray(target_t, dtype=float)
    mask = np.isfinite(source_time) & np.isfinite(source_values)
    source_time = source_time[mask]
    source_values = source_values[mask]
    if len(source_time) < 2:
        return None
    result = np.interp(target_time, source_time, source_values, left=np.nan, right=np.nan)
    if max_dt is not None:
        indices = np.searchsorted(source_time, target_time)
        left = np.clip(indices - 1, 0, len(source_time) - 1)
        right = np.clip(indices, 0, len(source_time) - 1)
        nearest_dt = np.minimum(np.abs(target_time - source_time[left]), np.abs(target_time - source_time[right]))
        result[nearest_dt > max_dt] = np.nan
    return result


def dataset_field(dataset, *names):
    if not dataset:
        return None
    for name in names:
        if name in dataset.fields:
            return dataset.fields[name]
    normalized = {normalize_name(key): key for key in dataset.fields}
    for name in names:
        key = normalized.get(normalize_name(name))
        if key is not None:
            return dataset.fields[key]
    return None


def truth_fraction(values):
    arr = finite_array(values)
    if len(arr) == 0:
        return 0.0
    return float(np.mean(np.abs(arr) > 0.5))


def numeric_values(values):
    arr = finite_array(values)
    if len(arr) == 0:
        return np.array([], dtype=float)
    return arr


AIRCRAFT_TYPE_ALIASES = {
    "fixed_wing": "fixed_wing",
    "fixed-wing": "fixed_wing",
    "fixedwing": "fixed_wing",
    "fw": "fixed_wing",
    "plane": "fixed_wing",
    "airplane": "fixed_wing",
    "固定翼": "fixed_wing",
    "multicopter": "multicopter",
    "multi_copter": "multicopter",
    "multi-copter": "multicopter",
    "mc": "multicopter",
    "quad": "multicopter",
    "quadcopter": "multicopter",
    "多旋翼": "multicopter",
    "compound_vtol": "compound_vtol",
    "vtol": "compound_vtol",
    "compound-vtol": "compound_vtol",
    "tiltrotor": "compound_vtol",
    "tailsitter": "compound_vtol",
    "复合翼": "compound_vtol",
}


def normalize_aircraft_type(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return AIRCRAFT_TYPE_ALIASES.get(text.lower()) or AIRCRAFT_TYPE_ALIASES.get(text)


def aircraft_label(kind):
    return {
        "fixed_wing": "固定翼",
        "multicopter": "多旋翼",
        "compound_vtol": "复合翼 / VTOL",
    }.get(kind, "机型证据不足")


def confidence_label(score):
    if score >= 0.85:
        return "high"
    if score >= 0.6:
        return "medium"
    return "low"


def phase_template_name(airframe):
    if airframe.get("kind") == "compound_vtol":
        return "复合翼自适应阶段：有转换时为五阶段，纯垂起垂降时为三阶段"
    if airframe.get("kind") == "multicopter":
        return "多旋翼阶段：起飞、悬停/任务、降落"
    if airframe.get("kind") == "fixed_wing":
        return "Fixed-wing phases: Ground / Taxi, Takeoff, Climb, Cruise, Descent, Approach, Landing, Postflight"
    return "机型证据不足：仅输出有效飞行段，不强行套用固定翼/复合翼模板"


def _project_aircraft_override():
    env_type = normalize_aircraft_type(os.environ.get("REPORT_AIRCRAFT_TYPE") or os.environ.get("AIRCRAFT_TYPE"))
    if env_type:
        return env_type, "environment"
    if not PROJECT_AIRCRAFT_CONFIG.exists():
        return None, None
    try:
        data = json.loads(PROJECT_AIRCRAFT_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return None, None
    kind = normalize_aircraft_type(data.get("aircraft_type") or data.get("aircraftType") or data.get("selected_aircraft_type"))
    if not kind:
        return None, None
    return kind, str(data.get("source") or "project_config")


def _option_aircraft_override(options):
    if not options:
        return None, None, False
    raw = options.get("aircraft_type") or options.get("aircraftType") or options.get("selected_aircraft_type")
    if raw is not None and str(raw).strip().lower() in {"auto", "automatic", "detect", "none"}:
        return None, None, True
    kind = normalize_aircraft_type(raw)
    if kind:
        return kind, "report_options", False
    return None, None, False


def _log_aircraft_override(params):
    for key in ("AIRCRAFT_TYPE", "REPORT_AIRCRAFT_TYPE", "PROJECT_AIRCRAFT_TYPE"):
        if key in params:
            kind = normalize_aircraft_type(params.get(key))
            if kind:
                return kind, "log_metadata"
    return None, None


def _candidate(kind, score, evidence):
    return {"type": kind, "label": aircraft_label(kind), "score": round(float(score), 2), "evidence": evidence}


def _airframe_result(kind, score, source, evidence, candidates, ignored_evidence=None, override=False):
    score = max(0.0, min(0.99, float(score)))
    return {
        "kind": kind,
        "selected_aircraft_type": kind,
        "label": aircraft_label(kind),
        "confidence": score,
        "confidence_label": confidence_label(score),
        "template": phase_template_name({"kind": kind}),
        "evidence": evidence or ["未找到明确机型证据"],
        "source": source,
        "aircraft_type_source": source,
        "override": bool(override),
        "override_source": source if override else None,
        "aircraft_type_override": kind if override else None,
        "aircraft_type_candidates": candidates,
        "ignored_evidence": ignored_evidence or [],
    }


def detect_airframe_type(log: FlightLog, options=None):
    status = log.topics.get("vehicle_status")
    params = log.parameters or {}
    global_evidence = []
    fixed_evidence = []
    multicopter_evidence = []
    vtol_evidence = []
    ignored_evidence = []
    vtol_score = 0
    fixed_score = 0
    multicopter_score = 0

    option_override, option_source, skip_project_config = _option_aircraft_override(options)
    project_override, project_source = (None, None) if skip_project_config else _project_aircraft_override()
    log_override, log_source = _log_aircraft_override(params)
    override_kind = option_override or project_override or log_override
    override_source = option_source or project_source or log_source

    is_vtol = dataset_field(status, "is_vtol")
    if truth_fraction(is_vtol) > 0.2:
        vtol_score += 4
        vtol_evidence.append("vehicle_status.is_vtol indicates VTOL")

    transition_mode = dataset_field(status, "in_transition_mode", "transition_mode")
    transition_to_fw = dataset_field(status, "in_transition_to_fw")
    if truth_fraction(transition_mode) > 0.02 or truth_fraction(transition_to_fw) > 0.02:
        vtol_score += 4
        vtol_evidence.append("transition flags are present")

    vehicle_type = numeric_values(dataset_field(status, "vehicle_type"))
    if len(vehicle_type):
        observed = {int(round(value)) for value in vehicle_type if math.isfinite(value)}
        if observed.intersection({19, 20, 21, 22, 23, 24, 25}):
            vtol_score += 4
            vtol_evidence.append(f"vehicle_type={sorted(observed)} is a MAVLink VTOL type")
        elif 1 in observed:
            fixed_score += 3
            fixed_evidence.append("vehicle_type=1 is MAVLink fixed-wing")
        elif observed.intersection({2, 3, 4, 13, 14, 15}):
            multicopter_score += 1
            multicopter_evidence.append(f"vehicle_type={sorted(observed)} suggests rotorcraft but is weak evidence")

    param_names = {str(name).upper() for name in params}
    if any(name.startswith("VT_") for name in param_names) or "VT_TYPE" in param_names:
        vtol_score += 3
        vtol_evidence.append("VT_* parameters exist")
    if any(name.startswith("FW_") for name in param_names):
        fixed_score += 3
        fixed_evidence.append("FW_* parameters exist")
    if any(name.startswith("TECS_") for name in param_names):
        fixed_score += 2
        fixed_evidence.append("TECS_* parameters exist")
    if any(name.startswith("MC_") for name in param_names) or any(name.startswith("MPC_") for name in param_names):
        multicopter_score += 2
        multicopter_evidence.append("MC_*/MPC_* parameters exist")
    if any(name.startswith("FW_") for name in param_names) and any(name.startswith("MC_") for name in param_names):
        global_evidence.append("FW_* and MC_* parameters coexist; automatic detection will not select rotorcraft from this alone")

    autostart = str(params.get("SYS_AUTOSTART", "")).strip()
    if autostart:
        global_evidence.append(f"SYS_AUTOSTART={autostart}")

    topic_names = {str(name).lower() for name in log.topics}
    if "airspeed" in topic_names or "airspeed_validated" in topic_names:
        fixed_score += 2
        fixed_evidence.append("airspeed topics exist")
    if any(name in topic_names for name in ("tecs_status", "fixedwing_position_control_status", "fw_virtual_attitude_setpoint")):
        fixed_score += 2
        fixed_evidence.append("fixed-wing control topics exist")
    if "actuator_servos" in topic_names:
        fixed_score += 1
        fixed_evidence.append("actuator_servos topic exists")
    if "actuator_motors" in topic_names:
        multicopter_score += 1
        multicopter_evidence.append("actuator_motors topic exists")

    candidates = [
        _candidate("fixed_wing", fixed_score, fixed_evidence),
        _candidate("compound_vtol", vtol_score, vtol_evidence),
        _candidate("multicopter", multicopter_score, multicopter_evidence),
    ]

    if override_kind:
        source_label = "用户/项目配置指定" if override_source in {"project_config", "environment", "report_options"} else "日志元信息指定"
        evidence = [f"{source_label} {override_kind}"]
        if override_kind == "fixed_wing":
            if fixed_evidence:
                evidence.extend(fixed_evidence)
            if multicopter_evidence:
                ignored_evidence.append("MC_*/MPC_* or vehicle_type rotorcraft hints exist but are ignored because fixed_wing is explicitly specified")
            if vtol_evidence:
                ignored_evidence.append("VTOL hints exist but are ignored because fixed_wing is explicitly specified")
        elif override_kind == "multicopter":
            evidence.extend(multicopter_evidence)
        elif override_kind == "compound_vtol":
            evidence.extend(vtol_evidence)
        return _airframe_result(override_kind, 0.99, override_source or "override", evidence, candidates, ignored_evidence, override=True)

    if vtol_score >= 3 and vtol_score >= max(fixed_score - 1, multicopter_score):
        confidence = min(0.98, 0.62 + vtol_score * 0.06)
        evidence = global_evidence + vtol_evidence + fixed_evidence[:2]
        return _airframe_result("compound_vtol", confidence, "auto_detection", evidence or ["检测到 VTOL 相关数据"], candidates)

    if fixed_score >= 3 and fixed_score >= multicopter_score:
        confidence = min(0.95, 0.58 + fixed_score * 0.07)
        evidence = global_evidence + fixed_evidence
        if multicopter_evidence:
            ignored_evidence.append("Rotorcraft hints are secondary because FW/TECS/fixed-wing evidence is stronger")
        return _airframe_result("fixed_wing", confidence, "auto_detection", evidence or ["检测到固定翼相关数据"], candidates, ignored_evidence)

    if multicopter_score >= 3 and multicopter_score > fixed_score and vtol_score == 0:
        confidence = min(0.92, 0.56 + multicopter_score * 0.07)
        evidence = global_evidence + multicopter_evidence
        return _airframe_result("multicopter", confidence, "auto_detection", evidence or ["检测到多旋翼相关数据"], candidates)

    evidence = global_evidence + fixed_evidence + vtol_evidence + multicopter_evidence
    return _airframe_result(
        "unknown",
        0.35,
        "auto_detection",
        evidence or ["日志未包含可确认机型的数据；未仅凭 vehicle_type 强行判定"],
        candidates,
    )


def error_metrics(actual_topic, actual_key, setpoint_topic, setpoint_key):
    if not actual_topic or not setpoint_topic:
        return None
    actual = actual_topic.fields.get(actual_key)
    setpoint = setpoint_topic.fields.get(setpoint_key)
    if actual is None or setpoint is None:
        return None
    sp = interp_to(actual_topic.time_s, setpoint_topic.time_s, setpoint, max_dt=0.25)
    if sp is None:
        return None
    count = min(len(actual), len(sp))
    actual_values = np.asarray(actual[:count], dtype=float)
    sp_values = sp[:count]
    if actual_key in {"yaw", "heading", "course"} or setpoint_key in {"yaw_sp", "heading_sp", "course_sp"}:
        error = finite_array(circular_error_deg(actual_values, sp_values))
    else:
        error = finite_array(actual_values - sp_values)
    if len(error) == 0:
        return None
    return {"max": float(np.max(np.abs(error))), "mean": float(np.mean(np.abs(error))), "bias": float(np.mean(error)), "rms": float(np.sqrt(np.mean(error ** 2)))}


def zero_crossings(values):
    arr = finite_array(values)
    if len(arr) < 3:
        return 0
    signs = np.sign(arr - np.mean(arr))
    return int(np.sum(np.diff(signs) != 0))


def haversine_m(lat1, lon1, lat2, lon2):
    radius = 6_371_000.0
    p1 = np.radians(lat1)
    p2 = np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlambda = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlambda / 2) ** 2
    return 2 * radius * np.arctan2(np.sqrt(a), np.sqrt(1 - a))


def path_distance_m(gps):
    if not gps or "lat" not in gps.fields or "lon" not in gps.fields:
        return None
    lat = finite_array(gps.fields["lat"])
    lon = finite_array(gps.fields["lon"])
    count = min(len(lat), len(lon))
    if count < 2:
        return None
    return float(np.nansum(haversine_m(lat[:count - 1], lon[:count - 1], lat[1:count], lon[1:count])))


def detect_voltage_drops(battery):
    if not battery or "voltage" not in battery.fields or len(battery.time_s) < 2:
        return []
    voltage = np.asarray(battery.fields["voltage"], dtype=float)
    time_s = battery.time_s
    count = min(len(voltage), len(time_s))
    dv = -np.diff(voltage[:count])
    dt = np.maximum(np.diff(time_s[:count]), 1e-3)
    rate = dv / dt
    indices = np.where(rate > THRESHOLDS["voltage_drop_v_per_s"])[0]
    if len(indices) == 0:
        return []
    intervals = []
    start_idx = int(indices[0])
    last_idx = int(indices[0])
    for index in indices[1:]:
        index = int(index)
        if float(time_s[index] - time_s[last_idx]) <= 5.0:
            last_idx = index
            continue
        intervals.append((start_idx, last_idx))
        start_idx = last_idx = index
    intervals.append((start_idx, last_idx))
    events = []
    for start_idx, end_idx in intervals[:6]:
        end_sample = min(end_idx + 1, count - 1)
        events.append({
            "time": float(time_s[start_idx]),
            "start_s": float(time_s[start_idx]),
            "end_s": float(time_s[end_sample]),
            "name": "电压下降趋势",
            "evidence": f"电压 {voltage[start_idx]:.2f}V -> {voltage[end_sample]:.2f}V，最大下降速率 {np.nanmax(rate[start_idx:end_idx + 1]):.2f} V/s",
            "risk": "中",
            "advice": "检查电池内阻、电源线和接插件；若电流数据不可信，不能仅凭该日志判断动力负载。",
        })
    return events


def detect_current_spikes(battery):
    if not battery or "current" not in battery.fields:
        return []
    current = finite_array(battery.fields["current"])
    if len(current) < 10:
        return []
    mean = np.mean(current)
    threshold = mean * THRESHOLDS["current_spike_factor"]
    if threshold <= 1:
        return []
    spikes = np.where(np.asarray(battery.fields["current"]) > threshold)[0]
    events = []
    for index in spikes[:10]:
        time_value = battery.time_s[index] if index < len(battery.time_s) else 0
        events.append({"time": float(time_value), "name": "电流尖峰", "evidence": f"电流 {battery.fields['current'][index]:.1f} A，高于平均值 {mean:.1f} A", "risk": "中", "advice": "检查急剧机动、电机负载、桨叶状态和控制输出突变。"})
    return events


def detect_gps_events(gps):
    if not gps:
        return []
    events = []
    if "satellites" in gps.fields:
        sat = finite_array(gps.fields["satellites"])
        if len(sat) and np.nanmin(sat) < THRESHOLDS["gps_min_satellites"]:
            events.append({"time": 0, "name": "GPS 卫星数量不足", "evidence": f"最低卫星数 {np.nanmin(sat):.0f}", "risk": "中", "advice": "避免在 GPS 状态差时使用 Mission/RTL/Position 模式。"})
    if "fix_type" in gps.fields:
        fix = finite_array(gps.fields["fix_type"])
        if len(fix) and np.nanmin(fix) < THRESHOLDS["gps_min_fix_type"]:
            events.append({"time": 0, "name": "GPS Fix 不稳定", "evidence": f"最低 fix_type {np.nanmin(fix):.0f}", "risk": "中", "advice": "检查天线位置、遮挡和干扰。"})
    if "lat" in gps.fields and "lon" in gps.fields:
        lat = gps.fields["lat"]
        lon = gps.fields["lon"]
        count = min(len(lat), len(lon), len(gps.time_s))
        if count > 2:
            jumps = haversine_m(lat[:count - 1], lon[:count - 1], lat[1:count], lon[1:count])
            for index in np.where(jumps > THRESHOLDS["gps_jump_m"])[0][:10]:
                events.append({"time": float(gps.time_s[index]), "name": "GPS 位置跳变", "evidence": f"相邻点跳变 {jumps[index]:.1f} m", "risk": "高", "advice": "检查 GPS 多路径、天线安装和电磁干扰。"})
    return events


def detect_actuator_events(actuator):
    if not actuator:
        return []
    events = []
    means = []
    for key, values in actuator.fields.items():
        arr = finite_array(values)
        if len(arr) < 5:
            continue
        span = float(np.nanmax(arr) - np.nanmin(arr))
        std = float(np.nanstd(arr))
        if span < 1e-6 or std < 1e-6 or np.allclose(arr, 0):
            continue
        if np.nanmax(arr) > 2:
            norm = np.clip((arr - 1000) / 1000, 0, 1)
            sat_mask = (norm > THRESHOLDS["actuator_saturation_high"]) | (norm < THRESHOLDS["actuator_saturation_low"])
        else:
            norm = arr
            sat_mask = (norm > THRESHOLDS["actuator_saturation_high"]) | (norm < -THRESHOLDS["actuator_saturation_high"])
        sat = np.mean(sat_mask) * 100
        means.append((key, float(np.mean(norm))))
        if sat > 5:
            events.append({"time": 0, "name": f"{key} 输出饱和", "evidence": f"饱和比例 {sat:.1f}%", "risk": "高", "advice": "先检查控制余量、重心、电机/桨和机体构型，不建议直接调 PID。"})
    if means:
        values = [item[1] for item in means]
        imbalance = (max(values) - min(values)) * 100
        if imbalance > THRESHOLDS["actuator_imbalance_percent"]:
            events.append({"time": 0, "name": "执行器输出不平衡", "evidence": f"平均输出差异 {imbalance:.1f}%", "risk": "中", "advice": "检查重心偏移、机架不对称、电机或桨效率差异。"})
    return events


def detect_attitude_events(attitude, setpoint):
    events = []
    if not attitude:
        return events
    for axis in ("roll", "pitch", "yaw"):
        arr = attitude.fields.get(axis)
        if arr is None:
            continue
        if axis == "yaw":
            span = circular_range_deg(arr)
            normalized = np.degrees(np.unwrap(np.radians(finite_array(arr))))
            crossings = zero_crossings(normalized)
            axis_stats = safe_stats(normalized)
        else:
            crossings = zero_crossings(arr)
            axis_stats = safe_stats(arr)
            span = axis_stats["span"] if axis_stats else None
        if crossings > THRESHOLDS["oscillation_zero_crossings"] and span is not None and span > (45 if axis == "yaw" else 8):
            confidence = "低" if axis == "yaw" else "中"
            events.append({"time": 0, "name": f"{AXIS_LABELS[axis]}姿态振荡待复核", "evidence": f"反向次数 {crossings}，循环处理后范围 {span:.1f} deg，置信度{confidence}", "risk": "中", "advice": "先检查机械振动、估计器和 setpoint 连续性，再考虑小幅调整 D/P。"})
        if setpoint:
            metrics = error_metrics(attitude, axis, setpoint, f"{axis}_sp")
            if metrics and metrics["rms"] > THRESHOLDS["attitude_rms_deg"]:
                events.append({"time": 0, "name": f"{AXIS_LABELS[axis]}跟随误差过大", "evidence": f"RMS 误差 {metrics['rms']:.1f} deg，最大误差 {metrics['max']:.1f} deg", "risk": "中", "advice": "检查响应不足、过冲或执行器余量。"})
    return events


def detect_sensor_events(sensor):
    if not sensor:
        return []
    events = []
    gyro = safe_stats(sensor.fields.get("gyro_norm"))
    accel = safe_stats(sensor.fields.get("accel_norm"))
    if gyro and gyro["std"] > THRESHOLDS["vibration_gyro_norm_std"]:
        events.append({"time": 0, "name": "陀螺仪高频噪声偏大", "evidence": f"gyro norm 标准差 {gyro['std']:.2f}", "risk": "中", "advice": "检查螺旋桨平衡、电机轴、机架松动和飞控减震。"})
    if accel and accel["std"] > THRESHOLDS["vibration_accel_norm_std"]:
        events.append({"time": 0, "name": "加速度计振动偏大", "evidence": f"accel norm 标准差 {accel['std']:.2f}", "risk": "中", "advice": "振动过大时不建议直接调 PID，应先排查机械问题。"})
    return events


def classify_message(text):
    normalized = text.lower()
    if any(token in normalized for token in ("armed by rc", "armed by", "disarmed", "takeoff detected", "landing detected")):
        return "操作事件", "低", "操作/飞行时间线事件，不作为故障或链路异常。"
    rules = [
        ("battery", "电池低电压或电源风险", "高", "检查电池电压、内阻和供电连接。"),
        ("gps", "GPS 或导航异常", "中", "检查卫星数量、天线位置和电磁干扰。"),
        ("compass", "罗盘/磁干扰异常", "中", "检查罗盘安装位置，远离电源线和电调线。"),
        ("ekf", "EKF 估计器告警", "高", "检查 GPS、IMU、罗盘和高度估计一致性。"),
        ("failsafe", "Failsafe 触发", "高", "复盘触发条件，检查遥控、数传、电池和导航状态。"),
        ("rc", "遥控链路异常", "中", "检查接收机、电台、天线和 failsafe 设置。"),
        ("altitude", "高度估计异常", "中", "检查气压计、GPS 高度和 EKF height innovation。"),
    ]
    for keyword, meaning, risk, advice in rules:
        if keyword in normalized:
            return meaning, risk, advice
    return "飞控日志消息", "低", "结合发生阶段和其它数据继续排查。"


def analyze_log(log: FlightLog, options=None):
    attitude = get_topic(log, "mapped_attitude")
    attitude_sp = get_topic(log, "mapped_attitude_setpoint")
    rates = get_topic(log, "mapped_rates")
    rates_sp = get_topic(log, "mapped_rates_setpoint")
    position = get_topic(log, "mapped_position")
    gps = get_topic(log, "mapped_gps")
    battery = get_topic(log, "mapped_battery")
    actuator = get_topic(log, "mapped_actuator")
    sensor = get_topic(log, "mapped_sensor")
    airframe = detect_airframe_type(log, options)

    attitude_metrics = {}
    for axis in ("roll", "pitch", "yaw"):
        attitude_metrics[axis] = {
            "actual": safe_stats(attitude.fields.get(axis) if attitude else None),
            "error": error_metrics(attitude, axis, attitude_sp, f"{axis}_sp"),
        }
    rate_metrics = {}
    for axis in ("roll", "pitch", "yaw"):
        rate_metrics[axis] = {
            "actual": safe_stats(rates.fields.get(f"{axis}_rate") if rates else None),
            "error": error_metrics(rates, f"{axis}_rate", rates_sp, f"{axis}_rate_sp"),
        }

    position_metrics = {
        "altitude": safe_stats(position.fields.get("altitude") if position else None),
        "vertical_velocity": safe_stats(position.fields.get("vertical_velocity") if position else None),
        "horizontal_speed": safe_stats(position.fields.get("horizontal_speed") if position else None),
        "distance_m": path_distance_m(gps),
    }
    power_metrics = {
        "voltage": safe_stats(battery.fields.get("voltage") if battery else None),
        "current": safe_stats(battery.fields.get("current") if battery else None),
        "remaining": safe_stats(battery.fields.get("remaining") if battery else None),
        "consumed_mah": safe_stats(battery.fields.get("consumed_mah") if battery else None),
    }
    gps_metrics = {
        "satellites": safe_stats(gps.fields.get("satellites") if gps else None),
        "fix_type": safe_stats(gps.fields.get("fix_type") if gps else None),
        "eph": safe_stats(gps.fields.get("eph") if gps else None),
        "epv": safe_stats(gps.fields.get("epv") if gps else None),
    }
    sensor_metrics = {
        "gyro_norm": safe_stats(sensor.fields.get("gyro_norm") if sensor else None),
        "accel_norm": safe_stats(sensor.fields.get("accel_norm") if sensor else None),
    }

    events = []
    events.extend(detect_attitude_events(attitude, attitude_sp))
    events.extend(detect_voltage_drops(battery))
    events.extend(detect_current_spikes(battery))
    events.extend(detect_gps_events(gps))
    events.extend(detect_actuator_events(actuator))
    events.extend(detect_sensor_events(sensor))

    warning_rows = []
    for message in log.messages:
        meaning, risk, advice = classify_message(message["text"])
        warning_rows.append([f"{message['time_s']:.1f}s", message["level"], message["text"], meaning, risk, advice])
        if risk in {"中", "高"}:
            events.append({"time": message["time_s"], "name": meaning, "evidence": message["text"], "risk": risk, "advice": advice})

    risk_rank = {"低": 1, "中": 2, "高": 3}
    risk = "低"
    if events:
        risk = max((item["risk"] for item in events), key=lambda value: risk_rank[value])
    success = "存在风险" if risk != "低" else "基本正常"

    missing = []
    for label, topic in [
        ("姿态控制数据", attitude),
        ("姿态 setpoint 数据", attitude_sp),
        ("角速度 setpoint 数据", rates_sp),
        ("位置/高度数据", position),
        ("电池数据", battery),
        ("GPS 数据", gps),
        ("执行器输出数据", actuator),
        ("IMU 振动数据", sensor),
    ]:
        if not topic:
            missing.append(label)

    root_causes = build_root_causes(events, attitude_metrics, sensor_metrics, power_metrics)
    recommendations = build_recommendations(events, risk)
    timeline = build_timeline(log, position, events)
    return {
        "log": log,
        "topics": {"attitude": attitude, "attitude_sp": attitude_sp, "rates": rates, "rates_sp": rates_sp, "position": position, "gps": gps, "battery": battery, "actuator": actuator, "sensor": sensor},
        "airframe": airframe,
        "metrics": {"attitude": attitude_metrics, "rates": rate_metrics, "position": position_metrics, "power": power_metrics, "gps": gps_metrics, "sensor": sensor_metrics},
        "events": sorted(events, key=lambda item: item.get("time", 0)),
        "warnings": warning_rows,
        "risk": risk,
        "success": success,
        "missing": missing,
        "root_causes": root_causes,
        "recommendations": recommendations,
        "timeline": timeline,
    }


def build_timeline(log, position, events):
    return [["时间段", "飞行阶段", "主要现象", "是否异常"], ["见 verified_flight_timeline", "统一可信时间线", "旧模板时间线已停用", "N/A"]]


def build_root_causes(events, attitude_metrics, sensor_metrics, power_metrics):
    causes = []
    event_names = " ".join(item["name"] for item in events)
    if "振动" in event_names or (sensor_metrics.get("gyro_norm") and sensor_metrics["gyro_norm"]["std"] > THRESHOLDS["vibration_gyro_norm_std"]):
        causes.append(["机械振动或结构松动待复核", "IMU 噪声或振动指标偏高，并可能伴随姿态控制抖动；需要先做机械和安装检查。", "高"])
    if any(metric.get("error") and metric["error"]["rms"] > THRESHOLDS["attitude_rms_deg"] for metric in attitude_metrics.values()):
        causes.append(["姿态跟随异常，PID/执行器/振动需联合复核", "姿态 setpoint 与 actual 存在较大 RMS 误差，但不能仅凭该指标直接归因为 PID。", "中"])
    if "输出" in event_names:
        causes.append(["控制余量或重心/动力不对称待复核", "有效执行器输出存在饱和或平均输出不平衡，需要结合实机通道映射和机械状态确认。", "中"])
    if power_metrics.get("voltage") and power_metrics["voltage"]["min"] < 10.5:
        causes.append(["供电余量不足待复核", "日志中最低电压偏低，可能影响后段控制性能；需结合电池节数、负载和电源模块校准判断。", "中"])
    if not causes:
        causes.append(["证据不足", "当前日志未显示足够强的异常证据，建议结合实际飞行现象继续确认。", "低"])
    return causes


def build_recommendations(events, risk):
    high = ["检查螺旋桨是否破损或不平衡。", "检查电机座、机架、飞控减震和线束是否松动。", "检查电池、电源线和接插件。"]
    medium = ["每次只小幅调整一个 PID 参数，建议幅度 5%–15%。", "在相同飞行条件下进行对比测试。", "增加日志记录字段，特别是 setpoint、actuator、IMU 和 estimator 数据。"]
    low = ["保留本次报告，与下一次飞行日志进行对比。", "继续优化报告模板和多日志对比功能。"]
    if any("GPS" in item["name"] for item in events):
        high.append("检查 GPS/罗盘安装位置，远离电源线和电调线。")
    if any("输出" in item["name"] for item in events):
        high.append("检查执行器是否卡滞、电机/桨效率是否一致。")
    return {"high": high, "medium": medium, "low": low}


def get_parameter(log: FlightLog, name, fallback):
    try:
        return float(log.parameters.get(name, fallback))
    except (TypeError, ValueError):
        return float(fallback)


def clamp_parameter(name, value):
    if name.endswith("_P"):
        lower, upper = 0.01, 1.5
    elif name.endswith("_I"):
        lower, upper = 0.0, 1.5
    elif name.endswith("_D"):
        lower, upper = 0.0, 0.08
    elif name == "MPC_Z_VEL_P_ACC":
        lower, upper = 0.5, 10.0
    elif name == "MPC_Z_VEL_I_ACC":
        lower, upper = 0.0, 6.0
    elif name == "MPC_Z_VEL_D_ACC":
        lower, upper = 0.0, 2.0
    else:
        lower, upper = 0.0, 10.0
    return round(max(lower, min(upper, value)), 4)


def pid_from_ulg(path, axis="roll", symptom="balanced", aggressiveness=2):
    log = load_flight_log(path)
    analysis = analyze_log(log)
    axis_stats = None
    if axis in {"roll", "pitch", "yaw"}:
        axis_stats = analysis["metrics"]["attitude"][axis]["actual"]
    elif axis == "altitude":
        axis_stats = analysis["metrics"]["position"]["altitude"]
    axis_stats = axis_stats or {"count": 0, "std": 0.0, "span": 0.0}
    scale = 0.04 + max(1, min(5, int(aggressiveness or 2))) * 0.025
    p_scale = i_scale = d_scale = 1.0
    reasons = []
    if symptom == "sluggish":
        p_scale += scale
        reasons.append("日志标记为响应慢：建议小幅提高 P。")
    elif symptom == "oscillation":
        p_scale -= scale
        d_scale += scale * 0.75
        reasons.append("日志标记为震荡：建议降低 P，并提高 D 增加阻尼。")
    elif symptom == "overshoot":
        p_scale -= scale * 0.5
        d_scale += scale
        reasons.append("日志标记为超调：建议提高 D 并略降 P。")
    elif symptom == "drift":
        i_scale += scale
        reasons.append("日志标记为漂移/稳态误差：建议提高 I。")
    elif axis_stats["std"] > 8 or axis_stats["span"] > 30:
        p_scale -= scale * 0.5
        d_scale += scale * 0.7
        reasons.append("日志中该轴波动较大：AI 偏向增加阻尼。")
    else:
        reasons.append("采用保守调参策略，避免一次性调整过大。")
    recommendations = []
    for name, fallback in PID_BASELINES[axis].items():
        current = get_parameter(log, name, fallback)
        factor = p_scale if name.endswith("_P") or name.endswith("_P_ACC") else i_scale if name.endswith("_I") or name.endswith("_I_ACC") else d_scale
        suggested = clamp_parameter(name, current * factor)
        recommendations.append({"name": name, "current": round(current, 4), "suggested": suggested, "deltaPercent": round((suggested - current) / current * 100, 1) if current else 0})
    confidence = 0.55 + (0.2 if axis_stats["count"] > 500 else 0) + (0.1 if log.parameters else 0)
    return {"source": log.path.name, "axis": axis, "axisLabel": AXIS_LABELS[axis], "confidence": round(min(confidence, 0.92), 2), "stats": axis_stats, "recommendations": recommendations, "reasons": reasons, "warning": "该建议基于日志离线分析，不会自动写入飞控。"}


def _metric_value(stats, key, digits=2):
    if not stats or stats.get(key) is None:
        return None
    value = float(stats[key])
    if not math.isfinite(value):
        return None
    return round(value, digits)


def _average(values, digits=2):
    clean = [float(item) for item in values if item is not None and math.isfinite(float(item))]
    if not clean:
        return None
    return round(sum(clean) / len(clean), digits)


def _attitude_rms(metrics):
    return _average([
        metrics["attitude"][axis]["error"]["rms"]
        for axis in ("roll", "pitch", "yaw")
        if metrics["attitude"].get(axis) and metrics["attitude"][axis].get("error")
    ])


def _score_limit(value, warn, fail, higher_is_better=False):
    if value is None:
        return 55
    value = float(value)
    if higher_is_better:
        if value >= warn:
            return 100
        if value <= fail:
            return 45
        return round(45 + (value - fail) / (warn - fail) * 55)
    if value <= warn:
        return 100
    if value >= fail:
        return 45
    return round(100 - (value - warn) / (fail - warn) * 55)


def _health_level(score):
    if score >= 85:
        return "优秀"
    if score >= 70:
        return "可用"
    if score >= 55:
        return "需关注"
    return "高风险"


def _event_count(events, keyword):
    return sum(1 for item in events if keyword in str(item.get("name", "")) or keyword in str(item.get("evidence", "")))


def _verified_events_for_analysis(analysis):
    verified = analysis.get("verified") or build_verified_analysis_summary(analysis)
    analysis["verified"] = verified
    return [item for item in verified.get("abnormal_events", []) if item.get("is_abnormal")]


def _verified_missing_items(analysis):
    verified = analysis.get("verified") or build_verified_analysis_summary(analysis)
    analysis["verified"] = verified
    missing = verified.get("missing_data") or {}
    if isinstance(missing, dict):
        return list(missing.get("items") or [])
    return list(missing or analysis.get("missing") or [])


def summarize_flight_log(path):
    log = load_flight_log(path)
    analysis = analyze_log(log)
    verified = analysis.get("verified") or build_verified_analysis_summary(analysis)
    analysis["verified"] = verified
    verified_events = _verified_events_for_analysis(analysis)
    missing_items = _verified_missing_items(analysis)
    risk = (verified.get("risk_assessment") or {}).get("level_cn", "低")
    metrics = analysis["metrics"]
    attitude_rms = _attitude_rms(metrics)
    overview = {
        "source": log.path.name,
        "kind": log.kind,
        "durationSeconds": round(float(log.duration_s or 0), 2),
        "topicCount": len(log.topics),
        "eventCount": len(verified_events),
        "risk": risk,
        "missing": missing_items,
        "maxAltitude": _metric_value(metrics["position"]["altitude"], "max"),
        "maxSpeed": _metric_value(metrics["position"]["horizontal_speed"], "max"),
        "distanceMeters": _metric_value({"value": metrics["position"]["distance_m"]}, "value") if metrics["position"].get("distance_m") is not None else None,
        "minVoltage": _metric_value(metrics["power"]["voltage"], "min"),
        "maxCurrent": _metric_value(metrics["power"]["current"], "max"),
        "minSatellites": _metric_value(metrics["gps"]["satellites"], "min", 0),
        "minFixType": _metric_value(metrics["gps"]["fix_type"], "min", 0),
        "maxEph": _metric_value(metrics["gps"]["eph"], "max"),
        "gyroStd": _metric_value(metrics["sensor"]["gyro_norm"], "std"),
        "accelStd": _metric_value(metrics["sensor"]["accel_norm"], "std"),
        "attitudeRms": attitude_rms,
        "gpsEvents": _event_count(verified_events, "GPS"),
        "powerEvents": _event_count(verified_events, "电"),
        "sensorEvents": _event_count(verified_events, "振动") + _event_count(verified_events, "gyro") + _event_count(verified_events, "accel"),
    }
    overview["sensorHealthScore"] = sensor_health_from_analysis(log, analysis)["overallScore"]
    return overview


def compare_flight_logs(paths):
    if len(paths) < 2:
        raise ValueError("多日志对比至少需要上传 2 个日志文件")
    logs = [summarize_flight_log(path) for path in paths]
    baseline = logs[0]
    for item in logs:
        item["delta"] = {}
        for key in ("durationSeconds", "maxAltitude", "maxSpeed", "minVoltage", "gyroStd", "accelStd", "attitudeRms", "sensorHealthScore"):
            base = baseline.get(key)
            value = item.get(key)
            item["delta"][key] = round(value - base, 2) if base is not None and value is not None else None

    recommendations = []
    worst_health = min(logs, key=lambda item: item.get("sensorHealthScore", 0))
    if worst_health.get("sensorHealthScore", 100) < 70:
        recommendations.append(f"{worst_health['source']} 的传感器健康分最低，建议优先检查 IMU/GPS/电源数据。")
    worst_vibration = max([item for item in logs if item.get("gyroStd") is not None], key=lambda item: item["gyroStd"], default=None)
    if worst_vibration and worst_vibration["gyroStd"] > THRESHOLDS["vibration_gyro_norm_std"]:
        recommendations.append(f"{worst_vibration['source']} 的 gyro norm 标准差偏高，优先排查桨叶平衡、机架松动和飞控减震。")
    worst_gps = min([item for item in logs if item.get("minSatellites") is not None], key=lambda item: item["minSatellites"], default=None)
    if worst_gps and worst_gps["minSatellites"] < THRESHOLDS["gps_min_satellites"]:
        recommendations.append(f"{worst_gps['source']} 的最低卫星数偏低，建议检查 GPS 天线位置、遮挡和电磁干扰。")
    worst_attitude = max([item for item in logs if item.get("attitudeRms") is not None], key=lambda item: item["attitudeRms"], default=None)
    if worst_attitude and worst_attitude["attitudeRms"] > THRESHOLDS["attitude_rms_deg"]:
        recommendations.append(f"{worst_attitude['source']} 的姿态跟随 RMS 误差最大，建议结合 PID 和执行器余量分析。")
    if not recommendations:
        recommendations.append("本组日志未发现明显劣化项，建议保留本次基线，后续继续用同场景日志做趋势对比。")

    return {
        "count": len(logs),
        "baseline": baseline["source"],
        "cards": {
            "日志数量": f"{len(logs)} 份",
            "最长飞行": f"{max(item['durationSeconds'] for item in logs):.1f}s",
            "最高速度": _format_optional(max((item.get("maxSpeed") for item in logs if item.get("maxSpeed") is not None), default=None), "m/s"),
            "最低健康分": f"{worst_health.get('sensorHealthScore', 0)} / 100",
            "总异常事件": f"{sum(item.get('eventCount', 0) for item in logs)} 条",
        },
        "logs": logs,
        "recommendations": recommendations,
    }


def _format_optional(value, unit="", digits=2):
    if value is None:
        return "日志未包含"
    return f"{float(value):.{digits}f}{unit}"


def sensor_health_from_analysis(log, analysis):
    metrics = analysis["metrics"]
    events = _verified_events_for_analysis(analysis)
    missing = _verified_missing_items(analysis)
    verified = analysis.get("verified") or {}
    risk = (verified.get("risk_assessment") or {}).get("level_cn", analysis.get("risk", "低"))

    gyro_std = _metric_value(metrics["sensor"]["gyro_norm"], "std")
    accel_std = _metric_value(metrics["sensor"]["accel_norm"], "std")
    imu_score = round(min(_score_limit(gyro_std, 0.45, 1.2), _score_limit(accel_std, 3.0, 7.5)))
    if gyro_std is None and accel_std is None:
        imu_score = 45

    min_sat = _metric_value(metrics["gps"]["satellites"], "min", 0)
    min_fix = _metric_value(metrics["gps"]["fix_type"], "min", 0)
    max_eph = _metric_value(metrics["gps"]["eph"], "max")
    gps_parts = [
        _score_limit(min_sat, 10, 6, True),
        _score_limit(min_fix, 3, 2, True),
        _score_limit(max_eph, 1.5, 5.0),
    ]
    gps_score = round(_average(gps_parts, 0) or 50)
    if min_sat is None and min_fix is None and max_eph is None:
        gps_score = 45

    voltage_min = _metric_value(metrics["power"]["voltage"], "min")
    voltage_span = _metric_value(metrics["power"]["voltage"], "span")
    current_max = _metric_value(metrics["power"]["current"], "max")
    power_score = round(_average([
        _score_limit(voltage_span, 1.2, 4.0),
        100 if voltage_min is not None else 55,
        _score_limit(current_max, 45, 95),
    ], 0) or 55)

    attitude_rms = _attitude_rms(metrics)
    attitude_score = _score_limit(attitude_rms, 3.0, 10.0)
    if attitude_rms is None:
        attitude_score = 65

    actuator_penalty = min(45, _event_count(events, "输出") * 15 + _event_count(events, "饱和") * 20)
    actuator_score = max(45, 100 - actuator_penalty)
    if "执行器输出数据" in missing:
        actuator_score = 60

    completeness_score = max(40, 100 - len(missing) * 8)
    items = [
        {
            "name": "IMU 振动健康",
            "score": imu_score,
            "level": _health_level(imu_score),
            "evidence": f"gyro std={_format_optional(gyro_std)}，accel std={_format_optional(accel_std)}",
            "advice": "分数偏低时先检查桨叶、电机轴、机架螺丝和飞控减震，再考虑 PID。"
        },
        {
            "name": "GPS / 导航健康",
            "score": gps_score,
            "level": _health_level(gps_score),
            "evidence": f"最低卫星={_format_optional(min_sat, '', 0)}，最低 Fix={_format_optional(min_fix, '', 0)}，最大 EPH={_format_optional(max_eph)}",
            "advice": "卫星数或 Fix 不稳定时，不建议执行自动航线、返航或定点任务。"
        },
        {
            "name": "电源链路健康",
            "score": power_score,
            "level": _health_level(power_score),
            "evidence": f"最低电压={_format_optional(voltage_min, 'V')}，电压波动={_format_optional(voltage_span, 'V')}，最大电流={_format_optional(current_max, 'A')}",
            "advice": "关注电池内阻、接插件、供电线和大机动时的电流尖峰。"
        },
        {
            "name": "姿态估计/控制健康",
            "score": attitude_score,
            "level": _health_level(attitude_score),
            "evidence": f"姿态平均 RMS 误差={_format_optional(attitude_rms, 'deg')}",
            "advice": "如果误差偏高，结合 setpoint、执行器输出和振动数据判断是 PID、动力余量还是机械问题。"
        },
        {
            "name": "执行器输出健康",
            "score": actuator_score,
            "level": _health_level(actuator_score),
            "evidence": f"执行器相关异常={_event_count(events, '输出') + _event_count(events, '饱和')} 条",
            "advice": "出现饱和或不平衡时，优先检查重心、电机/桨一致性和控制余量。"
        },
        {
            "name": "日志完整性",
            "score": completeness_score,
            "level": _health_level(completeness_score),
            "evidence": "缺失项：" + ("、".join(missing) if missing else "无关键缺失"),
            "advice": "缺少 topic 时结论会保守，建议开启 GPS、电池、IMU、actuator、setpoint 等关键记录。"
        },
    ]
    overall = round(sum(item["score"] for item in items) / len(items))
    return {
        "source": log.path.name,
        "overallScore": overall,
        "level": _health_level(overall),
        "items": items,
        "eventCount": len(events),
        "missing": missing,
        "risk": risk,
    }


def sensor_health_reports(paths):
    reports = []
    for path in paths:
        log = load_flight_log(path)
        analysis = analyze_log(log)
        reports.append(sensor_health_from_analysis(log, analysis))
    if not reports:
        raise ValueError("未收到可分析的日志文件")
    best = max(reports, key=lambda item: item["overallScore"])
    worst = min(reports, key=lambda item: item["overallScore"])
    return {
        "count": len(reports),
        "summary": {
            "平均健康分": f"{round(sum(item['overallScore'] for item in reports) / len(reports))} / 100",
            "最佳日志": f"{best['source']} ({best['overallScore']})",
            "最需关注": f"{worst['source']} ({worst['overallScore']})",
            "异常事件": f"{sum(item['eventCount'] for item in reports)} 条",
        },
        "reports": reports,
    }


def plot_line(path, series, title, ylabel):
    if plt is None:
        return False
    plt.figure(figsize=(7.4, 3.4), dpi=160)
    plotted = False
    for label, time_s, values in series:
        if time_s is None or values is None or len(time_s) == 0:
            continue
        count = min(len(time_s), len(values))
        if count < 2:
            continue
        plt.plot(time_s[:count], values[:count], label=label, linewidth=1.1)
        plotted = True
    if not plotted:
        plt.close()
        return False
    plt.title(title)
    plt.xlabel("时间 (s)")
    plt.ylabel(ylabel)
    plt.grid(True, alpha=0.28)
    plt.legend(loc="best")
    plt.tight_layout()
    plt.savefig(path)
    plt.close()
    return True


def plot_track(path, gps, position):
    if plt is None:
        return False
    plt.figure(figsize=(6.4, 4.3), dpi=160)
    plotted = False
    if gps and "lat" in gps.fields and "lon" in gps.fields and len(gps.fields["lat"]) > 1:
        plt.plot(gps.fields["lon"], gps.fields["lat"], linewidth=1.2)
        plt.xlabel("经度")
        plt.ylabel("纬度")
        plt.title("GPS 轨迹")
        plotted = True
    elif position and "x" in position.fields and "y" in position.fields and len(position.fields["x"]) > 1:
        plt.plot(position.fields["x"], position.fields["y"], linewidth=1.2)
        plt.xlabel("本地 X (m)")
        plt.ylabel("本地 Y (m)")
        plt.title("本地位置轨迹")
        plotted = True
    if not plotted:
        plt.close()
        return False
    plt.grid(True, alpha=0.28)
    plt.tight_layout()
    plt.savefig(path)
    plt.close()
    return True


def first_field(dataset, *names):
    if not dataset:
        return None
    for name in names:
        if name in dataset.fields:
            return dataset.fields[name]
    return None


def generate_charts(analysis, assets_dir):
    if plt is None:
        return []
    assets_dir.mkdir(parents=True, exist_ok=True)
    topics = analysis["topics"]
    charts = []
    attitude = topics["attitude"]
    attitude_sp = topics["attitude_sp"]
    rates = topics["rates"]
    rates_sp = topics["rates_sp"]
    position = topics["position"]
    battery = topics["battery"]
    actuator = topics["actuator"]
    gps = topics["gps"]
    sensor = topics["sensor"]

    for axis in ("roll", "pitch", "yaw"):
        series = []
        if attitude and axis in attitude.fields:
            series.append(("actual", attitude.time_s, attitude.fields[axis]))
        if attitude_sp and f"{axis}_sp" in attitude_sp.fields:
            series.append(("setpoint", attitude_sp.time_s, attitude_sp.fields[f"{axis}_sp"]))
        chart = assets_dir / f"{axis}_attitude.png"
        if plot_line(chart, series, f"{AXIS_LABELS[axis]} setpoint vs actual", "角度 (deg)"):
            charts.append((f"{AXIS_LABELS[axis]}姿态跟随", chart))

    for axis in ("roll", "pitch", "yaw"):
        series = []
        if rates and f"{axis}_rate" in rates.fields:
            series.append(("rate actual", rates.time_s, rates.fields[f"{axis}_rate"]))
        if rates_sp and f"{axis}_rate_sp" in rates_sp.fields:
            series.append(("rate setpoint", rates_sp.time_s, rates_sp.fields[f"{axis}_rate_sp"]))
        chart = assets_dir / f"{axis}_rate.png"
        if plot_line(chart, series, f"{AXIS_LABELS[axis]}角速度 setpoint vs actual", "角速度 (deg/s)"):
            charts.append((f"{AXIS_LABELS[axis]}角速度", chart))

    if position:
        chart = assets_dir / "altitude.png"
        if plot_line(chart, [("高度", position.time_s, position.fields.get("altitude"))], "高度随时间变化", "高度 (m)"):
            charts.append(("高度", chart))
        chart = assets_dir / "vertical_velocity.png"
        if plot_line(chart, [("垂直速度", position.time_s, position.fields.get("vertical_velocity"))], "垂直速度随时间变化", "速度 (m/s)"):
            charts.append(("垂直速度", chart))
    if battery:
        chart = assets_dir / "battery_voltage_current.png"
        if plot_line(chart, [("电压", battery.time_s, battery.fields.get("voltage")), ("电流", battery.time_s, battery.fields.get("current"))], "电池电压 / 电流", "V / A"):
            charts.append(("电池电压电流", chart))
    if actuator:
        series = [(key, actuator.time_s, values) for key, values in list(actuator.fields.items())[:8]]
        chart = assets_dir / "actuator_outputs.png"
        if plot_line(chart, series, "电机 / 执行器输出", "输出"):
            charts.append(("执行器输出", chart))
    chart = assets_dir / "track.png"
    if plot_track(chart, gps, position):
        charts.append(("轨迹", chart))
    if gps:
        chart = assets_dir / "gps_quality.png"
        gps_accuracy = first_field(gps, "eph", "hdop", "epv", "vdop")
        if plot_line(chart, [("卫星数", gps.time_s, gps.fields.get("satellites")), ("Fix Type", gps.time_s, gps.fields.get("fix_type")), ("EPH/HDOP", gps.time_s, gps_accuracy)], "GPS 质量", "数量 / 精度"):
            charts.append(("GPS 质量", chart))
    if sensor:
        chart = assets_dir / "vibration.png"
        if plot_line(chart, [("gyro norm", sensor.time_s, sensor.fields.get("gyro_norm")), ("accel norm", sensor.time_s, sensor.fields.get("accel_norm"))], "IMU 振动指标", "norm"):
            charts.append(("IMU 振动", chart))
    verified_events = _verified_events_for_analysis(analysis)
    if verified_events:
        chart = assets_dir / "warning_timeline.png"
        plt.figure(figsize=(7.4, 1.9), dpi=160)
        times = [event.get("time", event.get("start_s", 0)) for event in verified_events]
        levels = [{"低": 1, "中": 2, "高": 3}.get(event.get("risk"), 1) for event in verified_events]
        plt.scatter(times, levels, c=levels, cmap="autumn", s=38)
        plt.yticks([1, 2, 3], ["低", "中", "高"])
        plt.xlabel("时间 (s)")
        plt.title("告警 / 异常事件时间线")
        plt.grid(True, alpha=0.25)
        plt.tight_layout()
        plt.savefig(chart)
        plt.close()
        charts.append(("告警事件时间线", chart))
    return charts


def markdown_table(rows):
    if not rows:
        return ""
    header = "| " + " | ".join(str(item) for item in rows[0]) + " |\n"
    sep = "| " + " | ".join("---" for _ in rows[0]) + " |\n"
    body = "".join("| " + " | ".join(str(item).replace("\n", " ") for item in row) + " |\n" for row in rows[1:])
    return header + sep + body


def window_values(dataset, field, start_s, end_s):
    if not dataset or field not in dataset.fields or dataset.time_s is None:
        return np.array([], dtype=float), np.array([], dtype=float)
    count = min(len(dataset.time_s), len(dataset.fields[field]))
    if count == 0:
        return np.array([], dtype=float), np.array([], dtype=float)
    time_s = np.asarray(dataset.time_s[:count], dtype=float)
    values = np.asarray(dataset.fields[field][:count], dtype=float)
    mask = np.isfinite(time_s) & np.isfinite(values) & (time_s >= start_s) & (time_s <= end_s)
    return time_s[mask], values[mask]


def window_error_stats(actual_dataset, setpoint_dataset, actual_field, setpoint_field, start_s, end_s):
    if not actual_dataset or not setpoint_dataset or actual_field not in actual_dataset.fields or setpoint_field not in setpoint_dataset.fields:
        return None
    actual_time, actual_values = window_values(actual_dataset, actual_field, start_s, end_s)
    if len(actual_time) < 2:
        return None
    sp_count = min(len(setpoint_dataset.time_s), len(setpoint_dataset.fields[setpoint_field]))
    if sp_count < 2:
        return None
    sp_time = np.asarray(setpoint_dataset.time_s[:sp_count], dtype=float)
    sp_values = np.asarray(setpoint_dataset.fields[setpoint_field][:sp_count], dtype=float)
    mask = np.isfinite(sp_time) & np.isfinite(sp_values)
    sp_time = sp_time[mask]
    sp_values = sp_values[mask]
    if len(sp_time) < 2:
        return None
    indices = np.searchsorted(sp_time, actual_time)
    left = np.clip(indices - 1, 0, len(sp_time) - 1)
    right = np.clip(indices, 0, len(sp_time) - 1)
    nearest_dt = np.minimum(np.abs(actual_time - sp_time[left]), np.abs(actual_time - sp_time[right]))
    valid = nearest_dt <= 0.25
    if np.count_nonzero(valid) < 2:
        return None
    actual_time = actual_time[valid]
    actual_values = actual_values[valid]
    setpoint_values = np.interp(actual_time, sp_time, sp_values)
    if actual_field in {"yaw", "heading", "course"} or setpoint_field in {"yaw_sp", "heading_sp", "course_sp"}:
        error = circular_error_deg(actual_values, setpoint_values)
    else:
        error = actual_values - setpoint_values
    return {"max": float(np.max(np.abs(error))), "mean": float(np.mean(np.abs(error))), "bias": float(np.mean(error)), "rms": float(np.sqrt(np.mean(error ** 2)))}


def _percent_phases(duration, specs, basis):
    return [
        {"name": name, "start": duration * start, "end": duration * end, "basis": basis}
        for name, start, end in specs
        if duration * end > duration * start
    ]


def _transition_windows(status):
    if not status:
        return None, None
    transition = dataset_field(status, "in_transition_mode", "transition_mode")
    if transition is None:
        return None, None
    count = min(len(status.time_s), len(transition))
    if count < 2:
        return None, None
    time_s = np.asarray(status.time_s[:count], dtype=float)
    values = np.asarray(transition[:count], dtype=float)
    valid = np.isfinite(time_s) & np.isfinite(values)
    time_s = time_s[valid]
    values = values[valid]
    if len(time_s) < 2:
        return None, None
    mask = np.abs(values) > 0.5
    indices = np.where(mask)[0]
    if len(indices) == 0:
        return None, None

    dt = np.diff(time_s)
    median_dt = float(np.median(dt[np.isfinite(dt) & (dt > 0)])) if len(dt) else 0.1
    max_gap = max(2.0, median_dt * 4)
    groups = []
    current = [indices[0]]
    for index in indices[1:]:
        if time_s[index] - time_s[current[-1]] <= max_gap:
            current.append(index)
        else:
            groups.append(current)
            current = [index]
    groups.append(current)

    to_fw = dataset_field(status, "in_transition_to_fw")
    front = None
    back = None
    for order, group in enumerate(groups):
        start = float(time_s[group[0]])
        end = float(time_s[group[-1]])
        if end <= start:
            end = start + max(1.0, median_dt)
        direction = None
        if to_fw is not None:
            dir_count = min(len(status.time_s), len(to_fw))
            dir_values = np.asarray(to_fw[:dir_count], dtype=float)
            original_indices = np.asarray(group)
            original_indices = original_indices[original_indices < len(dir_values)]
            if len(original_indices):
                direction = "front" if truth_fraction(dir_values[original_indices]) >= 0.5 else "back"
        if direction == "front" or (direction is None and order == 0):
            front = (start, end)
        elif direction == "back" or direction is None:
            back = (start, end)
    return front, back


def _speed_stats(position, gps=None):
    speed = dataset_field(position, "horizontal_speed")
    if speed is None:
        speed = dataset_field(gps, "gps_speed")
    stats = safe_stats(speed)
    return stats or {}


def _vtol_vertical_only_phases(log, position):
    duration = float(log.duration_s or 0)
    if duration <= 0:
        return []
    altitude = dataset_field(position, "altitude")
    if altitude is None:
        return _percent_phases(duration, [
            ("旋翼起飞", 0.00, 0.25),
            ("悬停/垂直飞行", 0.25, 0.75),
            ("旋翼降落", 0.75, 1.00),
        ], "未检测到转换标记且缺少高度曲线，按垂直起降三阶段比例近似")

    count = min(len(position.time_s), len(altitude))
    if count < 5:
        return _percent_phases(duration, [
            ("旋翼起飞", 0.00, 0.25),
            ("悬停/垂直飞行", 0.25, 0.75),
            ("旋翼降落", 0.75, 1.00),
        ], "未检测到转换标记且高度点不足，按垂直起降三阶段比例近似")

    time_s = np.asarray(position.time_s[:count], dtype=float)
    alt = np.asarray(altitude[:count], dtype=float)
    valid = np.isfinite(time_s) & np.isfinite(alt)
    time_s = time_s[valid]
    alt = alt[valid]
    if len(time_s) < 5:
        return _percent_phases(duration, [
            ("旋翼起飞", 0.00, 0.25),
            ("悬停/垂直飞行", 0.25, 0.75),
            ("旋翼降落", 0.75, 1.00),
        ], "未检测到转换标记且高度有效点不足，按垂直起降三阶段比例近似")

    alt_rel = alt - float(np.nanmin(alt))
    max_alt = float(np.nanmax(alt_rel))
    airborne = np.where(alt_rel > max(1.5, max_alt * 0.12))[0]
    if len(airborne) < 2:
        return _percent_phases(duration, [
            ("旋翼起飞", 0.00, 0.25),
            ("悬停/垂直飞行", 0.25, 0.75),
            ("旋翼降落", 0.75, 1.00),
        ], "未检测到转换标记且高度变化不足，按垂直起降三阶段比例近似")

    takeoff_end = min(max(float(time_s[airborne[0]]) + duration * 0.08, duration * 0.10), duration * 0.40)
    landing_start = max(min(float(time_s[airborne[-1]]) - duration * 0.08, duration * 0.90), takeoff_end + duration * 0.10)
    return [
        {"name": "旋翼起飞", "start": 0.0, "end": takeoff_end, "basis": "未检测到 transition 标记，按高度离地段识别旋翼起飞"},
        {"name": "悬停/垂直飞行", "start": takeoff_end, "end": landing_start, "basis": "未检测到前/后转换且水平速度不满足固定翼巡航特征，按垂直飞行测试分析"},
        {"name": "旋翼降落", "start": landing_start, "end": duration, "basis": "未检测到 transition 标记，按末段高度回落识别旋翼降落"},
    ]


def _vtol_attitude_phases(log, position):
    duration = float(log.duration_s or 0)
    if duration <= 0:
        return []

    front, back = _transition_windows(log.topics.get("vehicle_status"))
    if not front and not back:
        speed = _speed_stats(position, log.topics.get("mapped_gps"))
        max_speed = float(speed.get("max", 0) or 0)
        mean_speed = float(speed.get("mean", 0) or 0)
        if max_speed < 10.0 and mean_speed < 6.0:
            return _vtol_vertical_only_phases(log, position)
        specs = [
            ("旋翼起飞", 0.00, 0.15),
            ("前转换", 0.15, 0.30),
            ("巡航", 0.30, 0.72),
            ("后转换", 0.72, 0.86),
            ("旋翼降落", 0.86, 1.00),
        ]
        return _percent_phases(duration, specs, "未检测到 transition 标记，但水平速度具有固定翼巡航特征，按复合翼五阶段近似")

    fs, fe = front if front else (duration * 0.15, duration * 0.30)
    bs, be = back if back else (duration * 0.72, duration * 0.86)
    fs = min(max(fs, duration * 0.02), duration * 0.55)
    fe = min(max(fe, fs + max(1.0, duration * 0.02)), duration * 0.65)
    bs = min(max(bs, fe + max(1.0, duration * 0.04)), duration * 0.92)
    be = min(max(be, bs + max(1.0, duration * 0.02)), duration * 0.98)
    return [
        {"name": "旋翼起飞", "start": 0.0, "end": fs, "basis": "从日志开始到前转换开始，按复合翼旋翼起飞阶段划分"},
        {"name": "前转换", "start": fs, "end": fe, "basis": "依据 vehicle_status transition 标记识别前转换窗口" if front else "未检测到前转换标记，按五阶段比例近似"},
        {"name": "巡航", "start": fe, "end": bs, "basis": "前转换结束到后转换开始，作为固定翼巡航/平飞段分析"},
        {"name": "后转换", "start": bs, "end": be, "basis": "依据 vehicle_status transition 标记识别后转换窗口" if back else "未检测到后转换标记，按五阶段比例近似"},
        {"name": "旋翼降落", "start": be, "end": duration, "basis": "后转换结束到日志结束，按复合翼旋翼降落阶段划分"},
    ]


def _fixed_wing_attitude_phases(log, position):
    duration = float(log.duration_s or 0)
    if duration <= 0:
        return []
    specs = [
        ("滑行", 0.00, 0.08),
        ("起飞", 0.08, 0.16),
        ("爬升", 0.16, 0.32),
        ("巡航", 0.32, 0.68),
        ("下降", 0.68, 0.82),
        ("进近", 0.82, 0.93),
        ("着陆", 0.93, 1.00),
    ]
    if not position or duration < 20:
        return _percent_phases(duration, specs, "缺少足够高度/速度信息，按固定翼七阶段比例近似")

    altitude = dataset_field(position, "altitude")
    if altitude is None:
        return _percent_phases(duration, specs, "缺少高度信息，按固定翼七阶段比例近似")
    count = min(len(position.time_s), len(altitude))
    if count < 5:
        return _percent_phases(duration, specs, "高度点数量不足，按固定翼七阶段比例近似")
    time_s = np.asarray(position.time_s[:count], dtype=float)
    alt = np.asarray(altitude[:count], dtype=float)
    valid = np.isfinite(time_s) & np.isfinite(alt)
    time_s = time_s[valid]
    alt = alt[valid]
    if len(time_s) < 5:
        return _percent_phases(duration, specs, "高度有效点数量不足，按固定翼七阶段比例近似")

    alt_rel = alt - float(np.nanmin(alt))
    max_alt = float(np.nanmax(alt_rel))
    if max_alt < 3:
        return _percent_phases(duration, specs, "高度变化不足，按固定翼七阶段比例近似")

    airborne = np.where(alt_rel > max(2.0, max_alt * 0.05))[0]
    cruise = np.where(alt_rel > max(3.0, max_alt * 0.72))[0]
    first_air = float(time_s[airborne[0]]) if len(airborne) else duration * 0.12
    last_air = float(time_s[airborne[-1]]) if len(airborne) else duration * 0.94
    cruise_start = float(time_s[cruise[0]]) if len(cruise) else duration * 0.32
    cruise_end = float(time_s[cruise[-1]]) if len(cruise) else duration * 0.68

    taxi_end = min(max(first_air, duration * 0.04), duration * 0.14)
    takeoff_end = min(max(first_air + duration * 0.06, taxi_end + duration * 0.04), duration * 0.25)
    climb_end = min(max(cruise_start, takeoff_end + duration * 0.08), duration * 0.45)
    cruise_end = min(max(cruise_end, climb_end + duration * 0.10), duration * 0.78)
    approach_start = min(max(last_air - duration * 0.14, cruise_end + duration * 0.04), duration * 0.88)
    landing_start = min(max(last_air - duration * 0.04, approach_start + duration * 0.04), duration * 0.96)
    bounds = [0.0, taxi_end, takeoff_end, climb_end, cruise_end, approach_start, landing_start, duration]
    if any(bounds[index + 1] <= bounds[index] for index in range(len(bounds) - 1)):
        return _percent_phases(duration, specs, "高度边界无法稳定排序，按固定翼七阶段比例近似")

    names = ["滑行", "起飞", "爬升", "巡航", "下降", "进近", "着陆"]
    bases = [
        "日志开始到离地前，按地面滑行阶段分析",
        "离地附近的快速姿态变化窗口，按固定翼起飞阶段分析",
        "高度持续增加到高空段前，按爬升阶段分析",
        "高高度平台段，按巡航阶段分析",
        "巡航后高度降低段，按下降阶段分析",
        "落地前低高度收敛段，按进近阶段分析",
        "最后接地和滑跑结束段，按着陆阶段分析",
    ]
    return [
        {"name": name, "start": bounds[index], "end": bounds[index + 1], "basis": bases[index]}
        for index, name in enumerate(names)
    ]


def infer_attitude_phases(log, position, airframe=None):
    airframe = airframe or detect_airframe_type(log)
    if airframe.get("kind") == "compound_vtol":
        return _vtol_attitude_phases(log, position)
    return _fixed_wing_attitude_phases(log, position)


def attitude_phase_analysis(analysis):
    log = analysis["log"]
    topics = analysis["topics"]
    attitude = topics["attitude"]
    attitude_sp = topics["attitude_sp"]
    position = topics["position"]
    airframe = analysis.get("airframe") or detect_airframe_type(log)
    verified = analysis.get("verified") or {}
    verified_phase = verified.get("phase_segments") or {}
    if verified_phase.get("segments"):
        phase_rows = [["机型判定", "阶段", "时间范围", "划分依据", "方法", "置信度"]]
        axis_rows = [["阶段", "轴", "实际均值", "实际范围", "最大误差", "RMS 误差", "反向次数", "阶段判定"]]
        conclusions = [
            f"机型识别为 {airframe['label']}，置信度约 {airframe['confidence'] * 100:.0f}%。阶段划分来自 verified_flight_phases。"
        ]
        for phase in verified_phase.get("segments", []):
            start_s = float(phase["start_s"])
            end_s = float(phase["end_s"])
            phase_rows.append([
                airframe["label"],
                phase["name"],
                f"{start_s:.1f}s - {end_s:.1f}s",
                phase.get("evidence") or phase.get("basis"),
                phase.get("method", "verified_phase_detector"),
                phase.get("confidence", "Low"),
            ])
            for axis in ("roll", "pitch", "yaw"):
                _time, values = window_values(attitude, axis, start_s, end_s)
                if axis == "yaw" and len(values):
                    span = circular_range_deg(values)
                    stats = safe_stats(np.degrees(np.unwrap(np.radians(finite_array(values)))))
                    reversals = zero_crossings(np.degrees(np.unwrap(np.radians(finite_array(values)))))
                else:
                    stats = safe_stats(values)
                    span = stats["span"] if stats else None
                    reversals = zero_crossings(values) if len(values) else 0
                err = window_error_stats(attitude, attitude_sp, axis, f"{axis}_sp", start_s, end_s)
                verdict = "阶段置信度低，仅作趋势参考" if phase.get("confidence") != "High" else "未发现明确异常/需结合任务动作"
                axis_rows.append([
                    phase["name"],
                    AXIS_LABELS[axis],
                    metric(stats["mean"] if stats else None, " deg"),
                    metric(span if span is not None else None, " deg") if axis == "yaw" else metric(stats["span"] if stats else None, " deg"),
                    metric(err["max"] if err else None, " deg") if err else "缺少 setpoint",
                    metric(err["rms"] if err else None, " deg") if err else "缺少 setpoint",
                    str(reversals) if stats else "日志未包含",
                    verdict,
                ])
        if verified_phase.get("confidence") != "High":
            conclusions.append(f"阶段划分置信度为 {verified_phase.get('confidence')}，不能作为确定事实，需要人工复核。")
        return {"phaseRows": phase_rows, "axisRows": axis_rows, "conclusions": conclusions, "airframe": airframe}
    phases = infer_attitude_phases(log, position, airframe)
    if not attitude or not phases:
        return {
            "phaseRows": [["阶段", "时间范围", "划分依据"], ["无法划分", "--", "日志缺少有效姿态时间序列或飞行时长"]],
            "axisRows": [["阶段", "轴", "实际均值", "实际范围", "最大误差", "RMS 误差", "反向次数", "阶段判定"]],
            "conclusions": [f"已选择阶段模板：{airframe['template']}。日志缺少可用于分阶段姿态分析的数据。"],
            "airframe": airframe,
        }

    phase_rows = [["机型判定", "阶段", "时间范围", "划分依据"]]
    axis_rows = [["阶段", "轴", "实际均值", "实际范围", "最大误差", "RMS 误差", "反向次数", "阶段判定"]]
    conclusions = [
        f"机型识别为 {airframe['label']}，置信度约 {airframe['confidence'] * 100:.0f}%，采用{airframe['template']}。"
    ]
    for phase in phases:
        start_s = float(phase["start"])
        end_s = float(phase["end"])
        phase_rows.append([airframe["label"], phase["name"], f"{start_s:.1f}s - {end_s:.1f}s", phase["basis"]])
        phase_flags = []
        for axis in ("roll", "pitch", "yaw"):
            _time, values = window_values(attitude, axis, start_s, end_s)
            stats = safe_stats(values)
            err = window_error_stats(attitude, attitude_sp, axis, f"{axis}_sp", start_s, end_s)
            reversals = zero_crossings(values) if len(values) else 0
            flags = []
            if err and err["rms"] > THRESHOLDS["attitude_rms_deg"]:
                flags.append("跟随误差偏大")
            if stats and stats["span"] > (60 if axis == "yaw" else 25):
                flags.append("姿态变化幅度大")
            if reversals > THRESHOLDS["oscillation_zero_crossings"]:
                flags.append("存在振荡倾向")
            verdict = "、".join(flags) if flags else "平稳/证据不足"
            if flags:
                phase_flags.extend(flags)
            axis_rows.append([
                phase["name"],
                AXIS_LABELS[axis],
                metric(stats["mean"] if stats else None, " deg"),
                metric(stats["span"] if stats else None, " deg"),
                metric(err["max"] if err else None, " deg") if err else "缺少 setpoint",
                metric(err["rms"] if err else None, " deg") if err else "缺少 setpoint",
                str(reversals) if stats else "日志未包含",
                verdict,
            ])
        unique_flags = "、".join(dict.fromkeys(phase_flags))
        if unique_flags:
            conclusions.append(f"{phase['name']}需要关注：{unique_flags}。")
        else:
            conclusions.append(f"{phase['name']}未发现明显姿态异常；若该阶段实际发生剧烈机动，应结合任务动作进一步复核。")
    return {"phaseRows": phase_rows, "axisRows": axis_rows, "conclusions": conclusions, "airframe": airframe}


def build_report_model(analysis, charts):
    log = analysis["log"]
    metrics = analysis["metrics"]
    max_alt = metrics["position"]["altitude"]
    max_speed = metrics["position"]["horizontal_speed"]
    voltage = metrics["power"]["voltage"]
    current = metrics["power"]["current"]
    gps_sat = metrics["gps"]["satellites"]
    airframe = analysis.get("airframe") or detect_airframe_type(log)
    verified = analysis.get("verified") or build_verified_analysis_summary(analysis)
    data_quality = verified["data_quality"]
    flight_events = verified["flight_events"]
    warning_counts = verified["warning_events"]["counts"]
    battery_quality = verified["battery_metrics"]
    gps_quality = verified["gps_metrics"]
    abnormal_events = [item for item in verified.get("abnormal_events", []) if item.get("is_abnormal")]
    root_items = verified.get("root_cause_analysis") or []
    risk_assessment = verified.get("risk_assessment") or {"level_cn": "低", "summary": "未生成 verified 风险评估"}
    top_event = abnormal_events[0]["name"] if abnormal_events else "未发现可确认的高风险异常"
    root = root_items[0]["candidate"] if root_items else "证据不足"
    summary = (
        f"本次日志总时长为 {metric(log.duration_s, ' s')}，有效飞行段为 "
        f"{metric(flight_events.get('effective_airborne_time'), ' s')}，识别机型为 {airframe['label']}。"
        f"数据完整性为 {data_quality.get('data_completeness')}，工程结论可信度为 {data_quality.get('engineering_confidence')}，"
        f"综合报告可信度为 {data_quality.get('overall_report_confidence')}（{data_quality['score']} 分）。"
        f"verified 风险等级为 {risk_assessment.get('level_cn', '低')}：{risk_assessment.get('summary', '')}"
        f"当前最需要复核的事件为：{top_event}。"
        f"电流数据质量为 {battery_quality['quality']}，GPS 数据质量为 {gps_quality['quality']}。"
        f"根因结论采用保守分级：{root}；PID 只作为人工复核后的可能候选，不作为默认根因。"
    )
    cards = {
        "识别机型": airframe["label"],
        "机型置信度": f"{airframe['confidence'] * 100:.0f}%",
        "数据完整性": data_quality.get("data_completeness", data_quality["level"]),
        "工程可信度": data_quality.get("engineering_confidence", data_quality["level"]),
        "综合可信度": f"{data_quality.get('overall_report_confidence', data_quality['level'])} / {data_quality['score']}",
        "有效飞行段": metric(flight_events.get("effective_airborne_time"), " s"),
        "飞行状态": "存在待复核风险" if risk_assessment.get("level_cn") in {"中", "高"} else "基本正常",
        "风险等级": f"{risk_assessment.get('level_cn', '低')} / {risk_assessment.get('score', '--')}",
        "最大高度": metric(max_alt["max"] if max_alt else None, " m"),
        "最大速度": metric(max_speed["max"] if max_speed else None, " m/s"),
        "最低电压": metric(voltage["min"] if voltage else None, " V"),
        "电流可信度": battery_quality["quality"],
        "GPS 可信度": gps_quality["quality"],
        "Warning/Error": f"{warning_counts['warning_count']}/{warning_counts['error_count']}",
    }
    return {"summary": summary, "cards": cards, "charts": charts}


def trusted_report_sections(analysis):
    verified = analysis.get("verified") or build_verified_analysis_summary(analysis)
    quality = verified["data_quality"]
    availability = verified.get("topic_availability") or quality.get("topic_availability") or {}
    flight = verified["flight_events"]
    phase = verified["phase_segments"]
    battery = verified["battery_metrics"]
    gps = verified["gps_metrics"]
    actuator = verified["actuator_metrics"]
    messages = verified["warning_events"]
    rows = []
    rows.append("\n## 数据质量声明\n")
    rows.append(markdown_table([
        ["项目", "结果"],
        ["数据完整性", f"{quality.get('data_completeness', quality['level'])} / {quality.get('data_completeness_score', quality['score'])}"],
        ["工程结论可信度", f"{quality.get('engineering_confidence', quality['level'])} / {quality.get('engineering_confidence_score', quality['score'])}"],
        ["综合报告可信度", f"{quality.get('overall_report_confidence', quality['level'])} / {quality['score']}"],
        ["主要限制", "；".join(quality["limitations"]) if quality["limitations"] else "未发现关键限制"],
        ["电流数据可信度", f"{battery['quality']}；{battery['reason']}"],
        ["GPS 数据可信度", f"{gps['quality']}；{gps['reason']}"],
        ["阶段划分置信度", f"{phase['confidence']}；{phase['basis']}"],
    ]))
    rows.append("\n### Topic 可用性\n")
    topic_rows = [["Topic", "可用", "样本数", "采样率", "覆盖时长", "缺失字段"]]
    for name, item in (availability.get("profiles") or {}).items():
        topic_rows.append([
            name,
            "是" if item.get("available") else "否",
            item.get("sample_count", 0),
            metric(item.get("sample_rate_hz"), " Hz") if item.get("sample_rate_hz") is not None else "N/A",
            metric(item.get("time_coverage_s"), " s"),
            "、".join(item.get("missing_fields") or []) or "无",
        ])
    if len(topic_rows) == 1:
        topic_rows.append(["N/A", "否", 0, "N/A", "N/A", "未生成 topic 可用性摘要"])
    rows.append(markdown_table(topic_rows))
    rows.append("\n### 有效飞行时间\n")
    rows.append(markdown_table([
        ["时间项", "值"],
        ["日志总时长", metric(analysis["log"].duration_s, " s")],
        ["有效飞行段起点", metric(flight.get("effective_flight_start_time"), " s")],
        ["有效飞行段终点", metric(flight.get("effective_flight_end_time"), " s")],
        ["解锁时间", metric(flight.get("armed_time"), " s")],
        ["起飞检测时间", metric(flight.get("takeoff_detected_time"), " s")],
        ["任务开始时间", metric(flight.get("mission_start_time"), " s")],
        ["降落检测时间", metric(flight.get("landing_detected_time"), " s")],
        ["上锁时间", metric(flight.get("disarmed_time"), " s")],
        ["有效空中时间", metric(flight.get("effective_airborne_time"), " s")],
        ["Fallback 规则", "；".join(flight.get("fallbacks") or ["未使用 fallback"])],
    ]))
    rows.append("\n### 阶段划分\n")
    phase_rows = [["阶段", "开始", "结束", "持续", "依据/证据", "方法", "置信度"]]
    for item in phase.get("segments", []):
        phase_rows.append([
            item["name"],
            metric(item["start_s"], " s"),
            metric(item["end_s"], " s"),
            metric(item["duration_s"], " s"),
            item.get("evidence") or item.get("basis"),
            item.get("method", "verified_phase_detector"),
            item["confidence"],
        ])
    rows.append(markdown_table(phase_rows))
    rows.append("\n### 可信姿态跟踪指标\n")
    attitude_rows = [["轴", "setpoint 是否有效", "RMS", "峰值误差", "偏置", "计算方法"]]
    for axis, label in [("roll", "横滚"), ("pitch", "俯仰"), ("yaw", "航向")]:
        metric_row = verified["attitude_metrics"].get(axis)
        if not metric_row or not metric_row.get("available"):
            attitude_rows.append([label, "否", "N/A", "N/A", "N/A", "日志未包含或时间不同步"])
            continue
        attitude_rows.append([
            label,
            "是",
            metric(metric_row.get("rms"), " deg"),
            metric(metric_row.get("max"), " deg"),
            metric(metric_row.get("bias"), " deg"),
            metric_row.get("method", "--"),
        ])
    rows.append(markdown_table(attitude_rows))
    rows.append("\n### 执行器有效通道分析\n")
    actuator_rows = [["通道", "最小", "最大", "均值", "标准差", "高端饱和", "低端饱和", "持续饱和", "参与风险"]]
    for item in actuator.get("valid_channels", [])[:16]:
        actuator_rows.append([
            item["channel"],
            metric(item["min"]),
            metric(item["max"]),
            metric(item["mean"]),
            metric(item["std"]),
            f"{item['high_saturation_percent']}%",
            f"{item['low_saturation_percent']}%",
            "是" if item["sustained_saturation"] else "否",
            "是" if item["participates_in_risk"] else "否",
        ])
    if len(actuator_rows) == 1:
        actuator_rows.append(["N/A", "日志未包含有效执行器通道", "N/A", "N/A", "N/A", "N/A", "N/A", "N/A", "否"])
    rows.append(markdown_table(actuator_rows))
    if actuator.get("unused_channels"):
        rows.append("* 未使用/固定/无效通道已从饱和结论中排除，且不参与异常事件判断：" + "、".join(item["channel"] for item in actuator["unused_channels"][:16]) + "\n")
    saturation_rows = [["通道", "类型", "开始", "结束", "持续", "阶段", "置信度", "证据"]]
    for item in actuator.get("saturation_events", [])[:20]:
        saturation_rows.append([
            item.get("channel"),
            item.get("saturation_type"),
            metric(item.get("start_s"), " s"),
            metric(item.get("end_s"), " s"),
            metric(item.get("duration_s"), " s"),
            item.get("phase"),
            item.get("confidence"),
            item.get("evidence"),
        ])
    if len(saturation_rows) > 1:
        rows.append("\n### 有效通道饱和区间\n")
        rows.append(markdown_table(saturation_rows))
    rows.append("\n### 告警与 Failsafe 分类\n")
    counts = messages["counts"]
    rows.append(markdown_table([
        ["统计项", "数量"],
        ["total messages", counts["total_messages"]],
        ["info events", counts["info_events"]],
        ["warning count", counts["warning_count"]],
        ["error count", counts["error_count"]],
        ["failsafe count", counts["failsafe_count"]],
    ]))
    grouped_rows = [["首次时间", "末次时间", "次数", "级别", "消息", "解释"]]
    for item in messages.get("groups", [])[:30]:
        if item.get("severity") == "INFO" and not item.get("is_anomaly"):
            continue
        grouped_rows.append([
            metric(item.get("first_time_s"), " s"),
            metric(item.get("last_time_s"), " s"),
            item.get("count", 0),
            item.get("severity", "INFO"),
            item.get("raw", ""),
            item.get("meaning", ""),
        ])
    if len(grouped_rows) > 1:
        rows.append("\n### 聚合告警事件\n")
        rows.append(markdown_table(grouped_rows))
    warning_rows = [["时间", "级别", "原始消息", "解释", "阶段", "影响判断", "建议"]]
    for item in messages["rows"][:40]:
        if item["severity"] == "INFO" and not item["is_anomaly"]:
            continue
        warning_rows.append([item["time"], item["severity"], item["raw"], item["meaning"], item["flight_phase"], item["impact"], item["advice"]])
    if len(warning_rows) == 1:
        warning_rows.append(["N/A", "INFO", "无 WARNING/ERROR/CRITICAL", "普通信息事件不计入异常", "N/A", "未见告警", "继续保持记录"])
    rows.append(markdown_table(warning_rows))
    rows.append("\n### 机型匹配 PID 参数\n")
    pid_rows = [["参数", "当前值", "来源"]]
    for item in verified["pid_parameters"]:
        pid_rows.append([item["name"], item["value"] if item["value"] is not None else "日志未包含", item["source"]])
    rows.append(markdown_table(pid_rows))
    return "".join(rows)


def event_time_text(event):
    value = event.get("time")
    if value is None:
        return "无法定位具体发生时间，仅检测到全局统计异常。"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "无法定位具体发生时间，仅检测到全局统计异常。"
    if not math.isfinite(number):
        return "无法定位具体发生时间，仅检测到全局统计异常。"
    if number == 0 and str(event.get("evidence", "")).strip():
        return "N/A（该事件来自全局统计，未定位到具体时间点）"
    return metric(number, " s")


def build_markdown(analysis, charts):
    log = analysis["log"]
    model = build_report_model(analysis, charts)
    metrics = analysis["metrics"]
    topics = analysis["topics"]
    params = log.parameters or {}
    airframe = analysis.get("airframe") or detect_airframe_type(log)
    verified = analysis.get("verified") or build_verified_analysis_summary(analysis)
    md = ["# 无人机飞行日志分析报告\n"]
    md.append("## 1. 飞行基本信息\n")
    md.append(markdown_table([
        ["项目", "内容"],
        ["日志文件名", log.path.name],
        ["生成时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
        ["飞行开始时间", log.start_time],
        ["飞行结束时间", log.end_time],
        ["总飞行时长", metric(log.duration_s, " s")],
        ["飞控类型/固件信息", log.parameters.get("SYS_AUTOSTART", "日志未包含") if log.parameters else "日志未包含"],
        ["日志机型识别", f"{airframe['label']}（置信度约 {airframe['confidence'] * 100:.0f}%）"],
        ["机型来源", f"{airframe.get('aircraft_type_source') or airframe.get('source') or 'auto_detection'}；{('用户/项目配置指定 ' + airframe.get('aircraft_type_override')) if airframe.get('aircraft_type_override') else '自动识别'}"],
        ["姿态阶段模板", airframe["template"]],
        ["机型识别依据", "；".join(airframe["evidence"])],
        ["飞行模式列表", "日志未包含" if "vehicle_status" not in log.topics else "已记录 vehicle_status"],
        ["飞机名称或编号", "PX6C-01"],
        ["电池信息", "已包含" if topics["battery"] else "日志未包含"],
        ["GPS 信息", "已包含" if topics["gps"] else "日志未包含"],
    ]))
    md.append(trusted_report_sections(analysis))
    md.append("\n## 2. 执行摘要\n")
    md.append(model["summary"] + "\n")
    md.append("\n## 3. 关键结论\n")
    md.extend([f"* {key}：{value}\n" for key, value in model["cards"].items()])
    md.append("\n## 4. 飞行时间线\n")
    md.append(markdown_table((verified.get("verified_flight_timeline") or {}).get("rows") or [["时间线", "日志未包含足够数据"]]))
    md.append(f"\n## 5. 姿态控制分析（{airframe['label']}）\n")
    rows = [["轴", "setpoint 是否有效", "最大误差", "平均误差", "RMS 误差", "偏置", "计算说明"]]
    for axis in ("roll", "pitch", "yaw"):
        item = (verified.get("attitude_metrics") or {}).get(axis) or {}
        if not item.get("available"):
            rows.append([AXIS_LABELS[axis], "否", "N/A", "N/A", "N/A", "N/A", item.get("reason", "日志未包含或时间不同步")])
            continue
        note = item.get("method", "")
        if axis == "yaw":
            note += "；航向使用 circular error，不使用 raw max-min 作为振荡证据"
        rows.append([
            AXIS_LABELS[axis],
            "是",
            metric(item.get("max"), " deg"),
            metric(item.get("mean"), " deg"),
            metric(item.get("rms"), " deg"),
            metric(item.get("bias"), " deg"),
            note,
        ])
    md.append(markdown_table(rows))
    yaw_warning = ((verified.get("attitude_metrics") or {}).get("yaw") or {}).get("wrap_warning") or {}
    if yaw_warning.get("wrap_detected"):
        md.append("航向分析提示：存在航向角跨零或 setpoint 跳变风险，需要人工复核，不能直接作为真实 360° 跟踪误差。\n")
    md.append("姿态分析结论：姿态误差只能说明跟踪表现，需要结合机械振动、执行器余量、电源、传感器和映射结果复核；不能直接推出 PID 是根因。\n")
    md.append("\n### 5.1 机型识别与姿态阶段划分\n")
    phase_rows = [["机型", "阶段", "时间范围", "依据", "方法", "置信度"]]
    for item in (verified.get("phase_segments") or {}).get("segments", []):
        phase_rows.append([
            airframe["label"],
            item.get("name"),
            f"{metric(item.get('start_s'), ' s')} - {metric(item.get('end_s'), ' s')}",
            item.get("evidence") or item.get("basis"),
            item.get("method", "verified_phase_detector"),
            item.get("confidence"),
        ])
    md.append(markdown_table(phase_rows))
    md.append("\n### 5.2 分阶段姿态指标\n")
    md.append("当前版本分阶段姿态指标仅基于 verified_flight_phases 和 verified attitude metrics 汇总；阶段置信度为 Medium/Low 时，只作为趋势参考。\n")
    md.append("\n### 5.3 分阶段姿态结论\n")
    phase_confidence = (verified.get("phase_segments") or {}).get("confidence", "Low")
    if phase_confidence != "High":
        md.append(f"* 阶段划分置信度为 {phase_confidence}，不能作为确定飞行阶段结论，需要人工复核。\n")
    else:
        md.append("* 阶段划分有明确日志/高度/转换证据支持。\n")
    md.append("\n## 6. PID / 控制参数分析\n")
    md.append("说明：PID 参数只作为复盘参考。参数调整必须在机械、电源、传感器和执行器映射确认正常后，进行小幅、可回滚的地面对比测试。\n")
    pid_rows = [["参数", "当前值", "来源"]]
    for item in verified["pid_parameters"]:
        pid_rows.append([
            item["name"],
            item["value"] if item["value"] is not None else "日志未包含",
            item["source"],
        ])
    md.append(markdown_table(pid_rows))
    if not log.parameters:
        md.append("日志未包含参数表，本报告不会给出具体 PID 数值建议；建议下次记录参数快照后再进行调参复盘。\n")
    md.append("\n## 7. 高度与位置分析\n")
    md.append(markdown_table([["指标", "结果"], ["最大高度", metric(metrics["position"]["altitude"]["max"] if metrics["position"]["altitude"] else None, " m")], ["最大爬升/下降率", metric(metrics["position"]["vertical_velocity"]["max"] if metrics["position"]["vertical_velocity"] else None, " m/s")], ["最大水平速度", metric(metrics["position"]["horizontal_speed"]["max"] if metrics["position"]["horizontal_speed"] else None, " m/s")], ["轨迹距离", metric(metrics["position"]["distance_m"], " m")]]))
    md.append("\n## 8. 电源系统分析\n")
    md.append(markdown_table([["指标", "结果"], ["最低电压", metric(metrics["power"]["voltage"]["min"] if metrics["power"]["voltage"] else None, " V")], ["最大电流", metric(metrics["power"]["current"]["max"] if metrics["power"]["current"] else None, " A")], ["平均电流", metric(metrics["power"]["current"]["mean"] if metrics["power"]["current"] else None, " A")], ["消耗容量", metric(metrics["power"]["consumed_mah"]["max"] if metrics["power"]["consumed_mah"] else None, " mAh")]]))
    if verified["battery_metrics"]["quality"] != "reliable":
        md.append("电流数据疑似无效或未正确校准，本报告不基于该电流值判断动力系统负载；仅将电压趋势作为有限证据。\n")
    md.append("\n## 9. 电机/舵机/执行器输出分析\n")
    if topics["actuator"]:
        risk_channels = verified["actuator_metrics"].get("risk_channels", [])
        if risk_channels:
            md.append("执行器输出存在疑似持续限幅，但结论仅基于已过滤的有效通道；建议结合实际舵面/电机映射人工复核。\n")
        else:
            md.append("未在有效执行器通道中确认持续饱和；未使用或固定通道已排除在风险判断之外。\n")
    else:
        md.append(MISSING + "\n")
    md.append("\n## 10. GPS 与导航状态分析\n")
    md.append(markdown_table([["指标", "结果"], ["最低卫星数", metric(metrics["gps"]["satellites"]["min"] if metrics["gps"]["satellites"] else None, "")], ["最低 Fix Type", verified["gps_metrics"].get("fix_type_label", "N/A")], ["最大 EPH/HDOP", metric(metrics["gps"]["eph"]["max"] if metrics["gps"]["eph"] else None, "")], ["GPS 数据可信度", verified["gps_metrics"]["quality"]]]))
    md.append("\n## 11. 传感器健康分析\n")
    md.append(markdown_table([["传感器", "指标"], ["Gyro norm std", metric(metrics["sensor"]["gyro_norm"]["std"] if metrics["sensor"]["gyro_norm"] else None, "")], ["Accel norm std", metric(metrics["sensor"]["accel_norm"]["std"] if metrics["sensor"]["accel_norm"] else None, "")], ["磁力计/气压计", "若日志包含相关 topic，可在附录字段列表中确认；当前版本保守报告缺失项"]]))
    md.append("\n## 12. 振动分析\n")
    md.append("振动过大时不建议直接调 PID，应先检查螺旋桨平衡、电机轴、机架松动、电机座、飞控减震和线束干涉。\n")
    md.append("\n## 13. EKF / 估计器分析\n")
    md.append("若日志包含 estimator_status / estimator_innovations，可继续判断 velocity、position、height、magnetometer innovation。当前报告会在缺失数据中明确列出未包含项。\n")
    md.append("\n## 14. 告警与 failsafe\n")
    verified_warning_rows = [["时间", "级别", "原始消息", "中文解释", "影响判断", "建议处理"]]
    for item in verified["warning_events"]["rows"][:80]:
        if item["severity"] == "INFO" and not item["is_anomaly"]:
            continue
        verified_warning_rows.append([item["time"], item["severity"], item["raw"], item["meaning"], item["impact"], item["advice"]])
    if len(verified_warning_rows) == 1:
        verified_warning_rows.append(["N/A", "INFO", "无 WARNING/ERROR/CRITICAL", "普通 INFO 事件不计入异常", "未见告警", "继续保持日志记录"])
    md.append(markdown_table(verified_warning_rows))
    md.append("\n## 15. 异常事件分析\n")
    abnormal_events = [item for item in verified.get("abnormal_events", []) if item.get("is_abnormal")]
    if abnormal_events:
        for index, event in enumerate(abnormal_events, 1):
            md.append(f"### 事件 {index}：{event['name']}\n")
            time_text = (
                f"{metric(event.get('start_s'), ' s')} - {metric(event.get('end_s'), ' s')}"
                if event.get("start_s") is not None and event.get("end_s") is not None
                else event_time_text(event)
            )
            md.append(
                f"* 发生时间：{time_text}\n"
                f"* 分类：{event.get('category', 'unknown')}\n"
                f"* 阶段：{event.get('phase', '未定位阶段')}\n"
                f"* 置信度：{event.get('confidence', 'Low')}\n"
                f"* 证据：{event.get('evidence', 'N/A')}\n"
                f"* 风险等级：{event.get('risk', '中')}\n"
                f"* 建议操作：{event.get('advice', '结合现场现象人工复核。')}\n"
            )
    else:
        md.append("verified_summary 未检测到可确认异常事件。INFO 操作事件只进入时间线，不进入异常事件。\n")
    md.append("\n## 16. 根因分析\n")
    root_rows = [["分级", "候选项", "数据证据", "下一步复核"]]
    for item in verified.get("root_cause_analysis", []):
        root_rows.append([item.get("level"), item.get("candidate"), item.get("evidence"), item.get("next_check")])
    md.append(markdown_table(root_rows))
    md.append("\n## 17. 下一次飞行建议\n")
    md.append("### 高优先级\n")
    md.append("* 按 verified abnormal_events 的事件区间复核现场现象，不使用 excluded actuator channel 做风险判断。\n")
    md.append("* 若电流数据 suspicious，只能把电压下降作为有限证据，不能直接判断动力负载。\n")
    md.append("* 对航向问题优先检查 EKF/yaw estimate、罗盘、GPS 航向和 setpoint 连续性，不能把 wrap 当成 360° 振荡。\n")
    md.append("### 中优先级\n")
    md.append("* 检查机械振动、结构松动、重心、舵面/电机映射和执行器余量。\n")
    md.append("* 保留参数快照，调参只能做小幅、单变量、可回滚对比。\n")
    md.append("### 低优先级\n")
    md.append("* 使用同一测试科目日志做多日志对比，观察趋势变化。\n")
    md.append("\n## 18. 附录\n")
    md.append("### 数据字段列表\n")
    md.append(", ".join(sorted(log.topics.keys())) + "\n")
    md.append("\n### 缺失数据说明\n")
    missing_verified = verified.get("missing_data") or {}
    if isinstance(missing_verified, dict):
        missing_items = missing_verified.get("items") or []
        if missing_verified.get("statement"):
            md.append(f"* 统一说明：{missing_verified.get('statement')}\n")
        for rule in missing_verified.get("do_not_conclude", []):
            md.append(f"* 禁止结论：{rule}\n")
    else:
        missing_items = missing_verified or analysis["missing"]
    md.extend([f"* {item}\n" for item in missing_items] or ["* 无明显缺失。\n"])
    md.append("\n### 图表清单\n")
    md.extend([f"* {caption}：{path.name}\n" for caption, path in charts])
    return "".join(md), model


def configure_doc(doc):
    section = doc.sections[0]
    section.top_margin = Inches(1)
    section.bottom_margin = Inches(1)
    section.left_margin = Inches(1)
    section.right_margin = Inches(1)
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)
    for style_name, size, color in [("Heading 1", 16, "2E74B5"), ("Heading 2", 13, "2E74B5"), ("Heading 3", 12, "1F4D78")]:
        style = doc.styles[style_name]
        style.font.name = "Calibri"
        style.font.size = Pt(size)
        style.font.color.rgb = RGBColor.from_string(color)
    return set_docx_fonts(doc)


def add_docx_table(doc, rows):
    normalized_rows = [[str(cell) for cell in row] for row in rows if row]
    if not normalized_rows:
        return None
    column_count = max(len(row) for row in normalized_rows)
    normalized_rows = [row + [""] * (column_count - len(row)) for row in normalized_rows]
    table = doc.add_table(rows=1, cols=column_count)
    table.style = "Table Grid"
    table.autofit = True
    usable_width = 6.4
    width_each = max(0.75, usable_width / max(1, column_count))
    for index, value in enumerate(normalized_rows[0]):
        table.rows[0].cells[index].text = str(value)
        table.rows[0].cells[index].paragraphs[0].runs[0].bold = True
        table.rows[0].cells[index].width = Inches(width_each)
    for row in normalized_rows[1:]:
        cells = table.add_row().cells
        for index, value in enumerate(row):
            cells[index].text = str(value)
            cells[index].width = Inches(width_each)
    for row in table.rows:
        for cell in row.cells:
            for paragraph in cell.paragraphs:
                paragraph.paragraph_format.space_after = Pt(0)
                for run in paragraph.runs:
                    run.font.size = Pt(9)
    set_docx_fonts(doc)
    return table


def markdown_to_docx(markdown_text, charts, output_path):
    doc = configure_doc(Document())
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run("无人机飞行日志分析报告")
    run.bold = True
    run.font.size = Pt(22)
    run.font.color.rgb = RGBColor(11, 37, 69)
    if charts:
        doc.add_heading("关键图表", level=1)
        for caption, path in charts:
            doc.add_heading(caption, level=2)
            doc.add_picture(str(path), width=Inches(6.2))
    lines = markdown_text.splitlines()
    index = 0
    chart_map = {caption: path for caption, path in charts}
    while index < len(lines):
        line = lines[index]
        if line.startswith("# "):
            index += 1
            continue
        if line.startswith("## "):
            heading = line[3:]
            doc.add_heading(heading, level=1)
            for caption, path in chart_map.items():
                if caption in heading:
                    doc.add_picture(str(path), width=Inches(6.2))
            index += 1
            continue
        if line.startswith("### "):
            doc.add_heading(line[4:], level=2)
            index += 1
            continue
        if line.startswith("| "):
            table_lines = []
            while index < len(lines) and lines[index].startswith("| "):
                if "---" not in lines[index]:
                    table_lines.append([cell.strip() for cell in lines[index].strip("|").split("|")])
                index += 1
            if table_lines:
                add_docx_table(doc, table_lines)
            continue
        if line.startswith("* "):
            doc.add_paragraph(line[2:], style="List Bullet")
        elif line.strip():
            doc.add_paragraph(line)
        index += 1
    set_docx_fonts(doc)
    doc.save(output_path)


def markdown_to_html(markdown_text, charts):
    body = []
    for line in markdown_text.splitlines():
        if line.startswith("# "):
            body.append(f"<h1>{html.escape(line[2:])}</h1>")
            if charts:
                body.append("<h2>关键图表</h2>")
                body.extend(f'<figure><img src="{html.escape(path.parent.name + "/" + path.name)}" alt="{html.escape(caption)}"><figcaption>{html.escape(caption)}</figcaption></figure>' for caption, path in charts)
        elif line.startswith("## "):
            body.append(f"<h2>{html.escape(line[3:])}</h2>")
            for caption, path in charts:
                if caption in line:
                    src = f"{path.parent.name}/{path.name}"
                    body.append(f'<img src="{html.escape(src)}" alt="{html.escape(caption)}">')
        elif line.startswith("### "):
            body.append(f"<h3>{html.escape(line[4:])}</h3>")
        elif line.startswith("* "):
            body.append(f"<p>• {html.escape(line[2:])}</p>")
        elif line.startswith("| "):
            body.append(f"<pre>{html.escape(line)}</pre>")
        elif line.strip():
            body.append(f"<p>{html.escape(line)}</p>")
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><style>
{html_font_css()}
</style></head><body>""" + "\n".join(body) + "</body></html>"


def markdown_to_pdf(markdown_text, charts, output_path):
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="CN", fontName="STSong-Light", fontSize=10, leading=15))
    styles.add(ParagraphStyle(name="CNH1", fontName="STSong-Light", fontSize=18, leading=24, textColor=colors.HexColor("#0B2545")))
    styles.add(ParagraphStyle(name="CNH2", fontName="STSong-Light", fontSize=14, leading=20, textColor=colors.HexColor("#2E74B5")))
    story = []
    for line in markdown_text.splitlines():
        if line.startswith("# "):
            story.append(Paragraph(html.escape(line[2:]), styles["CNH1"]))
            story.append(Spacer(1, 0.12 * inch))
            if charts:
                story.append(Paragraph("关键图表", styles["CNH2"]))
                for caption, path in charts:
                    if path.exists():
                        story.append(Paragraph(html.escape(caption), styles["CN"]))
                        story.append(Image(str(path), width=6.3 * inch, height=2.9 * inch))
                        story.append(Spacer(1, 0.08 * inch))
        elif line.startswith("## "):
            story.append(Paragraph(html.escape(line[3:]), styles["CNH2"]))
            for caption, path in charts:
                if caption in line and path.exists():
                    story.append(Image(str(path), width=6.3 * inch, height=2.9 * inch))
            story.append(Spacer(1, 0.08 * inch))
        elif line.startswith("### "):
            story.append(Paragraph(html.escape(line[4:]), styles["CN"]))
        elif line.startswith("| "):
            story.append(Paragraph(html.escape(line), styles["CN"]))
        elif line.strip():
            story.append(Paragraph(html.escape(line), styles["CN"]))
    SimpleDocTemplate(str(output_path), pagesize=A4, leftMargin=0.7 * inch, rightMargin=0.7 * inch, topMargin=0.7 * inch, bottomMargin=0.7 * inch).build(story)


def generate_flight_report(path, output_dir, options=None):
    options = options or {}
    log = load_flight_log(path)
    analysis = analyze_log(log, options)
    analysis["verified"] = build_verified_analysis_summary(analysis)
    verified_risk = (analysis["verified"].get("risk_assessment") or {})
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    stem = f"flight_report_algorithm_{timestamp}_{uuid.uuid4().hex[:6]}"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = output_dir / f"{stem}_assets"
    charts = generate_charts(analysis, assets_dir) if options.get("includeCharts", True) else []
    markdown_text, model = build_markdown(analysis, charts)
    consistency = check_report_consistency(analysis["verified"], markdown_text)
    if consistency["blocking_errors"]:
        raise RuntimeError("报告一致性检查失败，已阻止生成误导性报告：" + "；".join(consistency["blocking_errors"]))
    md_path = output_dir / f"{stem}.md"
    html_path = output_dir / f"{stem}.html"
    docx_path = output_dir / f"{stem}.docx"
    pdf_path = output_dir / f"{stem}.pdf"
    summary_path = output_dir / f"verified_summary_{timestamp}_{uuid.uuid4().hex[:6]}.json"
    md_path.write_text(markdown_text, encoding="utf-8")
    html_path.write_text(markdown_to_html(markdown_text, charts), encoding="utf-8")
    summary_path.write_text(json.dumps(analysis["verified"], ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_to_docx(markdown_text, charts, docx_path)
    markdown_to_pdf(markdown_text, charts, pdf_path)
    return {
        "reportPath": str(docx_path),
        "reportUrl": f"/reports/{docx_path.name}",
        "markdownUrl": f"/reports/{md_path.name}",
        "htmlUrl": f"/reports/{html_path.name}",
        "pdfUrl": f"/reports/{pdf_path.name}",
        "verifiedSummaryUrl": f"/reports/{summary_path.name}",
        "preview": markdown_text[:18000],
        "cards": model["cards"],
        "chartUrls": [{"caption": caption, "url": f"/reports/{path.parent.name}/{path.name}"} for caption, path in charts],
        "consistency": consistency,
        "summary": {
            "durationSeconds": round(log.duration_s, 2),
            "topics": len(log.topics),
            "risk": verified_risk.get("level_cn", "低"),
            "riskAssessment": verified_risk,
            "events": len([item for item in analysis["verified"].get("abnormal_events", []) if item.get("is_abnormal")]),
            "missing": analysis["missing"],
            "charts": [caption for caption, _ in charts],
        },
    }

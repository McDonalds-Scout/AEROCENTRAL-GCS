import argparse
import copy
import json
import math
import os
import re
import struct
import threading
import time
import urllib.request
from pathlib import Path

from pymavlink import mavutil
from serial.tools import list_ports

from services.mavlink_gcs import (
    is_vehicle_heartbeat,
    send_gcs_heartbeat,
    vehicle_target,
    wait_for_vehicle_heartbeat,
)
from services.mavlink_command_status import (
    FINAL_COMMAND_STATUSES,
    command_result_status,
    is_ack_timeout,
    load_command_statuses,
    should_process_queued_command,
    write_command_status,
)
from services import mavlink_command_status as command_status_store


PX4_MAIN_MODES = {
    1: "MANUAL",
    2: "ALTCTL",
    3: "POSCTL",
    4: "AUTO",
    5: "ACRO",
    6: "OFFBOARD",
    7: "STABILIZED",
    8: "RATTITUDE",
}

PX4_AUTO_SUBMODES = {
    1: "READY",
    2: "TAKEOFF",
    3: "LOITER",
    4: "MISSION",
    5: "RTL",
    6: "LAND",
    7: "RTGS",
    8: "FOLLOW_TARGET",
    9: "PRECLAND",
    10: "VTOL_TAKEOFF",
}

MESSAGE_INTERVALS = {
    mavutil.mavlink.MAVLINK_MSG_ID_HEARTBEAT: 1_000_000,
    mavutil.mavlink.MAVLINK_MSG_ID_SYS_STATUS: 500_000,
    mavutil.mavlink.MAVLINK_MSG_ID_GPS_RAW_INT: 100_000,
    mavutil.mavlink.MAVLINK_MSG_ID_GLOBAL_POSITION_INT: 50_000,
    mavutil.mavlink.MAVLINK_MSG_ID_VFR_HUD: 33_333,
    mavutil.mavlink.MAVLINK_MSG_ID_ATTITUDE: 20_000,
    mavutil.mavlink.MAVLINK_MSG_ID_EXTENDED_SYS_STATE: 1_000_000,
    mavutil.mavlink.MAVLINK_MSG_ID_HOME_POSITION: 2_000_000,
    mavutil.mavlink.MAVLINK_MSG_ID_BATTERY_STATUS: 200_000,
    mavutil.mavlink.MAVLINK_MSG_ID_RC_CHANNELS: 50_000,
    mavutil.mavlink.MAVLINK_MSG_ID_SERVO_OUTPUT_RAW: 50_000,
}
for _mission_message_id, _mission_interval_us in (
    (getattr(mavutil.mavlink, "MAVLINK_MSG_ID_MISSION_CURRENT", None), 500_000),
    (getattr(mavutil.mavlink, "MAVLINK_MSG_ID_MISSION_ITEM_REACHED", None), 0),
):
    if _mission_message_id is not None:
        MESSAGE_INTERVALS[_mission_message_id] = _mission_interval_us

UI_PUBLISH_HZ = 50.0
COMMAND_SCAN_INTERVAL_S = 0.1

REALTIME_UI_KEYS = (
    "vehicleId",
    "autopilot",
    "hardware",
    "connected",
    "armed",
    "mode",
    "systemStatus",
    "vehicle_type",
    "targetSystem",
    "targetComponent",
    "targetIdentified",
    "vehicleAutopilot",
    "vehicleBaseMode",
    "vehicleCustomMode",
    "vehicleMavlinkVersion",
    "vehicleHeartbeatAt",
    "gcsHeartbeat",
    "lat",
    "lon",
    "alt",
    "relativeAlt",
    "speed",
    "climb",
    "heading",
    "roll",
    "pitch",
    "yaw",
    "rollRate",
    "pitchRate",
    "yawRate",
    "attitudeTimeMs",
    "airspeed",
    "battery",
    "voltage",
    "current",
    "satellites",
    "fixType",
    "eph",
    "rssi",
    "landedState",
    "homeLat",
    "homeLon",
    "homeAlt",
    "home_position",
    "rc_signal",
    "rcSignalRaw",
    "rcUpdateTimeMs",
    "rcSource",
    "rcChannels",
    "rcRawChannels",
    "rcMapped",
    "rcMap",
    "rcMapAvailable",
    "rcIssues",
    "rcThrottlePwm",
    "rcThrottlePercent",
    "rcThrottleSource",
    "manualControl",
    "manualControlTimeMs",
    "servo_outputs",
    "motor_outputs",
    "warnings",
    "statustexts",
    "commandAcks",
    "calibration",
    "missionCurrent",
    "missionReached",
    "lastMessage",
)
FULL_UI_KEYS = ("parameters", "mission_items")
FULL_PUBLISH_MESSAGE_TYPES = {
    "PARAM_VALUE",
    "MISSION_ITEM",
    "MISSION_ITEM_INT",
}

COMMAND_QUEUE = Path(__file__).resolve().parent / "commands" / "px6c_commands.jsonl"
COMMAND_STATUS = Path(__file__).resolve().parent / "commands" / "px6c_command_status.json"
COMMAND_ACK_HISTORY = []
ACK_TIMEOUT_RESULTS = {"NO_ACK", "TIMEOUT"}
FINAL_COMMAND_STATUSES = {
    "accepted",
    "rejected",
    "failed",
    "timeout",
    "sent_no_ack",
    "expired",
    "unsupported",
    "interrupted",
    "partial",
}
PENDING_COMMAND_STATUSES = {"queued", "running"}
RECOVERABLE_QUEUE_WINDOW_MS = 10 * 60 * 1000
TRANSIENT_ACTUATOR_QUEUE_WINDOW_MS = 3000
SAFE_RECOVERABLE_COMMANDS = {
    "calibrate",
    "request_parameters",
    "set_parameter",
    "set_flight_mode",
    "arm_disarm",
    "list_flight_logs",
    "download_flight_log",
    "upload_mission",
    "clear_mission",
    "read_mission",
}
TRANSIENT_ACTUATOR_COMMANDS = {"set_servo", "test_motor"}
MAV_CMD_ACTUATOR_TEST = getattr(mavutil.mavlink, "MAV_CMD_ACTUATOR_TEST", 310)
MAV_RESULT_NAMES = {
    getattr(mavutil.mavlink, "MAV_RESULT_ACCEPTED", 0): "ACCEPTED",
    getattr(mavutil.mavlink, "MAV_RESULT_TEMPORARILY_REJECTED", 1): "TEMPORARILY_REJECTED",
    getattr(mavutil.mavlink, "MAV_RESULT_DENIED", 2): "DENIED",
    getattr(mavutil.mavlink, "MAV_RESULT_UNSUPPORTED", 3): "UNSUPPORTED",
    getattr(mavutil.mavlink, "MAV_RESULT_FAILED", 4): "FAILED",
    getattr(mavutil.mavlink, "MAV_RESULT_IN_PROGRESS", 5): "IN_PROGRESS",
}
MAV_SEVERITY_NAMES = {
    0: "EMERGENCY",
    1: "ALERT",
    2: "CRITICAL",
    3: "ERROR",
    4: "WARNING",
    5: "NOTICE",
    6: "INFO",
    7: "DEBUG",
}
MAG_CAL_STATUS_NAMES = {
    0: "NOT_STARTED",
    1: "WAITING_TO_START",
    2: "RUNNING_STEP_ONE",
    3: "RUNNING_STEP_TWO",
    4: "SUCCESS",
    5: "FAILED",
    6: "BAD_ORIENTATION",
    7: "BAD_RADIUS",
}
PX4_MOTOR_OUTPUT_FUNCTION_BASE = 101
PX4_SERVO_OUTPUT_FUNCTION_BASE = 33
MAV_MISSION_ACCEPTED = getattr(mavutil.mavlink, "MAV_MISSION_ACCEPTED", 0)
MAV_MISSION_TYPE_MISSION = getattr(mavutil.mavlink, "MAV_MISSION_TYPE_MISSION", 0)
INTEGER_PARAM_TYPES = {
    mavutil.mavlink.MAV_PARAM_TYPE_UINT8,
    mavutil.mavlink.MAV_PARAM_TYPE_INT8,
    mavutil.mavlink.MAV_PARAM_TYPE_UINT16,
    mavutil.mavlink.MAV_PARAM_TYPE_INT16,
    mavutil.mavlink.MAV_PARAM_TYPE_UINT32,
    mavutil.mavlink.MAV_PARAM_TYPE_INT32,
}
OUTPUT_FUNCTION_PARAMS = [
    *(f"PWM_MAIN_FUNC{index}" for index in range(1, 13)),
    *(f"PWM_AUX_FUNC{index}" for index in range(1, 9)),
]
RC_MAP_PARAMS = {
    "roll": "RC_MAP_ROLL",
    "pitch": "RC_MAP_PITCH",
    "throttle": "RC_MAP_THROTTLE",
    "yaw": "RC_MAP_YAW",
    "flightMode": "RC_MAP_FLTMODE",
    "armSwitch": "RC_MAP_ARM_SW",
}
RC_MAP_PARAMETER_NAMES = list(RC_MAP_PARAMS.values())
RC_CALIBRATION_PARAMETER_NAMES = [
    *(f"RC{index}_{suffix}" for index in range(1, 19) for suffix in ("MIN", "MAX", "TRIM", "REV", "DZ")),
]
PID_PARAMETER_NAMES = [
    "MC_ROLLRATE_P", "MC_ROLLRATE_I", "MC_ROLLRATE_D",
    "MC_PITCHRATE_P", "MC_PITCHRATE_I", "MC_PITCHRATE_D",
    "MC_YAWRATE_P", "MC_YAWRATE_I", "MC_YAWRATE_D",
    "MPC_Z_VEL_P_ACC", "MPC_Z_VEL_I_ACC", "MPC_Z_VEL_D_ACC",
    "FW_RR_P", "FW_RR_I", "FW_RR_D",
    "FW_PR_P", "FW_PR_I", "FW_PR_D",
    "FW_YR_P", "FW_YR_I", "FW_YR_D",
]
DEFAULT_PARAMETER_REQUESTS = [
    *PID_PARAMETER_NAMES,
    *OUTPUT_FUNCTION_PARAMS,
    *RC_MAP_PARAMETER_NAMES,
    *RC_CALIBRATION_PARAMETER_NAMES,
]
CALIBRATION_PARAMS = {
    "gyro": (1, 0, 0, 0, 0, 0, 0),
    "magnetometer": (0, 1, 0, 0, 0, 0, 0),
    "radio": (0, 0, 0, 1, 0, 0, 0),
    "accelerometer": (0, 0, 0, 0, 1, 0, 0),
    "level": (0, 0, 0, 0, 2, 0, 0),
    "airspeed": (0, 0, 0, 0, 0, 1, 0),
    "esc": (0, 0, 0, 0, 0, 0, 1),
}

CALIBRATION_LABELS = {
    "gyro": "陀螺仪",
    "magnetometer": "磁罗盘",
    "radio": "遥控器",
    "accelerometer": "加速度计",
    "level": "水平姿态",
    "airspeed": "空速计",
    "esc": "电调",
}

MISSION_COMMANDS = {
    "TAKEOFF": getattr(mavutil.mavlink, "MAV_CMD_NAV_TAKEOFF", 22),
    "WAYPOINT": getattr(mavutil.mavlink, "MAV_CMD_NAV_WAYPOINT", 16),
    "LOITER": getattr(mavutil.mavlink, "MAV_CMD_NAV_LOITER_TIME", 19),
    "LAND": getattr(mavutil.mavlink, "MAV_CMD_NAV_LAND", 21),
    "RTL": getattr(mavutil.mavlink, "MAV_CMD_NAV_RETURN_TO_LAUNCH", 20),
    "DO_CHANGE_SPEED": getattr(mavutil.mavlink, "MAV_CMD_DO_CHANGE_SPEED", 178),
}

PX4_FLIGHT_MODE_COMMANDS = {
    "manual": {"label": "手动模式", "px4": "MANUAL", "mainMode": 1, "subMode": 0},
    "position": {"label": "位置模式", "px4": "POSCTL", "mainMode": 3, "subMode": 0},
    "altitude": {"label": "定高模式", "px4": "ALTCTL", "mainMode": 2, "subMode": 0},
    "land": {"label": "降落模式", "px4": "AUTO LAND", "mainMode": 4, "subMode": 6},
    "mission": {"label": "任务模式", "px4": "AUTO MISSION", "mainMode": 4, "subMode": 4},
}


def px4_mode(custom_mode):
    main_mode = (int(custom_mode) >> 16) & 0xFF
    sub_mode = (int(custom_mode) >> 24) & 0xFF
    main_name = PX4_MAIN_MODES.get(main_mode, f"MODE_{main_mode}")
    if main_mode == 4:
        return f"AUTO {PX4_AUTO_SUBMODES.get(sub_mode, f'SUB_{sub_mode}')}"
    return main_name


def post_json(url, payload, timeout=0.5):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response.read()


def build_ui_payload(state, full=False):
    payload = {}
    for key in REALTIME_UI_KEYS:
        if key in state:
            payload[key] = copy.deepcopy(state.get(key))
    if full:
        for key in FULL_UI_KEYS:
            if key in state:
                payload[key] = copy.deepcopy(state.get(key))
    return payload


class UiTelemetryPublisher:
    def __init__(self, url, hz=UI_PUBLISH_HZ):
        self.url = url
        self.interval = 1.0 / max(1.0, float(hz))
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.latest_payload = None
        self.revision = 0
        self.sent_revision = 0
        self.thread = threading.Thread(target=self._run, name="ui-telemetry-publisher", daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=1.0)

    def publish(self, state, full=False):
        payload = build_ui_payload(state, full=full)
        with self.lock:
            self.latest_payload = payload
            self.revision += 1

    def _next_payload(self):
        with self.lock:
            if self.latest_payload is None or self.revision == self.sent_revision:
                return None
            self.sent_revision = self.revision
            return self.latest_payload

    def _run(self):
        next_tick = time.monotonic()
        while not self.stop_event.is_set():
            now = time.monotonic()
            wait = next_tick - now
            if wait > 0:
                self.stop_event.wait(wait)
                continue
            next_tick = max(next_tick + self.interval, time.monotonic())
            payload = self._next_payload()
            if payload is None:
                continue
            try:
                post_json(self.url, payload, timeout=0.35)
            except Exception:
                # Keep MAVLink receive independent from UI/server hiccups.
                continue


def request_message_intervals(master):
    for message_id, interval_us in MESSAGE_INTERVALS.items():
        master.mav.command_long_send(
            master.target_system,
            master.target_component,
            mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,
            0,
            message_id,
            interval_us,
            0,
            0,
            0,
            0,
            0,
        )
        time.sleep(0.03)
    stream_rates = [
        (mavutil.mavlink.MAV_DATA_STREAM_EXTRA1, 50),
        (mavutil.mavlink.MAV_DATA_STREAM_EXTRA2, 30),
        (mavutil.mavlink.MAV_DATA_STREAM_POSITION, 20),
        (mavutil.mavlink.MAV_DATA_STREAM_EXTENDED_STATUS, 5),
        (getattr(mavutil.mavlink, "MAV_DATA_STREAM_RC_CHANNELS", 3), 20),
    ]
    for stream_id, rate_hz in stream_rates:
        master.mav.request_data_stream_send(
            master.target_system,
            master.target_component,
            stream_id,
            rate_hz,
            1,
        )
        time.sleep(0.01)


def command_ack_entry(message):
    result = int(getattr(message, "result", -1))
    return {
        "timeMs": int(time.time() * 1000),
        "command": int(getattr(message, "command", -1)),
        "result": result,
        "resultText": MAV_RESULT_NAMES.get(result, str(result)),
        "progress": int(getattr(message, "progress", 0) or 0),
    }


def remember_command_ack(message):
    entry = command_ack_entry(message)
    if COMMAND_ACK_HISTORY:
        last = COMMAND_ACK_HISTORY[-1]
        duplicate = (
            last.get("command") == entry.get("command")
            and last.get("result") == entry.get("result")
            and last.get("progress") == entry.get("progress")
            and entry["timeMs"] - int(last.get("timeMs") or 0) < 250
        )
        if duplicate:
            return last
    COMMAND_ACK_HISTORY.append(entry)
    del COMMAND_ACK_HISTORY[:-100]
    return entry


def mavlink_text(message):
    text = getattr(message, "text", "")
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    return str(text).rstrip("\x00")


def calibration_evidence_text(message):
    message_type = message.get_type()
    if message_type == "STATUSTEXT":
        text = mavlink_text(message)
        lower = text.lower()
        if any(term in lower for term in ("[cal]", "calibration", "calibrate", "mag", "compass", "gyro", "accel")):
            return text
    if message_type == "MAG_CAL_PROGRESS":
        return f"MAG_CAL_PROGRESS {int(getattr(message, 'completion_pct', 0) or 0)}%"
    if message_type == "MAG_CAL_REPORT":
        status_code = int(getattr(message, "cal_status", 0) or 0)
        return f"MAG_CAL_REPORT {MAG_CAL_STATUS_NAMES.get(status_code, str(status_code))}"
    return ""


def request_output_function_params(master):
    for name in [*OUTPUT_FUNCTION_PARAMS, *RC_MAP_PARAMETER_NAMES, *RC_CALIBRATION_PARAMETER_NAMES]:
        master.mav.param_request_read_send(
            master.target_system,
            master.target_component,
            name.encode("ascii"),
            -1,
        )
        time.sleep(0.02)


def valid_rc_pwm(value):
    return isinstance(value, (int, float)) and 800 <= int(value) <= 2200


def pwm_percent(value):
    if not valid_rc_pwm(value):
        return None
    return round(max(0, min(100, (int(value) - 1000) / 10)), 1)


def apply_rc_map(state):
    raw_channels = state.get("rcChannels") or []
    parameters = state.get("parameters") or {}
    rc_map = {}
    mapped = {}
    issues = []
    map_available = True

    for label, param_name in RC_MAP_PARAMS.items():
        raw_param = parameters.get(param_name)
        channel = None
        try:
            channel = int(round(float(raw_param)))
        except (TypeError, ValueError):
            map_available = False
        if not channel or channel < 1:
            map_available = False
            channel = None
        rc_map[param_name] = channel
        pwm = raw_channels[channel - 1] if channel and channel <= len(raw_channels) else None
        mapped[label] = {
            "param": param_name,
            "channel": channel,
            "pwm": pwm if valid_rc_pwm(pwm) else None,
            "percent": pwm_percent(pwm),
            "unused": not valid_rc_pwm(pwm),
        }

    used_channels = [
        item["channel"]
        for item in mapped.values()
        if item.get("channel")
    ]
    duplicates = sorted({channel for channel in used_channels if used_channels.count(channel) > 1})
    if duplicates:
        issues.append(f"RC_MAP duplicate channel(s): {', '.join(map(str, duplicates))}")
    if not map_available:
        issues.append("RC_MAP parameters unavailable")

    throttle = mapped.get("throttle") or {}
    throttle_pwm = throttle.get("pwm")
    if valid_rc_pwm(throttle_pwm):
        state["rcThrottlePwm"] = int(throttle_pwm)
        state["rcThrottlePercent"] = pwm_percent(throttle_pwm)
        state["rcThrottleSource"] = (
            f"RC_CHANNELS CH{throttle.get('channel')} via RC_MAP_THROTTLE"
        )
        if state["rcThrottlePercent"] is not None and state["rcThrottlePercent"] > 8:
            issues.append("Throttle is not at minimum")
    else:
        state["rcThrottlePwm"] = None
        state["rcThrottlePercent"] = None
        state["rcThrottleSource"] = None

    state["rcMap"] = rc_map
    state["rcMapped"] = mapped
    state["rcMapAvailable"] = map_available
    state["rcIssues"] = issues



def calibration_label_ascii(calibration_type):
    return {
        "gyro": "gyro",
        "magnetometer": "compass",
        "radio": "radio",
        "accelerometer": "accelerometer",
        "level": "level horizon",
        "airspeed": "airspeed",
        "esc": "ESC",
    }.get(calibration_type, str(calibration_type or "unknown"))


def monitor_magnetometer_calibration(master, command_id=None, ack=None, on_message=None):
    write_command_status(
        command_id,
        status="running",
        message="Compass calibration accepted; follow PX4 rotate prompts",
        results=[{"type": "magnetometer", "ack": ack or {}, "progress": 0}],
    )
    deadline = time.monotonic() + 120.0
    last_progress = None
    last_text = ""
    while time.monotonic() < deadline:
        send_gcs_heartbeat(master)
        message = master.recv_match(blocking=True, timeout=0.25)
        if message is None:
            continue
        if on_message:
            on_message(message)
        message_type = message.get_type()
        if message_type == "STATUSTEXT":
            text = getattr(message, "text", "")
            if isinstance(text, bytes):
                text = text.decode("utf-8", errors="replace")
            text = str(text).rstrip("\x00")
            if any(term in text.lower() for term in ["cal", "mag", "compass", "rotate"]):
                last_text = text
                write_command_status(
                    command_id,
                    status="running",
                    message=f"Compass calibration prompt: {text}",
                    results=[{"type": "magnetometer", "ack": ack or {}, "progress": last_progress, "text": text}],
                )
        elif message_type == "MAG_CAL_PROGRESS":
            progress = int(getattr(message, "completion_pct", 0) or 0)
            compass_id = int(getattr(message, "compass_id", 0) or 0)
            status_code = int(getattr(message, "cal_status", 0) or 0)
            status_text = MAG_CAL_STATUS_NAMES.get(status_code, str(status_code))
            last_progress = progress
            write_command_status(
                command_id,
                status="running",
                message=f"Compass {compass_id} calibration progress: {progress}% / {status_text}",
                results=[{
                    "type": "magnetometer",
                    "ack": ack or {},
                    "compassId": compass_id,
                    "progress": progress,
                    "status": status_code,
                    "statusText": status_text,
                }],
            )
        elif message_type == "MAG_CAL_REPORT":
            compass_id = int(getattr(message, "compass_id", 0) or 0)
            status_code = int(getattr(message, "cal_status", 0) or 0)
            status_text = MAG_CAL_STATUS_NAMES.get(status_code, str(status_code))
            success = status_text == "SUCCESS" or status_code == 4
            write_command_status(
                command_id,
                status="accepted" if success else "rejected",
                message=(
                    f"Compass {compass_id} calibration completed: {status_text}"
                    if success
                    else f"Compass {compass_id} calibration failed: {status_text}"
                ),
                results=[{
                    "type": "magnetometer",
                    "ack": ack or {},
                    "compassId": compass_id,
                    "progress": 100 if success else last_progress,
                    "status": status_code,
                    "statusText": status_text,
                    "fitness": float(getattr(message, "fitness", 0.0) or 0.0),
                    "autosaved": bool(getattr(message, "autosaved", 0)),
                }],
            )
            return
    write_command_status(
        command_id,
        status="partial",
        message=(
            f"Compass calibration still running or no final report; last progress {last_progress}%"
            if last_progress is not None
            else f"Compass calibration did not report progress. Last PX4 text: {last_text or 'none'}"
        ),
        results=[{"type": "magnetometer", "ack": ack or {}, "progress": last_progress, "text": last_text}],
    )


def send_calibration(master, calibration_type, command_id=None, on_message=None):
    params = CALIBRATION_PARAMS.get(calibration_type)
    label = calibration_label_ascii(calibration_type)
    if params is None:
        print(f"Unknown calibration type ignored: {calibration_type}")
        write_command_status(
            command_id,
            status="rejected",
            message=f"Unknown calibration type: {calibration_type}",
            results=[],
        )
        return
    write_command_status(
        command_id,
        status="running",
        message=f"{label} calibration command sent to flight controller; waiting for COMMAND_ACK",
        results=[],
    )
    master.mav.command_long_send(
        master.target_system,
        master.target_component,
        mavutil.mavlink.MAV_CMD_PREFLIGHT_CALIBRATION,
        0,
        *params,
    )
    ack = wait_for_command_ack(
        master,
        mavutil.mavlink.MAV_CMD_PREFLIGHT_CALIBRATION,
        timeout=5.0,
        on_message=on_message,
        evidence_predicate=calibration_evidence_text,
    )
    accepted = ack["resultText"] in {"ACCEPTED", "IN_PROGRESS"}
    if accepted and calibration_type == "magnetometer":
        monitor_magnetometer_calibration(master, command_id, ack, on_message=on_message)
        return
    evidence_text = ack.get("evidenceText") if isinstance(ack, dict) else ""
    write_command_status(
        command_id,
        status="accepted" if accepted else ("sent_no_ack" if is_ack_timeout(ack) else "rejected"),
        message=(
            f"Flight controller accepted {label} calibration command; follow PX4 prompts"
            if accepted
            else f"{label} calibration command was not confirmed by flight controller: {ack['resultText']}"
            + (f"; PX4 text: {evidence_text}" if evidence_text else "")
        ),
        results=[{
            "type": calibration_type,
            "label": label,
            "ack": ack,
            "text": evidence_text or None,
        }],
    )
    print(f"PX4 {label} calibration command sent: ack={ack['resultText']}")


def write_command_status(command_id, **status):
    return command_status_store.write_command_status(command_id, **status)
    if not command_id:
        return
    COMMAND_STATUS.parent.mkdir(exist_ok=True)
    try:
        statuses = json.loads(COMMAND_STATUS.read_text(encoding="utf-8"))
        if not isinstance(statuses, dict):
            statuses = {}
    except (FileNotFoundError, json.JSONDecodeError):
        statuses = {}
    statuses[command_id] = {
        **statuses.get(command_id, {}),
        **status,
        "updatedAt": int(time.time() * 1000),
    }
    payload = json.dumps(statuses, ensure_ascii=False, indent=2)
    for attempt in range(5):
        temporary = COMMAND_STATUS.with_name(
            f"{COMMAND_STATUS.stem}.{os.getpid()}.{time.time_ns()}.tmp"
        )
        try:
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(COMMAND_STATUS)
            return
        except OSError as error:
            try:
                temporary.unlink()
            except OSError:
                pass
            if attempt == 4:
                print(f"写入命令状态失败：{error}")
                return
            time.sleep(0.08 * (attempt + 1))


def load_command_statuses():
    return command_status_store.load_command_statuses()
    try:
        statuses = json.loads(COMMAND_STATUS.read_text(encoding="utf-8"))
        return statuses if isinstance(statuses, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def command_status_text(command_id, statuses=None):
    return command_status_store.command_status_text(command_id, statuses)
    if not command_id:
        return ""
    data = (statuses or load_command_statuses()).get(command_id) or {}
    return str(data.get("status") or "").strip().lower()


def should_process_queued_command(command, statuses=None):
    return command_status_store.should_process_queued_command(command, statuses)
    command_id = command.get("id")
    status = command_status_text(command_id, statuses)
    if status in FINAL_COMMAND_STATUSES:
        return False
    if status and status not in PENDING_COMMAND_STATUSES:
        return False

    command_name = str(command.get("command") or "")
    if command_name not in SAFE_RECOVERABLE_COMMANDS and command_name not in TRANSIENT_ACTUATOR_COMMANDS:
        write_command_status(
            command_id,
            status="expired",
            message=f"Command {command_name or 'unknown'} was not replayed by the connector safety filter",
            results=[],
        )
        return False

    created_at = command.get("createdAt")
    try:
        age_ms = int(time.time() * 1000) - int(created_at)
    except (TypeError, ValueError):
        age_ms = RECOVERABLE_QUEUE_WINDOW_MS + 1
    queue_window_ms = (
        TRANSIENT_ACTUATOR_QUEUE_WINDOW_MS
        if command_name in TRANSIENT_ACTUATOR_COMMANDS
        else RECOVERABLE_QUEUE_WINDOW_MS
    )
    if age_ms > queue_window_ms:
        write_command_status(
            command_id,
            status="expired",
            message=(
                "Actuator test command expired before the connector could send it; click the UI action again"
                if command_name in TRANSIENT_ACTUATOR_COMMANDS
                else "Command expired before the PX6C connector could send it; please click the UI action again"
            ),
            results=[],
        )
        return False
    return True


def command_result_status(ack):
    return command_status_store.command_result_status(ack)
    result_text = str((ack or {}).get("resultText") or "").upper()
    if result_text in {"ACCEPTED", "IN_PROGRESS"}:
        return "accepted"
    if result_text == "TIMEOUT":
        return "timeout"
    if result_text == "UNSUPPORTED":
        return "unsupported"
    if result_text in {"DENIED", "TEMPORARILY_REJECTED"}:
        return "rejected"
    if result_text in {"FAILED"}:
        return "failed"
    if result_text == "NO_ACK":
        return "sent_no_ack"
    return "rejected"


def wait_for_command_ack(master, mav_command, timeout=1.0, on_message=None, evidence_predicate=None):
    deadline = time.monotonic() + timeout
    evidence = ""
    latest_statustext = ""
    while time.monotonic() < deadline:
        send_gcs_heartbeat(master)
        message = master.recv_match(blocking=True, timeout=0.1)
        if message is None:
            continue
        if on_message:
            on_message(message)
        if message.get_type() == "STATUSTEXT":
            latest_statustext = mavlink_text(message)
            lower_text = latest_statustext.lower()
            if not evidence or any(
                term in lower_text
                for term in (
                    "fail",
                    "denied",
                    "reject",
                    "preflight",
                    "safety",
                    "cal",
                    "arm",
                    "mode",
                    "motor",
                    "servo",
                )
            ):
                evidence = latest_statustext
        if evidence_predicate:
            try:
                evidence = evidence or str(evidence_predicate(message) or "")
            except Exception:
                evidence = evidence or ""
        if message.get_type() == "COMMAND_ACK":
            remember_command_ack(message)
        if message.get_type() == "COMMAND_ACK" and int(getattr(message, "command", -1)) == int(mav_command):
            result = int(getattr(message, "result", -1))
            return {
                "command": int(mav_command),
                "result": result,
                "resultText": MAV_RESULT_NAMES.get(result, f"RESULT_{result}"),
                "evidenceText": evidence,
                "latestStatustext": latest_statustext,
                **vehicle_target(master),
            }
    if evidence:
        in_progress = getattr(mavutil.mavlink, "MAV_RESULT_IN_PROGRESS", 5)
        return {
            "command": int(mav_command),
            "result": in_progress,
            "resultText": MAV_RESULT_NAMES.get(in_progress, "IN_PROGRESS"),
            "evidenceText": evidence,
            "latestStatustext": latest_statustext,
            **vehicle_target(master),
        }
    return {
        "command": int(mav_command),
        "result": None,
        "resultText": "TIMEOUT",
        "evidenceText": evidence,
        "latestStatustext": latest_statustext,
        **vehicle_target(master),
    }


def request_flight_log_entries(master, command_id=None, on_message=None):
    write_command_status(
        command_id,
        status="running",
        message="正在读取飞控 SD 卡 .ulg 日志列表",
        results=[],
    )
    master.mav.log_request_list_send(master.target_system, master.target_component, 0, 0xFFFF)
    deadline = time.monotonic() + 5.0
    logs = {}
    expected_count = None
    while time.monotonic() < deadline:
        send_gcs_heartbeat(master)
        message = master.recv_match(blocking=True, timeout=0.25)
        if message is None:
            continue
        if message.get_type() != "LOG_ENTRY":
            if on_message:
                on_message(message)
            continue
        item = {
            "id": int(message.id),
            "numLogs": int(message.num_logs),
            "lastLogNum": int(message.last_log_num),
            "timeUtc": int(getattr(message, "time_utc", 0) or 0),
            "size": int(getattr(message, "size", 0) or 0),
        }
        logs[item["id"]] = item
        expected_count = item["numLogs"]
        if expected_count is not None and len(logs) >= expected_count:
            break
    ordered = [logs[key] for key in sorted(logs)]
    status = "accepted" if ordered else "rejected"
    message = f"读取到 {len(ordered)} 条飞控 .ulg 日志" if ordered else "没有读取到飞控日志列表；请确认 SD 卡已插入且 PX4 日志可用"
    write_command_status(command_id, status=status, message=message, results=ordered)
    print(message)


def safe_ulg_filename(log_id, time_utc=0):
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(time_utc)) if time_utc else time.strftime("%Y%m%d_%H%M%S")
    return re.sub(r"[^A-Za-z0-9_.-]", "_", f"px4_log_{log_id}_{stamp}.ulg")


def request_log_data_chunk(master, log_id, offset, count, timeout=1.2, on_message=None):
    master.mav.log_request_data_send(
        master.target_system,
        master.target_component,
        int(log_id),
        int(offset),
        int(count),
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        send_gcs_heartbeat(master)
        message = master.recv_match(blocking=True, timeout=0.1)
        if message is None:
            continue
        if message.get_type() != "LOG_DATA":
            if on_message:
                on_message(message)
            continue
        if int(message.id) != int(log_id) or int(message.ofs) != int(offset):
            continue
        data = bytes(message.data[: int(message.count)])
        return data
    return None


def download_flight_log(master, log_id, size=0, time_utc=0, output_dir="downloads/ulg", command_id=None, on_message=None):
    log_id = int(log_id)
    size = max(0, int(size or 0))
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    filename = safe_ulg_filename(log_id, time_utc)
    output_path = output_root / filename
    partial_path = output_root / f"{filename}.part"
    if output_path.exists() and (size <= 0 or output_path.stat().st_size >= size):
        final_size = output_path.stat().st_size
        result = {
            "id": log_id,
            "size": size or final_size,
            "downloaded": final_size,
            "progress": 100,
            "path": str(output_path),
            "url": f"/downloads/ulg/{output_path.name}",
            "filename": output_path.name,
            "bytesPerSecond": 0,
            "etaSeconds": 0,
            "resumed": True,
        }
        write_command_status(
            command_id,
            status="accepted",
            message=f"飞控日志 {log_id} 已存在：{output_path.name}",
            results=[result],
        )
        return
    offset = partial_path.stat().st_size if partial_path.exists() else 0
    if size > 0 and offset > size:
        partial_path.unlink()
        offset = 0
    write_command_status(
        command_id,
        status="running",
        message=f"正在下载飞控日志 {log_id}" + (f"（从 {offset} 字节继续）" if offset else ""),
        results=[{"id": log_id, "size": size, "downloaded": offset, "progress": round(offset * 100 / size, 1) if size else 0, "resumed": bool(offset)}],
    )
    chunk_size = 90
    started_at = time.monotonic()
    last_status_at = 0
    initial_offset = offset
    with partial_path.open("ab") as handle:
        while True:
            request_count = chunk_size if size <= 0 else min(chunk_size, size - offset)
            if request_count <= 0:
                break
            chunk = None
            for _attempt in range(4):
                chunk = request_log_data_chunk(
                    master,
                    log_id,
                    offset,
                    request_count,
                    on_message=on_message,
                )
                if chunk is not None:
                    break
            if chunk is None:
                progress = round(offset * 100 / size, 1) if size else 0
                write_command_status(
                    command_id,
                    status="interrupted",
                    message=f"飞控日志 {log_id} 在 {offset} 字节处下载超时，已保留断点，下次可继续",
                    results=[{
                        "id": log_id,
                        "size": size,
                        "downloaded": offset,
                        "progress": progress,
                        "partialPath": str(partial_path),
                        "filename": output_path.name,
                        "resumable": True,
                    }],
                )
                try:
                    master.mav.log_request_end_send(master.target_system, master.target_component)
                except AttributeError:
                    pass
                return
            handle.write(chunk)
            handle.flush()
            offset += len(chunk)
            now = time.monotonic()
            if now - last_status_at > 0.8:
                progress = round(offset * 100 / size, 1) if size else 0
                elapsed = max(0.1, now - started_at)
                speed = round((offset - initial_offset) / elapsed, 1)
                eta = round((size - offset) / speed, 1) if size and speed > 0 else None
                write_command_status(
                    command_id,
                    status="running",
                    message=f"正在下载飞控日志 {log_id}：{progress}%",
                    results=[{
                        "id": log_id,
                        "size": size,
                        "downloaded": offset,
                        "progress": progress,
                        "bytesPerSecond": speed,
                        "etaSeconds": eta,
                        "resumed": bool(initial_offset),
                    }],
                )
                last_status_at = now
            if size <= 0 and len(chunk) < chunk_size:
                break
    try:
        master.mav.log_request_end_send(master.target_system, master.target_component)
    except AttributeError:
        pass
    partial_path.replace(output_path)
    final_size = output_path.stat().st_size
    result = {
        "id": log_id,
        "size": size or final_size,
        "downloaded": final_size,
        "progress": 100,
        "path": str(output_path),
        "url": f"/downloads/ulg/{output_path.name}",
        "filename": output_path.name,
        "bytesPerSecond": round((final_size - initial_offset) / max(0.1, time.monotonic() - started_at), 1),
        "etaSeconds": 0,
        "resumed": bool(initial_offset),
    }
    write_command_status(
        command_id,
        status="accepted",
        message=f"飞控日志 {log_id} 已下载完成：{output_path.name}",
        results=[result],
    )
    print(f"飞控日志 {log_id} 已下载：{output_path}")


def mission_ack_text(ack_type):
    names = {
        0: "ACCEPTED",
        1: "ERROR",
        2: "UNSUPPORTED_FRAME",
        3: "UNSUPPORTED",
        4: "NO_SPACE",
        5: "INVALID",
        6: "INVALID_PARAM1",
        7: "INVALID_PARAM2",
        8: "INVALID_PARAM3",
        9: "INVALID_PARAM4",
        10: "INVALID_PARAM5_X",
        11: "INVALID_PARAM6_Y",
        12: "INVALID_PARAM7",
        13: "INVALID_SEQUENCE",
        14: "DENIED",
        15: "OPERATION_CANCELLED",
    }
    return names.get(int(ack_type), f"ACK_{ack_type}")


def send_mission_clear_all(master):
    try:
        master.mav.mission_clear_all_send(
            master.target_system,
            master.target_component,
            MAV_MISSION_TYPE_MISSION,
        )
    except TypeError:
        master.mav.mission_clear_all_send(master.target_system, master.target_component)


def wait_mission_ack(master, timeout=5.0, on_message=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        send_gcs_heartbeat(master)
        message = master.recv_match(blocking=True, timeout=0.2)
        if message is None:
            continue
        if message.get_type() == "MISSION_ACK":
            ack_type = int(getattr(message, "type", -1))
            return {"type": ack_type, "resultText": mission_ack_text(ack_type)}
        if on_message:
            on_message(message)
    return {"type": -1, "resultText": "TIMEOUT"}


def clear_mission(master, command_id=None, on_message=None):
    write_command_status(command_id, status="running", message="正在清空飞控任务", results=[])
    send_mission_clear_all(master)
    ack = wait_mission_ack(master, timeout=5.0, on_message=on_message)
    accepted = ack["type"] == MAV_MISSION_ACCEPTED
    write_command_status(
        command_id,
        status="accepted" if accepted else "rejected",
        message="飞控任务已清空" if accepted else f"清空任务失败：{ack['resultText']}",
        results=[{"ack": ack}],
    )


def mission_item_payload(item, seq, total):
    command_name = str(item.get("command", "WAYPOINT")).upper()
    command = MISSION_COMMANDS.get(command_name, MISSION_COMMANDS["WAYPOINT"])
    frame = getattr(mavutil.mavlink, "MAV_FRAME_GLOBAL_RELATIVE_ALT_INT", 6)
    lat = float(item.get("lat", 0) or 0)
    lon = float(item.get("lon", 0) or 0)
    altitude = float(item.get("altitude", 0) or 0)
    hold = float(item.get("hold", 0) or 0)
    param1 = hold if command_name in {"WAYPOINT", "LOITER"} else 0
    param2 = 0
    param3 = 0
    param4 = float("nan")
    if command_name == "DO_CHANGE_SPEED":
        frame = getattr(mavutil.mavlink, "MAV_FRAME_MISSION", 2)
        param1 = 1
        param2 = float(item.get("speed", 0) or 0)
        param3 = -1
        param4 = 0
        lat = lon = altitude = 0
    if command_name == "RTL":
        lat = lon = altitude = 0
    return {
        "seq": int(seq),
        "frame": frame,
        "command": int(command),
        "current": 1 if seq == 0 else 0,
        "autocontinue": 1 if seq < total - 1 else 0,
        "param1": param1,
        "param2": param2,
        "param3": param3,
        "param4": param4,
        "x": int(round(lat * 1e7)),
        "y": int(round(lon * 1e7)),
        "z": altitude,
        "name": command_name,
    }


def expand_mission_upload_items(waypoints):
    expanded = []
    last_speed = None
    for item in waypoints:
        command_name = str(item.get("command", "WAYPOINT")).upper()
        speed = float(item.get("speed", 0) or 0)
        if speed > 0 and command_name not in {"RTL"} and speed != last_speed:
            expanded.append({"command": "DO_CHANGE_SPEED", "speed": speed})
            last_speed = speed
        expanded.append(item)
    return expanded


def send_mission_item(master, item, seq, total, use_int=True):
    payload = mission_item_payload(item, seq, total)
    if use_int:
        try:
            master.mav.mission_item_int_send(
                master.target_system,
                master.target_component,
                payload["seq"],
                payload["frame"],
                payload["command"],
                payload["current"],
                payload["autocontinue"],
                payload["param1"],
                payload["param2"],
                payload["param3"],
                payload["param4"],
                payload["x"],
                payload["y"],
                payload["z"],
                MAV_MISSION_TYPE_MISSION,
            )
            return payload
        except TypeError:
            master.mav.mission_item_int_send(
                master.target_system,
                master.target_component,
                payload["seq"],
                payload["frame"],
                payload["command"],
                payload["current"],
                payload["autocontinue"],
                payload["param1"],
                payload["param2"],
                payload["param3"],
                payload["param4"],
                payload["x"],
                payload["y"],
                payload["z"],
            )
            return payload
    args = [
        master.target_system,
        master.target_component,
        payload["seq"],
        getattr(mavutil.mavlink, "MAV_FRAME_GLOBAL_RELATIVE_ALT", 3),
        payload["command"],
        payload["current"],
        payload["autocontinue"],
        payload["param1"],
        payload["param2"],
        payload["param3"],
        payload["param4"],
        payload["x"] / 1e7,
        payload["y"] / 1e7,
        payload["z"],
    ]
    try:
        master.mav.mission_item_send(*args, MAV_MISSION_TYPE_MISSION)
    except TypeError:
        master.mav.mission_item_send(*args)
    return payload


def send_mission_count(master, count):
    try:
        master.mav.mission_count_send(
            master.target_system,
            master.target_component,
            int(count),
            MAV_MISSION_TYPE_MISSION,
        )
    except TypeError:
        master.mav.mission_count_send(master.target_system, master.target_component, int(count))


def upload_mission(master, waypoints, command_id=None, clear_existing=True, on_message=None):
    waypoints = list(waypoints or [])
    if not waypoints:
        raise RuntimeError("任务中没有航点")
    upload_items = expand_mission_upload_items(waypoints)
    write_command_status(
        command_id,
        status="running",
        message=f"正在上传 Mission：0/{len(upload_items)}",
        results=[{
            "count": len(upload_items),
            "sourceWaypointCount": len(waypoints),
            "uploaded": 0,
            "progress": 0,
        }],
    )
    if clear_existing:
        send_mission_clear_all(master)
        wait_mission_ack(master, timeout=3.0, on_message=on_message)
    send_mission_count(master, len(upload_items))
    sent = {}
    deadline = time.monotonic() + max(20.0, len(upload_items) * 4.0)
    last_activity = time.monotonic()
    count_retries = 0
    while time.monotonic() < deadline:
        send_gcs_heartbeat(master)
        message = master.recv_match(blocking=True, timeout=0.3)
        if message is None:
            if time.monotonic() - last_activity > 2.0 and count_retries < 3:
                count_retries += 1
                send_mission_count(master, len(upload_items))
                last_activity = time.monotonic()
                write_command_status(
                    command_id,
                    status="running",
                    message=f"Mission 上传等待飞控请求航点，已重发 MISSION_COUNT {count_retries}/3",
                    results=[{
                        "count": len(upload_items),
                        "sourceWaypointCount": len(waypoints),
                        "uploaded": len(sent),
                        "progress": round(len(sent) * 100 / len(upload_items), 1),
                        "countRetries": count_retries,
                    }],
                )
            continue
        msg_type = message.get_type()
        last_activity = time.monotonic()
        if msg_type in {"MISSION_REQUEST_INT", "MISSION_REQUEST"}:
            seq = int(getattr(message, "seq", -1))
            if not 0 <= seq < len(upload_items):
                raise RuntimeError(f"飞控请求了无效航点序号 {seq}")
            use_int = msg_type == "MISSION_REQUEST_INT"
            payload = send_mission_item(master, upload_items[seq], seq, len(upload_items), use_int=use_int)
            sent[seq] = payload
            progress = round(len(sent) * 100 / len(upload_items), 1)
            write_command_status(
                command_id,
                status="running",
                message=f"正在上传 Mission：{len(sent)}/{len(upload_items)}",
                results=[{
                    "count": len(upload_items),
                    "sourceWaypointCount": len(waypoints),
                    "uploaded": len(sent),
                    "progress": progress,
                    "lastSeq": seq,
                    "lastCommand": payload["name"],
                }],
            )
            deadline = time.monotonic() + 8.0
            continue
        if msg_type == "MISSION_ACK":
            ack_type = int(getattr(message, "type", -1))
            accepted = ack_type == MAV_MISSION_ACCEPTED
            verification = None
            if accepted:
                verification = verify_uploaded_mission(master, upload_items, on_message=on_message)
            result = {
                "count": len(upload_items),
                "sourceWaypointCount": len(waypoints),
                "uploaded": len(sent),
                "progress": 100 if accepted else round(len(sent) * 100 / len(upload_items), 1),
                "ack": {"type": ack_type, "resultText": mission_ack_text(ack_type)},
                "verified": bool(verification and verification.get("matched")),
                "verification": verification,
            }
            write_command_status(
                command_id,
                status="accepted" if accepted and result["verified"] else ("partial" if accepted else "rejected"),
                message=(
                    f"Mission 上传完成并回读校验通过：{len(waypoints)} 个航点，{len(upload_items)} 条任务项"
                    if accepted and result["verified"]
                    else f"Mission 已被飞控接受，但回读校验未通过：{(verification or {}).get('reason', 'no verification')}"
                    if accepted
                    else f"Mission 上传失败：{mission_ack_text(ack_type)}"
                ),
                results=[result],
            )
            return
        if on_message:
            on_message(message)
    write_command_status(
        command_id,
        status="rejected",
        message="Mission 上传超时：未收到完整 MISSION_REQUEST/MISSION_ACK",
        results=[{"count": len(upload_items), "uploaded": len(sent), "progress": round(len(sent) * 100 / len(upload_items), 1)}],
    )


def request_mission_item(master, seq):
    try:
        master.mav.mission_request_int_send(
            master.target_system,
            master.target_component,
            int(seq),
            MAV_MISSION_TYPE_MISSION,
        )
    except (AttributeError, TypeError):
        try:
            master.mav.mission_request_send(
                master.target_system,
                master.target_component,
                int(seq),
                MAV_MISSION_TYPE_MISSION,
            )
        except TypeError:
            master.mav.mission_request_send(master.target_system, master.target_component, int(seq))


def request_mission_list(master):
    try:
        master.mav.mission_request_list_send(
            master.target_system,
            master.target_component,
            MAV_MISSION_TYPE_MISSION,
        )
    except TypeError:
        master.mav.mission_request_list_send(master.target_system, master.target_component)


def mav_command_label(command):
    command = int(command)
    for name, value in MISSION_COMMANDS.items():
        if int(value) == command:
            return name
    return f"CMD_{command}"


def mission_message_to_dict(message):
    msg_type = message.get_type()
    command = int(getattr(message, "command", 0))
    if msg_type == "MISSION_ITEM_INT":
        lat = float(getattr(message, "x", 0) or 0) / 1e7
        lon = float(getattr(message, "y", 0) or 0) / 1e7
    else:
        lat = float(getattr(message, "x", 0) or 0)
        lon = float(getattr(message, "y", 0) or 0)
    item = {
        "seq": int(getattr(message, "seq", 0)),
        "frame": int(getattr(message, "frame", 0)),
        "command": command,
        "commandName": mav_command_label(command),
        "current": int(getattr(message, "current", 0)),
        "autocontinue": int(getattr(message, "autocontinue", 0)),
        "param1": float(getattr(message, "param1", 0) or 0),
        "param2": float(getattr(message, "param2", 0) or 0),
        "param3": float(getattr(message, "param3", 0) or 0),
        "param4": float(getattr(message, "param4", 0) or 0),
        "lat": lat,
        "lon": lon,
        "altitude": float(getattr(message, "z", 0) or 0),
    }
    if item["commandName"] == "DO_CHANGE_SPEED":
        item["speed"] = item["param2"]
    return item


def mission_items_match_upload(upload_items, read_items):
    if len(read_items) != len(upload_items):
        return False, f"任务项数量不一致：上传 {len(upload_items)}，回读 {len(read_items)}"
    by_seq = {int(item.get("seq", -1)): item for item in read_items}
    for seq, upload in enumerate(upload_items):
        read_item = by_seq.get(seq)
        if not read_item:
            return False, f"缺少回读任务项 seq={seq}"
        expected_command = int(upload.get("command", -1))
        actual_command = int(read_item.get("command", -2))
        if expected_command != actual_command:
            return False, f"seq={seq} 命令不一致：上传 {expected_command}，回读 {actual_command}"
        if upload.get("command") in {MISSION_COMMANDS["WAYPOINT"], MISSION_COMMANDS["TAKEOFF"], MISSION_COMMANDS["LAND"], MISSION_COMMANDS["LOITER"]}:
            lat_diff = abs(float(upload.get("lat", 0) or 0) - float(read_item.get("lat", 0) or 0))
            lon_diff = abs(float(upload.get("lon", 0) or 0) - float(read_item.get("lon", 0) or 0))
            alt_diff = abs(float(upload.get("altitude", 0) or 0) - float(read_item.get("altitude", 0) or 0))
            if lat_diff > 1e-6 or lon_diff > 1e-6 or alt_diff > 0.5:
                return False, f"seq={seq} 位置/高度不一致"
    return True, "回读任务与上传任务一致"


def read_mission_items_from_vehicle(master, command_id=None, on_message=None, label="Reading flight mission"):
    request_mission_list(master)
    deadline = time.monotonic() + 6.0
    count = None
    while time.monotonic() < deadline:
        send_gcs_heartbeat(master)
        message = master.recv_match(blocking=True, timeout=0.25)
        if message is None:
            continue
        if message.get_type() != "MISSION_COUNT":
            if on_message:
                on_message(message)
            continue
        count = int(getattr(message, "count", 0))
        break
    if count is None:
        return {"count": None, "items": [], "status": "timeout", "reason": "Mission read timed out: no MISSION_COUNT"}

    items = {}
    for seq in range(count):
        received = False
        for attempt in range(3):
            request_mission_item(master, seq)
            item_deadline = time.monotonic() + 2.0
            while time.monotonic() < item_deadline:
                send_gcs_heartbeat(master)
                message = master.recv_match(blocking=True, timeout=0.18)
                if message is None:
                    continue
                if message.get_type() not in {"MISSION_ITEM", "MISSION_ITEM_INT"}:
                    if on_message:
                        on_message(message)
                    continue
                item = mission_message_to_dict(message)
                if item["seq"] != seq:
                    continue
                items[seq] = item
                received = True
                if on_message:
                    on_message(message)
                write_command_status(
                    command_id,
                    status="running",
                    message=f"{label}: {len(items)}/{count}",
                    results=[{"count": count, "items": [items[index] for index in sorted(items)]}],
                )
                break
            if received:
                break
        if not received:
            return {
                "count": count,
                "items": [items[index] for index in sorted(items)],
                "status": "partial",
                "reason": f"Mission read missing seq={seq}",
            }
    return {
        "count": count,
        "items": [items[index] for index in sorted(items)],
        "status": "accepted",
        "reason": f"Flight mission read: {len(items)}/{count}",
    }


def verify_uploaded_mission(master, upload_items, on_message=None):
    readback = read_mission_items_from_vehicle(master, command_id=None, on_message=on_message, label="Verifying uploaded Mission")
    if readback.get("status") != "accepted":
        return {"matched": False, "reason": readback.get("reason") or "Mission readback failed", "readback": readback}
    matched, reason = mission_items_match_upload(upload_items, readback.get("items") or [])
    return {"matched": matched, "reason": reason, "readback": readback}


def read_flight_mission(master, command_id=None, on_message=None):
    write_command_status(command_id, status="running", message="Reading flight mission", results=[])
    readback = read_mission_items_from_vehicle(master, command_id=command_id, on_message=on_message)
    ordered = readback.get("items") or []
    count = readback.get("count") or 0
    status = "accepted" if readback.get("status") == "accepted" else ("partial" if ordered else "sent_no_ack")
    write_command_status(
        command_id,
        status=status,
        message=readback.get("reason") or f"Flight mission read: {len(ordered)}/{count}",
        results=[{"count": count, "items": ordered}],
    )


def send_px4_actuator_test(master, channel, pwm, timeout_s=2.0, output_function=None):
    normalized = max(-1.0, min(1.0, (pwm - 1500) / 500))
    output_function = mavlink_servo_test_function(channel, output_function)
    master.mav.command_long_send(
        master.target_system,
        master.target_component,
        MAV_CMD_ACTUATOR_TEST,
        0,
        normalized,
        timeout_s,
        0,
        0,
        output_function,
        0,
        0,
    )
    return wait_for_command_ack(master, MAV_CMD_ACTUATOR_TEST, timeout=1.0)


def mavlink_servo_test_function(channel, output_function=None):
    if output_function not in (None, ""):
        value = int(output_function)
        if 201 <= value <= 208:
            return 33 + (value - 201)
        if 33 <= value <= 40:
            return value
    return PX4_SERVO_OUTPUT_FUNCTION_BASE + channel - 1


def mavlink_motor_test_function(motor, output_function=None):
    if output_function not in (None, ""):
        value = int(output_function)
        if 101 <= value <= 112:
            return value - 100
        if 1 <= value <= 12:
            return value
    return motor


def send_px4_motor_actuator_test(master, motor, throttle_percent, timeout_s=2.0, output_function=None):
    normalized = max(0.0, min(1.0, throttle_percent / 100))
    test_function = mavlink_motor_test_function(motor, output_function)
    master.mav.command_long_send(
        master.target_system,
        master.target_component,
        MAV_CMD_ACTUATOR_TEST,
        0,
        normalized,
        timeout_s,
        0,
        0,
        test_function,
        0,
        0,
    )
    return wait_for_command_ack(master, MAV_CMD_ACTUATOR_TEST, timeout=1.0)


def send_legacy_servo(master, channel, pwm):
    master.mav.command_long_send(
        master.target_system,
        master.target_component,
        mavutil.mavlink.MAV_CMD_DO_SET_SERVO,
        0,
        channel,
        pwm,
        0,
        0,
        0,
        0,
        0,
    )
    return wait_for_command_ack(master, mavutil.mavlink.MAV_CMD_DO_SET_SERVO, timeout=0.8)


def wait_for_param_value(master, name, expected=None, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        send_gcs_heartbeat(master)
        message = master.recv_match(type="PARAM_VALUE", blocking=True, timeout=0.1)
        if message is None:
            continue
        param_id = message.param_id
        if isinstance(param_id, bytes):
            param_id = param_id.decode("ascii", errors="replace")
        if str(param_id).rstrip("\x00") != name:
            continue
        value = decode_param_value(message)
        if expected is None or int(round(float(value))) == int(round(float(expected))):
            return {"name": name, "value": value, "confirmed": True}
        return {"name": name, "value": value, "confirmed": False}
    return {"name": name, "value": None, "confirmed": False}


def request_parameter_values(master, names=None, command_id=None, on_message=None):
    requested = [
        str(name or "").strip().upper()
        for name in (names or DEFAULT_PARAMETER_REQUESTS)
        if str(name or "").strip()
    ]
    requested = list(dict.fromkeys(requested))
    if not requested:
        requested = list(DEFAULT_PARAMETER_REQUESTS)
    write_command_status(
        command_id,
        status="running",
        message=f"正在读取飞控参数：0/{len(requested)}",
        results=[],
    )
    for name in requested:
        master.mav.param_request_read_send(
            master.target_system,
            master.target_component,
            name.encode("ascii", errors="ignore"),
            -1,
        )
        time.sleep(0.015)

    pending = set(requested)
    results = {}
    deadline = time.monotonic() + max(4.0, len(requested) * 0.16)
    while pending and time.monotonic() < deadline:
        send_gcs_heartbeat(master)
        message = master.recv_match(blocking=True, timeout=0.12)
        if message is None:
            continue
        if message.get_type() != "PARAM_VALUE":
            if on_message:
                on_message(message)
            continue
        if on_message:
            on_message(message)
        param_id = message.param_id
        if isinstance(param_id, bytes):
            param_id = param_id.decode("ascii", errors="replace")
        name = str(param_id).rstrip("\x00")
        if name not in pending:
            continue
        value = decode_param_value(message)
        results[name] = {
            "name": name,
            "value": value,
            "index": int(getattr(message, "param_index", -1)),
            "count": int(getattr(message, "param_count", -1)),
            "type": int(getattr(message, "param_type", 0) or 0),
        }
        pending.discard(name)
        write_command_status(
            command_id,
            status="running",
            message=f"正在读取飞控参数：{len(results)}/{len(requested)}",
            results=list(results.values()),
        )

    status = "accepted" if results else "sent_no_ack"
    if pending and results:
        status = "partial"
    write_command_status(
        command_id,
        status=status,
        message=(
            f"参数读取完成：{len(results)}/{len(requested)}"
            if results
            else "参数读取超时：未收到 PARAM_VALUE"
        ),
        results=list(results.values()),
        missing=sorted(pending),
    )


def send_parameter(master, name, value, command_id=None):
    name = str(name or "").strip().upper()
    numeric_value = float(value)
    if name.startswith(("PWM_MAIN_FUNC", "PWM_AUX_FUNC")):
        encoded_value = struct.unpack("<f", struct.pack("<i", int(numeric_value)))[0]
        param_type = mavutil.mavlink.MAV_PARAM_TYPE_INT32
    else:
        encoded_value = numeric_value
        param_type = mavutil.mavlink.MAV_PARAM_TYPE_REAL32
    write_command_status(
        command_id,
        status="running",
        message=f"参数 {name} 正在写入飞控",
        results=[],
    )
    master.mav.param_set_send(
        master.target_system,
        master.target_component,
        name.encode("ascii"),
        encoded_value,
        param_type,
    )
    result = wait_for_param_value(master, name, expected=numeric_value, timeout=2.0)
    status = "accepted" if result["confirmed"] else "sent_no_ack"
    message = f"参数 {name} 已确认写入为 {result['value']}" if result["confirmed"] else f"参数 {name} 已发送，但未收到确认"
    write_command_status(
        command_id,
        status=status,
        message=message,
        results=[{"name": name, "value": numeric_value, "ack": result}],
    )
    print(f"已发送参数写入：{name}={numeric_value}, confirmed={result['confirmed']}")


def send_servo_outputs(master, outputs, command_id=None):
    write_command_status(
        command_id,
        status="running",
        message="舵机测试命令正在发送到飞控",
        results=[],
    )
    results = []
    for item in outputs or []:
        channel = int(item.get("channel", 0))
        pwm = int(item.get("pwm", 1500))
        output_function = item.get("outputFunction")
        if not 1 <= channel <= 16:
            print(f"忽略无效舵机通道：{channel}")
            continue
        pwm = max(800, min(2200, pwm))
        actuator_ack = send_px4_actuator_test(master, channel, pwm, output_function=output_function)
        method = "PX4_ACTUATOR_TEST"
        ack = actuator_ack
        if actuator_ack["resultText"] not in {"ACCEPTED", "IN_PROGRESS"}:
            legacy_ack = send_legacy_servo(master, channel, pwm)
            method = "DO_SET_SERVO"
            ack = legacy_ack
        result = {
            "channel": channel,
            "pwm": pwm,
            "outputFunction": output_function,
            "testFunction": mavlink_servo_test_function(channel, output_function),
            "method": method,
            "ack": ack,
        }
        results.append(result)
        print(
            "已发送舵机测试命令："
            f"channel={channel}, pwm={pwm}, method={method}, ack={ack['resultText']}"
        )
    accepted = any(item["ack"]["resultText"] in {"ACCEPTED", "IN_PROGRESS"} for item in results)
    no_ack = results and all(is_ack_timeout(item["ack"]) for item in results)
    unsupported = results and all(item["ack"]["resultText"] == "UNSUPPORTED" for item in results)
    if accepted:
        status = "accepted"
        message = "飞控已确认舵机测试命令；若舵机仍不动，请检查 PX4 输出功能/舵机供电。"
    elif no_ack:
        status = "sent_no_ack"
        message = "命令已发出，但飞控没有回 ACK；请检查 MAVLink 路由和 PX4 是否允许执行器测试。"
    elif unsupported:
        status = "unsupported"
        message = "飞控回复不支持当前舵机测试命令；请在 PX4 参数里确认输出功能映射。"
    else:
        status = "rejected"
        message = "飞控未接受舵机测试命令；请确认飞控未解锁、已在安全测试状态。"
    write_command_status(command_id, status=status, message=message, results=results)


def send_motor_test(master, motor, throttle_percent, duration, command_id=None, output_function=None, output=None):
    write_command_status(
        command_id,
        status="running",
        message="电机测试命令正在发送到飞控",
        results=[],
    )
    motor = max(1, min(12, int(motor)))
    throttle_percent = max(0.0, min(10.0, float(throttle_percent)))
    duration = max(0.2, min(5.0, float(duration)))
    actuator_ack = send_px4_motor_actuator_test(master, motor, throttle_percent, duration, output_function=output_function)
    ack = actuator_ack
    legacy_ack = None
    method = "PX4_ACTUATOR_TEST"
    if ack["resultText"] not in {"ACCEPTED", "IN_PROGRESS"}:
        master.mav.command_long_send(
            master.target_system,
            master.target_component,
            mavutil.mavlink.MAV_CMD_DO_MOTOR_TEST,
            0,
            motor,
            mavutil.mavlink.MOTOR_TEST_THROTTLE_PERCENT,
            throttle_percent,
            duration,
            1,
            mavutil.mavlink.MOTOR_TEST_ORDER_DEFAULT,
            0,
        )
        legacy_ack = wait_for_command_ack(master, mavutil.mavlink.MAV_CMD_DO_MOTOR_TEST, timeout=1.2)
        ack = legacy_ack
        method = "DO_MOTOR_TEST"
    accepted = ack["resultText"] in {"ACCEPTED", "IN_PROGRESS"}
    status = "accepted" if accepted else ("sent_no_ack" if is_ack_timeout(ack) else "rejected")
    message = (
        f"飞控已确认电机 {motor} 测试命令"
        if accepted
        else f"电机 {motor} 测试未被飞控确认：{ack['resultText']}"
    )
    write_command_status(
        command_id,
        status=status,
        message=message,
        results=[{
            "motor": motor,
            "output": output,
            "outputFunction": output_function if output_function not in (None, "") else PX4_MOTOR_OUTPUT_FUNCTION_BASE + motor - 1,
            "testFunction": mavlink_motor_test_function(motor, output_function),
            "throttlePercent": throttle_percent,
            "duration": duration,
            "method": method,
            "ack": ack,
            "actuatorAck": actuator_ack,
            "legacyAck": legacy_ack,
        }],
    )
    print(
        "已发送电机测试命令："
        f"motor={motor}, outputFunction={output_function}, testFunction={mavlink_motor_test_function(motor, output_function)}, "
        f"throttle={throttle_percent}%, duration={duration}s, method={method}, ack={ack['resultText']}"
    )


def px4_custom_mode(main_mode, sub_mode=0):
    return (int(main_mode) << 16) | (int(sub_mode) << 24)


def send_flight_mode(master, mode_key, command_id=None, on_message=None):
    mode_key = str(mode_key or "").strip().lower()
    preset = PX4_FLIGHT_MODE_COMMANDS.get(mode_key)
    if not preset:
        write_command_status(
            command_id,
            status="rejected",
            message=f"不支持的 PX4 飞行模式：{mode_key}",
            results=[],
        )
        return

    main_mode = int(preset["mainMode"])
    sub_mode = int(preset["subMode"])
    custom_mode = px4_custom_mode(main_mode, sub_mode)
    base_mode = getattr(mavutil.mavlink, "MAV_MODE_FLAG_CUSTOM_MODE_ENABLED", 1)
    mav_command = mavutil.mavlink.MAV_CMD_DO_SET_MODE

    write_command_status(
        command_id,
        status="running",
        message=f"正在切换飞控模式到 {preset['label']} / {preset['px4']}",
        results=[],
    )
    master.mav.command_long_send(
        master.target_system,
        master.target_component,
        mav_command,
        0,
        base_mode,
        main_mode,
        sub_mode,
        0,
        0,
        0,
        0,
    )
    ack = wait_for_command_ack(master, mav_command, timeout=1.2)
    fallback_sent = False
    if ack["resultText"] not in {"ACCEPTED", "IN_PROGRESS"}:
        fallback_sent = True
        master.mav.set_mode_send(master.target_system, base_mode, custom_mode)

    confirmed = False
    observed_mode = None
    deadline = time.monotonic() + 2.5
    while time.monotonic() < deadline:
        send_gcs_heartbeat(master)
        message = master.recv_match(blocking=True, timeout=0.2)
        if message is None:
            continue
        if message.get_type() == "HEARTBEAT" and is_vehicle_heartbeat(message):
            observed_mode = px4_mode(getattr(message, "custom_mode", 0))
            if int(getattr(message, "custom_mode", -1)) == custom_mode:
                confirmed = True
                break
        if on_message:
            on_message(message)

    accepted_ack = ack["resultText"] in {"ACCEPTED", "IN_PROGRESS"}
    if confirmed:
        status = "accepted"
        status_message = f"飞控已切换到 {preset['label']} / {preset['px4']}"
    elif accepted_ack:
        status = "accepted"
        status_message = f"飞控已接收模式切换命令：{preset['label']}，等待 HEARTBEAT 回读确认"
    else:
        status = "sent_no_ack" if is_ack_timeout(ack) or fallback_sent else command_result_status(ack)
        status_message = f"模式切换未被飞控确认：{ack['resultText']}"

    write_command_status(
        command_id,
        status=status,
        message=status_message,
        results=[{
            "mode": mode_key,
            "label": preset["label"],
            "px4Mode": preset["px4"],
            "mainMode": main_mode,
            "subMode": sub_mode,
            "customMode": custom_mode,
            "ack": ack,
            "fallbackSetModeSent": fallback_sent,
            "heartbeatConfirmed": confirmed,
            "observedMode": observed_mode,
        }],
    )
    print(f"飞行模式切换：{preset['px4']} ack={ack['resultText']} confirmed={confirmed} observed={observed_mode}")


def send_arm_disarm(master, arm, command_id=None, on_message=None):
    mav_command = mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM
    arm = bool(arm)
    action_label = "arm" if arm else "disarm"
    write_command_status(
        command_id,
        status="running",
        message=f"Sending {action_label} command to flight controller",
        results=[],
    )
    master.mav.command_long_send(
        master.target_system,
        master.target_component,
        mav_command,
        0,
        1 if arm else 0,
        0,
        0,
        0,
        0,
        0,
        0,
    )
    ack = wait_for_command_ack(master, mav_command, timeout=1.5)
    accepted = ack["resultText"] in {"ACCEPTED", "IN_PROGRESS"}
    confirmed = False
    observed_armed = None
    deadline = time.monotonic() + 2.5
    while time.monotonic() < deadline:
        send_gcs_heartbeat(master)
        message = master.recv_match(blocking=True, timeout=0.2)
        if message is None:
            continue
        if message.get_type() == "HEARTBEAT" and is_vehicle_heartbeat(message):
            observed_armed = bool(
                int(getattr(message, "base_mode", 0))
                & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED
            )
            if observed_armed == arm:
                confirmed = True
                break
        if on_message:
            on_message(message)

    if confirmed:
        status = "accepted"
        message = f"Flight controller confirmed {action_label}"
    elif accepted:
        status = "accepted"
        message = f"Flight controller accepted {action_label}; waiting for HEARTBEAT confirmation"
    else:
        status = "sent_no_ack" if is_ack_timeout(ack) else command_result_status(ack)
        message = f"{action_label} was not confirmed by flight controller: {ack['resultText']}"

    write_command_status(
        command_id,
        status=status,
        message=message,
        results=[{
            "command": "MAV_CMD_COMPONENT_ARM_DISARM",
            "arm": arm,
            "ack": ack,
            "observedArmed": observed_armed,
            "confirmed": confirmed,
        }],
    )
    print(f"{action_label} command sent: ack={ack['resultText']} confirmed={confirmed}")


def read_connector_commands(offset):
    if not COMMAND_QUEUE.exists():
        return offset, []
    statuses = load_command_statuses()
    with COMMAND_QUEUE.open("r", encoding="utf-8") as handle:
        handle.seek(offset)
        lines = handle.readlines()
        offset = handle.tell()
    commands = []
    for line in lines:
        try:
            command = json.loads(line)
            if should_process_queued_command(command, statuses):
                commands.append(command)
        except json.JSONDecodeError:
            print("忽略损坏的本地指令行")
    return offset, commands


def process_connector_commands(master, offset, on_message=None):
    offset, commands = read_connector_commands(offset)
    for command in commands:
        command_id = command.get("id")
        command_name = str(command.get("command") or "unknown")
        write_command_status(
            command_id,
            status="running",
            message=f"Connector picked up {command_name}; sending to flight controller",
            results=[],
            command=command_name,
            target=vehicle_target(master),
            pickedUpAt=int(time.time() * 1000),
        )
        try:
            if command.get("command") == "calibrate":
                send_calibration(
                    master,
                    command.get("type"),
                    command.get("id"),
                    on_message=on_message,
                )
            elif command.get("command") == "request_parameters":
                request_parameter_values(
                    master,
                    command.get("names") or DEFAULT_PARAMETER_REQUESTS,
                    command.get("id"),
                    on_message=on_message,
                )
            elif command.get("command") == "set_parameter":
                send_parameter(
                    master,
                    command.get("name", ""),
                    command.get("value", 0),
                    command.get("id"),
                )
            elif command.get("command") == "set_flight_mode":
                send_flight_mode(
                    master,
                    command.get("mode", ""),
                    command.get("id"),
                    on_message=on_message,
                )
            elif command.get("command") == "arm_disarm":
                send_arm_disarm(
                    master,
                    bool(command.get("arm")),
                    command.get("id"),
                    on_message=on_message,
                )
            elif command.get("command") == "set_servo":
                send_servo_outputs(master, command.get("outputs", []), command.get("id"))
            elif command.get("command") == "test_motor":
                send_motor_test(
                    master,
                    command.get("motor", 1),
                    command.get("throttlePercent", 0),
                    command.get("duration", 2),
                    command.get("id"),
                    command.get("outputFunction"),
                    command.get("output"),
                )
            elif command.get("command") == "list_flight_logs":
                request_flight_log_entries(master, command.get("id"), on_message=on_message)
            elif command.get("command") == "download_flight_log":
                download_flight_log(
                    master,
                    command.get("logId", 0),
                    command.get("size", 0),
                    command.get("timeUtc", 0),
                    command.get("outputDir", "downloads/ulg"),
                    command.get("id"),
                    on_message=on_message,
                )
            elif command.get("command") == "upload_mission":
                upload_mission(
                    master,
                    command.get("waypoints", []),
                    command.get("id"),
                    bool(command.get("clearExisting", True)),
                    on_message=on_message,
                )
            elif command.get("command") == "clear_mission":
                clear_mission(master, command.get("id"), on_message=on_message)
            elif command.get("command") == "read_mission":
                read_flight_mission(master, command.get("id"), on_message=on_message)
        except Exception as error:
            write_command_status(
                command.get("id"),
                status="rejected",
                message=f"命令执行失败：{error}",
                results=[],
            )
            print(f"命令执行失败：{error}")
    return offset


def detect_serial_port():
    ports = list(list_ports.comports())
    physical = [
        port for port in ports
        if "bluetooth" not in port.description.lower()
        and "蓝牙" not in port.description
    ]
    if len(physical) == 1:
        return physical[0].device
    if not physical:
        raise SystemExit("未发现 USB 数传串口。请插入数传电台后重试。")

    print("检测到多个物理串口：")
    for index, port in enumerate(physical, start=1):
        print(f"  {index}. {port.device} - {port.description}")
    while True:
        selected = input("请选择 PX6C 数传串口编号：").strip()
        if selected.isdigit() and 1 <= int(selected) <= len(physical):
            return physical[int(selected) - 1].device


def create_state(vehicle_id):
    return {
        "vehicleId": vehicle_id,
        "autopilot": "PX4",
        "hardware": "Pixhawk 6C",
        "connected": False,
        "armed": False,
        "mode": "UNKNOWN",
        "systemStatus": None,
        "lat": None,
        "lon": None,
        "alt": None,
        "relativeAlt": None,
        "speed": None,
        "climb": None,
        "heading": None,
        "roll": None,
        "pitch": None,
        "yaw": None,
        "battery": None,
        "voltage": None,
        "current": None,
        "satellites": None,
        "fixType": None,
        "eph": None,
        "rssi": None,
        "landedState": None,
        "homeLat": None,
        "homeLon": None,
        "homeAlt": None,
        "home_position": None,
        "vehicle_type": "UNKNOWN",
        "targetSystem": None,
        "targetComponent": None,
        "targetIdentified": False,
        "vehicleAutopilot": None,
        "vehicleBaseMode": None,
        "vehicleCustomMode": None,
        "vehicleMavlinkVersion": None,
        "vehicleHeartbeatAt": None,
        "gcsHeartbeat": {
            "enabled": False,
            "sending": False,
            "rateHz": 0,
            "lastSentMs": None,
            "count": 0,
            "ourSystemId": None,
            "ourComponentId": None,
        },
        "airspeed": None,
        "rc_signal": None,
        "rcSignalRaw": None,
        "rcUpdateTimeMs": None,
        "rcSource": "unavailable",
        "rcChannels": [],
        "rcRawChannels": [],
        "rcMapped": {},
        "rcMap": {},
        "rcMapAvailable": False,
        "rcIssues": ["RC_MAP parameters unavailable"],
        "rcThrottlePwm": None,
        "rcThrottlePercent": None,
        "rcThrottleSource": None,
        "manualControl": {},
        "manualControlTimeMs": None,
        "servo_outputs": [],
        "motor_outputs": [],
        "warnings": [],
        "statustexts": [],
        "commandAcks": [],
        "calibration": {},
        "parameters": {},
        "mission_items": [],
        "missionCurrent": {},
        "missionReached": {},
        "lastMessage": None,
    }


def update_state(state, message):
    message_type = message.get_type()
    state["lastMessage"] = message_type

    if message_type == "HEARTBEAT":
        if not is_vehicle_heartbeat(message):
            return False
        state["connected"] = True
        state["targetSystem"] = int(message.get_srcSystem())
        state["targetComponent"] = int(message.get_srcComponent())
        state["targetIdentified"] = bool(state["targetSystem"])
        state["vehicleAutopilot"] = int(getattr(message, "autopilot", -1))
        state["vehicleBaseMode"] = int(message.base_mode)
        state["vehicleCustomMode"] = int(message.custom_mode)
        state["vehicleMavlinkVersion"] = int(getattr(message, "mavlink_version", 0) or 0)
        state["vehicleHeartbeatAt"] = int(time.time() * 1000)
        state["armed"] = bool(
            message.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED
        )
        state["mode"] = px4_mode(message.custom_mode)
        state["systemStatus"] = int(message.system_status)
        state["vehicle_type"] = int(message.type)
    elif message_type == "GLOBAL_POSITION_INT":
        state["lat"] = message.lat / 1e7
        state["lon"] = message.lon / 1e7
        state["alt"] = message.alt / 1000
        state["relativeAlt"] = message.relative_alt / 1000
        state["heading"] = None if message.hdg == 65535 else message.hdg / 100
        vx = getattr(message, "vx", None)
        vy = getattr(message, "vy", None)
        if vx is not None and vy is not None:
            state["speed"] = math.hypot(float(vx), float(vy)) / 100.0
    elif message_type == "GPS_RAW_INT":
        state["satellites"] = int(message.satellites_visible)
        state["fixType"] = int(message.fix_type)
        state["eph"] = None if message.eph == 65535 else message.eph / 100
    elif message_type == "VFR_HUD":
        state["speed"] = float(message.groundspeed)
        state["airspeed"] = float(message.airspeed)
        state["climb"] = float(message.climb)
        state["heading"] = float(message.heading)
    elif message_type == "ATTITUDE":
        state["roll"] = math.degrees(message.roll)
        state["pitch"] = math.degrees(message.pitch)
        state["yaw"] = math.degrees(message.yaw)
        state["rollRate"] = math.degrees(getattr(message, "rollspeed", 0.0))
        state["pitchRate"] = math.degrees(getattr(message, "pitchspeed", 0.0))
        state["yawRate"] = math.degrees(getattr(message, "yawspeed", 0.0))
        state["attitudeTimeMs"] = int(time.time() * 1000)
    elif message_type == "SYS_STATUS":
        state["battery"] = (
            None if message.battery_remaining < 0 else int(message.battery_remaining)
        )
        state["voltage"] = (
            None if message.voltage_battery == 65535 else message.voltage_battery / 1000
        )
        state["current"] = (
            None if message.current_battery == -1 else message.current_battery / 100
        )
    elif message_type == "BATTERY_STATUS":
        valid_voltages = [value for value in message.voltages if value not in (0, 65535)]
        if valid_voltages:
            state["voltage"] = sum(valid_voltages) / 1000
        if message.current_battery != -1:
            state["current"] = message.current_battery / 100
        if message.battery_remaining >= 0:
            state["battery"] = int(message.battery_remaining)
    elif message_type == "RC_CHANNELS":
        state["rcSignalRaw"] = None if message.rssi == 255 else int(message.rssi)
        state["rc_signal"] = None if message.rssi == 255 else round(message.rssi / 255 * 100)
        state["rcUpdateTimeMs"] = int(time.time() * 1000)
        state["rcSource"] = "RC_CHANNELS"
        channel_count = int(getattr(message, "chancount", 0) or 0)
        channels = []
        for index in range(1, 19):
            value = int(getattr(message, f"chan{index}_raw", 0) or 0)
            if value and value != 65535 and (channel_count <= 0 or index <= channel_count):
                channels.append(value)
            else:
                channels.append(None)
        state["rcChannels"] = channels
        state["rcRawChannels"] = channels
        apply_rc_map(state)
    elif message_type == "RC_CHANNELS_RAW":
        state["rcSignalRaw"] = None if message.rssi == 255 else int(message.rssi)
        state["rc_signal"] = None if message.rssi == 255 else round(message.rssi / 255 * 100)
        channels = []
        for index in range(1, 9):
            value = int(getattr(message, f"chan{index}_raw", 0) or 0)
            channels.append(value if value and value != 65535 else None)
        state["rcChannelsRawLegacy"] = channels
    elif message_type == "MANUAL_CONTROL":
        z_value = int(getattr(message, "z", 32767))
        state["manualControlTimeMs"] = int(time.time() * 1000)
        state["manualControl"] = {
            "x": int(getattr(message, "x", 0)),
            "y": int(getattr(message, "y", 0)),
            "z": z_value,
            "r": int(getattr(message, "r", 0)),
            "buttons": int(getattr(message, "buttons", 0)),
        }
    elif message_type == "SERVO_OUTPUT_RAW":
        outputs = [
            int(getattr(message, f"servo{index}_raw", 0))
            for index in range(1, 17)
            if hasattr(message, f"servo{index}_raw")
        ]
        state["servo_outputs"] = outputs
        state["motor_outputs"] = outputs[:12]
    elif message_type == "RADIO_STATUS":
        state["rssi"] = min(100, round(int(message.rssi) / 255 * 100))
    elif message_type == "EXTENDED_SYS_STATE":
        state["landedState"] = int(message.landed_state)
    elif message_type == "HOME_POSITION":
        state["homeLat"] = message.latitude / 1e7
        state["homeLon"] = message.longitude / 1e7
        state["homeAlt"] = message.altitude / 1000
        state["home_position"] = {
            "latitude": state["homeLat"],
            "longitude": state["homeLon"],
            "altitude": state["homeAlt"],
        }
    elif message_type == "STATUSTEXT":
        text = message.text
        if isinstance(text, bytes):
            text = text.decode("utf-8", errors="replace")
        severity = MAV_SEVERITY_NAMES.get(int(getattr(message, "severity", 6) or 6), "INFO")
        clean_text = str(text).rstrip(chr(0))
        highlight_terms = [
            "Preflight Fail",
            "Arming denied",
            "Arming",
            "Arm",
            "Failsafe",
            "EKF",
            "Compass",
            "GPS",
            "Battery",
            "Safety",
            "Throttle",
            "RC",
            "Kill",
        ]
        matched = [term for term in highlight_terms if term.lower() in clean_text.lower()]
        entry = {
            "timeMs": int(time.time() * 1000),
            "severity": severity,
            "text": clean_text,
            "highlight": bool(matched),
            "category": matched[0] if matched else None,
        }
        state["statustexts"] = (state.get("statustexts", []) + [entry])[-100:]
        state["warnings"] = (state["warnings"] + [f"{severity}: {clean_text}"])[-20:]
    elif message_type == "PARAM_VALUE":
        param_id = message.param_id
        if isinstance(param_id, bytes):
            param_id = param_id.decode("ascii", errors="replace")
        name = str(param_id).rstrip("\x00")
        state["parameters"][name] = decode_param_value(message)
        if name in RC_MAP_PARAMETER_NAMES:
            apply_rc_map(state)
    elif message_type == "COMMAND_ACK":
        entry = remember_command_ack(message)
        state["commandAcks"] = (state.get("commandAcks", []) + [entry])[-100:]
    elif message_type == "MAG_CAL_PROGRESS":
        status_code = int(getattr(message, "cal_status", 0) or 0)
        state["calibration"] = {
            "type": "magnetometer",
            "running": True,
            "compassId": int(getattr(message, "compass_id", 0) or 0),
            "progress": int(getattr(message, "completion_pct", 0) or 0),
            "status": status_code,
            "statusText": MAG_CAL_STATUS_NAMES.get(status_code, str(status_code)),
            "timeMs": int(time.time() * 1000),
        }
    elif message_type == "MAG_CAL_REPORT":
        status_code = int(getattr(message, "cal_status", 0) or 0)
        status_text = MAG_CAL_STATUS_NAMES.get(status_code, str(status_code))
        state["calibration"] = {
            "type": "magnetometer",
            "running": False,
            "compassId": int(getattr(message, "compass_id", 0) or 0),
            "progress": 100 if status_text == "SUCCESS" or status_code == 4 else None,
            "status": status_code,
            "statusText": status_text,
            "fitness": float(getattr(message, "fitness", 0.0) or 0.0),
            "autosaved": bool(getattr(message, "autosaved", 0)),
            "timeMs": int(time.time() * 1000),
        }
    elif message_type in {"MISSION_ITEM", "MISSION_ITEM_INT"}:
        item = message.to_dict()
        state["mission_items"] = [
            existing for existing in state["mission_items"]
            if existing.get("seq") != item.get("seq")
        ] + [item]
        state["mission_items"].sort(key=lambda value: value.get("seq", 0))
    elif message_type == "MISSION_CURRENT":
        state["missionCurrent"] = {
            "seq": int(getattr(message, "seq", -1)),
            "total": int(getattr(message, "total", 0) or 0) if hasattr(message, "total") else None,
            "mission_state": int(getattr(message, "mission_state", -1)) if hasattr(message, "mission_state") else None,
            "mission_mode": int(getattr(message, "mission_mode", -1)) if hasattr(message, "mission_mode") else None,
            "timeMs": int(time.time() * 1000),
        }
    elif message_type == "MISSION_ITEM_REACHED":
        state["missionReached"] = {
            "seq": int(getattr(message, "seq", -1)),
            "timeMs": int(time.time() * 1000),
        }
    else:
        return False
    return True


def connect(connection, baud, source_system=255, source_component=None):
    print(f"正在连接 PX6C/Pixhawk 6C：{connection} @ {baud}")
    source_component = source_component or mavutil.mavlink.MAV_COMP_ID_MISSIONPLANNER
    master = mavutil.mavlink_connection(
        connection,
        baud=baud,
        autoreconnect=True,
        source_system=source_system,
        source_component=source_component,
    )
    heartbeat = wait_for_vehicle_heartbeat(master, connection, timeout=30)
    if heartbeat is None:
        raise TimeoutError("30 秒内未收到 PX4 HEARTBEAT")
    if heartbeat.autopilot != mavutil.mavlink.MAV_AUTOPILOT_PX4:
        print("警告：已收到 MAVLink，但飞控未报告为 PX4，继续以兼容模式读取。")
    master.target_system = heartbeat.get_srcSystem()
    master.target_component = heartbeat.get_srcComponent()
    print(
        f"PX4 已连接：system={master.target_system}, "
        f"component={master.target_component}"
    )
    request_message_intervals(master)
    request_output_function_params(master)
    return master, heartbeat


def decode_param_value(message):
    value = float(message.param_value)
    param_type = int(getattr(message, "param_type", 0) or 0)
    if param_type not in INTEGER_PARAM_TYPES:
        return value
    raw = struct.pack("<f", value)
    if param_type == mavutil.mavlink.MAV_PARAM_TYPE_UINT8:
        return struct.unpack("<B", raw[:1])[0]
    if param_type == mavutil.mavlink.MAV_PARAM_TYPE_INT8:
        return struct.unpack("<b", raw[:1])[0]
    if param_type == mavutil.mavlink.MAV_PARAM_TYPE_UINT16:
        return struct.unpack("<H", raw[:2])[0]
    if param_type == mavutil.mavlink.MAV_PARAM_TYPE_INT16:
        return struct.unpack("<h", raw[:2])[0]
    if param_type == mavutil.mavlink.MAV_PARAM_TYPE_UINT32:
        return struct.unpack("<I", raw)[0]
    if param_type == mavutil.mavlink.MAV_PARAM_TYPE_INT32:
        return struct.unpack("<i", raw)[0]
    return value


def main():
    parser = argparse.ArgumentParser(description="PX6C/Pixhawk 6C PX4 connector")
    parser.add_argument(
        "--connection",
        default="auto",
        help="auto、COM9 或 udpin:0.0.0.0:14550",
    )
    parser.add_argument("--baud", type=int, default=57600)
    parser.add_argument("--source-system", type=int, default=255)
    parser.add_argument(
        "--source-component",
        type=int,
        default=mavutil.mavlink.MAV_COMP_ID_MISSIONPLANNER,
    )
    parser.add_argument(
        "--ui",
        default="http://127.0.0.1:8080/api/telemetry",
    )
    parser.add_argument("--vehicle", default="PX6C-01")
    args = parser.parse_args()

    connection = detect_serial_port() if args.connection == "auto" else args.connection
    state = create_state(args.vehicle)
    last_heartbeat = 0.0
    publisher = UiTelemetryPublisher(args.ui, hz=UI_PUBLISH_HZ)
    publisher.start()

    while True:
        try:
            master, heartbeat = connect(
                connection,
                args.baud,
                source_system=args.source_system,
                source_component=args.source_component,
            )
            command_offset = 0
            last_command_scan = 0.0
            update_state(state, heartbeat)
            send_gcs_heartbeat(master, state, force=True)
            last_heartbeat = time.monotonic()
            publisher.publish(state, full=True)
            print("PX4 连接状态已发送到 UI，等待 GPS 定位...")

            def handle_command_side_message(side_message):
                nonlocal last_heartbeat
                side_now = time.monotonic()
                if is_vehicle_heartbeat(side_message):
                    last_heartbeat = side_now
                changed_side = update_state(state, side_message)
                if changed_side:
                    publisher.publish(
                        state,
                        full=side_message.get_type() in FULL_PUBLISH_MESSAGE_TYPES,
                    )

            while True:
                message = master.recv_match(blocking=True, timeout=0.02)
                now = time.monotonic()
                if now - last_command_scan >= COMMAND_SCAN_INTERVAL_S:
                    command_offset = process_connector_commands(
                        master,
                        command_offset,
                        on_message=handle_command_side_message,
                    )
                    last_command_scan = time.monotonic()
                if COMMAND_ACK_HISTORY:
                    state["commandAcks"] = COMMAND_ACK_HISTORY[-100:]
                now = time.monotonic()
                heartbeat_sent = send_gcs_heartbeat(master, state)
                if message is None:
                    if heartbeat_sent:
                        publisher.publish(state)
                    if now - last_heartbeat > 5:
                        raise ConnectionError("PX4 心跳超时")
                    continue

                if is_vehicle_heartbeat(message):
                    last_heartbeat = now
                changed = update_state(state, message)
                if not changed:
                    continue

                publisher.publish(
                    state,
                    full=message.get_type() in FULL_PUBLISH_MESSAGE_TYPES,
                )
        except KeyboardInterrupt:
            publisher.stop()
            print("PX6C 连接已停止。")
            return
        except Exception as error:
            state["connected"] = False
            publisher.publish(state, full=True)
            print(f"PX6C 连接中断：{error}，2 秒后重连...")
            time.sleep(2)


if __name__ == "__main__":
    main()

import argparse
import copy
import json
import math
import os
import threading
import time
import urllib.request

from pymavlink import mavutil

from core.runtime_paths import runtime_data_path
from backend.communication.heartbeat_manager import (
    is_vehicle_heartbeat,
    send_gcs_heartbeat,
    vehicle_target,
)
from backend.communication.command_queue import (
    command_result_status,
    is_ack_timeout,
    load_command_statuses,
    should_process_queued_command,
    write_command_status,
)
from backend.communication import command_sender
from backend.communication import parameter_manager
from backend.communication import calibration_manager
from backend.communication import mission_manager
from backend.communication import actuator_test_manager
from backend.communication import flight_log_manager
from backend.communication import connection_manager
from backend.communication.communication_monitor import CommunicationMonitor
from backend.communication.message_bus import MessageBus
from backend.communication.mavlink_receiver import MavlinkReceiver
from backend.communication.telemetry_state_manager import TelemetryStateManager
from backend.communication.telemetry_state_cache import TelemetryStateCache


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

COMMAND_QUEUE = runtime_data_path("commands", "px6c_commands.jsonl")
COMMAND_ACK_HISTORY = []
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
        self.publish_payload(payload)

    def publish_payload(self, payload):
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
    return command_sender.command_ack_entry(message, MAV_RESULT_NAMES)


def remember_command_ack(message):
    return command_sender.remember_command_ack(COMMAND_ACK_HISTORY, message, MAV_RESULT_NAMES)


def mavlink_text(message):
    return command_sender.mavlink_text(message)


def calibration_evidence_text(message):
    return calibration_manager.calibration_evidence_text(message, MAG_CAL_STATUS_NAMES)


def request_output_function_params(master):
    parameter_manager.request_output_function_params(
        master,
        OUTPUT_FUNCTION_PARAMS,
        RC_MAP_PARAMETER_NAMES,
        RC_CALIBRATION_PARAMETER_NAMES,
    )


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
    return calibration_manager.calibration_label_ascii(calibration_type)


def monitor_magnetometer_calibration(master, command_id=None, ack=None, on_message=None):
    calibration_manager.monitor_magnetometer_calibration(
        master,
        command_id=command_id,
        ack=ack,
        on_message=on_message,
        mag_cal_status_names=MAG_CAL_STATUS_NAMES,
    )


def send_calibration(master, calibration_type, command_id=None, on_message=None):
    calibration_manager.send_calibration(
        master,
        calibration_type,
        command_id=command_id,
        on_message=on_message,
        calibration_params=CALIBRATION_PARAMS,
        mag_cal_status_names=MAG_CAL_STATUS_NAMES,
        ack_history=COMMAND_ACK_HISTORY,
        result_names=MAV_RESULT_NAMES,
    )
    print(f"PX4 {calibration_label_ascii(calibration_type)} calibration command requested")


def wait_for_command_ack(master, mav_command, timeout=1.0, on_message=None, evidence_predicate=None):
    return command_sender.wait_for_command_ack(
        master,
        mav_command,
        timeout=timeout,
        on_message=on_message,
        evidence_predicate=evidence_predicate,
        ack_history=COMMAND_ACK_HISTORY,
        result_names=MAV_RESULT_NAMES,
        text_decoder=mavlink_text,
    )


def request_flight_log_entries(master, command_id=None, on_message=None):
    flight_log_manager.request_flight_log_entries(
        master,
        command_id=command_id,
        on_message=on_message,
    )


def safe_ulg_filename(log_id, time_utc=0):
    return flight_log_manager.safe_ulg_filename(log_id, time_utc)


def request_log_data_chunk(master, log_id, offset, count, timeout=1.2, on_message=None):
    return flight_log_manager.request_log_data_chunk(
        master,
        log_id,
        offset,
        count,
        timeout=timeout,
        on_message=on_message,
    )


def download_flight_log(master, log_id, size=0, time_utc=0, output_dir="downloads/ulg", command_id=None, on_message=None):
    flight_log_manager.download_flight_log(
        master,
        log_id,
        size=size,
        time_utc=time_utc,
        output_dir=output_dir,
        command_id=command_id,
        on_message=on_message,
    )


def mission_ack_text(ack_type):
    return mission_manager.mission_ack_text(ack_type)


def send_mission_clear_all(master):
    mission_manager.send_mission_clear_all(master, mission_type=MAV_MISSION_TYPE_MISSION)


def wait_mission_ack(master, timeout=5.0, on_message=None):
    return mission_manager.wait_mission_ack(master, timeout=timeout, on_message=on_message)


def clear_mission(master, command_id=None, on_message=None):
    mission_manager.clear_mission(
        master,
        command_id=command_id,
        on_message=on_message,
        accepted_ack=MAV_MISSION_ACCEPTED,
        mission_type=MAV_MISSION_TYPE_MISSION,
    )


def mission_item_payload(item, seq, total):
    return mission_manager.mission_item_payload(
        item,
        seq,
        total,
        mission_commands=MISSION_COMMANDS,
    )


def expand_mission_upload_items(waypoints):
    return mission_manager.expand_mission_upload_items(waypoints)


def send_mission_item(master, item, seq, total, use_int=True):
    return mission_manager.send_mission_item(
        master,
        item,
        seq,
        total,
        use_int=use_int,
        mission_commands=MISSION_COMMANDS,
        mission_type=MAV_MISSION_TYPE_MISSION,
    )


def send_mission_count(master, count):
    mission_manager.send_mission_count(master, count, mission_type=MAV_MISSION_TYPE_MISSION)


def upload_mission(master, waypoints, command_id=None, clear_existing=True, on_message=None):
    mission_manager.upload_mission(
        master,
        waypoints,
        command_id=command_id,
        clear_existing=clear_existing,
        on_message=on_message,
        mission_commands=MISSION_COMMANDS,
        accepted_ack=MAV_MISSION_ACCEPTED,
        mission_type=MAV_MISSION_TYPE_MISSION,
    )


def request_mission_item(master, seq):
    mission_manager.request_mission_item(master, seq, mission_type=MAV_MISSION_TYPE_MISSION)


def request_mission_list(master):
    mission_manager.request_mission_list(master, mission_type=MAV_MISSION_TYPE_MISSION)


def mav_command_label(command):
    return mission_manager.mav_command_label(command, mission_commands=MISSION_COMMANDS)


def mission_message_to_dict(message):
    return mission_manager.mission_message_to_dict(message, mission_commands=MISSION_COMMANDS)


def mission_items_match_upload(upload_items, read_items):
    return mission_manager.mission_items_match_upload(
        upload_items,
        read_items,
        mission_commands=MISSION_COMMANDS,
    )


def read_mission_items_from_vehicle(master, command_id=None, on_message=None, label="Reading flight mission"):
    return mission_manager.read_mission_items_from_vehicle(
        master,
        command_id=command_id,
        on_message=on_message,
        label=label,
        mission_commands=MISSION_COMMANDS,
        mission_type=MAV_MISSION_TYPE_MISSION,
    )


def verify_uploaded_mission(master, upload_items, on_message=None):
    return mission_manager.verify_uploaded_mission(
        master,
        upload_items,
        on_message=on_message,
        mission_commands=MISSION_COMMANDS,
        mission_type=MAV_MISSION_TYPE_MISSION,
    )


def read_flight_mission(master, command_id=None, on_message=None):
    mission_manager.read_flight_mission(
        master,
        command_id=command_id,
        on_message=on_message,
        mission_commands=MISSION_COMMANDS,
        mission_type=MAV_MISSION_TYPE_MISSION,
    )


def send_px4_actuator_test(master, channel, pwm, timeout_s=2.0, output_function=None):
    return actuator_test_manager.send_px4_actuator_test(
        master,
        channel,
        pwm,
        timeout_s=timeout_s,
        output_function=output_function,
        ack_history=COMMAND_ACK_HISTORY,
        result_names=MAV_RESULT_NAMES,
    )


def mavlink_servo_test_function(channel, output_function=None):
    return actuator_test_manager.mavlink_servo_test_function(channel, output_function)


def mavlink_motor_test_function(motor, output_function=None):
    return actuator_test_manager.mavlink_motor_test_function(motor, output_function)


def send_px4_motor_actuator_test(master, motor, throttle_percent, timeout_s=2.0, output_function=None):
    return actuator_test_manager.send_px4_motor_actuator_test(
        master,
        motor,
        throttle_percent,
        timeout_s=timeout_s,
        output_function=output_function,
        ack_history=COMMAND_ACK_HISTORY,
        result_names=MAV_RESULT_NAMES,
    )


def send_legacy_servo(master, channel, pwm):
    return actuator_test_manager.send_legacy_servo(
        master,
        channel,
        pwm,
        ack_history=COMMAND_ACK_HISTORY,
        result_names=MAV_RESULT_NAMES,
    )


def send_actuator_test(master, output, command_id=None):
    actuator_test_manager.send_actuator_test(
        master,
        output,
        command_id=command_id,
        ack_history=COMMAND_ACK_HISTORY,
        result_names=MAV_RESULT_NAMES,
    )


def send_servo_test(master, channel, pwm=1500, command_id=None, output_function=None):
    actuator_test_manager.send_servo_test(
        master,
        channel,
        pwm=pwm,
        command_id=command_id,
        output_function=output_function,
        ack_history=COMMAND_ACK_HISTORY,
        result_names=MAV_RESULT_NAMES,
    )


def wait_for_param_value(master, name, expected=None, timeout=2.0):
    return parameter_manager.wait_for_param_value(
        master,
        name,
        expected=expected,
        timeout=timeout,
        decoder=decode_param_value,
    )


def request_parameter_values(master, names=None, command_id=None, on_message=None):
    parameter_manager.request_parameter_values(
        master,
        names,
        command_id=command_id,
        on_message=on_message,
        default_parameter_requests=DEFAULT_PARAMETER_REQUESTS,
        decoder=decode_param_value,
    )


def send_parameter(master, name, value, command_id=None):
    parameter_manager.send_parameter(master, name, value, command_id=command_id)
    print(f"Parameter write requested: {str(name or '').strip().upper()}={float(value)}")


def send_servo_outputs(master, outputs, command_id=None):
    actuator_test_manager.send_servo_outputs(
        master,
        outputs,
        command_id=command_id,
        ack_history=COMMAND_ACK_HISTORY,
        result_names=MAV_RESULT_NAMES,
    )


def send_motor_test(master, motor, throttle_percent, duration, command_id=None, output_function=None, output=None):
    actuator_test_manager.send_motor_test(
        master,
        motor,
        throttle_percent,
        duration,
        command_id=command_id,
        output_function=output_function,
        output=output,
        ack_history=COMMAND_ACK_HISTORY,
        result_names=MAV_RESULT_NAMES,
    )


def px4_custom_mode(main_mode, sub_mode=0):
    return (int(main_mode) << 16) | (int(sub_mode) << 24)


def send_flight_mode(master, mode_key, command_id=None, on_message=None):
    command_sender.send_flight_mode(
        master,
        mode_key,
        command_id=command_id,
        on_message=on_message,
        flight_mode_commands=PX4_FLIGHT_MODE_COMMANDS,
        mode_encoder=px4_custom_mode,
        mode_decoder=px4_mode,
        ack_history=COMMAND_ACK_HISTORY,
        result_names=MAV_RESULT_NAMES,
    )
    preset = PX4_FLIGHT_MODE_COMMANDS.get(str(mode_key or "").strip().lower())
    if preset:
        print(f"Flight mode command sent: {preset['px4']}")


def send_arm_disarm(master, arm, command_id=None, on_message=None):
    command_sender.send_arm_disarm(
        master,
        arm,
        command_id=command_id,
        on_message=on_message,
        ack_history=COMMAND_ACK_HISTORY,
        result_names=MAV_RESULT_NAMES,
    )
    print(f"{'arm' if bool(arm) else 'disarm'} command sent")


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
    return connection_manager.detect_serial_port()


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
    print(f"Connecting PX6C/Pixhawk 6C: {connection} @ {baud}")
    master, heartbeat = connection_manager.connect(
        connection,
        baud,
        source_system=source_system,
        source_component=source_component or mavutil.mavlink.MAV_COMP_ID_MISSIONPLANNER,
    )
    if heartbeat.autopilot != mavutil.mavlink.MAV_AUTOPILOT_PX4:
        print("Warning: MAVLink heartbeat received, but autopilot is not PX4; continuing in compatible mode.")
    print(
        f"PX4 connected: system={master.target_system}, "
        f"component={master.target_component}"
    )
    request_message_intervals(master)
    request_output_function_params(master)
    return master, heartbeat


def decode_param_value(message):
    return parameter_manager.decode_param_value(message)


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
    publisher = UiTelemetryPublisher(args.ui, hz=UI_PUBLISH_HZ)
    publisher.start()

    while True:
        receiver = None
        telemetry_manager = None
        message_bus = None
        try:
            master, heartbeat = connect(
                connection,
                args.baud,
                source_system=args.source_system,
                source_component=args.source_component,
            )
            monitor = CommunicationMonitor()
            message_bus = MessageBus(monitor=monitor)
            message_bus.attach_master(master)
            state_cache = TelemetryStateCache(build_ui_payload)
            command_offset = 0
            last_command_scan = 0.0
            update_state(state, heartbeat)
            telemetry_manager = TelemetryStateManager(
                state=state,
                message_bus=message_bus,
                update_state=update_state,
                publisher=publisher,
                full_publish_message_types=FULL_PUBLISH_MESSAGE_TYPES,
                heartbeat_filter=is_vehicle_heartbeat,
                monitor=monitor,
                state_cache=state_cache,
                publish_hz=UI_PUBLISH_HZ,
            )
            telemetry_manager.start()
            message_bus.publish(heartbeat, received_mono=time.monotonic())
            receiver = MavlinkReceiver(
                master,
                message_bus,
                heartbeat_filter=is_vehicle_heartbeat,
                timeout_s=0.02,
            )
            receiver.last_vehicle_heartbeat_mono = time.monotonic()
            receiver.start()
            with telemetry_manager.state_lock:
                send_gcs_heartbeat(master, state, force=True)
            telemetry_manager.publish_once(full=True)
            print("PX4 连接状态已发送到 UI，等待 GPS 定位...")

            while True:
                now = time.monotonic()
                if now - last_command_scan >= COMMAND_SCAN_INTERVAL_S:
                    command_offset = process_connector_commands(
                        master,
                        command_offset,
                        on_message=None,
                    )
                    last_command_scan = time.monotonic()
                if COMMAND_ACK_HISTORY:
                    with telemetry_manager.state_lock:
                        state["commandAcks"] = COMMAND_ACK_HISTORY[-100:]
                with telemetry_manager.state_lock:
                    heartbeat_sent = send_gcs_heartbeat(master, state)
                    state["communicationMonitor"] = message_bus.snapshot()
                if heartbeat_sent:
                    telemetry_manager.publish_once()
                if receiver.error is not None:
                    raise receiver.error
                heartbeat_age = telemetry_manager.last_vehicle_heartbeat_age_s()
                if heartbeat_age is not None and heartbeat_age > 5:
                    raise ConnectionError("PX4 heartbeat timeout")
                time.sleep(0.01)
                continue
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
            if receiver is not None:
                receiver.stop()
            if telemetry_manager is not None:
                telemetry_manager.stop()
            if message_bus is not None:
                message_bus.close()
            publisher.stop()
            print("PX6C 连接已停止。")
            return
        except Exception as error:
            if receiver is not None:
                receiver.stop()
            if telemetry_manager is not None:
                telemetry_manager.stop()
            if message_bus is not None:
                message_bus.close()
            state["connected"] = False
            publisher.publish(state, full=True)
            print(f"PX6C 连接中断：{error}，2 秒后重连...")
            time.sleep(2)


if __name__ == "__main__":
    main()

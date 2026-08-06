import json
import hmac
import mimetypes
import os
import re
import cgi
import subprocess
import socket
import sys
import threading
import time
import urllib.parse
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app.safety_manager import SafetyManager
from app.safety_manager import PROP_CONFIRMATION_TEXTS, confirmation_matches
from core.runtime_paths import executable_dir, packaged_entry_command, resource_root, runtime_data_root
from services.rc_calibration_engine import (
    RC_PARAMETER_NAMES,
    RcCalibrationSession,
    build_preview,
    channel_snapshot,
    detect_moving_channel,
    summarize_samples,
)
from services.rc_link_analyzer import analyze_rc_link
from services.aircraft_calibration_service import (
    build_overview as build_aircraft_calibration_overview,
    cancel_session as cancel_aircraft_calibration_session,
    get_session as get_aircraft_calibration_session,
    session_messages as aircraft_calibration_messages,
    start_session as start_aircraft_calibration_session,
)
from core.vehicle_state import VehicleState
from services.session_logger import SessionLogger
from services.version_info import version_payload

try:
    from serial.tools import list_ports
except ImportError:
    list_ports = None


ROOT = resource_root()
DATA_ROOT = runtime_data_root()
TEXT_CONTENT_TYPES = {
    "application/javascript",
    "application/json",
    "image/svg+xml",
    "text/css",
    "text/html",
    "text/javascript",
    "text/plain",
    "text/xml",
}


def load_dotenv(path):
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


for env_file in (
    DATA_ROOT / ".env",
    executable_dir() / ".env",
    ROOT / ".env",
):
    load_dotenv(env_file)

def load_json_config(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def config_string(config, key, env_key, default):
    value = os.environ.get(env_key)
    if value is None:
        value = config.get(key, default)
    value = str(value or "").strip()
    return value or default


def config_int(config, key, env_key, default):
    value = os.environ.get(env_key)
    if value is None:
        value = config.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


DEFAULT_CONFIG = load_json_config(ROOT / "config" / "default.json")
DEFAULT_MAVLINK_HOST = config_string(DEFAULT_CONFIG, "mavlink_host", "MAVLINK_HOST", "127.0.0.1")
DEFAULT_MAVLINK_PORT = config_int(DEFAULT_CONFIG, "mavlink_port", "MAVLINK_PORT", 14550)
DEFAULT_MAVLINK_LISTEN_ADDRESS = config_string(
    DEFAULT_CONFIG,
    "mavlink_listen_address",
    "MAVLINK_LISTEN_ADDRESS",
    "0.0.0.0",
)
DEFAULT_MAVLINK_LISTEN_PORT = config_int(
    DEFAULT_CONFIG,
    "mavlink_listen_port",
    "MAVLINK_LISTEN_PORT",
    DEFAULT_MAVLINK_PORT,
)
DEFAULT_MAVLINK_TARGET_PORT = config_int(
    DEFAULT_CONFIG,
    "mavlink_target_port",
    "MAVLINK_TARGET_PORT",
    DEFAULT_MAVLINK_PORT,
)


PORT = int(os.environ.get("PORT", "8080"))
STATE_LOCK = threading.Lock()
STATE = VehicleState()
LAST_PACKET_MONOTONIC = 0.0
LAST_PACKET_WALL_MS = 0
TELEMETRY_SEQUENCE = 0
PACKET_TIMES = []
MESSAGE_STATS = {
    "counts": {},
    "lastSeen": {},
    "rates": {},
    "recent": [],
}
SSE_CLIENTS = set()
SSE_LOCK = threading.Lock()
LOGGER = SessionLogger(DATA_ROOT / "logs")
SAFETY = SafetyManager("demo")
MISSION = []
CONNECTION_PROCESSES = []
CONNECTION_LOG_HANDLE = None
COMMAND_DIR = DATA_ROOT / "commands"
COMMAND_QUEUE = COMMAND_DIR / "px6c_commands.jsonl"
COMMAND_STATUS_FILE = COMMAND_DIR / "px6c_command_status.json"
CONNECTION_PID_FILE = DATA_ROOT / "connection.pids"
TELEMETRY_HISTORY = []
ULG_UPLOAD_DIR = DATA_ROOT / "uploads" / "ulg"
ULG_DOWNLOAD_DIR = DATA_ROOT / "downloads" / "ulg"
REPORT_DIR = DATA_ROOT / "reports"
CONNECTION_CONFIG = {
    "type": "demo",
    "serialPort": "",
    "baud": 57600,
    "listenAddress": DEFAULT_MAVLINK_LISTEN_ADDRESS,
    "listenPort": DEFAULT_MAVLINK_LISTEN_PORT,
    "targetIp": DEFAULT_MAVLINK_HOST,
    "targetPort": DEFAULT_MAVLINK_TARGET_PORT,
    "effectiveListenAddress": "",
    "effectiveConnection": "",
}
RC_CALIBRATION_SESSIONS = {}
AIRCRAFT_CALIBRATION_SESSIONS = {}
RC_LINK_HISTORY = []
PX4_FLIGHT_MODES = {
    "manual": {"label": "鎵嬪姩妯″紡", "px4": "MANUAL", "mainMode": 1, "subMode": 0, "requiresMission": False},
    "position": {"label": "浣嶇疆妯″紡", "px4": "POSCTL", "mainMode": 3, "subMode": 0, "requiresMission": False},
    "altitude": {"label": "瀹氶珮妯″紡", "px4": "ALTCTL", "mainMode": 2, "subMode": 0, "requiresMission": False},
    "land": {"label": "闄嶈惤妯″紡", "px4": "AUTO LAND", "mainMode": 4, "subMode": 6, "requiresMission": False},
    "rtl": {"label": "返航模式", "px4": "AUTO RTL", "mainMode": 4, "subMode": 5, "requiresMission": False},
    "mission": {"label": "浠诲姟妯″紡", "px4": "AUTO MISSION", "mainMode": 4, "subMode": 4, "requiresMission": True},
}
CONNECTION_LOST_RECORDED = False
SESSION_TOKEN = uuid.uuid4().hex
AUTH_USERS = {
    os.environ.get("GCS_ADMIN_USER", "admin"): {
        "password": os.environ.get("GCS_ADMIN_PASSWORD", "CHANGE_ME_ADMIN_PASSWORD"),
        "role": "admin",
        "label": "管理员",
    },
    os.environ.get("GCS_OPERATOR_USER", "operator"): {
        "password": os.environ.get("GCS_OPERATOR_PASSWORD", "CHANGE_ME_OPERATOR_PASSWORD"),
        "role": "operator",
        "label": "操作员",
    },
}
AUTH_SESSIONS = {}
ROLE_LEVELS = {"guest": 0, "operator": 1, "admin": 2}
DANGEROUS_POST_PATHS = {
    "/api/connection/start",
    "/api/connection/stop",
    "/api/mode",
    "/api/flight-mode",
    "/api/arm",
    "/api/calibration/start",
    "/api/aircraft-calibration/start",
    "/api/aircraft-calibration/cancel",
    "/api/aircraft-calibration/force-refresh",
    "/api/servo/test",
    "/api/servo/repair-mapping",
    "/api/motor/test",
    "/api/flight-logs/list",
    "/api/flight-logs/download",
    "/api/parameters/request",
    "/api/parameters/apply",
    "/api/rc/calibration/start",
    "/api/rc/calibration/step",
    "/api/rc/calibration/preview",
    "/api/rc/calibration/apply",
    "/api/rc/calibration/restore",
    "/api/ai/pid/apply",
    "/api/ai/pid/rollback",
    "/api/mission",
    "/api/mission/check",
    "/api/safety/check",
    "/api/mission/upload",
    "/api/mission/clear",
    "/api/mission/read",
}
POST_ROLE_REQUIREMENTS = {}
PARAMETER_LIMITS = {
    "MC_ROLLRATE_P": (0.01, 0.8),
    "MC_ROLLRATE_I": (0.0, 1.0),
    "MC_ROLLRATE_D": (0.0, 0.05),
    "MC_PITCHRATE_P": (0.01, 0.8),
    "MC_PITCHRATE_I": (0.0, 1.0),
    "MC_PITCHRATE_D": (0.0, 0.05),
    "MC_YAWRATE_P": (0.01, 1.0),
    "MC_YAWRATE_I": (0.0, 1.0),
    "MC_YAWRATE_D": (0.0, 0.05),
    "MPC_Z_VEL_P_ACC": (0.5, 10.0),
    "MPC_Z_VEL_I_ACC": (0.0, 6.0),
    "MPC_Z_VEL_D_ACC": (0.0, 2.0),
    "FW_RR_P": (0.0, 1.0),
    "FW_RR_I": (0.0, 1.0),
    "FW_RR_D": (0.0, 0.1),
    "FW_PR_P": (0.0, 1.0),
    "FW_PR_I": (0.0, 1.0),
    "FW_PR_D": (0.0, 0.1),
    "FW_YR_P": (0.0, 1.0),
    "FW_YR_I": (0.0, 1.0),
    "FW_YR_D": (0.0, 0.1),
}


def local_ipv4_addresses():
    addresses = []
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = item[4][0]
            if ip and ip not in addresses:
                addresses.append(ip)
    except OSError:
        pass
    try:
        output = subprocess.check_output(["ipconfig"], text=True, encoding="utf-8", errors="ignore")
        for match in re.findall(r"IPv4[^:\r\n]*:\s*([0-9]+(?:\.[0-9]+){3})", output):
            if match not in addresses:
                addresses.append(match)
    except Exception:
        pass
    return addresses


def validate_udp_listen_address(address):
    address = str(address or "0.0.0.0").strip()
    if address in {"0.0.0.0", "127.0.0.1", "localhost"}:
        return address
    local_ips = local_ipv4_addresses()
    if address not in local_ips:
        suggestion = "0.0.0.0"
        if local_ips:
            suggestion += " or " + " / ".join(local_ips)
        raise ValueError(f"UDP listen address {address} is not a local address. Use {suggestion}.")
    return address

CALIBRATION_TYPES = {
    "gyro": "陀螺仪校准",
    "accelerometer": "加速度计校准",
    "magnetometer": "磁罗盘校准",
    "level": "水平姿态校准",
    "airspeed": "空速计校准",
    "radio": "遥控器校准",
    "esc": "电调校准",
}

PID_BASELINES = {
    "roll": {
        "MC_ROLLRATE_P": 0.15,
        "MC_ROLLRATE_I": 0.20,
        "MC_ROLLRATE_D": 0.003,
    },
    "pitch": {
        "MC_PITCHRATE_P": 0.15,
        "MC_PITCHRATE_I": 0.20,
        "MC_PITCHRATE_D": 0.003,
    },
    "yaw": {
        "MC_YAWRATE_P": 0.20,
        "MC_YAWRATE_I": 0.10,
        "MC_YAWRATE_D": 0.0,
    },
    "altitude": {
        "MPC_Z_VEL_P_ACC": 4.0,
        "MPC_Z_VEL_I_ACC": 2.0,
        "MPC_Z_VEL_D_ACC": 0.0,
    },
}

PID_AXIS_LABELS = {
    "roll": "妯粴",
    "pitch": "淇话",
    "yaw": "鑸悜",
    "altitude": "楂樺害",
}


def json_bytes(data):
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def public_user(session):
    if not session:
        return {"authenticated": False, "role": "guest", "label": "未登录"}
    return {
        "authenticated": True,
        "username": session["username"],
        "role": session["role"],
        "label": session["label"],
        "expiresAt": session["expiresAt"],
    }


def session_from_cookie(cookie_header):
    cookies = {}
    for part in str(cookie_header or "").split(";"):
        if "=" not in part:
            continue
        key, value = part.strip().split("=", 1)
        cookies[key] = value
    session_id = cookies.get("gcs_session")
    session = AUTH_SESSIONS.get(session_id)
    if not session:
        return None
    if session.get("expiresAt", 0) < time.time():
        AUTH_SESSIONS.pop(session_id, None)
        return None
    return session


def login_user(username, password):
    username = str(username or "").strip()
    record = AUTH_USERS.get(username)
    if not record or not hmac.compare_digest(str(password or ""), str(record["password"])):
        return None, None
    session_id = uuid.uuid4().hex
    session = {
        "username": username,
        "role": record["role"],
        "label": record["label"],
        "createdAt": int(time.time()),
        "expiresAt": int(time.time() + 8 * 3600),
    }
    AUTH_SESSIONS[session_id] = session
    return session_id, session


def role_allowed(current_role, required_role):
    return ROLE_LEVELS.get(current_role or "guest", 0) >= ROLE_LEVELS.get(required_role or "guest", 0)


def connection_diagnostics(age, process_running, rate, heartbeat):
    kind = CONNECTION_CONFIG.get("type", "demo")
    link_label = {
        "serial": "USB serial",
        "udp": "UDP active link",
        "udp_listen": "UDP listen",
        "demo": "Demo link",
    }.get(kind, "MAVLink")
    effective = CONNECTION_CONFIG.get("effectiveConnection") or ""
    if kind == "serial":
        endpoint = CONNECTION_CONFIG.get("serialPort") or "no serial port selected"
    elif kind == "udp":
        endpoint = f"{CONNECTION_CONFIG.get('targetIp') or '--'}:{CONNECTION_CONFIG.get('targetPort') or CONNECTION_CONFIG.get('listenPort') or '--'}"
    elif kind == "udp_listen":
        endpoint = f"{CONNECTION_CONFIG.get('effectiveListenAddress') or CONNECTION_CONFIG.get('listenAddress') or '0.0.0.0'}:{CONNECTION_CONFIG.get('listenPort') or '--'}"
    else:
        endpoint = effective or "local demo"

    if not process_running:
        phase = "stopped"
        level = "offline"
        summary = "Connection process is not running"
        advice = "Start the connection from Connection Settings."
    elif age is None:
        phase = "waiting_packets"
        level = "warning"
        summary = f"{link_label} started, waiting for MAVLink packets"
        advice = "Check COM/baud/power for serial, or IP/port/firewall/network segment for UDP."
    elif not heartbeat:
        phase = "packets_no_heartbeat" if age < 3 else "heartbeat_timeout"
        level = "warning" if age < 5 else "critical"
        summary = f"Packets received but no fresh HEARTBEAT. Last packet {round(age, 1)} s ago"
        advice = "Check target link, MAVLink direction, and target system/component."
    elif rate <= 0:
        phase = "low_rate"
        level = "warning"
        summary = "Heartbeat is online, but current message rate is 0 Hz"
        advice = "Check telemetry stream rate and message interval requests."
    else:
        phase = "online"
        level = "ok"
        summary = f"Link online, receiving {rate} Hz"
        advice = "Connection status is normal."
    return {
        "phase": phase,
        "level": level,
        "kind": kind,
        "label": link_label,
        "endpoint": endpoint,
        "effective": effective,
        "summary": summary,
        "advice": advice,
    }


def connection_status():
    age = time.monotonic() - LAST_PACKET_MONOTONIC if LAST_PACKET_MONOTONIC else None
    process_running = any(process.poll() is None for process in CONNECTION_PROCESSES) or any(
        is_pid_running(pid) for pid in read_connection_pid_file()
    )
    link_name = {
        "serial": "USB serial",
        "udp": "UDP link",
        "udp_listen": "UDP listen",
        "demo": "Demo link",
    }.get(CONNECTION_CONFIG.get("type"), "MAVLink")
    if age is None:
        status = "Disconnected"
    elif not process_running and age >= 3:
        status = "Disconnected"
    elif age >= 5:
        status = f"{link_name} disconnected"
    elif age >= 3:
        status = f"{link_name} may be stale"
    else:
        status = "Vehicle heartbeat received" if STATE.connected else "Receiving data"
    now = time.monotonic()
    rate = len([stamp for stamp in PACKET_TIMES if now - stamp <= 1.0])
    message_stats = mavlink_message_stats()
    heartbeat = bool(STATE.connected and age is not None and age < 3)
    diagnostics = connection_diagnostics(age, process_running, rate, heartbeat)
    return {
        **CONNECTION_CONFIG,
        "status": status,
        "heartbeat": heartbeat,
        "rateHz": rate,
        "messageStats": message_stats,
        "lastDataTime": STATE.timestamp if LAST_PACKET_MONOTONIC else None,
        "ageSeconds": round(age, 1) if age is not None else None,
        "mode": SAFETY.mode,
        "processRunning": process_running,
        "diagnostics": diagnostics,
        "phase": diagnostics["phase"],
        "faultLevel": diagnostics["level"],
    }


def mavlink_message_stats():
    now = time.monotonic()
    counts = dict(MESSAGE_STATS.get("counts") or {})
    last_seen = dict(MESSAGE_STATS.get("lastSeen") or {})
    ages = {}
    for name, stamp in last_seen.items():
        try:
            ages[name] = round(max(0, now - float(stamp)), 2)
        except (TypeError, ValueError):
            ages[name] = None
    return {
        "counts": counts,
        "rates": dict(MESSAGE_STATS.get("rates") or {}),
        "ages": ages,
        "recent": list(MESSAGE_STATS.get("recent") or [])[:24],
        "required": ["HEARTBEAT", "ATTITUDE", "GLOBAL_POSITION_INT", "GPS_RAW_INT", "VFR_HUD", "SYS_STATUS", "RC_CHANNELS"],
    }


def update_message_stats(payload):
    now = time.monotonic()
    incoming = payload.get("messageStats") if isinstance(payload, dict) else None
    if isinstance(incoming, dict):
        counts = incoming.get("counts")
        rates = incoming.get("rates")
        last_seen = incoming.get("lastSeen")
        recent = incoming.get("recent")
        if isinstance(counts, dict):
            MESSAGE_STATS["counts"] = {str(key): int(value or 0) for key, value in counts.items()}
        if isinstance(rates, dict):
            MESSAGE_STATS["rates"] = {str(key): float(value or 0) for key, value in rates.items()}
        if isinstance(last_seen, dict):
            converted = {}
            for key, value in last_seen.items():
                try:
                    converted[str(key)] = now - max(0.0, float(value))
                except (TypeError, ValueError):
                    converted[str(key)] = now
            MESSAGE_STATS["lastSeen"] = converted
        if isinstance(recent, list):
            MESSAGE_STATS["recent"] = [str(item)[:80] for item in recent[:24]]

    message_type = payload.get("messageType") or payload.get("lastMessage")
    if message_type:
        name = str(message_type).strip().upper()
        MESSAGE_STATS.setdefault("counts", {})[name] = MESSAGE_STATS.setdefault("counts", {}).get(name, 0) + 1
        MESSAGE_STATS.setdefault("lastSeen", {})[name] = now
        recent = MESSAGE_STATS.setdefault("recent", [])
        if not recent or recent[0] != name:
            recent.insert(0, name)
            del recent[24:]


def record_rc_link_history(state_dict):
    rc_time = state_dict.get("rcUpdateTimeMs")
    channels = list(state_dict.get("rcRawChannels") or state_dict.get("rcChannels") or [])
    if not rc_time or not channels:
        return
    try:
        time_ms = int(float(rc_time))
    except (TypeError, ValueError):
        return
    if RC_LINK_HISTORY and RC_LINK_HISTORY[-1].get("timeMs") == time_ms:
        return
    RC_LINK_HISTORY.append({
        "timeMs": time_ms,
        "channels": channels[:18],
        "mapped": state_dict.get("rcMapped") or {},
    })
    cutoff = int(time.time() * 1000) - 8000
    del RC_LINK_HISTORY[:-160]
    while RC_LINK_HISTORY and int(RC_LINK_HISTORY[0].get("timeMs") or 0) < cutoff:
        RC_LINK_HISTORY.pop(0)


def connection_self_check():
    status = connection_status()
    telemetry = telemetry_payload()
    stats = status.get("messageStats") or {}
    ages = stats.get("ages") or {}
    counts = stats.get("counts") or {}

    def check(check_id, label, passed, detail, severity="info"):
        return {
            "id": check_id,
            "label": label,
            "passed": bool(passed),
            "detail": detail,
            "severity": severity if not passed else "ok",
        }

    heartbeat_ok = bool(status.get("heartbeat"))
    attitude_ok = ages.get("ATTITUDE") is not None and ages.get("ATTITUDE") < 2.5
    gps_ok = ages.get("GPS_RAW_INT") is not None and ages.get("GPS_RAW_INT") < 3.5
    pos_ok = ages.get("GLOBAL_POSITION_INT") is not None and ages.get("GLOBAL_POSITION_INT") < 3.5
    hud_ok = ages.get("VFR_HUD") is not None and ages.get("VFR_HUD") < 3.5
    sys_ok = ages.get("SYS_STATUS") is not None and ages.get("SYS_STATUS") < 3.5
    rc_ok = ages.get("RC_CHANNELS") is not None and ages.get("RC_CHANNELS") < 3.5
    fix_type = telemetry.get("fixType")
    sats = telemetry.get("satellites")
    battery = telemetry.get("battery")
    rc_channels = telemetry.get("rcChannels") or []

    checks = [
        check("backend", "UI backend", True, "Python backend responded"),
        check("process", "Connection process", status.get("processRunning"), "MAVLink bridge is running" if status.get("processRunning") else "Connection process is not running", "warning"),
        check("heartbeat", "MAVLink heartbeat", heartbeat_ok, f"{status.get('status')} / {status.get('rateHz', 0)} Hz", "critical"),
        check("attitude", "ATTITUDE message", attitude_ok, f"count={counts.get('ATTITUDE', 0)} age={ages.get('ATTITUDE', '--')}s", "warning"),
        check("position", "GLOBAL_POSITION_INT", pos_ok, f"count={counts.get('GLOBAL_POSITION_INT', 0)} age={ages.get('GLOBAL_POSITION_INT', '--')}s", "warning"),
        check("gps", "GPS_RAW_INT", gps_ok, f"Fix {fix_type if fix_type is not None else '--'} / {sats if sats is not None else '--'} sats", "warning"),
        check("hud", "VFR_HUD", hud_ok, f"count={counts.get('VFR_HUD', 0)} age={ages.get('VFR_HUD', '--')}s", "info"),
        check("power", "SYS_STATUS power", sys_ok, f"Battery {battery if battery is not None else '--'}%", "warning"),
    ]
    checks.append(check("rc", "RC_CHANNELS", rc_ok, f"{len(rc_channels)} channels / RSSI {telemetry.get('rcSignal', '--')}%", "warning"))
    failed = [item for item in checks if not item["passed"]]
    return {
        "ok": not failed,
        "summary": "Connection self-check passed" if not failed else f"{len(failed)} checks need attention",
        "checks": checks,
        "connection": status,
        "telemetry": telemetry,
        "messageStats": stats,
        "updatedAt": int(time.time() * 1000),
    }


def telemetry_payload():
    with STATE_LOCK:
        if not LAST_PACKET_MONOTONIC:
            return {}
        age = time.monotonic() - LAST_PACKET_MONOTONIC
        data = STATE.to_dict()
        if age >= 5 or not any(process.poll() is None for process in CONNECTION_PROCESSES):
            data["connected"] = False
            data["stale"] = True
        data["ageSeconds"] = round(age, 1)
        data["receivedAt"] = LAST_PACKET_WALL_MS
        data["packetSeq"] = TELEMETRY_SEQUENCE
        data["messageStats"] = mavlink_message_stats()
        data["rcLink"] = analyze_rc_link(data, data["messageStats"], list(RC_LINK_HISTORY))
        return compact_live_telemetry(data)


LIVE_TELEMETRY_OMIT_KEYS = {
    "parameters",
    "mission_items",
    "mission_current",
    "mission_reached",
    "vehicle_id",
    "flight_mode",
    "roll_deg",
    "pitch_deg",
    "yaw_deg",
    "attitude_time_ms",
    "roll_rate_degps",
    "pitch_rate_degps",
    "yaw_rate_degps",
    "altitude_m",
    "relative_altitude_m",
    "vertical_speed_mps",
    "ground_speed_mps",
    "airspeed_mps",
    "latitude",
    "longitude",
    "heading_deg",
    "gps_fix_type",
    "satellites_visible",
    "hdop",
    "battery_voltage_v",
    "battery_current_a",
    "battery_remaining_percent",
    "rc_signal_raw",
    "rc_update_time_ms",
    "rc_source",
    "rc_channels",
    "rc_throttle_pwm",
    "rc_throttle_percent",
    "rc_throttle_source",
    "manual_control",
    "manual_control_time_ms",
    "target_system",
    "target_component",
    "target_identified",
    "vehicle_autopilot",
    "vehicle_base_mode",
    "vehicle_custom_mode",
    "vehicle_mavlink_version",
    "vehicle_heartbeat_at",
    "gcs_heartbeat",
    "rc_raw_channels",
    "rc_mapped",
    "rc_map",
    "rc_map_available",
    "rc_issues",
    "telemetry_signal",
    "command_acks",
    "system_status",
    "landed_state",
    "last_message",
}


def compact_live_telemetry(data):
    compact = dict(data)
    for key in LIVE_TELEMETRY_OMIT_KEYS:
        compact.pop(key, None)
    return compact


def broadcast(state):
    packet = f"data: {json.dumps(state, ensure_ascii=False)}\n\n".encode("utf-8")
    dead = []
    with SSE_LOCK:
        clients = list(SSE_CLIENTS)
    for client in clients:
        try:
            try:
                client.connection.settimeout(0.05)
            except Exception:
                pass
            client.wfile.write(packet)
            client.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            dead.append(client)
    if dead:
        with SSE_LOCK:
            for client in dead:
                SSE_CLIENTS.discard(client)


def update_state(payload):
    global STATE, LAST_PACKET_MONOTONIC, LAST_PACKET_WALL_MS, TELEMETRY_SEQUENCE, CONNECTION_LOST_RECORDED
    with STATE_LOCK:
        previous = STATE
        update_message_stats(payload)
        STATE = VehicleState.from_payload(payload, previous)
        record_rc_link_history(STATE.to_dict())
        now = time.monotonic()
        LAST_PACKET_MONOTONIC = now
        LAST_PACKET_WALL_MS = int(time.time() * 1000)
        TELEMETRY_SEQUENCE += 1
        PACKET_TIMES.append(now)
        del PACKET_TIMES[:-200]
        if previous.armed != STATE.armed:
            LOGGER.log_event(
                "armed_changed",
                "Vehicle armed" if STATE.armed else "Vehicle disarmed",
                "PX4",
            )
        if previous.flight_mode != STATE.flight_mode:
            LOGGER.log_event(
                "flight_mode_changed",
                f"{previous.flight_mode} -> {STATE.flight_mode}",
                "PX4",
            )
        new_warnings = [item for item in STATE.warnings if item not in previous.warnings]
        for warning in new_warnings:
            LOGGER.log_warning("warning", warning, "PX4 STATUSTEXT")
            LOGGER.log_event("px4_warning", warning, "PX4 STATUSTEXT")
        if CONNECTION_LOST_RECORDED:
            LOGGER.log_event("connection_restored", "MAVLink connection restored", "system")
            CONNECTION_LOST_RECORDED = False
        LOGGER.append_telemetry(STATE)
        state_dict = STATE.to_dict()
        state_dict["receivedAt"] = LAST_PACKET_WALL_MS
        state_dict["packetSeq"] = TELEMETRY_SEQUENCE
        state_dict["ageSeconds"] = 0
        state_dict["messageStats"] = mavlink_message_stats()
        state_dict["rcLink"] = analyze_rc_link(state_dict, state_dict["messageStats"], list(RC_LINK_HISTORY))
        TELEMETRY_HISTORY.append({
            "time": now,
            "roll": STATE.roll_deg,
            "pitch": STATE.pitch_deg,
            "yaw": STATE.yaw_deg,
            "altitude": STATE.relative_altitude_m,
            "speed": STATE.ground_speed_mps,
            "airspeed": STATE.airspeed_mps,
        })
        del TELEMETRY_HISTORY[:-500]
    response_dict = compact_live_telemetry(state_dict)
    broadcast(response_dict)
    return response_dict


def reset_telemetry_state():
    global STATE, LAST_PACKET_MONOTONIC, LAST_PACKET_WALL_MS, TELEMETRY_SEQUENCE, CONNECTION_LOST_RECORDED, MESSAGE_STATS
    with STATE_LOCK:
        STATE = VehicleState()
        LAST_PACKET_MONOTONIC = 0.0
        LAST_PACKET_WALL_MS = 0
        TELEMETRY_SEQUENCE = 0
        PACKET_TIMES.clear()
        MESSAGE_STATS = {"counts": {}, "lastSeen": {}, "rates": {}, "recent": []}
        CONNECTION_LOST_RECORDED = False
    broadcast({"connected": False, "vehicleId": "PX6C-01", "stale": True})


def finite_number(value):
    return isinstance(value, (int, float)) and value == value and value not in (float("inf"), float("-inf"))


def series_stats(values):
    values = [float(value) for value in values if finite_number(value)]
    if not values:
        return {"count": 0, "span": 0.0, "std": 0.0, "rate": 0.0}
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    rate = abs(values[-1] - values[0]) / max(1, len(values) - 1)
    return {
        "count": len(values),
        "span": max(values) - min(values),
        "std": variance ** 0.5,
        "rate": rate,
    }


def current_parameter_value(name, fallback):
    with STATE_LOCK:
        value = STATE.parameters.get(name)
    return float(value) if finite_number(value) else float(fallback)


def parameter_bounds(name):
    name = str(name or "").strip().upper()
    if name in PARAMETER_LIMITS:
        return PARAMETER_LIMITS[name]
    if re.fullmatch(r"PWM_(MAIN|AUX)_FUNC[1-9][0-9]?", name):
        return (0, 1000)
    return None


def validate_parameter_change(name, value):
    name = str(name or "").strip().upper()
    bounds = parameter_bounds(name)
    if bounds is None:
        raise ValueError(f"Parameter {name or '--'} is not in the GCS write whitelist")
    lower, upper = bounds
    numeric_value = float(value)
    if not lower <= numeric_value <= upper:
        raise ValueError(f"鍙傛暟 {name}={numeric_value:g} 瓒呭嚭瀹夊叏鑼冨洿 {lower:g}-{upper:g}")
    return name, numeric_value


def clamp_parameter(name, value):
    ranges = {
        "_P": (0.01, 1.5),
        "_I": (0.0, 1.5),
        "_D": (0.0, 0.08),
        "MPC_Z_VEL_P_ACC": (0.5, 10.0),
        "MPC_Z_VEL_I_ACC": (0.0, 6.0),
        "MPC_Z_VEL_D_ACC": (0.0, 2.0),
    }
    lower, upper = 0.0, 10.0
    for suffix, bounds in ranges.items():
        if name.endswith(suffix) or name == suffix:
            lower, upper = bounds
            break
    return round(max(lower, min(upper, value)), 4)


def generate_pid_recommendation(payload):
    axis = payload.get("axis", "roll")
    symptom = payload.get("symptom", "balanced")
    aggressiveness = max(1, min(5, int(payload.get("aggressiveness", 2) or 2)))
    if axis not in PID_BASELINES:
        raise ValueError("Unknown PID tuning axis")

    with STATE_LOCK:
        history = list(TELEMETRY_HISTORY)
        connected = STATE.connected
        armed = STATE.armed

    values = [item.get(axis) for item in history[-240:]]
    stats = series_stats(values)
    scale = 0.04 + aggressiveness * 0.025
    p_scale = i_scale = d_scale = 1.0
    reasons = []

    if symptom == "sluggish":
        p_scale += scale
        reasons.append("Response is sluggish: slightly increase P for stronger tracking.")
    elif symptom == "oscillation":
        p_scale -= scale
        d_scale += scale * 0.7
        reasons.append("Oscillation detected: reduce P and increase D damping.")
    elif symptom == "overshoot":
        p_scale -= scale * 0.5
        d_scale += scale
        reasons.append("Overshoot is visible: reduce P and increase D to reduce overshoot.")
    elif symptom == "drift":
        i_scale += scale
        reasons.append("Steady-state drift: increase I to reduce long-term error.")
    else:
        if stats["std"] > 8 or stats["span"] > 25:
            p_scale -= scale * 0.5
            d_scale += scale * 0.6
            reasons.append("Recent attitude variation is high: prefer more damping.")
        elif stats["std"] < 2 and connected:
            p_scale += scale * 0.35
            reasons.append("Recent attitude is stable: a small response increase is acceptable.")
        else:
            reasons.append("Data is limited or neutral: use conservative tuning.")

    confidence = 0.35
    if connected:
        confidence += 0.2
    if stats["count"] >= 80:
        confidence += 0.25
    if not armed:
        reasons.append("Vehicle is disarmed; treat this as ground-side review, not flight-loop validation.")
    if stats["count"] < 40:
        reasons.append("Sample count is low; collect more telemetry or flight data before applying changes.")
    confidence = round(min(0.9, confidence), 2)

    recommendations = []
    for name, fallback in PID_BASELINES[axis].items():
        current = current_parameter_value(name, fallback)
        factor = 1.0
        if name.endswith("_P") or name.endswith("_P_ACC"):
            factor = p_scale
        elif name.endswith("_I") or name.endswith("_I_ACC"):
            factor = i_scale
        elif name.endswith("_D") or name.endswith("_D_ACC"):
            factor = d_scale
        suggested = clamp_parameter(name, current * factor)
        recommendations.append({
            "name": name,
            "current": round(current, 4),
            "suggested": suggested,
            "deltaPercent": round((suggested - current) / current * 100, 1) if current else 0,
        })

    LOGGER.log_event(
        "ai_pid_recommendation",
        f"{PID_AXIS_LABELS[axis]} {symptom} confidence={confidence}",
        "AI PID",
    )
    return {
        "axis": axis,
        "axisLabel": PID_AXIS_LABELS[axis],
        "symptom": symptom,
        "confidence": confidence,
        "stats": stats,
        "recommendations": recommendations,
        "reasons": reasons,
        "warning": "AI suggestions are not written to the flight controller automatically. Review and test safely before applying.",
    }


def monitor_connection():
    global CONNECTION_LOST_RECORDED
    while True:
        time.sleep(1)
        if not LAST_PACKET_MONOTONIC:
            continue
        age = time.monotonic() - LAST_PACKET_MONOTONIC
        if age >= 5 and not CONNECTION_LOST_RECORDED:
            CONNECTION_LOST_RECORDED = True
            with STATE_LOCK:
                STATE.connected = False
            LOGGER.connection_losses += 1
            LOGGER.log_warning("涓ラ噸", "WiFi 鏁颁紶杩炴帴涓㈠け", "鏁颁紶")
            LOGGER.log_event("connection_lost", "WiFi 鏁颁紶杩炴帴涓㈠け", "system")


def read_connection_pid_file():
    if not CONNECTION_PID_FILE.exists():
        return []
    pids = []
    for line in CONNECTION_PID_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            pid = int(line.strip())
        except ValueError:
            continue
        if pid and pid != os.getpid():
            pids.append(pid)
    return pids


def is_pid_running(pid):
    if not pid or pid == os.getpid():
        return False
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            return str(pid) in result.stdout
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def kill_process_tree(pid):
    if not pid or pid == os.getpid():
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True, timeout=4)
        else:
            os.kill(pid, 15)
    except Exception:
        pass


def write_connection_pid_file():
    pids = [str(process.pid) for process in CONNECTION_PROCESSES if process and process.poll() is None]
    if pids:
        CONNECTION_PID_FILE.write_text("\n".join(pids), encoding="utf-8")
    else:
        try:
            CONNECTION_PID_FILE.unlink()
        except FileNotFoundError:
            pass


def stop_connection():
    global CONNECTION_PROCESSES, CONNECTION_LOG_HANDLE
    known_pids = set(read_connection_pid_file())
    for process in CONNECTION_PROCESSES:
        if process.poll() is None:
            known_pids.discard(process.pid)
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                kill_process_tree(process.pid)
    for pid in known_pids:
        kill_process_tree(pid)
    CONNECTION_PROCESSES = []
    try:
        CONNECTION_PID_FILE.unlink()
    except FileNotFoundError:
        pass
    if CONNECTION_LOG_HANDLE:
        CONNECTION_LOG_HANDLE.close()
        CONNECTION_LOG_HANDLE = None
    reset_telemetry_state()
    LOGGER.log_event("connection_stopped", "鐢ㄦ埛鏂紑杩炴帴", "system")


def enqueue_connector_command(command):
    COMMAND_DIR.mkdir(exist_ok=True)
    packet = {
        "id": uuid.uuid4().hex,
        "createdAt": int(time.time() * 1000),
        **command,
    }
    with COMMAND_QUEUE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(packet, ensure_ascii=False) + "\n")
    try:
        statuses = json.loads(COMMAND_STATUS_FILE.read_text(encoding="utf-8"))
        if not isinstance(statuses, dict):
            statuses = {}
    except (FileNotFoundError, json.JSONDecodeError):
        statuses = {}
    statuses[packet["id"]] = {
        "status": "queued",
        "message": "Command is queued locally; waiting for connector to send it to the flight controller",
        "command": packet.get("command"),
        "createdAt": packet["createdAt"],
        "updatedAt": packet["createdAt"],
        "results": [],
    }
    COMMAND_STATUS_FILE.write_text(json.dumps(statuses, ensure_ascii=False, indent=2), encoding="utf-8")
    return packet


def connector_command_status(command_id):
    if not command_id:
        return {"status": "missing", "message": "缂哄皯 commandId"}
    try:
        statuses = json.loads(COMMAND_STATUS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        statuses = {}
    fallback = statuses.get(command_id, {
        "status": "queued",
        "message": "Command is queued locally; waiting for connector to send it to the flight controller",
        "results": [],
    })
    if fallback.get("status") == "queued":
        process_running = any(process.poll() is None for process in CONNECTION_PROCESSES) or any(
            is_pid_running(pid) for pid in read_connection_pid_file()
        )
        if not process_running:
            fallback = {
                **fallback,
                "status": "waiting_connector",
                "message": "Command is queued, but the MAVLink connector is not running. Start the flight controller connection first.",
            }
    return fallback


def safe_filename(filename):
    name = Path(str(filename or "flight.ulg").strip().strip('"').strip("'")).name
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
    name = name.strip("._") or "flight.ulg"
    suffix = Path(name).suffix.lower()
    return name if suffix in {".ulg", ".csv", ".json", ".txt"} else f"{name}.ulg"


def parse_bool_field(fields, name, default=True):
    value = fields.get(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "on", "yes", "y"}


def preflight_checklist():
    with STATE_LOCK:
        state = STATE
        has_packet = bool(LAST_PACKET_MONOTONIC)
        age = time.monotonic() - LAST_PACKET_MONOTONIC if LAST_PACKET_MONOTONIC else None
        mission = list(MISSION)
    rate = len([stamp for stamp in PACKET_TIMES if time.monotonic() - stamp <= 1.0])
    checks = [
        {
            "id": "link",
            "label": "MAVLink data link",
            "passed": bool(has_packet and age is not None and age < 3 and rate >= 1),
            "blocking": True,
            "detail": f"Receiving {rate} Hz, last packet {round(age, 1) if age is not None else '--'} s ago",
        },
        {
            "id": "armed",
            "label": "Vehicle disarmed",
            "passed": not state.armed,
            "blocking": True,
            "detail": "Vehicle is armed; preflight configuration is blocked" if state.armed else "Vehicle is disarmed",
        },
        {
            "id": "gps",
            "label": "GPS fix quality",
            "passed": (state.gps_fix_type or 0) >= 3 and (state.satellites_visible or 0) >= 8,
            "blocking": True,
            "detail": f"Fix {state.gps_fix_type or '--'}, satellites {state.satellites_visible or '--'}",
        },
        {
            "id": "battery",
            "label": "Battery remaining",
            "passed": state.battery_remaining_percent is None or state.battery_remaining_percent >= 30,
            "blocking": True,
            "detail": f"{state.battery_remaining_percent if state.battery_remaining_percent is not None else '--'}%",
        },
        {
            "id": "attitude",
            "label": "Attitude data",
            "passed": all(finite_number(value) for value in (state.roll_deg, state.pitch_deg, state.yaw_deg)),
            "blocking": True,
            "detail": f"Roll {state.roll_deg if state.roll_deg is not None else '--'} / Pitch {state.pitch_deg if state.pitch_deg is not None else '--'} / Yaw {state.yaw_deg if state.yaw_deg is not None else '--'}",
        },
        {
            "id": "home",
            "label": "Home point",
            "passed": bool(state.home_position or (state.latitude is not None and state.longitude is not None)),
            "blocking": False,
            "detail": "Home or current position is available" if state.home_position or (state.latitude is not None and state.longitude is not None) else "Home/current position unavailable",
        },
        {
            "id": "mission",
            "label": "Mission route",
            "passed": len(mission) >= 2,
            "blocking": False,
            "detail": f"{len(mission)} waypoints",
        },
        {
            "id": "blackbox",
            "label": "Blackbox recording",
            "passed": LOGGER.active,
            "blocking": False,
            "detail": "Recording" if LOGGER.active else "Enable recording before flight when needed",
        },
        {
            "id": "mode",
            "label": "Safety mode",
            "passed": SAFETY.mode in {"real_readonly", "real_command", "simulation", "demo"},
            "blocking": False,
            "detail": SAFETY.mode,
        },
    ]
    blocking_failed = [item for item in checks if item["blocking"] and not item["passed"]]
    failed = [item for item in checks if not item["passed"]]
    return {
        "allowed": not blocking_failed,
        "score": len(checks) - len(failed),
        "total": len(checks),
        "summary": "Preflight check passed" if not blocking_failed else f"{len(blocking_failed)} blocking checks failed",
        "checks": checks,
    }


def apply_parameter_queue(payload):
    from services.safety_gate import safety_gate

    result = SAFETY.can_write_parameters(STATE)
    if not result.allowed:
        return {"accepted": False, "reason": result.reason, "queued": []}
    if payload.get("confirmation") != "纭鍐欏叆鍙傛暟":
        return {"accepted": False, "reason": "璇疯緭鍏ョ‘璁ゆ枃鏈細纭鍐欏叆鍙傛暟", "queued": []}
    parameters = payload.get("parameters", [])
    recommendations = []
    with STATE_LOCK:
        known_parameters = dict(STATE.parameters)
    for item in parameters:
        name = str(item.get("name", "")).strip().upper()
        if not name:
            continue
        current = known_parameters.get(name)
        if current is None and item.get("currentSource") == "aircraft":
            current = item.get("current", item.get("oldValue"))
        if current is None:
            return {
                "accepted": False,
                "reason": f"Parameter {name} is missing the current aircraft value. Request the parameter list before writing.",
                "queued": [],
            }
        recommendations.append({
            "name": name,
            "current": current,
            "suggested": item.get("value"),
        })
    gate = safety_gate(recommendations, STATE, confirmation=payload.get("confirmation", ""))
    if not gate["allowed"]:
        return {"accepted": False, "reason": gate["summary"], "safetyGate": gate, "queued": []}
    queued = []
    for item in gate["accepted"]:
        try:
            name, numeric_value = validate_parameter_change(item.get("name", ""), item.get("suggested"))
        except (TypeError, ValueError):
            raise
        with STATE_LOCK:
            old_value = STATE.parameters.get(name)
            STATE.parameters[name] = numeric_value
        command = enqueue_connector_command({
            "command": "set_parameter",
            "name": name,
            "value": numeric_value,
        })
        LOGGER.log_parameter(name, old_value, numeric_value)
        LOGGER.log_event("parameter_queued", f"{name}: {old_value} -> {numeric_value}", "parameter_panel")
        queued.append({"id": command["id"], "name": name, "value": numeric_value, "oldValue": old_value})
    return {
        "accepted": bool(queued),
        "reason": f"{len(queued)} parameters queued for PX6C command service" if queued else "No valid parameters to write",
        "safetyGate": gate,
        "queued": queued,
    }


def load_mock_pid_case(case_id):
    case_path = ROOT / "mock" / "ai_pid_cases.json"
    try:
        cases = json.loads(case_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        cases = []
    for item in cases:
        if item.get("id") == case_id:
            return item
    return cases[0] if cases else None


def ai_pid_history():
    from services.rollback_manager import list_rollbacks

    return {"history": list_rollbacks()}


def ai_pid_status_payload():
    from services.ai_pid_advisor import ai_status
    from services.ai_model_config import public_ai_status
    from services.token_usage import usage_summary

    status = ai_status()
    case_path = ROOT / "mock" / "ai_pid_cases.json"
    try:
        cases = json.loads(case_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        cases = []
    status["mockCases"] = [{"id": item.get("id"), "label": item.get("label")} for item in cases]
    status["aiConfig"] = public_ai_status("ai_pid_advisor")
    status["usage"] = usage_summary()
    return status


def generate_ai_pid_advice(payload):
    from services.ai_pid_advisor import build_pid_advice
    from services.feature_extractor import extract_features_from_telemetry
    from services.llm_pid_advisor import ALLOWED_PROVIDERS, build_openai_pid_advice
    from services.safety_gate import safety_gate

    with STATE_LOCK:
        history = list(TELEMETRY_HISTORY)
        state_dict = STATE.to_dict()
    source = payload.get("source", "live")
    axis = payload.get("axis", "roll")
    symptom = payload.get("symptom", "balanced")
    aggressiveness = payload.get("aggressiveness", 2)
    provider = str(payload.get("provider") or os.environ.get("AI_PROVIDER", "local")).lower()
    model_mode = payload.get("modelMode") or payload.get("mode")
    use_similar_cases = payload.get("useSimilarCases", True)

    if payload.get("features"):
        features = payload["features"]
    elif source == "mock":
        case = load_mock_pid_case(payload.get("caseId", ""))
        if not case:
            raise ValueError("鏈壘鍒?AI PID Mock 绀轰緥")
        features = case.get("features", {})
        axis = payload.get("axis") or case.get("axis", axis)
        symptom = payload.get("symptom") or case.get("symptom", symptom)
    else:
        features = extract_features_from_telemetry(history, state_dict)

    provider_error = None
    if provider in ALLOWED_PROVIDERS:
        try:
            advice = build_openai_pid_advice(
                features,
                axis=axis,
                symptom=symptom,
                aggressiveness=int(aggressiveness or 2),
                state=state_dict,
                mode=model_mode,
                use_similar_cases=bool(use_similar_cases),
            )
        except Exception as error:
            provider_error = str(error)
            advice = build_pid_advice(features, axis=axis, symptom=symptom, aggressiveness=aggressiveness, state=state_dict)
            advice["provider"] = "local"
            advice["providerLabel"] = "Local engineering rules - OpenAI fallback"
            advice.setdefault("risks", []).append(f"OpenAI/ChatGPT call failed; local Advisor fallback used: {provider_error}")
    else:
        advice = build_pid_advice(features, axis=axis, symptom=symptom, aggressiveness=aggressiveness, state=state_dict)
        advice.setdefault("provider", "local")
        advice.setdefault("providerLabel", "Local engineering rules")
    advice["safetyPreview"] = safety_gate(advice.get("recommendations"), STATE, require_confirmation=False)
    try:
        from services.token_usage import usage_summary
        advice["usageSummary"] = usage_summary()
    except Exception:
        advice["usageSummary"] = {}
    LOGGER.log_event("ai_pid_advisor_analyzed", f"{advice['axis']} confidence={advice['confidence']} provider={advice.get('provider')}", "AI PID")
    return advice


def generate_ai_pid_advice_from_log(upload_path, fields):
    from services.ai_pid_advisor import build_pid_advice
    from services.feature_extractor import extract_features_from_log
    from services.llm_pid_advisor import ALLOWED_PROVIDERS, build_openai_pid_advice
    from services.safety_gate import safety_gate

    with STATE_LOCK:
        state_dict = STATE.to_dict()
    axis = fields.get("axis", "roll")
    symptom = fields.get("symptom", "balanced")
    aggressiveness = int(fields.get("aggressiveness", "2") or 2)
    provider = str(fields.get("provider") or os.environ.get("AI_PROVIDER", "local")).lower()
    model_mode = fields.get("modelMode") or fields.get("mode")
    use_similar_cases = str(fields.get("useSimilarCases", "true")).lower() != "false"
    features = extract_features_from_log(upload_path)
    if provider in ALLOWED_PROVIDERS:
        try:
            advice = build_openai_pid_advice(
                features,
                axis=axis,
                symptom=symptom,
                aggressiveness=aggressiveness,
                state=state_dict,
                mode=model_mode,
                use_similar_cases=use_similar_cases,
            )
        except Exception as error:
            advice = build_pid_advice(features, axis=axis, symptom=symptom, aggressiveness=aggressiveness, state=state_dict)
            advice["provider"] = "local"
            advice["providerLabel"] = "Local engineering rules - OpenAI fallback"
            advice.setdefault("risks", []).append(f"OpenAI/ChatGPT call failed; local Advisor fallback used: {error}")
    else:
        advice = build_pid_advice(features, axis=axis, symptom=symptom, aggressiveness=aggressiveness, state=state_dict)
        advice.setdefault("provider", "local")
        advice.setdefault("providerLabel", "Local engineering rules")
    advice["safetyPreview"] = safety_gate(advice.get("recommendations"), STATE, require_confirmation=False)
    try:
        from services.token_usage import usage_summary
        advice["usageSummary"] = usage_summary()
    except Exception:
        advice["usageSummary"] = {}
    LOGGER.log_event("ai_pid_advisor_log_analyzed", f"{Path(upload_path).name} {advice['axis']} provider={advice.get('provider')}", "AI PID")
    return advice


def queue_pid_recommendations(recommendations, source, note=""):
    from services.rollback_manager import create_rollback_snapshot, mark_applied

    snapshot = create_rollback_snapshot(recommendations, STATE, source=source, note=note)
    queued = []
    for item in recommendations:
        name, numeric_value = validate_parameter_change(item.get("name", ""), item.get("suggested"))
        with STATE_LOCK:
            old_value = STATE.parameters.get(name)
            STATE.parameters[name] = numeric_value
        command = enqueue_connector_command({
            "command": "set_parameter",
            "name": name,
            "value": numeric_value,
        })
        LOGGER.log_parameter(name, old_value, numeric_value)
        LOGGER.log_event("ai_pid_parameter_queued", f"{name}: {old_value} -> {numeric_value}", source)
        queued.append({"id": command["id"], "name": name, "value": numeric_value, "oldValue": old_value})
    mark_applied(snapshot["id"], queued)
    return snapshot, queued


def apply_ai_pid(payload):
    from services.safety_gate import safety_gate

    recommendations = payload.get("recommendations", [])
    gate = safety_gate(recommendations, STATE, confirmation=payload.get("confirmation", ""))
    if not gate["allowed"]:
        return {"accepted": False, "reason": gate["summary"], "safetyGate": gate, "queued": []}
    snapshot, queued = queue_pid_recommendations(gate["accepted"], payload.get("source", "AI PID Advisor"), payload.get("note", ""))
    return {
        "accepted": bool(queued),
        "reason": f"{len(queued)} 涓?AI PID 鍙傛暟宸插姞鍏?PX6C 鍛戒护闃熷垪",
        "rollbackId": snapshot["id"],
        "safetyGate": gate,
        "queued": queued,
    }


def rollback_ai_pid(payload):
    from services.rollback_manager import mark_rolled_back, rollback_recommendations
    from services.safety_gate import safety_gate

    snapshot_id = payload.get("rollbackId") or payload.get("id")
    recommendations = rollback_recommendations(snapshot_id)
    gate = safety_gate(recommendations, STATE, confirmation=payload.get("confirmation", ""))
    if not gate["allowed"]:
        return {"accepted": False, "reason": gate["summary"], "safetyGate": gate, "queued": []}
    _snapshot, queued = queue_pid_recommendations(gate["accepted"], "AI PID Rollback", f"rollback {snapshot_id}")
    mark_rolled_back(snapshot_id, queued)
    return {
        "accepted": bool(queued),
        "reason": f"{len(queued)} PID rollback parameters queued",
        "safetyGate": gate,
        "queued": queued,
    }


def flight_mode_options():
    return {
        "modes": [
            {"key": key, **value}
            for key, value in PX4_FLIGHT_MODES.items()
        ]
    }


def change_flight_mode(payload):
    mode_key = str(payload.get("mode", "")).strip().lower()
    preset = PX4_FLIGHT_MODES.get(mode_key)
    if not preset:
        raise ValueError("Unsupported flight mode")
    safety = SAFETY.can_change_mode(STATE)
    if not safety.allowed:
        return {"accepted": False, "reason": safety.reason, "mode": mode_key}
    if preset.get("requiresMission") and not MISSION:
        return {"accepted": False, "reason": "Mission mode requires a planned or loaded mission route", "mode": mode_key}
    if mode_key == "land" and not payload.get("confirmLand"):
        return {"accepted": False, "reason": "Land mode requires secondary confirmation", "mode": mode_key}
    if mode_key == "rtl":
        rtl_safety = SAFETY.can_return_to_launch(STATE)
        if not rtl_safety.allowed:
            return {"accepted": False, "reason": rtl_safety.reason, "mode": mode_key}
        if not payload.get("confirmRtl"):
            return {"accepted": False, "reason": "RTL mode requires secondary confirmation", "mode": mode_key}
    command = enqueue_connector_command({
        "command": "set_flight_mode",
        "mode": mode_key,
        "label": preset["label"],
        "px4Mode": preset["px4"],
        "mainMode": preset["mainMode"],
        "subMode": preset["subMode"],
    })
    LOGGER.log_event("flight_mode_command_queued", f"{preset['label']} / {preset['px4']}", "flight_mode")
    return {
        "accepted": True,
        "reason": f"宸插姞鍏ラ鎺фā寮忓垏鎹㈤槦鍒楋細{preset['label']}",
        "commandId": command["id"],
        "mode": mode_key,
        "target": preset,
    }


def arm_vehicle(payload):
    arm = bool(payload.get("arm"))
    if arm:
        safety = SAFETY.can_arm(STATE)
        if not safety.allowed:
            return {"accepted": False, "reason": safety.reason}
        if payload.get("confirmation") != "纭宸叉媶妗ㄥ苟澶勪簬瀹夊叏娴嬭瘯鐜":
            return {"accepted": False, "reason": "瑙ｉ攣鍓嶅繀椤荤‘璁わ細纭宸叉媶妗ㄥ苟澶勪簬瀹夊叏娴嬭瘯鐜"}
    else:
        safety = SAFETY.can_disarm(STATE)
        if not safety.allowed:
            return {"accepted": False, "reason": safety.reason}
        if not STATE.connected:
            return {"accepted": False, "reason": "鏃犳硶涓婇攣锛氶鎺ф湭杩炴帴鎴栭摼璺凡瓒呮椂"}

    command = enqueue_connector_command({
        "command": "arm_disarm",
        "arm": arm,
    })
    LOGGER.log_event(
        "arm_command_queued",
        "瑙ｉ攣鍛戒护宸插姞鍏?PX6C 闃熷垪" if arm else "涓婇攣鍛戒护宸插姞鍏?PX6C 闃熷垪",
        "flight_control",
    )
    return {
        "accepted": True,
        "reason": "瑙ｉ攣鍛戒护宸插姞鍏?PX6C 闃熷垪" if arm else "涓婇攣鍛戒护宸插姞鍏?PX6C 闃熷垪",
        "commandId": command["id"],
        "arm": arm,
    }


def request_parameter_values(payload):
    status = connection_status()
    if not status.get("processRunning"):
        return {"accepted": False, "reason": "PX6C 杩炴帴绋嬪簭鏈繍琛岋紝鏃犳硶璇诲彇椋炴帶鍙傛暟"}
    if not status.get("heartbeat"):
        return {"accepted": False, "reason": "灏氭湭鏀跺埌椋炴帶 HEARTBEAT锛岃鍏堢‘璁?MAVLink 杩炴帴鎴愬姛"}
    names = payload.get("names") or [
        "MC_ROLLRATE_P", "MC_ROLLRATE_I", "MC_ROLLRATE_D",
        "MC_PITCHRATE_P", "MC_PITCHRATE_I", "MC_PITCHRATE_D",
        "MC_YAWRATE_P", "MC_YAWRATE_I", "MC_YAWRATE_D",
        "MPC_Z_VEL_P_ACC", "MPC_Z_VEL_I_ACC", "MPC_Z_VEL_D_ACC",
        "FW_RR_P", "FW_RR_I", "FW_RR_D",
        "FW_PR_P", "FW_PR_I", "FW_PR_D",
        "FW_YR_P", "FW_YR_I", "FW_YR_D",
        *(f"PWM_MAIN_FUNC{index}" for index in range(1, 13)),
        *(f"PWM_AUX_FUNC{index}" for index in range(1, 9)),
    ]
    names = [str(name or "").strip().upper() for name in names if str(name or "").strip()]
    command = enqueue_connector_command({
        "command": "request_parameters",
        "names": names,
    })
    LOGGER.log_event("parameters_requested", f"{len(names)} 涓弬鏁拌鍙栧凡鍔犲叆闃熷垪", "parameter_panel")
    return {
        "accepted": True,
        "reason": f"{len(names)} 涓弬鏁拌鍙栧凡鍔犲叆 PX6C 闃熷垪",
        "commandId": command["id"],
        "names": names,
    }


def rc_state_snapshot():
    with STATE_LOCK:
        state = STATE
        state_dict = state.to_dict()
        parameters = dict(state.parameters or {})
        channels = list(state.rc_raw_channels or state.rc_channels or [])
    snapshot = channel_snapshot(channels, parameters)
    function_labels = {
        "roll": "Roll",
        "pitch": "Pitch",
        "throttle": "Throttle",
        "yaw": "Yaw",
        "flightMode": "Flight Mode",
        "armSwitch": "Arm Switch",
    }
    by_channel = {}
    for key, item in (snapshot.get("mapped") or {}).items():
        try:
            channel = int((item or {}).get("channel") or 0)
        except (TypeError, ValueError):
            channel = 0
        if channel > 0:
            by_channel.setdefault(channel, []).append(function_labels.get(key, key))
    for item in snapshot.get("raw") or []:
        channel = int(item.get("channel") or 0)
        labels = by_channel.get(channel, [])
        item["function"] = " / ".join(labels) if labels else ("Aux channel / switch" if item.get("used") else "Unmapped")
    connected = bool(state_dict.get("connected"))
    rc_link = analyze_rc_link(state_dict, mavlink_message_stats(), list(RC_LINK_HISTORY))
    return {
        "connected": connected,
        "vehicleHeartbeat": connected,
        "gcsHeartbeat": state_dict.get("gcsHeartbeat", {}),
        "targetSystem": state_dict.get("targetSystem"),
        "targetComponent": state_dict.get("targetComponent"),
        "armed": bool(state_dict.get("armed")),
        "mode": state_dict.get("mode"),
        "rcOnline": any(item["pwm"] is not None for item in snapshot["raw"]),
        "rcLink": rc_link,
        "lastRcUpdateTime": state_dict.get("timestamp"),
        "rssi": state_dict.get("rcSignal"),
        "parameters": {name: parameters.get(name) for name in RC_PARAMETER_NAMES},
        "raw": snapshot["raw"],
        "mapped": snapshot["mapped"],
        "rcMapAvailable": snapshot["rcMapAvailable"],
        "warnings": list(state_dict.get("rcIssues") or []),
    }


def rc_status_payload():
    status = connection_status()
    rc = rc_state_snapshot()
    return {
        "connection": status,
        "connected": rc["connected"],
        "rcOnline": rc["rcOnline"],
        "rcLink": rc["rcLink"],
        "gcsHeartbeat": rc["gcsHeartbeat"],
        "targetSystem": rc["targetSystem"],
        "targetComponent": rc["targetComponent"],
        "armed": rc["armed"],
        "mode": rc["mode"],
        "lastRcUpdateTime": rc["lastRcUpdateTime"],
    }


def rc_channels_payload():
    rc = rc_state_snapshot()
    return {"channels": rc["raw"], "rssi": rc["rssi"], "rcLink": rc["rcLink"]}


def rc_mapping_payload():
    rc = rc_state_snapshot()
    return {
        "mapped": rc["mapped"],
        "parameters": {name: rc["parameters"].get(name) for name in RC_PARAMETER_NAMES},
        "rcMapAvailable": rc["rcMapAvailable"],
        "rcLink": rc["rcLink"],
        "warnings": rc["warnings"],
    }


def rc_full_payload():
    status = connection_status()
    rc = rc_state_snapshot()
    return {
        "connection": status,
        "connected": rc["connected"],
        "rcOnline": rc["rcOnline"],
        "rcLink": rc["rcLink"],
        "gcsHeartbeat": rc["gcsHeartbeat"],
        "targetSystem": rc["targetSystem"],
        "targetComponent": rc["targetComponent"],
        "armed": rc["armed"],
        "mode": rc["mode"],
        "lastRcUpdateTime": rc["lastRcUpdateTime"],
        "rssi": rc["rssi"],
        "channels": rc["raw"],
        "mapped": rc["mapped"],
        "parameters": {name: rc["parameters"].get(name) for name in RC_PARAMETER_NAMES},
        "rcMapAvailable": rc["rcMapAvailable"],
        "warnings": rc["warnings"],
    }


def start_rc_calibration(payload):
    if STATE.armed:
        return {"accepted": False, "reason": "Vehicle is armed; RC calibration is blocked"}
    session = RcCalibrationSession()
    with STATE_LOCK:
        session.backup = {name: STATE.parameters.get(name) for name in RC_PARAMETER_NAMES}
    RC_CALIBRATION_SESSIONS[session.id] = session
    if payload.get("requestParameters", True):
        enqueue_connector_command({"command": "request_parameters", "names": RC_PARAMETER_NAMES})
    LOGGER.log_event("rc_calibration_started", session.id, "rc_setup")
    return {"accepted": True, "session": session.to_dict(), "reason": "RC calibration session created; RC parameters requested"}


def get_rc_session(session_id):
    session = RC_CALIBRATION_SESSIONS.get(session_id)
    if not session:
        return {"accepted": False, "reason": "RC calibration session not found"}
    return {"accepted": True, "session": session.to_dict()}


def rc_calibration_step(payload):
    session = RC_CALIBRATION_SESSIONS.get(payload.get("sessionId"))
    if not session:
        return {"accepted": False, "reason": "RC calibration session not found"}
    if STATE.armed:
        return {"accepted": False, "reason": "椋炴帶宸茶В閿侊紝绂佹缁х画鏍″噯"}
    step = str(payload.get("step") or "").strip()
    channels = payload.get("samples") or []
    if channels and isinstance(channels[0], (int, float, type(None))):
        channels = [channels]
    session.samples[step] = channels
    session.step = step
    session.updated_at = time.time()
    used = {
        int(item.get("channel"))
        for item in session.detected.values()
        if item.get("channel")
    }
    result = None
    if step in {"roll", "pitch", "throttle", "yaw", "flightMode", "armSwitch"}:
        result = detect_moving_channel(channels, used_channels=used)
        if result.get("channel"):
            result["reversed"] = bool(payload.get("reversed", False))
            session.detected[step] = result
    elif step in {"center", "ranges"}:
        result = {"ok": True, "summaries": summarize_samples(channels)}
    else:
        result = {"ok": True, "message": "Step recorded"}
    return {"accepted": True, "session": session.to_dict(), "result": result}


def rc_calibration_preview(payload):
    session = RC_CALIBRATION_SESSIONS.get(payload.get("sessionId"))
    if not session:
        return {"accepted": False, "reason": "RC calibration session not found"}
    with STATE_LOCK:
        parameters = dict(STATE.parameters or {})
    preview = build_preview(session, parameters)
    return {"accepted": True, "session": session.to_dict(), "preview": preview}


def apply_rc_calibration(payload):
    session = RC_CALIBRATION_SESSIONS.get(payload.get("sessionId"))
    if not session:
        return {"accepted": False, "reason": "RC calibration session not found", "queued": []}
    if STATE.armed:
        return {"accepted": False, "reason": "椋炴帶宸茶В閿侊紝绂佹鍐欏叆 RC 鍙傛暟", "queued": []}
    result = SAFETY.can_write_parameters(STATE)
    if not result.allowed:
        return {"accepted": False, "reason": result.reason, "queued": []}
    if payload.get("confirmation") != "纭鍐欏叆RC鍙傛暟":
        return {"accepted": False, "reason": "璇疯緭鍏ョ‘璁ゆ枃鏈細纭鍐欏叆RC鍙傛暟", "queued": []}
    with STATE_LOCK:
        parameters = dict(STATE.parameters or {})
    preview = session.preview or build_preview(session, parameters)
    queued = []
    for item in preview:
        if not item.get("changed"):
            continue
        name, value = validate_parameter_change(item["name"], item["value"])
        command = enqueue_connector_command({"command": "set_parameter", "name": name, "value": value})
        with STATE_LOCK:
            old_value = STATE.parameters.get(name)
            STATE.parameters[name] = value
        LOGGER.log_parameter(name, old_value, value)
        queued.append({"id": command["id"], "name": name, "value": value, "oldValue": old_value})
    if queued:
        enqueue_connector_command({"command": "request_parameters", "names": RC_PARAMETER_NAMES})
    return {"accepted": bool(queued), "reason": f"{len(queued)} RC parameters queued for writing", "queued": queued}


def restore_rc_calibration(payload):
    session = RC_CALIBRATION_SESSIONS.get(payload.get("sessionId"))
    if not session:
        return {"accepted": False, "reason": "RC calibration session not found", "queued": []}
    if STATE.armed:
        return {"accepted": False, "reason": "椋炴帶宸茶В閿侊紝绂佹鎭㈠ RC 鍙傛暟", "queued": []}
    if payload.get("confirmation") != "纭鎭㈠RC鍙傛暟":
        return {"accepted": False, "reason": "璇疯緭鍏ョ‘璁ゆ枃鏈細纭鎭㈠RC鍙傛暟", "queued": []}
    queued = []
    for name, value in session.backup.items():
        if value is None:
            continue
        name, numeric_value = validate_parameter_change(name, value)
        command = enqueue_connector_command({"command": "set_parameter", "name": name, "value": numeric_value})
        queued.append({"id": command["id"], "name": name, "value": numeric_value})
    if queued:
        enqueue_connector_command({"command": "request_parameters", "names": RC_PARAMETER_NAMES})
    return {"accepted": bool(queued), "reason": f"{len(queued)} backup RC parameters queued for restore", "queued": queued}


def start_calibration(payload):
    calibration_type = payload.get("type", "")
    if calibration_type not in CALIBRATION_TYPES:
        raise ValueError("鏈煡鏍″噯绫诲瀷")
    result = SAFETY.can_calibrate(
        STATE,
        calibration_type,
        payload.get("confirmation", ""),
    )
    if not result.allowed:
        return {"allowed": False, "reason": result.reason}
    command = enqueue_connector_command({
        "command": "calibrate",
        "type": calibration_type,
        "label": CALIBRATION_TYPES[calibration_type],
    })
    LOGGER.log_event("calibration_requested", CALIBRATION_TYPES[calibration_type], "user")
    return {
        "allowed": True,
        "reason": f"{CALIBRATION_TYPES[calibration_type]} 鎸囦护宸插彂閫佸埌 PX6C 闃熷垪",
        "commandId": command["id"],
    }


def aircraft_calibration_overview():
    with STATE_LOCK:
        state = STATE
    return build_aircraft_calibration_overview(state, SAFETY, SAFETY.mode)


def aircraft_calibration_status():
    overview = aircraft_calibration_overview()
    sessions = [
        get_aircraft_calibration_session(session_id, AIRCRAFT_CALIBRATION_SESSIONS, connector_command_status, STATE).get("session")
        for session_id in AIRCRAFT_CALIBRATION_SESSIONS
    ]
    return {**overview, "sessions": [item for item in sessions if item]}


def start_aircraft_calibration(payload):
    with STATE_LOCK:
        state = STATE
    return start_aircraft_calibration_session(
        payload,
        state,
        SAFETY,
        AIRCRAFT_CALIBRATION_SESSIONS,
        enqueue_connector_command,
        LOGGER,
    )


def cancel_aircraft_calibration(payload):
    return cancel_aircraft_calibration_session(
        payload.get("session_id") or payload.get("sessionId") or "",
        AIRCRAFT_CALIBRATION_SESSIONS,
    )


def force_refresh_aircraft_calibration(payload=None):
    now_ms = int(time.time() * 1000)
    calibration_terms = (
        "[cal]",
        "calibration",
        "calibrate",
        "command denied during calibration",
    )
    with STATE_LOCK:
        before_sessions = len(AIRCRAFT_CALIBRATION_SESSIONS)
        AIRCRAFT_CALIBRATION_SESSIONS.clear()
        before_texts = len(STATE.statustexts or [])
        STATE.calibration = {}
        STATE.statustexts = [
            item for item in (STATE.statustexts or [])
            if not any(term in str(item.get("text", "")).lower() for term in calibration_terms)
        ][-100:]
        removed_texts = before_texts - len(STATE.statustexts)
        STATE.statustexts.append({
            "timeMs": now_ms,
            "severity": "INFO",
            "text": "Local aircraft calibration state cleared by operator",
            "highlight": False,
            "category": None,
        })
        state = STATE.to_dict()
    LOGGER.log_event(
        "aircraft_calibration_force_refresh",
        f"cleared_sessions={before_sessions}, cleared_texts={removed_texts}",
        "calibration",
    )
    return {
        "accepted": True,
        "reason": "已清除本地校准会话和卡住的校准状态；未向飞控发送取消校准命令。",
        "clearedSessions": before_sessions,
        "clearedStatusTexts": removed_texts,
        "overview": build_aircraft_calibration_overview(state, SAFETY, SAFETY.mode),
    }


def start_servo_test(payload):
    result = SAFETY.can_test_servo(STATE)
    if not result.allowed:
        return {"accepted": False, "reason": result.reason, "queued": []}
    outputs = payload.get("outputs", [])
    if not isinstance(outputs, list) or not outputs:
        raise ValueError("缂哄皯鑸垫満杈撳嚭閫氶亾")
    queued_outputs = []
    for item in outputs:
        try:
            channel = int(item.get("channel"))
            pwm = int(item.get("pwm"))
            output_function = item.get("outputFunction")
        except (TypeError, ValueError, AttributeError):
            continue
        if not 1 <= channel <= 16:
            raise ValueError(f"鑸垫満閫氶亾 {channel} 瓒呭嚭鑼冨洿")
        if not 800 <= pwm <= 2200:
            raise ValueError(f"鑸垫満 {channel} PWM 瓒呭嚭瀹夊叏鑼冨洿 800-2200us")
        queued = {"channel": channel, "pwm": pwm}
        if output_function not in (None, ""):
            queued["outputFunction"] = int(output_function)
        queued_outputs.append(queued)
    if not queued_outputs:
        raise ValueError("娌℃湁鏈夋晥鑸垫満杈撳嚭")
    command = enqueue_connector_command({
        "command": "set_servo",
        "outputs": queued_outputs,
    })
    LOGGER.log_event("servo_test_queued", f"{len(queued_outputs)} 璺埖鏈烘祴璇曞懡浠ゅ凡鍔犲叆闃熷垪", "servo_panel")
    return {
        "accepted": True,
        "reason": f"{len(queued_outputs)} 璺埖鏈?PWM 宸插姞鍏?PX6C 鍛戒护闃熷垪",
        "commandId": command["id"],
        "queued": queued_outputs,
    }


def servo_layout():
    outputs = list(STATE.servo_outputs or [])
    active = []
    for index, pwm in enumerate(outputs[:16]):
        value = int(pwm or 0)
        if not 800 <= value <= 2200:
            continue
        channel = index + 1
        if channel <= 8:
            param_name = f"PWM_MAIN_FUNC{channel}"
        else:
            param_name = f"PWM_AUX_FUNC{channel - 8}"
        function_value = STATE.parameters.get(param_name)
        decoded_function = int(function_value) if function_value is not None and int(function_value) != 0 else None
        active.append({
            "channel": channel,
            "pwm": value,
            "param": param_name,
            "outputFunction": decoded_function,
        })
    if not active:
        active = [{"channel": index, "pwm": 1500, "param": f"PWM_MAIN_FUNC{index}", "outputFunction": None} for index in range(1, 5)]
        source = "fallback"
    else:
        source = "telemetry"
    function_groups = {}
    for item in active:
        if item.get("outputFunction") is None:
            continue
        function_groups.setdefault(str(item["outputFunction"]), []).append(item["channel"])
    duplicates = [
        {"outputFunction": int(function_id), "channels": channels}
        for function_id, channels in function_groups.items()
        if len(channels) > 1
    ]
    return {
        "source": source,
        "count": len(active),
        "channels": active,
        "duplicates": duplicates,
        "recommended": [
            {
                "name": item["param"],
                "current": item.get("outputFunction"),
                "recommended": 200 + item["channel"],
            }
            for item in active
            if item.get("param") and item.get("outputFunction") != 200 + item["channel"]
        ],
        "raw": outputs,
        "message": f"Detected {len(active)} active servo outputs" if source == "telemetry" else "No active servo output telemetry yet; showing temporary fallback channels",
    }


def motor_layout():
    motors = []
    for prefix, count in (("PWM_MAIN_FUNC", 12), ("PWM_AUX_FUNC", 8)):
        for index in range(1, count + 1):
            param_name = f"{prefix}{index}"
            raw_value = STATE.parameters.get(param_name)
            if raw_value is None:
                continue
            try:
                output_function = int(round(float(raw_value)))
            except (TypeError, ValueError):
                continue
            if not 101 <= output_function <= 112:
                continue
            motors.append({
                "motor": output_function - 100,
                "param": param_name,
                "outputFunction": output_function,
                "output": f"{'MAIN' if prefix == 'PWM_MAIN_FUNC' else 'AUX'}{index}",
            })
    motors.sort(key=lambda item: (item["motor"], item["param"]))
    groups = {}
    for item in motors:
        groups.setdefault(str(item["outputFunction"]), []).append(item["param"])
    duplicates = [
        {"outputFunction": int(function_id), "params": params}
        for function_id, params in groups.items()
        if len(params) > 1
    ]
    return {
        "source": "parameters" if motors else "waiting",
        "count": len(motors),
        "motors": motors,
        "duplicates": duplicates,
        "message": f"Detected {len(motors)} motor outputs" if motors else "PX4 motor output mapping not available yet; keep connected and wait a few seconds",
    }


def repair_servo_mapping(payload):
    result = SAFETY.can_write_parameters(STATE)
    if not result.allowed:
        return {"accepted": False, "reason": result.reason, "queued": []}
    if payload.get("confirmation") != "纭鍐欏叆鍙傛暟":
        return {"accepted": False, "reason": "璇疯緭鍏ョ‘璁ゆ枃鏈細纭鍐欏叆鍙傛暟", "queued": []}
    layout = servo_layout()
    repairs = layout.get("recommended", [])
    queued = []
    for item in repairs:
        name, value = validate_parameter_change(item["name"], int(item["recommended"]))
        command = enqueue_connector_command({
            "command": "set_parameter",
            "name": name,
            "value": value,
        })
        with STATE_LOCK:
            old_value = STATE.parameters.get(name)
            STATE.parameters[name] = value
        LOGGER.log_parameter(name, old_value, value)
        LOGGER.log_event("servo_mapping_repair_queued", f"{name}: {old_value} -> {value}", "servo_panel")
        queued.append({"id": command["id"], "name": name, "value": value, "oldValue": old_value})
    return {
        "accepted": bool(queued),
        "reason": f"{len(queued)} 涓埖鏈烘槧灏勫弬鏁板凡鍔犲叆鍐欏叆闃熷垪" if queued else "褰撳墠鑸垫満鏄犲皠鏃犻渶淇",
        "queued": queued,
    }


def start_motor_test(payload):
    confirmation = payload.get("confirmation", "")
    result = SAFETY.can_test_motor(STATE, confirmation_matches(confirmation, PROP_CONFIRMATION_TEXTS))
    if not result.allowed:
        return {"accepted": False, "reason": result.reason}
    try:
        motor = int(payload.get("motor", 1))
        throttle = float(payload.get("throttlePercent", 0))
        duration = float(payload.get("duration", 2))
        output_function = payload.get("outputFunction")
        output_function = int(output_function) if output_function not in (None, "") else None
    except (TypeError, ValueError):
        raise ValueError("Invalid motor test parameters")
    if not 1 <= motor <= 12:
        raise ValueError("Motor index must be between 1 and 12")
    if output_function is not None and not 101 <= output_function <= 112:
        raise ValueError("Motor output function must be between 101 and 112")
    if not 0 <= throttle <= 10:
        raise ValueError("Motor test throttle must be between 0 and 10%")
    if not 0.2 <= duration <= 5:
        raise ValueError("Motor test duration must be between 0.2 and 5 seconds")
    command = enqueue_connector_command({
        "command": "test_motor",
        "motor": motor,
        "outputFunction": output_function,
        "output": payload.get("output"),
        "throttlePercent": throttle,
        "duration": duration,
    })
    LOGGER.log_event("motor_test_queued", f"Motor {motor} test command queued", "motor_panel")
    return {
        "accepted": True,
        "reason": f"Motor {motor} test command queued for PX6C",
        "commandId": command["id"],
    }


def request_flight_log_list(_payload=None):
    status = connection_status()
    if not status.get("processRunning"):
        return {"accepted": False, "reason": "PX6C 杩炴帴绋嬪簭鏈繍琛岋紝鏃犳硶璇诲彇椋炴帶鏃ュ織鍒楄〃"}
    if not status.get("heartbeat"):
        return {"accepted": False, "reason": "灏氭湭鏀跺埌椋炴帶 HEARTBEAT锛岃鍏堢‘璁?MAVLink 杩炴帴鎴愬姛鍚庡啀璇诲彇 .ulg 鏃ュ織鍒楄〃"}
    command = enqueue_connector_command({
        "command": "list_flight_logs",
    })
    LOGGER.log_event("flight_log_list_requested", "璇锋眰椋炴帶 .ulg 鏃ュ織鍒楄〃", "log_panel")
    return {
        "accepted": True,
        "reason": "宸茶姹傞鎺?.ulg 鏃ュ織鍒楄〃",
        "commandId": command["id"],
    }


def download_flight_log(payload):
    status = connection_status()
    if not status.get("processRunning"):
        return {"accepted": False, "reason": "PX6C connection process is not running; cannot download flight logs"}
    if not status.get("heartbeat"):
        return {"accepted": False, "reason": "No vehicle HEARTBEAT yet; confirm MAVLink connection before downloading .ulg logs"}
    try:
        log_id = int(payload.get("id"))
        size = int(payload.get("size", 0) or 0)
        time_utc = int(payload.get("timeUtc", 0) or 0)
    except (TypeError, ValueError):
        raise ValueError("Invalid flight log id")
    if log_id < 0:
        raise ValueError("Invalid flight log id")
    ULG_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    command = enqueue_connector_command({
        "command": "download_flight_log",
        "logId": log_id,
        "size": size,
        "timeUtc": time_utc,
        "outputDir": str(ULG_DOWNLOAD_DIR),
    })
    LOGGER.log_event("flight_log_download_requested", f"Requested flight log download {log_id}", "log_panel")
    return {
        "accepted": True,
        "reason": f"Flight log {log_id} queued for download",
        "commandId": command["id"],
    }


def normalize_mission_waypoints(items):
    waypoints = []
    for index, item in enumerate(items or []):
        command = str(item.get("command", "WAYPOINT")).upper()
        if command not in {"TAKEOFF", "WAYPOINT", "LOITER", "LAND", "RTL"}:
            raise ValueError(f"Waypoint {index + 1} has an invalid command type")
        lat = float(item.get("lat", 0))
        lon = float(item.get("lon", 0))
        altitude = float(item.get("altitude", 0))
        hold = float(item.get("hold", 0) or 0)
        speed = float(item.get("speed", 0) or 0)
        if command != "RTL":
            if not -90 <= lat <= 90 or not -180 <= lon <= 180:
                raise ValueError(f"Waypoint {index + 1} has invalid latitude/longitude")
            if not 0 <= altitude <= 1000:
                raise ValueError(f"Waypoint {index + 1} altitude must be 0-1000 m")
        if not 0 <= hold <= 3600:
            raise ValueError(f"Waypoint {index + 1} hold time is invalid")
        if not 0 <= speed <= 60:
            raise ValueError(f"Waypoint {index + 1} speed must be 0-60 m/s")
        waypoints.append({
            "seq": index,
            "command": command,
            "lat": lat,
            "lon": lon,
            "altitude": altitude,
            "hold": hold,
            "speed": speed,
        })
    return waypoints


def save_local_mission(waypoints):
    global MISSION
    MISSION = normalize_mission_waypoints(waypoints)
    if LOGGER.active and LOGGER.session_dir:
        (LOGGER.session_dir / "mission.json").write_text(
            json.dumps(MISSION, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return {"waypoints": MISSION}


def upload_mission_to_flight_controller(payload):
    waypoints = normalize_mission_waypoints(payload.get("waypoints", MISSION))
    result = SAFETY.can_upload_mission(STATE, waypoints)
    if not result.allowed:
        return {"accepted": False, "reason": result.reason}
    command = enqueue_connector_command({
        "command": "upload_mission",
        "waypoints": waypoints,
        "clearExisting": bool(payload.get("clearExisting", True)),
    })
    LOGGER.log_event("mission_upload_queued", f"{len(waypoints)} waypoints queued for upload", "mission")
    return {
        "accepted": True,
        "reason": f"{len(waypoints)} waypoints queued for PX6C mission upload",
        "commandId": command["id"],
    }


def clear_flight_mission(_payload=None):
    result = SAFETY.can_clear_mission(STATE)
    if not result.allowed:
        return {"accepted": False, "reason": result.reason}
    command = enqueue_connector_command({"command": "clear_mission"})
    LOGGER.log_event("mission_clear_queued", "Clear mission command queued", "mission")
    return {
        "accepted": True,
        "reason": "Clear mission command queued for PX6C",
        "commandId": command["id"],
    }


def read_flight_mission(_payload=None):
    status = connection_status()
    if not status.get("processRunning"):
        return {"accepted": False, "reason": "PX6C connection process is not running; cannot read flight mission"}
    if not status.get("heartbeat"):
        return {"accepted": False, "reason": "No vehicle HEARTBEAT yet; confirm MAVLink connection first"}
    command = enqueue_connector_command({"command": "read_mission"})
    LOGGER.log_event("mission_read_queued", "Read mission command queued", "mission")
    return {
        "accepted": True,
        "reason": "Read mission command queued for PX6C",
        "commandId": command["id"],
    }


def start_connection(config):
    global CONNECTION_PROCESSES, CONNECTION_CONFIG, CONNECTION_LOG_HANDLE
    stop_connection()
    CONNECTION_CONFIG = {**CONNECTION_CONFIG, **config}
    kind = CONNECTION_CONFIG["type"]
    python = sys.executable
    if not getattr(sys, "frozen", False):
        dependency_check = subprocess.run(
            [python, "-c", "import pymavlink, serial"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if dependency_check.returncode != 0:
            raise RuntimeError("MAVLink 渚濊禆缂哄け锛岃杩愯 install-mavlink.cmd")
    child_env = os.environ.copy()
    child_env["PYTHONUNBUFFERED"] = "1"
    child_env["GCS_DATA_DIR"] = str(DATA_ROOT)
    child_env["GCS_RESOURCE_ROOT"] = str(ROOT)
    for key in list(child_env):
        if key.upper().startswith(("DEBUGPY", "PYDEVD")):
            child_env.pop(key, None)
    CONNECTION_LOG_HANDLE = (DATA_ROOT / "connection.log").open("a", encoding="utf-8")
    popen_options = {
        "cwd": DATA_ROOT,
        "env": child_env,
        "stdout": CONNECTION_LOG_HANDLE,
        "stderr": subprocess.STDOUT,
        "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    }
    if kind == "demo":
        command = [
            *packaged_entry_command("mavlink_simulator.py", python),
            "--connection", f"udpout:127.0.0.1:{CONNECTION_CONFIG['listenPort']}",
        ]
        bridge = [
            *packaged_entry_command("px6c_connector.py", python),
            "--connection", f"udpin:0.0.0.0:{CONNECTION_CONFIG['listenPort']}",
            "--ui", f"http://127.0.0.1:{PORT}/api/telemetry",
            "--vehicle", "DEMO-01",
        ]
        CONNECTION_PROCESSES = [
            subprocess.Popen(command, **popen_options),
            subprocess.Popen(bridge, **popen_options),
        ]
        SAFETY.set_mode("demo")
    elif kind == "serial":
        port = CONNECTION_CONFIG.get("serialPort")
        if not port:
            raise ValueError("请选择串口")
        if list_ports:
            available_ports = {item.device for item in list_ports.comports()}
            if available_ports and port not in available_ports:
                raise ValueError(f"串口 {port} 当前不存在。可用串口：{', '.join(sorted(available_ports))}")
        command = [
            *packaged_entry_command("px6c_connector.py", python),
            "--connection", port,
            "--baud", str(CONNECTION_CONFIG.get("baud", 57600)),
            "--ui", f"http://127.0.0.1:{PORT}/api/telemetry",
            "--vehicle", "PX6C-01",
        ]
        CONNECTION_PROCESSES = [subprocess.Popen(command, **popen_options)]
        SAFETY.set_mode("real_readonly")
    elif kind == "udp":
        target_ip = str(CONNECTION_CONFIG.get("targetIp", DEFAULT_MAVLINK_HOST) or DEFAULT_MAVLINK_HOST).strip()
        target_port = int(CONNECTION_CONFIG.get("targetPort") or CONNECTION_CONFIG.get("listenPort", DEFAULT_MAVLINK_TARGET_PORT))
        connection = f"udpout:{target_ip}:{target_port}"
        CONNECTION_CONFIG["effectiveConnection"] = connection
        CONNECTION_CONFIG["effectiveListenAddress"] = ""
        command = [
            *packaged_entry_command("px6c_connector.py", python),
            "--connection", connection,
            "--ui", f"http://127.0.0.1:{PORT}/api/telemetry",
            "--vehicle", "PX6C-01",
        ]
        CONNECTION_PROCESSES = [subprocess.Popen(command, **popen_options)]
        SAFETY.set_mode("real_readonly")
    elif kind == "udp_listen":
        address = str(CONNECTION_CONFIG.get("listenAddress", "0.0.0.0") or "0.0.0.0").strip()
        listen_port = int(CONNECTION_CONFIG.get("listenPort", 14550))
        bind_address = address
        CONNECTION_CONFIG["effectiveListenAddress"] = bind_address
        CONNECTION_CONFIG["effectiveConnection"] = f"udpin:{bind_address}:{listen_port}"
        command = [
            *packaged_entry_command("px6c_connector.py", python),
            "--connection", f"udpin:{bind_address}:{listen_port}",
            "--ui", f"http://127.0.0.1:{PORT}/api/telemetry",
            "--vehicle", "PX6C-01",
        ]
        CONNECTION_PROCESSES = [subprocess.Popen(command, **popen_options)]
        SAFETY.set_mode("real_readonly")
    else:
        raise ValueError("涓嶆敮鎸佺殑杩炴帴鏂瑰紡")
    write_connection_pid_file()
    LOGGER.log_event("connection_started", f"寮€濮嬭繛鎺ワ細{kind}", "system")
    return connection_status()


class Handler(BaseHTTPRequestHandler):
    server_version = "AEROCENTRALGCS/1.0"

    def log_message(self, fmt, *args):
        return

    def has_session_token(self):
        return self.headers.get("X-GCS-Token", "") == SESSION_TOKEN

    def current_session(self):
        return session_from_cookie(self.headers.get("Cookie", ""))

    def role_error(self, required_role):
        session = self.current_session()
        role = session["role"] if session else "guest"
        if role_allowed(role, required_role):
            return None
        return {"error": f"鏉冮檺涓嶈冻锛氶渶瑕?{required_role} 鏉冮檺"}

    def send_json(self, data, status=200, headers=None):
        payload = json_bytes(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("X-App-Version", str(version_payload().get("version", "unknown")))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def read_ulg_upload(self):
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            raise ValueError("Use multipart/form-data to upload log files")
        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": content_type,
            },
        )
        file_item = form["file"] if "file" in form else None
        if file_item is None or not getattr(file_item, "filename", ""):
            raise ValueError("No log file received")
        filename = safe_filename(file_item.filename)
        ULG_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        upload_path = ULG_UPLOAD_DIR / f"{int(time.time())}_{uuid.uuid4().hex[:8]}_{filename}"
        with upload_path.open("wb") as handle:
            while True:
                chunk = file_item.file.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
        if upload_path.stat().st_size < 16:
            raise ValueError("Uploaded log file is empty or invalid")
        fields = {}
        for key in form.keys():
            item = form[key]
            if key == "file" or getattr(item, "filename", None):
                continue
            fields[key] = item.value
        return upload_path, fields

    def read_multi_ulg_upload(self):
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            raise ValueError("Use multipart/form-data to upload log files")
        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": content_type,
            },
        )
        file_items = []
        for key in ("files", "file"):
            if key not in form:
                continue
            item = form[key]
            file_items.extend(item if isinstance(item, list) else [item])
        file_items = [item for item in file_items if getattr(item, "filename", "")]
        if not file_items:
            raise ValueError("No log file received")

        ULG_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        upload_paths = []
        for file_item in file_items:
            filename = safe_filename(file_item.filename)
            upload_path = ULG_UPLOAD_DIR / f"{int(time.time())}_{uuid.uuid4().hex[:8]}_{filename}"
            with upload_path.open("wb") as handle:
                while True:
                    chunk = file_item.file.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
            if upload_path.stat().st_size < 16:
                raise ValueError(f"{filename} is empty or invalid")
            upload_paths.append(upload_path)

        fields = {}
        for key in form.keys():
            item = form[key]
            if key in {"file", "files"}:
                continue
            if isinstance(item, list) or getattr(item, "filename", None):
                continue
            fields[key] = item.value
        return upload_paths, fields

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in {"/api/version", "/version.json"}:
            return self.send_json(version_payload())
        if path == "/api/telemetry":
            return self.send_json(telemetry_payload())
        if path == "/api/telemetry.js":
            data = telemetry_payload()
            script = (
                "window.__receiveTelemetry && window.__receiveTelemetry("
                + json.dumps(data, ensure_ascii=False)
                + ");"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(script)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(script)
            return
        if path == "/api/telemetry/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            with SSE_LOCK:
                SSE_CLIENTS.add(self)
            try:
                initial_packet = f"data: {json.dumps(telemetry_payload(), ensure_ascii=False)}\n\n".encode("utf-8")
                self.wfile.write(initial_packet)
                self.wfile.flush()
                while True:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    time.sleep(15)
            except (BrokenPipeError, ConnectionResetError, OSError):
                with SSE_LOCK:
                    SSE_CLIENTS.discard(self)
            return
        if path == "/api/connection/status":
            return self.send_json(connection_status())
        if path == "/api/rc/status":
            return self.send_json(rc_status_payload())
        if path == "/api/rc/channels":
            return self.send_json(rc_channels_payload())
        if path == "/api/rc/mapping":
            return self.send_json(rc_mapping_payload())
        if path == "/api/rc/full":
            return self.send_json(rc_full_payload())
        if path.startswith("/api/rc/calibration/session/"):
            return self.send_json(get_rc_session(path.rsplit("/", 1)[-1]))
        if path == "/api/aircraft-calibration/overview":
            return self.send_json(aircraft_calibration_overview())
        if path == "/api/aircraft-calibration/status":
            return self.send_json(aircraft_calibration_status())
        if path.startswith("/api/aircraft-calibration/session/"):
            session_id = path.rsplit("/", 1)[-1]
            return self.send_json(get_aircraft_calibration_session(session_id, AIRCRAFT_CALIBRATION_SESSIONS, connector_command_status, STATE))
        if path.startswith("/api/aircraft-calibration/messages/"):
            session_id = path.rsplit("/", 1)[-1]
            return self.send_json(aircraft_calibration_messages(session_id, AIRCRAFT_CALIBRATION_SESSIONS, connector_command_status, STATE))
        if path == "/api/connection/self-check":
            return self.send_json(connection_self_check())
        if path == "/api/flight-modes":
            return self.send_json(flight_mode_options())
        if path == "/api/ai/status":
            return self.send_json(ai_pid_status_payload())
        if path == "/api/ai/usage":
            from services.token_usage import usage_summary
            return self.send_json(usage_summary())
        if path == "/api/ai/cases":
            from services.flight_case_library import list_cases
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            return self.send_json(list_cases({
                "aircraft_type": query.get("aircraft_type", [""])[0],
                "issue": query.get("issue", [""])[0],
                "reviewed": query.get("reviewed", [""])[0],
                "q": query.get("q", [""])[0],
            }))
        if path == "/api/ai/pid/history":
            return self.send_json(ai_pid_history())
        if path == "/api/ai-report/status":
            from services.ai_report_service import ai_report_status
            return self.send_json(ai_report_status())
        if path == "/api/ai-report":
            from services.ai_report_service import get_ai_report
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            return self.send_json(get_ai_report(REPORT_DIR, query.get("id", [""])[0]))
        if path.startswith("/api/ai-report/"):
            from services.ai_report_service import get_ai_report
            report_id = path.rsplit("/", 1)[-1]
            return self.send_json(get_ai_report(REPORT_DIR, report_id))
        if path == "/api/command/status":
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            return self.send_json(connector_command_status(query.get("id", [""])[0]))
        if path == "/api/servo/layout":
            return self.send_json(servo_layout())
        if path == "/api/motor/layout":
            return self.send_json(motor_layout())
        if path == "/api/serial-ports":
            ports = []
            if list_ports:
                ports = [
                    {
                        "device": item.device,
                        "description": item.description,
                        "hwid": item.hwid,
                    }
                    for item in list_ports.comports()
                ]
            return self.send_json(ports)
        if path == "/api/network-interfaces":
            ips = local_ipv4_addresses()
            return self.send_json({
                "listenAny": "0.0.0.0",
                "localIps": ips,
                "recommended": "0.0.0.0",
            })
        if path == "/api/logging/status":
            return self.send_json(LOGGER.status())
        if path == "/api/logging/sessions":
            return self.send_json(LOGGER.list_sessions())
        if path == "/api/logging/session":
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            return self.send_json(LOGGER.session_detail(query.get("name", [""])[0]))
        if path == "/api/preflight/check":
            return self.send_json(preflight_checklist())
        if path == "/api/mission":
            return self.send_json(MISSION)
        if path == "/api/system/status":
            session = self.current_session()
            return self.send_json({
                "connection": connection_status(),
                "logging": LOGGER.status(),
                "mode": SAFETY.mode,
                "sessionToken": SESSION_TOKEN,
                "user": public_user(session),
                "safety": {
                    "parameterWrite": SAFETY.can_write_parameters(STATE).to_dict(),
                    "calibration": SAFETY.can_calibrate(STATE).to_dict(),
                    "servoTest": SAFETY.can_test_servo(STATE).to_dict(),
                    "motorTest": SAFETY.can_test_motor(STATE).to_dict(),
                    "missionUpload": SAFETY.can_upload_mission(STATE, MISSION).to_dict(),
                },
            })
        return self.serve_static(path)

    def do_POST(self):
        global MISSION
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/api/auth/login":
                payload = self.read_json()
                session_id, session = login_user(payload.get("username"), payload.get("password"))
                if not session:
                    return self.send_json({"error": "鐢ㄦ埛鍚嶆垨瀵嗙爜閿欒"}, 401)
                cookie = f"gcs_session={session_id}; Path=/; HttpOnly; SameSite=Strict; Max-Age={8 * 3600}"
                LOGGER.log_event("auth_login", f"{session['username']} 鐧诲綍涓?{session['role']}", "auth")
                return self.send_json({"user": public_user(session)}, headers={"Set-Cookie": cookie})
            if path == "/api/auth/logout":
                session = self.current_session()
                if session:
                    LOGGER.log_event("auth_logout", f"{session['username']} logged out", "auth")
                return self.send_json(
                    {"user": public_user(None)},
                    headers={"Set-Cookie": "gcs_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"},
                )
            if path in DANGEROUS_POST_PATHS and not self.has_session_token():
                return self.send_json({"error": "Dangerous operation missing local session token. Refresh the UI and retry."}, 403)
            required_role = POST_ROLE_REQUIREMENTS.get(path)
            if required_role:
                role_error = self.role_error(required_role)
                if role_error is not None:
                    return self.send_json(role_error, 403)
            if path == "/api/ulg/pid":
                from services.ulg_analyzer import pid_from_ulg
                upload_path, fields = self.read_ulg_upload()
                return self.send_json(pid_from_ulg(
                    upload_path,
                    axis=fields.get("axis", "roll"),
                    symptom=fields.get("symptom", "balanced"),
                    aggressiveness=int(fields.get("aggressiveness", "2") or 2),
                ))
            if path == "/api/ai/pid/analyze" and "multipart/form-data" in self.headers.get("Content-Type", ""):
                upload_path, fields = self.read_ulg_upload()
                return self.send_json(generate_ai_pid_advice_from_log(upload_path, fields), 201)
            if path == "/api/ai-report/generate":
                from services.ai_report_service import generate_ai_report
                upload_path, fields = self.read_ulg_upload()
                options = {
                    "language": fields.get("language", "zh"),
                    "detailLevel": fields.get("detailLevel", "standard"),
                    "audience": fields.get("audience", "engineering"),
                    "includePidAdvice": parse_bool_field(fields, "includePidAdvice"),
                    "modelMode": fields.get("modelMode", "standard_analysis"),
                    "useSimilarCases": parse_bool_field(fields, "useSimilarCases"),
                }
                return self.send_json(generate_ai_report(upload_path, REPORT_DIR, options), 201)
            if path == "/api/ulg/report":
                from services.ulg_analyzer import generate_flight_report
                from services.report_data_builder import build_report_data
                upload_path, fields = self.read_ulg_upload()
                options = {
                    "language": fields.get("language", "zh"),
                    "detailLevel": fields.get("detailLevel", "engineering"),
                    "includeCharts": parse_bool_field(fields, "includeCharts"),
                    "includePid": parse_bool_field(fields, "includePid"),
                    "includeBattery": parse_bool_field(fields, "includeBattery"),
                    "includeGps": parse_bool_field(fields, "includeGps"),
                    "includeSensors": parse_bool_field(fields, "includeSensors"),
                    "includeEvents": parse_bool_field(fields, "includeEvents"),
                    "includeRecommendations": parse_bool_field(fields, "includeRecommendations"),
                    "aircraft_type": fields.get("aircraft_type") or fields.get("aircraftType") or "",
                }
                report = generate_flight_report(upload_path, REPORT_DIR, options)
                report["reportData"] = build_report_data(upload_path, options)
                return self.send_json(report, 201)
            if path == "/api/ulg/compare":
                from services.ulg_analyzer import compare_flight_logs
                upload_paths, _fields = self.read_multi_ulg_upload()
                return self.send_json(compare_flight_logs(upload_paths), 201)
            if path == "/api/ulg/sensor-health":
                from services.ulg_analyzer import sensor_health_reports
                upload_paths, _fields = self.read_multi_ulg_upload()
                return self.send_json(sensor_health_reports(upload_paths), 201)
            payload = self.read_json()
            if path == "/api/telemetry":
                return self.send_json({"ok": True, "state": update_state(payload)}, 202)
            if path == "/api/connection/start":
                return self.send_json(start_connection(payload), 202)
            if path == "/api/connection/stop":
                stop_connection()
                return self.send_json(connection_status())
            if path == "/api/mode":
                SAFETY.set_mode(payload.get("mode", "demo"))
                LOGGER.log_event("safety_mode_changed", f"鍒囨崲鍒?{SAFETY.mode}", "user")
                return self.send_json({"mode": SAFETY.mode})
            if path == "/api/flight-mode":
                return self.send_json(change_flight_mode(payload), 202)
            if path == "/api/arm":
                return self.send_json(arm_vehicle(payload), 202)
            if path == "/api/logging/start":
                return self.send_json(LOGGER.start(payload.get("name", "flight")), 201)
            if path == "/api/logging/stop":
                return self.send_json(LOGGER.stop())
            if path == "/api/logging/export":
                return self.send_json(LOGGER.export_session(payload.get("name", "")))
            if path == "/api/preflight/check":
                return self.send_json(preflight_checklist())
            if path == "/api/mission":
                return self.send_json(save_local_mission(payload.get("waypoints", [])))
            if path == "/api/mission/check":
                return self.send_json(SAFETY.can_upload_mission(STATE, normalize_mission_waypoints(payload.get("waypoints", MISSION))).to_dict())
            if path == "/api/mission/upload":
                return self.send_json(upload_mission_to_flight_controller(payload), 202)
            if path == "/api/mission/clear":
                return self.send_json(clear_flight_mission(payload), 202)
            if path == "/api/mission/read":
                return self.send_json(read_flight_mission(payload), 202)
            if path == "/api/pid/ai":
                return self.send_json(generate_pid_recommendation(payload))
            if path == "/api/ai/pid/analyze":
                return self.send_json(generate_ai_pid_advice(payload))
            if path == "/api/ai/pid/apply":
                return self.send_json(apply_ai_pid(payload), 202)
            if path == "/api/ai/pid/rollback":
                return self.send_json(rollback_ai_pid(payload), 202)
            if path == "/api/ai/cases/review":
                from services.flight_case_library import update_human_review
                return self.send_json(update_human_review(payload.get("case_id", ""), payload.get("human_review") or payload), 202)
            if path == "/api/ai-report/export":
                from services.ai_report_service import export_existing_ai_report
                return self.send_json(export_existing_ai_report(
                    REPORT_DIR,
                    payload.get("report_id", ""),
                    payload.get("format", "docx"),
                ))
            if path == "/api/calibration/start":
                return self.send_json(start_calibration(payload), 202)
            if path == "/api/aircraft-calibration/start":
                return self.send_json(start_aircraft_calibration(payload), 202)
            if path == "/api/aircraft-calibration/cancel":
                return self.send_json(cancel_aircraft_calibration(payload), 202)
            if path == "/api/aircraft-calibration/force-refresh":
                return self.send_json(force_refresh_aircraft_calibration(payload), 202)
            if path == "/api/servo/test":
                return self.send_json(start_servo_test(payload), 202)
            if path == "/api/servo/repair-mapping":
                return self.send_json(repair_servo_mapping(payload), 202)
            if path == "/api/motor/test":
                return self.send_json(start_motor_test(payload), 202)
            if path == "/api/flight-logs/list":
                return self.send_json(request_flight_log_list(payload), 202)
            if path == "/api/flight-logs/download":
                return self.send_json(download_flight_log(payload), 202)
            if path == "/api/parameters/apply":
                return self.send_json(apply_parameter_queue(payload), 202)
            if path == "/api/parameters/request":
                return self.send_json(request_parameter_values(payload), 202)
            if path == "/api/rc/calibration/start":
                return self.send_json(start_rc_calibration(payload), 201)
            if path == "/api/rc/calibration/step":
                return self.send_json(rc_calibration_step(payload), 202)
            if path == "/api/rc/calibration/preview":
                return self.send_json(rc_calibration_preview(payload), 202)
            if path == "/api/rc/calibration/apply":
                return self.send_json(apply_rc_calibration(payload), 202)
            if path == "/api/rc/calibration/restore":
                return self.send_json(restore_rc_calibration(payload), 202)
            if path == "/api/safety/check":
                action = payload.get("action", "")
                checks = {
                    "calibration": lambda: SAFETY.can_calibrate(
                        STATE,
                        payload.get("type", ""),
                        payload.get("confirmation", ""),
                    ),
                    "servo": lambda: SAFETY.can_test_servo(STATE),
                    "motor": lambda: SAFETY.can_test_motor(
                        STATE, confirmation_matches(payload.get("confirmation", ""), PROP_CONFIRMATION_TEXTS)
                    ),
                    "parameters": lambda: SAFETY.can_write_parameters(STATE),
                    "mode": lambda: SAFETY.can_change_mode(STATE),
                    "mission": lambda: SAFETY.can_upload_mission(STATE, MISSION),
                    "rtl": lambda: SAFETY.can_return_to_launch(STATE),
                    "land": lambda: SAFETY.can_land(STATE),
                }
                result = checks.get(action, lambda: None)()
                if result is None:
                    raise ValueError("Unknown safety action")
                return self.send_json(result.to_dict())
            return self.send_json({"error": "API endpoint not found"}, 404)
        except (ValueError, json.JSONDecodeError) as error:
            return self.send_json({"error": str(error)}, 400)
        except Exception as error:
            return self.send_json({"error": str(error)}, 500)

    def static_cache_headers(self, file_path, query, relative=""):
        relative = relative or file_path.name
        suffix = file_path.suffix.lower()
        if file_path.name == "index.html" or suffix == ".html":
            return {
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            }
        is_versioned_request = bool(query.get("v") or query.get("hash"))
        is_asset = relative.startswith(("assets/", "vendor/", "reports/", "downloads/"))
        if is_versioned_request or is_asset:
            return {"Cache-Control": "public, max-age=31536000, immutable"}
        return {
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        }

    def maybe_version_index(self, file_path, content):
        if file_path.name != "index.html":
            return content
        version = version_payload().get("frontendHash", "dev")
        html = content.decode("utf-8", errors="replace")
        html = re.sub(r'(href="styles\.css)(\?v=[^"]*)?(")', rf"\1?v={version}\3", html)
        html = re.sub(r'(src="app\.js)(\?v=[^"]*)?(")', rf"\1?v={version}\3", html)
        return html.encode("utf-8")

    def resolve_static_path(self, relative):
        data_prefixes = ("reports/", "downloads/", "logs/")
        base = DATA_ROOT if relative.startswith(data_prefixes) else ROOT
        base = base.resolve()
        file_path = (base / relative).resolve()
        if base not in file_path.parents and file_path != base:
            return None
        return file_path

    def serve_static(self, request_path):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        relative = "index.html" if request_path == "/" else request_path.lstrip("/")
        file_path = self.resolve_static_path(relative)
        if file_path is None:
            return self.send_error(403)
        if not file_path.is_file():
            return self.send_error(404)
        content = self.maybe_version_index(file_path, file_path.read_bytes())
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        charset = "; charset=utf-8" if content_type in TEXT_CONTENT_TYPES or content_type.startswith("text/") else ""
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}{charset}")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-App-Version", str(version_payload().get("version", "unknown")))
        self.send_header("X-Frontend-Hash", str(version_payload().get("frontendHash", "unknown")))
        for key, value in self.static_cache_headers(file_path, query, relative).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(content)


class ExclusiveThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        return super().server_bind()


def create_http_server():
    candidates = [PORT, 8094, 8095, 8096, 8097]
    seen = set()
    last_error = None
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            return ExclusiveThreadingHTTPServer(("127.0.0.1", candidate), Handler), candidate
        except OSError as error:
            last_error = error
            if getattr(error, "winerror", None) not in {10013, 10048} and getattr(error, "errno", None) not in {13, 48, 98}:
                raise
            print(f"Port {candidate} is busy, trying another port...")
    raise RuntimeError(f"No available UI port found. Last error: {last_error}")


def main():
    server, actual_port = create_http_server()
    url = f"http://127.0.0.1:{actual_port}"
    print(f"AEROCENTRAL Ground Control Station：{url}")
    if os.environ.get("AUTO_OPEN") == "1":
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    threading.Thread(target=monitor_connection, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_connection()
        LOGGER.stop()
        server.server_close()


if __name__ == "__main__":
    main()

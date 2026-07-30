import argparse
import json
import math
import time
import urllib.request

try:
    from pymavlink import mavutil
except ImportError:
    raise SystemExit(
        "缺少 pymavlink。请运行：python -m pip install pymavlink"
    )


def post_json(url, payload):
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        response.read()


def main():
    parser = argparse.ArgumentParser(description="MAVLink to UAV dashboard bridge")
    parser.add_argument(
        "--connection",
        default="udpin:0.0.0.0:14550",
        help="例如 COM3，或 udpin:0.0.0.0:14550",
    )
    parser.add_argument("--baud", type=int, default=57600)
    parser.add_argument(
        "--ui",
        default="http://127.0.0.1:8080/api/telemetry",
    )
    parser.add_argument("--vehicle", default="UAV-01")
    args = parser.parse_args()

    print(f"连接 MAVLink：{args.connection}")
    master = mavutil.mavlink_connection(
        args.connection,
        baud=args.baud,
        autoreconnect=True,
    )
    master.wait_heartbeat(timeout=30)
    print(f"已收到心跳：system={master.target_system}")

    state = {
        "vehicleId": args.vehicle,
        "connected": True,
        "mode": "UNKNOWN",
        "armed": False,
        "lat": None,
        "lon": None,
        "alt": None,
        "relativeAlt": None,
        "roll": None,
        "pitch": None,
        "yaw": None,
        "speed": None,
        "airspeed": None,
        "climb": None,
        "heading": None,
        "battery": None,
        "voltage": None,
        "current": None,
        "satellites": None,
        "fixType": None,
        "lastMessage": None,
        "warnings": [],
    }
    last_post = 0.0
    message_counts = {}
    message_last_seen = {}
    recent_messages = []
    rate_window = []

    def remember_message(message_type):
        now_stamp = time.monotonic()
        message_counts[message_type] = message_counts.get(message_type, 0) + 1
        message_last_seen[message_type] = now_stamp
        rate_window.append((now_stamp, message_type))
        del rate_window[:-500]
        if not recent_messages or recent_messages[0] != message_type:
            recent_messages.insert(0, message_type)
            del recent_messages[24:]
        state["lastMessage"] = message_type
        state["messageType"] = message_type

    def message_stats():
        now_stamp = time.monotonic()
        rates = {}
        for stamp, name in rate_window:
            if now_stamp - stamp <= 1.0:
                rates[name] = rates.get(name, 0) + 1
        return {
            "counts": message_counts,
            "rates": rates,
            "lastSeen": {
                name: round(max(0.0, now_stamp - stamp), 2)
                for name, stamp in message_last_seen.items()
            },
            "recent": recent_messages,
        }

    while True:
        message = master.recv_match(blocking=True, timeout=1)
        if message is None:
            continue

        message_type = message.get_type()
        remember_message(message_type)
        if message_type == "HEARTBEAT":
            state["connected"] = True
            try:
                state["mode"] = mavutil.mode_string_v10(message)
            except Exception:
                state["mode"] = "UNKNOWN"
            state["armed"] = bool(message.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
        elif message_type == "ATTITUDE":
            state["roll"] = math.degrees(float(message.roll))
            state["pitch"] = math.degrees(float(message.pitch))
            state["yaw"] = (math.degrees(float(message.yaw)) + 360) % 360
        elif message_type == "GLOBAL_POSITION_INT":
            state["lat"] = message.lat / 1e7
            state["lon"] = message.lon / 1e7
            state["alt"] = message.alt / 1000
            state["relativeAlt"] = message.relative_alt / 1000
            state["climb"] = message.vz / -100
            state["heading"] = None if message.hdg == 65535 else message.hdg / 100
        elif message_type == "VFR_HUD":
            state["speed"] = float(message.groundspeed)
            state["airspeed"] = float(message.airspeed)
            state["climb"] = float(message.climb)
            state["heading"] = float(message.heading)
        elif message_type == "SYS_STATUS":
            state["battery"] = None if message.battery_remaining < 0 else int(message.battery_remaining)
            state["voltage"] = None if message.voltage_battery == 65535 else float(message.voltage_battery) / 1000
            state["current"] = None if message.current_battery == -1 else float(message.current_battery) / 100
        elif message_type == "BATTERY_STATUS":
            if getattr(message, "battery_remaining", -1) >= 0:
                state["battery"] = int(message.battery_remaining)
        elif message_type == "GPS_RAW_INT":
            state["satellites"] = int(message.satellites_visible)
            state["fixType"] = int(message.fix_type)
            if getattr(message, "eph", 65535) != 65535:
                state["eph"] = float(message.eph) / 100
        elif message_type == "RADIO_STATUS":
            state["rssi"] = min(100, round(int(message.rssi) / 255 * 100))
        elif message_type == "STATUSTEXT":
            text = str(getattr(message, "text", "")).strip()
            if text:
                state["warnings"] = ([text] + state.get("warnings", []))[:12]
        else:
            if time.monotonic() - last_post < 0.5:
                continue

        now = time.monotonic()
        if now - last_post < 0.1:
            continue

        try:
            state["messageStats"] = message_stats()
            post_json(args.ui, state)
            last_post = now
        except Exception as error:
            print(f"推送 UI 失败：{error}")
            time.sleep(1)


if __name__ == "__main__":
    main()

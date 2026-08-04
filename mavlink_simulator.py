import argparse
import math
import time

from pymavlink import mavutil


def main():
    parser = argparse.ArgumentParser(description="UAV MAVLink GPS simulator")
    parser.add_argument("--connection", default="udpout:127.0.0.1:14550")
    parser.add_argument("--lat", type=float, default=31.2304)
    parser.add_argument("--lon", type=float, default=121.4737)
    parser.add_argument("--vehicle", type=int, default=1)
    args = parser.parse_args()

    link = mavutil.mavlink_connection(
        args.connection,
        source_system=args.vehicle,
        source_component=1,
    )
    boot_time = time.monotonic()
    print(f"MAVLink simulator sending to {args.connection}")

    while True:
        elapsed = time.monotonic() - boot_time
        angle = elapsed / 18.0
        lat = args.lat + math.sin(angle) * 0.0012
        lon = args.lon + math.cos(angle) * 0.0015
        relative_alt = 85.0 + math.sin(angle * 1.7) * 12.0
        ground_speed = 13.5 + math.sin(angle * 2.1) * 2.5
        heading = int((math.degrees(angle) + 90) % 360)
        battery = max(20, 92 - int(elapsed / 45))
        time_boot_ms = int(elapsed * 1000) & 0xFFFFFFFF

        link.mav.heartbeat_send(
            mavutil.mavlink.MAV_TYPE_QUADROTOR,
            mavutil.mavlink.MAV_AUTOPILOT_PX4,
            mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
            3 << 16,
            mavutil.mavlink.MAV_STATE_ACTIVE,
        )
        link.mav.gps_raw_int_send(
            int(time.time() * 1_000_000),
            3,
            int(lat * 1e7),
            int(lon * 1e7),
            int((relative_alt + 30) * 1000),
            80,
            120,
            int(ground_speed * 100),
            int(heading * 100),
            18,
        )
        link.mav.global_position_int_send(
            time_boot_ms,
            int(lat * 1e7),
            int(lon * 1e7),
            int((relative_alt + 30) * 1000),
            int(relative_alt * 1000),
            int(math.cos(angle) * ground_speed * 100),
            int(math.sin(angle) * ground_speed * 100),
            0,
            int(heading * 100),
        )
        link.mav.vfr_hud_send(
            ground_speed + 1.2,
            ground_speed,
            heading,
            55,
            relative_alt,
            math.cos(angle) * 0.8,
        )
        link.mav.attitude_send(
            time_boot_ms,
            math.radians(math.sin(angle) * 8),
            math.radians(math.cos(angle) * 5),
            math.radians(heading),
            0.02,
            0.01,
            0.05,
        )
        link.mav.extended_sys_state_send(
            mavutil.mavlink.MAV_VTOL_STATE_UNDEFINED,
            mavutil.mavlink.MAV_LANDED_STATE_IN_AIR,
        )
        link.mav.sys_status_send(
            0,
            0,
            0,
            500,
            16000,
            420,
            battery,
            0,
            0,
            0,
            0,
            0,
            0,
        )
        link.mav.radio_status_send(230, 220, 96, 25, 18, 0, 0)
        time.sleep(0.5)


if __name__ == "__main__":
    main()

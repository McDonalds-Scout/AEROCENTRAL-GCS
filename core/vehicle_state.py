from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import math
from typing import Any


@dataclass
class VehicleState:
    connected: bool = False
    armed: bool = False
    flight_mode: str = "UNKNOWN"
    vehicle_type: str = "UNKNOWN"
    vehicle_id: str = "PX6C-01"
    autopilot: str = "PX4"
    hardware: str = "Pixhawk 6C"
    roll_deg: float | None = None
    pitch_deg: float | None = None
    yaw_deg: float | None = None
    attitude_time_ms: int | None = None
    roll_rate_degps: float | None = None
    pitch_rate_degps: float | None = None
    yaw_rate_degps: float | None = None
    altitude_m: float | None = None
    relative_altitude_m: float | None = None
    vertical_speed_mps: float | None = None
    ground_speed_mps: float | None = None
    airspeed_mps: float | None = None
    latitude: float | None = None
    longitude: float | None = None
    heading_deg: float | None = None
    gps_fix_type: int | None = None
    satellites_visible: int | None = None
    hdop: float | None = None
    battery_voltage_v: float | None = None
    battery_current_a: float | None = None
    battery_remaining_percent: int | None = None
    rc_signal: int | None = None
    rc_signal_raw: int | None = None
    rc_update_time_ms: int | None = None
    rc_source: str | None = None
    rc_channels: list[int] = field(default_factory=list)
    rc_throttle_pwm: int | None = None
    rc_throttle_percent: float | None = None
    rc_throttle_source: str | None = None
    manual_control: dict[str, Any] = field(default_factory=dict)
    manual_control_time_ms: int | None = None
    target_system: int | None = None
    target_component: int | None = None
    target_identified: bool = False
    vehicle_autopilot: int | None = None
    vehicle_base_mode: int | None = None
    vehicle_custom_mode: int | None = None
    vehicle_mavlink_version: int | None = None
    vehicle_heartbeat_at: int | None = None
    gcs_heartbeat: dict[str, Any] = field(default_factory=dict)
    rc_raw_channels: list[Any] = field(default_factory=list)
    rc_mapped: dict[str, Any] = field(default_factory=dict)
    rc_map: dict[str, Any] = field(default_factory=dict)
    rc_map_available: bool = False
    rc_issues: list[str] = field(default_factory=list)
    telemetry_signal: int | None = None
    servo_outputs: list[int] = field(default_factory=list)
    motor_outputs: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    statustexts: list[dict[str, Any]] = field(default_factory=list)
    command_acks: list[dict[str, Any]] = field(default_factory=list)
    calibration: dict[str, Any] = field(default_factory=dict)
    home_position: dict[str, float] | None = None
    parameters: dict[str, float] = field(default_factory=dict)
    mission_items: list[dict[str, Any]] = field(default_factory=list)
    system_status: int | None = None
    landed_state: int | None = None
    last_message: str | None = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @classmethod
    def from_payload(cls, payload: dict[str, Any], previous=None):
        state = cls(**asdict(previous)) if previous else cls()
        aliases = {
            "vehicleId": "vehicle_id",
            "mode": "flight_mode",
            "roll": "roll_deg",
            "pitch": "pitch_deg",
            "yaw": "yaw_deg",
            "attitudeTimeMs": "attitude_time_ms",
            "rollRate": "roll_rate_degps",
            "pitchRate": "pitch_rate_degps",
            "yawRate": "yaw_rate_degps",
            "alt": "altitude_m",
            "relativeAlt": "relative_altitude_m",
            "climb": "vertical_speed_mps",
            "speed": "ground_speed_mps",
            "airspeed": "airspeed_mps",
            "lat": "latitude",
            "lon": "longitude",
            "heading": "heading_deg",
            "fixType": "gps_fix_type",
            "satellites": "satellites_visible",
            "eph": "hdop",
            "voltage": "battery_voltage_v",
            "current": "battery_current_a",
            "battery": "battery_remaining_percent",
            "rssi": "telemetry_signal",
            "rcSignal": "rc_signal",
            "rcSignalRaw": "rc_signal_raw",
            "rcUpdateTimeMs": "rc_update_time_ms",
            "rcSource": "rc_source",
            "rcChannels": "rc_channels",
            "rcThrottlePwm": "rc_throttle_pwm",
            "rcThrottlePercent": "rc_throttle_percent",
            "rcThrottleSource": "rc_throttle_source",
            "manualControl": "manual_control",
            "manualControlTimeMs": "manual_control_time_ms",
            "targetSystem": "target_system",
            "targetComponent": "target_component",
            "targetIdentified": "target_identified",
            "vehicleAutopilot": "vehicle_autopilot",
            "vehicleBaseMode": "vehicle_base_mode",
            "vehicleCustomMode": "vehicle_custom_mode",
            "vehicleMavlinkVersion": "vehicle_mavlink_version",
            "vehicleHeartbeatAt": "vehicle_heartbeat_at",
            "gcsHeartbeat": "gcs_heartbeat",
            "rcRawChannels": "rc_raw_channels",
            "rcMapped": "rc_mapped",
            "rcMap": "rc_map",
            "rcMapAvailable": "rc_map_available",
            "rcIssues": "rc_issues",
            "statustexts": "statustexts",
            "commandAcks": "command_acks",
            "calibration": "calibration",
            "systemStatus": "system_status",
            "landedState": "landed_state",
            "lastMessage": "last_message",
            "homeLat": "home_position",
        }
        for key, value in payload.items():
            target = aliases.get(key, key)
            if key == "homeLat" and value is not None:
                state.home_position = {
                    "latitude": value,
                    "longitude": payload.get("homeLon"),
                    "altitude": payload.get("homeAlt"),
                }
                continue
            if hasattr(state, target) and value is not None:
                setattr(state, target, value)
        state.timestamp = datetime.now(timezone.utc).isoformat()
        return state

    def to_dict(self):
        data = asdict(self)
        data.update(
            {
                "vehicleId": self.vehicle_id,
                "mode": self.flight_mode,
                "roll": self.roll_deg,
                "pitch": self.pitch_deg,
                "yaw": self.yaw_deg,
                "attitudeTimeMs": self.attitude_time_ms,
                "rollRate": self.roll_rate_degps,
                "pitchRate": self.pitch_rate_degps,
                "yawRate": self.yaw_rate_degps,
                "alt": self.altitude_m,
                "relativeAlt": self.relative_altitude_m,
                "climb": self.vertical_speed_mps,
                "speed": self.ground_speed_mps,
                "airspeed": self.airspeed_mps,
                "lat": self.latitude,
                "lon": self.longitude,
                "heading": self.heading_deg,
                "fixType": self.gps_fix_type,
                "satellites": self.satellites_visible,
                "eph": self.hdop,
                "voltage": self.battery_voltage_v,
                "current": self.battery_current_a,
                "battery": self.battery_remaining_percent,
                "rssi": self.telemetry_signal,
                "rcSignal": self.rc_signal,
                "rcSignalRaw": self.rc_signal_raw,
                "rcUpdateTimeMs": self.rc_update_time_ms,
                "rcSource": self.rc_source,
                "rcChannels": self.rc_channels,
                "rcThrottlePwm": self.rc_throttle_pwm,
                "rcThrottlePercent": self.rc_throttle_percent,
                "rcThrottleSource": self.rc_throttle_source,
                "manualControl": self.manual_control,
                "manualControlTimeMs": self.manual_control_time_ms,
                "targetSystem": self.target_system,
                "targetComponent": self.target_component,
                "targetIdentified": self.target_identified,
                "vehicleAutopilot": self.vehicle_autopilot,
                "vehicleBaseMode": self.vehicle_base_mode,
                "vehicleCustomMode": self.vehicle_custom_mode,
                "vehicleMavlinkVersion": self.vehicle_mavlink_version,
                "vehicleHeartbeatAt": self.vehicle_heartbeat_at,
                "gcsHeartbeat": self.gcs_heartbeat,
                "rcRawChannels": self.rc_raw_channels,
                "rcMapped": self.rc_mapped,
                "rcMap": self.rc_map,
                "rcMapAvailable": self.rc_map_available,
                "rcIssues": self.rc_issues,
                "statustexts": self.statustexts,
                "commandAcks": self.command_acks,
                "calibration": self.calibration,
                "systemStatus": self.system_status,
                "landedState": self.landed_state,
                "lastMessage": self.last_message,
            }
        )
        def sanitize(value):
            if isinstance(value, float) and not math.isfinite(value):
                return None
            if isinstance(value, dict):
                return {key: sanitize(item) for key, item in value.items()}
            if isinstance(value, list):
                return [sanitize(item) for item in value]
            return value
        return sanitize(data)

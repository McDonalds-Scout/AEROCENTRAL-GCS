from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable


REAL_MAVLINK_TYPES = {
    "gyro": "gyro",
    "accel": "accelerometer",
    "compass": "magnetometer",
    "level_horizon": "level",
    "airspeed": "airspeed",
}


CATALOG = {
    "gyro": {
        "title": "陀螺仪校准",
        "english": "Gyroscope Calibration",
        "mavlink": True,
        "mock": True,
        "status_basis": ["COMMAND_ACK", "STATUSTEXT"],
        "conditions": ["飞控已连接", "飞机未解锁", "飞机保持静止", "螺旋桨已拆除"],
        "instructions": [
            "把飞机放在稳定、水平、不晃动的台面上。",
            "点击开始后不要移动飞机，等待 PX4 返回完成提示。",
            "校准过程中避免桌面、电机或地面振动。",
            "确认螺旋桨已拆除，并且飞机处于未解锁状态。",
        ],
    },
    "accel": {
        "title": "加速度计校准",
        "english": "Accelerometer Calibration",
        "mavlink": True,
        "mock": True,
        "status_basis": ["COMMAND_ACK", "STATUSTEXT"],
        "conditions": ["飞控已连接", "飞机未解锁", "按 PX4 六面姿态提示操作"],
        "poses": ["水平正放", "左侧朝下", "右侧朝下", "机头朝下", "机头朝上", "倒置"],
        "instructions": [
            "根据 PX4 提示依次摆放飞机的六个姿态。",
            "每个姿态保持稳定，直到 PX4 接受该姿态。",
            "采样时不要晃动机架，也不要用手持续抖动飞机。",
        ],
    },
    "compass": {
        "title": "磁罗盘校准",
        "english": "Compass Calibration",
        "mavlink": True,
        "mock": True,
        "status_basis": ["COMMAND_ACK", "STATUSTEXT", "MAG_CAL_PROGRESS", "MAG_CAL_REPORT"],
        "conditions": ["飞控已连接", "飞机未解锁", "远离金属和强磁干扰"],
        "instructions": [
            "远离金属桌、手机、电脑、磁铁和大电流线缆。",
            "按 PX4 提示绕各个方向缓慢旋转飞机。",
            "如果提示磁干扰，换到更空旷的位置后重新校准。",
        ],
    },
    "level_horizon": {
        "title": "水平姿态校准",
        "english": "Level Horizon Calibration",
        "mavlink": True,
        "mock": True,
        "status_basis": ["COMMAND_ACK", "STATUSTEXT"],
        "conditions": ["飞控已连接", "飞机未解锁", "放在期望的水平飞行姿态"],
        "instructions": [
            "把飞机放在你希望作为水平基准的姿态上。",
            "四旋翼通常放在水平、稳定的起降姿态。",
            "校准过程中保持机架稳定，不要移动。",
        ],
    },
    "airspeed": {
        "title": "空速计校准",
        "english": "Airspeed Calibration",
        "mavlink": True,
        "mock": True,
        "status_basis": ["COMMAND_ACK", "STATUSTEXT", "VFR_HUD/airspeed", "airspeed parameters"],
        "conditions": ["检测到空速数据或相关参数", "飞机保持静止", "空速管无气流"],
        "instructions": [
            "保持飞机静止，避免空速管进气或被风吹到。",
            "按 PX4 提示遮挡或保持空速管静止。",
            "不要在风扇、桨流或强风环境下校准。",
        ],
    },
    "power": {
        "title": "电源 / 电池诊断",
        "english": "Power / Battery Diagnostics",
        "mavlink": False,
        "mock": True,
        "status_basis": ["SYS_STATUS", "BATTERY_STATUS", "parameters"],
        "conditions": ["只读诊断", "不会自动写入电压/电流比例参数"],
        "instructions": [
            "当前阶段只显示电压、电流和数据可信度。",
            "后续如果写入电池比例参数，必须提供万用表实测值并显示参数差异预览。",
        ],
    },
    "actuator": {
        "title": "执行器测试安全",
        "english": "Actuator Test Safety",
        "mavlink": False,
        "mock": True,
        "status_basis": ["SERVO_OUTPUT_RAW", "safety confirmation"],
        "conditions": ["默认只读", "测试模式关闭", "AI 不能触发输出"],
        "instructions": [
            "本页只显示执行器输出和安全限制。",
            "真实执行器输出请使用专门的手动测试页面。",
            "任何主动输出都必须拆除螺旋桨并进行二次确认。",
        ],
    },
    "motor": {
        "title": "电机测试安全",
        "english": "Motor Test Safety",
        "mavlink": False,
        "mock": True,
        "status_basis": ["SERVO_OUTPUT_RAW", "safety confirmation"],
        "conditions": ["默认只读", "不会转动电机", "AI 不能触发输出"],
        "instructions": [
            "本页不会让电机转动。",
            "电机测试必须拆除螺旋桨，并确保飞机未解锁。",
            "真实电机测试请使用专门的电机测试入口。",
        ],
    },
}


MOCK_SCENARIOS = {
    "gyro_success": ("gyro", "completed", ["陀螺仪校准已开始", "保持飞机静止", "陀螺仪校准完成"]),
    "gyro_vehicle_moved": ("gyro", "failed", ["陀螺仪校准已开始", "校准失败：飞机发生移动"]),
    "accel_success": ("accel", "completed", ["请水平放置飞机", "请左侧朝下", "请右侧朝下", "请机头朝下", "请机头朝上", "请倒置飞机", "加速度计校准完成"]),
    "accel_failed": ("accel", "failed", ["请水平放置飞机", "校准失败：检测到移动"]),
    "compass_success": ("compass", "completed", ["请绕各方向旋转飞机", "磁罗盘进度 35%", "磁罗盘进度 80%", "磁罗盘校准完成"]),
    "compass_interference": ("compass", "failed", ["请绕各方向旋转飞机", "校准失败：检测到磁干扰"]),
    "level_success": ("level_horizon", "completed", ["水平姿态校准已开始", "水平姿态校准完成"]),
    "airspeed_not_available": ("airspeed", "rejected", ["未检测到空速传感器"]),
    "airspeed_success": ("airspeed", "completed", ["空速计校准已开始", "空速计校准完成"]),
    "ack_timeout": ("gyro", "timeout", ["校准命令已发送", "COMMAND_ACK 超时"]),
    "statustext_only": ("compass", "running", ["等待飞控返回校准提示"]),
    "vehicle_armed": ("gyro", "rejected", ["飞机已解锁，校准被拒绝"]),
    "no_heartbeat": ("gyro", "rejected", ["未收到飞控心跳，校准被拒绝"]),
    "power_readonly": ("power", "completed", ["电源诊断已就绪", "电压/电流仅只读显示", "没有修改任何电池参数"]),
    "actuator_readonly": ("actuator", "completed", ["执行器安全框架已就绪", "测试模式保持关闭", "没有发送执行器输出"]),
    "motor_readonly": ("motor", "completed", ["电机安全框架已就绪", "电机输出保持禁用", "没有发送电机命令"]),
}

CALIBRATION_LABELS = {
    "gyro": "陀螺仪",
    "accelerometer": "加速度计",
    "accel": "加速度计",
    "magnetometer": "磁罗盘",
    "compass": "磁罗盘",
    "level": "水平姿态",
    "level_horizon": "水平姿态",
    "airspeed": "空速计",
    "radio": "遥控器",
    "esc": "电调",
}

FINAL_SESSION_STATUSES = {"completed", "failed", "rejected", "timeout", "expired", "cancelled"}
ACTIVE_SESSION_STATUSES = {"queued", "started", "running", "accepted", "partial", "sent_no_ack"}
CALIBRATION_ACTIVITY_WINDOW_MS = 180_000
COMMAND_HANDOFF_WINDOW_MS = 10_000


def _calibration_label(calibration_type: str) -> str:
    config = CATALOG.get(calibration_type, {})
    return CALIBRATION_LABELS.get(calibration_type) or config.get("title") or calibration_type or "校准"


def _translate_px4_text(text: Any) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    lower = value.lower()
    replacements = [
        ("Preflight: GPS Speed Accuracy too low", "飞前检查：GPS 速度精度过低"),
        ("Preflight Fail: vertical velocity unstable", "飞前检查失败：垂直速度不稳定"),
        ("Preflight: GPS Horizontal Pos Error too high", "飞前检查：GPS 水平位置误差过大"),
        ("Preflight: GPS Horizontal Pos Drift too high", "飞前检查：GPS 水平位置漂移过大"),
        ("Preflight Fail: No connection to the ground contro", "飞前检查失败：地面站连接中断"),
        ("Preflight Fail: No connection to the ground control station", "飞前检查失败：地面站连接中断"),
        ("Preflight Fail: Found 0 compass (required: 1)", "飞前检查失败：未检测到磁罗盘，当前无法进行磁罗盘校准"),
        ("Preflight Fail: Missing FMU SD Card", "飞前检查失败：飞控未检测到 FMU SD 卡"),
        ("Preflight Fail: Airspeed invalid", "飞前检查失败：空速数据无效"),
        ("Preflight Fail: Attitude failure (roll)", "飞前检查失败：横滚姿态异常，请先完成传感器校准并保持静止"),
        ("Preflight Fail: Attitude failure (pitch)", "飞前检查失败：俯仰姿态异常，请先完成传感器校准并保持静止"),
        ("Preflight Fail: Accel 0 inconsistent - check cal", "飞前检查失败：加速度计 0 数据不一致，请重新完成加速度计校准"),
        ("command denied during calibration", "飞控拒绝命令：当前已有校准正在进行，请先完成当前校准"),
        ("GCS connection regained", "地面站连接已恢复"),
        ("command is queued locally; waiting for connector to send it to the flight controller", "命令已提交到连接程序，正在发送给飞控"),
        ("COMMAND_ACK timeout", "等待飞控命令确认超时"),
        ("calibration command sent to flight controller; waiting for COMMAND_ACK", "校准命令已发送到飞控，正在等待命令确认"),
        ("Flight controller accepted gyro calibration command; follow PX4 prompts", "飞控已确认陀螺仪校准命令，请保持飞机静止并等待完成提示"),
        ("Flight controller accepted accelerometer calibration command; follow PX4 prompts", "飞控已确认加速度计校准命令，请按 PX4 提示摆放飞机"),
        ("Flight controller accepted compass calibration command; follow PX4 prompts", "飞控已确认磁罗盘校准命令，请按 PX4 提示旋转飞机"),
        ("Flight controller accepted level horizon calibration command; follow PX4 prompts", "飞控已确认水平姿态校准命令，请保持飞机稳定"),
        ("Flight controller accepted airspeed calibration command; follow PX4 prompts", "飞控已确认空速计校准命令，请按 PX4 提示操作"),
        ("calibration command was not confirmed by flight controller", "校准命令未被飞控确认"),
        ("Compass calibration accepted; follow PX4 rotate prompts", "飞控已确认磁罗盘校准，请按 PX4 提示旋转飞机"),
        ("Compass calibration did not report progress", "磁罗盘校准未返回进度"),
        ("Compass calibration still running or no final report", "磁罗盘校准仍在进行或尚未返回最终报告"),
        ("[cal] hold vehicle still on a pending side", "校准提示：请把飞机保持在当前待采样姿态，不要晃动"),
        ("[cal] detected rest position", "校准提示：飞控检测到静止姿态，请继续保持不动"),
        ("[cal] orientation detected", "校准提示：飞控已识别当前方向，请继续保持稳定"),
        ("[cal] rotate to a different orientation", "当前姿态采样完成，请换到下一个未完成姿态"),
        ("rotate to a different side", "当前姿态采样完成，请换到下一个未完成姿态"),
        ("rotate to a different orientation", "当前姿态采样完成，请换到下一个未完成姿态"),
        ("[cal] down side done", "下方朝下姿态已完成，请换到下一个未完成姿态"),
        ("[cal] up side done", "上方朝下姿态已完成，请换到下一个未完成姿态"),
        ("[cal] left side done", "左侧朝下姿态已完成，请换到下一个未完成姿态"),
        ("[cal] right side done", "右侧朝下姿态已完成，请换到下一个未完成姿态"),
        ("[cal] front side done", "机头朝下姿态已完成，请换到下一个未完成姿态"),
        ("[cal] back side done", "机尾朝下姿态已完成，请换到下一个未完成姿态"),
        ("all sides complete", "六面姿态采样完成，等待飞控保存结果"),
        ("vehicle moved", "校准失败：飞机移动过大，请重新开始并保持静止"),
        ("[cal] detected", "校准提示：飞控已识别当前姿态，请继续保持稳定"),
        ("[cal] rotate vehicle", "校准提示：请按飞控要求缓慢旋转飞机"),
        ("[cal] calibration done", "校准完成"),
        ("[cal] calibration failed", "校准失败"),
        ("MAG_CAL_PROGRESS", "磁罗盘校准进度"),
        ("MAG_CAL_REPORT SUCCESS", "磁罗盘校准完成"),
    ]
    for source, target in replacements:
        if source.lower() in lower:
            return target
    if "[cal] pending:" in lower:
        side_names = {
            "back": "机尾朝下",
            "front": "机头朝下",
            "left": "左侧朝下",
            "right": "右侧朝下",
            "up": "上方朝下",
            "down": "下方朝下",
        }
        pending_raw = value.split(":", 1)[-1].strip()
        pending = [side_names.get(item, item) for item in pending_raw.split()]
        return f"待完成姿态：{'、'.join(pending)}"
    if "[cal] progress" in lower:
        progress = value.split("<")[-1].split(">")[0] if "<" in value and ">" in value else ""
        return f"校准进度：{progress}%" if progress else "校准正在进行"
    if "calibration progress" in lower:
        progress = value.split("progress:")[-1].split("/")[0].strip() if "progress:" in lower else ""
        return f"校准进度：{progress}" if progress else "校准正在进行"
    if "[cal] calibration started" in lower or "calibration started" in lower:
        if "gyro" in lower:
            return "陀螺仪校准已开始，请保持飞机静止"
        if "accel" in lower:
            return "加速度计校准已开始，请按飞控提示摆放飞机"
        if "mag" in lower or "compass" in lower:
            return "磁罗盘校准已开始，请按飞控提示旋转飞机"
        return "校准已开始，请按飞控提示操作"
    if "calibration done" in lower or "calibration complete" in lower or "calibration successful" in lower:
        return "校准完成"
    if "calibration failed" in lower or "cal failed" in lower:
        return f"校准失败：{value}"
    return value


def _format_command_result(result: dict[str, Any], calibration_type: str) -> str:
    if not isinstance(result, dict):
        return _translate_px4_text(result)
    result_type = str(result.get("type") or calibration_type or "")
    label = _calibration_label(result_type)
    ack = result.get("ack") or {}
    if isinstance(ack, dict):
        ack_text = str(ack.get("resultText") or ack.get("result") or "").strip()
        if ack_text:
            if ack_text in {"ACCEPTED", "IN_PROGRESS"}:
                return f"飞控已确认{label}校准命令：{ack_text}"
            return f"{label}校准命令未被飞控确认：{ack_text}"
    if result.get("progress") is not None:
        return f"{label}校准进度：{result.get('progress')}%"
    if result.get("text"):
        return _translate_px4_text(result.get("text"))
    return f"{label}校准命令状态已更新"


def _append_unique_message(session: "AircraftCalibrationSession", text: str, level: str = "info", source: str = "飞机校准"):
    clean_text = _translate_px4_text(text)
    if not clean_text:
        return
    if any(existing.get("text") == clean_text and existing.get("source") == source for existing in session.messages[-30:]):
        return
    session.add_message(clean_text, level, source)


def _explicit_calibration_type_from_text(raw_text: str) -> str | None:
    lower = str(raw_text or "").lower()
    if any(token in lower for token in ["gyro", "gyroscope", "陀螺"]):
        return "gyro"
    if any(token in lower for token in ["accel", "accelerometer", "加速度"]):
        return "accel"
    if any(token in lower for token in ["mag_cal", "magnetometer", "compass", "磁罗盘", "罗盘"]):
        return "compass"
    if any(token in lower for token in ["airspeed", "空速"]):
        return "airspeed"
    if any(token in lower for token in ["level horizon", "水平姿态"]):
        return "level_horizon"
    return None


def _text_looks_like_accel_calibration(raw_text: str) -> bool:
    lower = str(raw_text or "").lower()
    return any(token in lower for token in [
        "[cal] pending:",
        "pending side",
        "orientation detected",
        "detected rest position",
        "hold still",
        "measuring",
        "side result",
        "side done",
        "already completed",
        "rotate to a different side",
        "back front left right up down",
    ])


def _statustext_applies_to_session(session: "AircraftCalibrationSession", item: dict[str, Any]) -> bool:
    text = str(item.get("text", ""))
    item_time = int(item.get("timeMs") or item.get("time_ms") or 0)
    if item_time and item_time < session.created_at - 1_500:
        return False
    explicit_type = _explicit_calibration_type_from_text(text)
    if explicit_type:
        return explicit_type == session.calibration_type
    if _text_looks_like_accel_calibration(text):
        return session.calibration_type == "accel"
    lower = text.lower()
    if "[cal] progress" in lower:
        return session.status in ACTIVE_SESSION_STATUSES
    if "command denied during calibration" in lower or "calibration failed" in lower or "cal failed" in lower:
        return session.status in {"running", "accepted", "partial", "sent_no_ack"}
    if "calibration done" in lower or "calibration complete" in lower or "calibration successful" in lower:
        return session.status in {"running", "accepted", "partial"}
    return False


def _update_session_from_px4_text(session: "AircraftCalibrationSession", raw_text: str):
    lower = str(raw_text or "").lower()
    if not ("[cal]" in lower or "calibration" in lower or "calibrate" in lower):
        return
    translated = _translate_px4_text(raw_text)
    if "progress" in lower or "calibration started" in lower or "[cal]" in lower:
        if session.status not in FINAL_SESSION_STATUSES:
            session.status = "running"
            session.reason = translated or f"PX4 正在执行{_calibration_label(session.calibration_type)}校准"
    if any(token in lower for token in ["calibration done", "calibration complete", "calibration successful", "calibration succeeded"]):
        session.status = "completed"
        session.reason = translated or f"{_calibration_label(session.calibration_type)}校准完成"
    if any(token in lower for token in ["calibration failed", "cal failed", "calibration error"]):
        session.status = "failed"
        session.reason = translated or f"{_calibration_label(session.calibration_type)}校准失败"


@dataclass
class AircraftCalibrationSession:
    id: str
    calibration_type: str
    mode: str = "real"
    status: str = "started"
    reason: str = ""
    command_id: str | None = None
    created_at: int = field(default_factory=lambda: int(time.time() * 1000))
    updated_at: int = field(default_factory=lambda: int(time.time() * 1000))
    safety_checks: list[dict[str, Any]] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)

    def add_message(self, text: str, level: str = "info", source: str = "飞机校准"):
        self.updated_at = int(time.time() * 1000)
        self.messages.append({
            "timeMs": self.updated_at,
            "level": level,
            "source": source,
            "text": text,
        })
        self.messages = self.messages[-120:]

    def to_dict(self):
        config = CATALOG.get(self.calibration_type, {})
        return {
            "session_id": self.id,
            "calibration_type": self.calibration_type,
            "title": config.get("title", self.calibration_type),
            "mode": self.mode,
            "status": self.status,
            "reason": self.reason,
            "commandId": self.command_id,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "safety_checks": self.safety_checks,
            "messages": self.messages,
        }


def _state_dict(state) -> dict[str, Any]:
    return state.to_dict() if hasattr(state, "to_dict") else dict(state or {})


def _status_level(status: str) -> str:
    return {
        "calibrated": "green",
        "available": "green",
        "running": "blue",
        "started": "blue",
        "queued": "blue",
        "accepted": "blue",
        "partial": "blue",
        "required": "yellow",
        "warning": "yellow",
        "sent_no_ack": "yellow",
        "failed": "red",
        "rejected": "red",
        "timeout": "red",
        "not_available": "grey",
        "unknown": "grey",
    }.get(status, "grey")


def _recent_text(state: dict[str, Any], keywords: list[str]) -> dict[str, Any] | None:
    items = state.get("statustexts") or state.get("statusTexts") or []
    for item in reversed(items):
        text = str(item.get("text", ""))
        lower = text.lower()
        if any(keyword.lower() in lower for keyword in keywords):
            return item
    return None


def _calibration_status(calibration_type: str, state: dict[str, Any]) -> tuple[str, str]:
    if calibration_type == "airspeed":
        if _airspeed_available(state):
            return "available", "已检测到空速数据或相关参数"
        return "not_available", "未检测到空速数据或相关参数"
    if calibration_type == "power":
        voltage = state.get("voltage") or state.get("battery_voltage_v")
        current = state.get("current") or state.get("battery_current_a")
        if voltage is None:
            return "unknown", "等待电池电压数据"
        if current is None or abs(float(current or 0)) < 0.05:
            return "warning", "电流数据缺失或接近 0，建议检查电源模块"
        return "available", "电压/电流数据可用于只读诊断"
    if calibration_type in {"actuator", "motor"}:
        return "available", "只读安全框架可用，不会主动发送输出"
    cal = state.get("calibration") or {}
    if cal.get("type") in {calibration_type, REAL_MAVLINK_TYPES.get(calibration_type)}:
        if cal.get("running"):
            return "running", f"PX4 正在上报校准进度 {cal.get('progress', '--')}%"
        status_text = str(cal.get("statusText") or "")
        if status_text == "SUCCESS":
            return "calibrated", "PX4 已上报校准成功"
        if status_text:
            return "warning", f"PX4 校准状态：{status_text}"
    recent = _recent_text(state, [calibration_type, CATALOG[calibration_type]["english"], CATALOG[calibration_type]["title"]])
    if recent:
        text = recent.get("text", "")
        if any(token in text.lower() for token in ["fail", "failed", "denied", "澶辫触", "鎷掔粷"]):
            return "failed", _translate_px4_text(text)
        if any(token in text.lower() for token in ["complete", "success", "done", "瀹屾垚", "鎴愬姛"]):
            return "calibrated", _translate_px4_text(text)
        return "warning", _translate_px4_text(text)
    return "unknown", "暂时无法从飞控数据判断校准状态"


def _airspeed_available(state: dict[str, Any]) -> bool:
    airspeed = state.get("airspeed") if "airspeed" in state else state.get("airspeed_mps")
    if isinstance(airspeed, (int, float)) and airspeed >= -1:
        return True
    parameters = state.get("parameters") or {}
    return any(str(name).startswith(("ASPD_", "FW_ARSP", "CAL_AIR")) for name in parameters)


def _power_diagnostics(state: dict[str, Any]) -> dict[str, Any]:
    voltage = state.get("voltage") if "voltage" in state else state.get("battery_voltage_v")
    current = state.get("current") if "current" in state else state.get("battery_current_a")
    battery = state.get("battery") if "battery" in state else state.get("battery_remaining_percent")
    warnings = []
    voltage_valid = isinstance(voltage, (int, float)) and voltage > 3
    current_valid = isinstance(current, (int, float)) and abs(current) > 0.05
    if not voltage_valid:
        warnings.append("电压数据不可用或可信度不足")
    if not current_valid:
        warnings.append("电流数据缺失或接近 0，电流传感器可能需要检查")
    return {
        "voltage": voltage,
        "current": current,
        "batteryPercent": battery,
        "voltageValid": voltage_valid,
        "currentValid": current_valid,
        "warnings": warnings,
    }


def _active_px4_calibration(state: dict[str, Any]) -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    items = state.get("statustexts") or state.get("statusTexts") or []
    latest_activity = None
    latest_terminal = None
    for item in items:
        text = str(item.get("text", ""))
        lower = text.lower()
        item_time = int(item.get("timeMs") or item.get("time_ms") or 0)
        if item_time and now_ms - item_time > CALIBRATION_ACTIVITY_WINDOW_MS:
            continue
        if "[cal]" not in lower and "calibration" not in lower and "calibrate" not in lower:
            continue
        if any(token in lower for token in ["calibration done", "calibration complete", "calibration successful", "calibration failed", "cal failed"]):
            latest_terminal = item
        else:
            latest_activity = item
    if latest_activity and (
        not latest_terminal
        or int(latest_activity.get("timeMs") or 0) > int(latest_terminal.get("timeMs") or 0)
    ):
        return {
            "active": True,
            "text": _translate_px4_text(latest_activity.get("text", "")),
            "timeMs": latest_activity.get("timeMs"),
        }
    cal = state.get("calibration") or {}
    if cal.get("running"):
        return {
            "active": True,
            "text": f"PX4 正在上报校准进度 {cal.get('progress', '--')}%",
            "timeMs": cal.get("timeMs"),
        }
    return {"active": False}


def build_overview(state, safety_manager=None, command_mode: str = "demo") -> dict[str, Any]:
    data = _state_dict(state)
    active_calibration = _active_px4_calibration(data)
    cards = []
    for key, config in CATALOG.items():
        status, reason = _calibration_status(key, data)
        executable = bool(config.get("mavlink")) and status != "not_available"
        if not data.get("connected"):
            executable = False
        if data.get("armed"):
            executable = False
        if not data.get("targetIdentified") and not data.get("target_identified"):
            executable = False
        if active_calibration.get("active"):
            executable = False
        cards.append({
            "type": key,
            "title": config["title"],
            "english": config["english"],
            "status": status,
            "level": _status_level(status),
            "reason": reason,
            "executable": executable,
            "mavlink": bool(config.get("mavlink")),
            "mock": bool(config.get("mock")),
            "conditions": config.get("conditions", []),
            "instructions": config.get("instructions", []),
            "poses": config.get("poses", []),
            "statusBasis": config.get("status_basis", []),
        })
    return {
        "connected": bool(data.get("connected")),
        "armed": bool(data.get("armed")),
        "mode": data.get("mode") or data.get("flight_mode") or "--",
        "targetSystem": data.get("targetSystem") or data.get("target_system"),
        "targetComponent": data.get("targetComponent") or data.get("target_component"),
        "targetIdentified": bool(data.get("targetIdentified") or data.get("target_identified")),
        "gcsHeartbeat": data.get("gcsHeartbeat") or data.get("gcs_heartbeat") or {},
        "latestCommandAck": (data.get("commandAcks") or data.get("command_acks") or [])[-1:] or [],
        "latestStatusText": (data.get("statustexts") or [])[-1:] or [],
        "power": _power_diagnostics(data),
        "activeCalibration": active_calibration,
        "commandMode": command_mode,
        "cards": cards,
        "catalog": CATALOG,
    }


def safety_checks(state, confirmation: str, calibration_type: str) -> list[dict[str, Any]]:
    data = _state_dict(state)
    gcs = data.get("gcsHeartbeat") or data.get("gcs_heartbeat") or {}
    confirmation_text = str(confirmation or "")
    confirmed = any(token in confirmation_text for token in [
        "确认安全",
        "已确认安全",
        "已拆除螺旋桨",
        "螺旋桨已拆除",
        "已拆桨",
        "拆除螺旋桨",
    ]) or "safe" in confirmation_text.lower()
    checks = [
        ("vehicle_connected", "飞控已连接", bool(data.get("connected")), "飞控未连接或遥测数据已超时"),
        ("vehicle_heartbeat", "飞控心跳", bool(data.get("vehicleHeartbeatAt") or data.get("vehicle_heartbeat_at")), "未收到飞控 HEARTBEAT"),
        ("target_identified", "目标系统已识别", bool(data.get("targetIdentified") or data.get("target_identified")), "尚未识别 target_system / target_component"),
        ("gcs_heartbeat", "地面站心跳", bool(gcs.get("sending")), "GCS HEARTBEAT 未发送"),
        ("disarmed", "飞机未解锁", not bool(data.get("armed")), "飞机已解锁，禁止校准"),
        ("not_flying", "地面状态", data.get("landedState") in (None, 1, "1"), "飞控状态可能不是地面静止"),
        ("confirmation", "安全确认", confirmed, "请输入确认文本：已确认安全"),
    ]
    if calibration_type in {"motor", "actuator"}:
        checks.append(("readonly", "默认只读", False, "该模块不会主动输出，请使用专门的手动测试页面"))
    return [
        {"key": key, "label": label, "ok": bool(ok), "reason": "" if ok else reason}
        for key, label, ok, reason in checks
    ]


def start_session(
    payload: dict[str, Any],
    state,
    safety_manager,
    sessions: dict[str, AircraftCalibrationSession],
    enqueue_command: Callable[[dict[str, Any]], dict[str, Any]],
    logger=None,
) -> dict[str, Any]:
    calibration_type = str(payload.get("calibration_type") or payload.get("type") or "").strip()
    mock = bool(payload.get("mock"))
    confirmation = str(payload.get("confirmation") or "").strip()
    if calibration_type not in CATALOG:
        return {"status": "failed", "reason": "未知飞机校准类型", "safety_checks": []}

    if mock:
        scenario = payload.get("mockScenario") or _default_mock_scenario(calibration_type)
        session = _mock_session(scenario, calibration_type)
        sessions[session.id] = session
        return {"session_id": session.id, "status": session.status, "reason": session.reason, "safety_checks": session.safety_checks, "session": session.to_dict()}

    checks = safety_checks(state, confirmation, calibration_type)
    failed = [item for item in checks if not item["ok"]]
    if failed:
        session = AircraftCalibrationSession(
            id=uuid.uuid4().hex,
            calibration_type=calibration_type,
            status="rejected",
            reason=failed[0]["reason"],
            safety_checks=checks,
        )
        session.add_message(session.reason, "warning", "安全门")
        sessions[session.id] = session
        return {"session_id": session.id, "status": "rejected", "reason": session.reason, "safety_checks": checks, "session": session.to_dict()}

    active_px4 = _active_px4_calibration(_state_dict(state))
    now_ms = int(time.time() * 1000)
    active_sessions = [
        item for item in sessions.values()
        if item.status in {"queued", "started", "sent_no_ack"}
        and now_ms - item.updated_at < COMMAND_HANDOFF_WINDOW_MS
    ]
    if active_px4.get("active") or active_sessions:
        reason = active_px4.get("text") or "PX4 正在执行另一个校准，请先按提示完成当前校准，或等待其失败/超时后再重试。"
        session = AircraftCalibrationSession(
            id=uuid.uuid4().hex,
            calibration_type=calibration_type,
            status="rejected",
            reason=reason,
            safety_checks=checks,
        )
        session.add_message(reason, "warning", "飞机校准")
        sessions[session.id] = session
        return {"session_id": session.id, "status": "rejected", "reason": reason, "safety_checks": checks, "session": session.to_dict()}

    if calibration_type in {"power", "actuator", "motor"}:
        session = AircraftCalibrationSession(
            id=uuid.uuid4().hex,
            calibration_type=calibration_type,
            status="rejected",
            reason="该模块是只读诊断/安全框架，不会主动发送校准或输出命令",
            safety_checks=checks,
        )
        session.add_message(session.reason, "info", "飞机校准")
        sessions[session.id] = session
        return {"session_id": session.id, "status": "rejected", "reason": session.reason, "safety_checks": checks, "session": session.to_dict()}

    connector_type = REAL_MAVLINK_TYPES[calibration_type]
    safety = safety_manager.can_calibrate(state, connector_type, "确认安全")
    if not safety.allowed:
        session = AircraftCalibrationSession(
            id=uuid.uuid4().hex,
            calibration_type=calibration_type,
            status="rejected",
            reason=safety.reason,
            safety_checks=checks,
        )
        session.add_message(safety.reason, "warning", "安全管理器")
        sessions[session.id] = session
        return {"session_id": session.id, "status": "rejected", "reason": safety.reason, "safety_checks": checks, "session": session.to_dict()}

    packet = enqueue_command({
        "command": "calibrate",
        "type": connector_type,
        "label": CATALOG[calibration_type]["title"],
    })
    session = AircraftCalibrationSession(
        id=uuid.uuid4().hex,
        calibration_type=calibration_type,
        status="queued",
        reason=f"{CATALOG[calibration_type]['title']}命令已提交到连接程序，正在发送到 PX4",
        command_id=packet["id"],
        safety_checks=checks,
    )
    session.add_message("命令已提交到连接程序，正在等待 PX4 返回 COMMAND_ACK / STATUSTEXT。", source="飞机校准")
    sessions[session.id] = session
    if logger:
        logger.log_event("aircraft_calibration_started", f"{calibration_type}:{packet['id']}", "calibration")
    return {"session_id": session.id, "status": "queued", "reason": session.reason, "safety_checks": checks, "commandId": packet["id"], "session": session.to_dict()}


def refresh_session(session: AircraftCalibrationSession, command_status: Callable[[str], dict[str, Any]], state=None) -> AircraftCalibrationSession:
    if session.command_id:
        status = command_status(session.command_id)
        if status and status.get("status") not in {"missing", ""}:
            status_name = status.get("status", session.status)
            if session.status not in FINAL_SESSION_STATUSES:
                session.status = status_name
            session.reason = _translate_px4_text(status.get("message") or session.reason)
            session.updated_at = int(status.get("updatedAt") or time.time() * 1000)
            results = status.get("results") or []
            if results:
                _append_unique_message(session, _format_command_result(results[-1], session.calibration_type), "info", "命令确认")
    if state is not None:
        data = _state_dict(state)
        for item in (data.get("statustexts") or [])[-30:]:
            if not _statustext_applies_to_session(session, item):
                continue
            text = str(item.get("text", ""))
            if text:
                _append_unique_message(session, text, str(item.get("severity", "info")).lower(), "飞控文本")
                _update_session_from_px4_text(session, text)
    return session


def get_session(session_id: str, sessions: dict[str, AircraftCalibrationSession], command_status, state=None) -> dict[str, Any]:
    session = sessions.get(session_id)
    if not session:
        return {"status": "missing", "reason": "未找到飞机校准会话"}
    refresh_session(session, command_status, state)
    return {"status": session.status, "session": session.to_dict()}


def session_messages(session_id: str, sessions: dict[str, AircraftCalibrationSession], command_status, state=None) -> dict[str, Any]:
    result = get_session(session_id, sessions, command_status, state)
    session = result.get("session") or {}
    return {"status": result.get("status"), "messages": session.get("messages", [])}


def cancel_session(session_id: str, sessions: dict[str, AircraftCalibrationSession]) -> dict[str, Any]:
    session = sessions.get(session_id)
    if not session:
        return {"status": "missing", "reason": "未找到飞机校准会话"}
    session.status = "cancelled"
    session.reason = "用户已取消校准会话。如果 PX4 已经开始内部校准，请继续按 PX4 提示完成，或在 QGC/PX4 中确认状态。"
    session.add_message(session.reason, "warning", "飞机校准")
    return {"status": session.status, "session": session.to_dict()}


def _default_mock_scenario(calibration_type: str) -> str:
    return {
        "gyro": "gyro_success",
        "accel": "accel_success",
        "compass": "compass_success",
        "level_horizon": "level_success",
        "airspeed": "airspeed_success",
        "power": "power_readonly",
        "actuator": "actuator_readonly",
        "motor": "motor_readonly",
    }.get(calibration_type, "gyro_success")


def _mock_session(scenario: str, fallback_type: str) -> AircraftCalibrationSession:
    calibration_type, status, messages = MOCK_SCENARIOS.get(scenario, (fallback_type, "completed", ["模拟校准完成"]))
    session = AircraftCalibrationSession(
        id=uuid.uuid4().hex,
        calibration_type=calibration_type,
        mode="mock",
        status=status,
        reason="模拟模式：未使用真实飞控，仅用于界面测试。",
        safety_checks=[{"key": "mock", "label": "模拟模式", "ok": True, "reason": ""}],
    )
    for message in messages:
        level = "error" if any(word in message.lower() for word in ["fail", "timeout", "denied"]) else "info"
        session.add_message(message, level, "模拟")
    return session

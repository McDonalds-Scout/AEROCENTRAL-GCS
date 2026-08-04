from dataclasses import dataclass


PROP_CONFIRMATION_TEXTS = {
    "已拆除螺旋桨",
    "已拆除桨叶",
    "确认已拆除螺旋桨",
    "确认已拆桨",
}

SAFE_CONFIRMATION_TEXTS = {
    "已确认安全",
    "确认安全",
    "确认已确认安全",
}

PROP_CONFIRMATION_TEXTS.update({
    "已拆除螺旋桨",
    "已拆桨",
    "确认已拆除螺旋桨",
    "确认已拆桨",
})

SAFE_CONFIRMATION_TEXTS.update({
    "已确认安全",
    "确认安全",
    "确认已确认安全",
})


def confirmation_matches(value, accepted):
    text = str(value or "").strip()
    return text in accepted


@dataclass(frozen=True)
class SafetyResult:
    allowed: bool
    reason: str

    def to_dict(self):
        return {"allowed": self.allowed, "reason": self.reason}


class SafetyManager:
    MODES = {"demo", "simulation", "real_readonly", "real_command"}

    def __init__(self, mode="demo"):
        self.mode = mode

    def set_mode(self, mode):
        if mode not in self.MODES:
            raise ValueError("无效运行模式")
        self.mode = mode

    def _command_mode(self, action):
        if self.mode != "real_command":
            return SafetyResult(False, f"无法{action}：当前不是实机指令模式")
        return SafetyResult(True, "安全模式检查通过")

    def can_arm(self, state):
        result = self._command_mode("解锁")
        if not result.allowed:
            return result
        if not state.connected:
            return SafetyResult(False, "无法解锁：飞控未连接或链路已超时")
        if (state.gps_fix_type or 0) < 3:
            return SafetyResult(False, "无法解锁：GPS 未定位")
        return SafetyResult(True, "允许解锁，仍需用户二次确认")

    def can_disarm(self, state):
        return self._command_mode("上锁")

    def can_test_motor(self, state, propellers_removed=False):
        result = self._command_mode("进行电机测试")
        if not result.allowed:
            return result
        if not state.connected:
            return SafetyResult(False, "无法进行电机测试：飞控未连接或链路已超时")
        if state.armed:
            return SafetyResult(False, "无法进行电机测试：飞控当前已解锁")
        if not propellers_removed:
            return SafetyResult(False, "无法进行电机测试：请先输入“已拆除螺旋桨”")
        return SafetyResult(True, "允许电机测试，输出仍受安全上限限制")

    def can_test_servo(self, state):
        result = self._command_mode("进行舵机测试")
        if not result.allowed:
            return result
        if not state.connected:
            return SafetyResult(False, "无法进行舵机测试：飞控未连接或链路已超时")
        if state.armed:
            return SafetyResult(False, "无法进行舵机测试：飞控当前已解锁")
        return SafetyResult(True, "允许舵机测试，仍需用户二次确认")

    def can_write_parameters(self, state):
        result = self._command_mode("写入参数")
        if not result.allowed:
            return result
        if not state.connected:
            return SafetyResult(False, "无法写入参数：飞控未连接或链路已超时")
        if state.armed:
            return SafetyResult(False, "无法写入参数：飞控当前已解锁")
        return SafetyResult(True, "允许写入参数，仍需逐项确认")

    def can_calibrate(self, state, calibration_type="", confirmation=""):
        result = self._command_mode("执行飞机校准")
        if not result.allowed:
            return result
        if not state.connected:
            return SafetyResult(False, "无法校准：飞控未连接")
        if state.armed:
            return SafetyResult(False, "无法校准：飞控当前已解锁")

        if calibration_type == "esc":
            if confirmation_matches(confirmation, PROP_CONFIRMATION_TEXTS):
                return SafetyResult(True, "允许执行电调校准，发送前仍需二次确认")
            return SafetyResult(False, "无法执行电调校准：请先输入“已拆除螺旋桨”")

        if confirmation_matches(confirmation, SAFE_CONFIRMATION_TEXTS):
            return SafetyResult(True, "允许执行校准，发送前仍需二次确认")
        return SafetyResult(False, "无法校准：请输入“已确认安全”")

    def can_upload_mission(self, state, mission):
        result = self._command_mode("上传任务")
        if not result.allowed:
            return result
        if not state.connected:
            return SafetyResult(False, "无法上传任务：飞控未连接")
        if (state.gps_fix_type or 0) < 3:
            return SafetyResult(False, "无法上传任务：GPS 未定位")
        if not state.home_position:
            return SafetyResult(False, "无法上传任务：尚未获得 Home 点")
        if state.battery_remaining_percent is not None and state.battery_remaining_percent < 25:
            return SafetyResult(False, "无法上传任务：电池电量过低")
        if not mission:
            return SafetyResult(False, "无法上传任务：任务中没有航点")
        if not any(item.get("command") in {"LAND", "RTL", "降落", "返航"} for item in mission):
            return SafetyResult(False, "无法上传任务：任务中缺少降落或返航指令")
        return SafetyResult(True, "任务安全检查通过，仍需用户确认")

    def can_clear_mission(self, state):
        return self._command_mode("清空飞控任务")

    def can_change_mode(self, state):
        result = self._command_mode("更改飞行模式")
        if not result.allowed:
            return result
        if not state.connected:
            return SafetyResult(False, "无法切换飞行模式：飞控未连接或链路已超时")
        return SafetyResult(True, "允许切换飞行模式，命令将发送到飞控")

    def can_return_to_launch(self, state):
        return self._command_mode("返航")

    def can_land(self, state):
        return self._command_mode("降落")

    def can_emergency_stop(self, state):
        return self._command_mode("紧急停止")

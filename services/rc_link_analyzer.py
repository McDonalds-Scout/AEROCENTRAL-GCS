from __future__ import annotations

import math
import time
from statistics import pstdev
from typing import Any


RC_WARNING_KEYWORDS = (
    "rc",
    "radio",
    "manual control",
    "manual_control",
    "receiver",
    "failsafe",
    "datalink",
    "throttle",
    "arm",
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _finite_number(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number)


def _pwm(value: Any) -> int | None:
    if not _finite_number(value):
        return None
    number = int(round(float(value)))
    return number if 800 <= number <= 2200 else None


def _valid_channels(channels: list[Any] | None) -> list[int]:
    return [value for value in (_pwm(item) for item in channels or []) if value is not None]


def _mapped_channel(mapped: dict[str, Any] | None, key: str) -> int | None:
    try:
        channel = int((mapped or {}).get(key, {}).get("channel") or 0)
    except (TypeError, ValueError):
        return None
    return channel if 1 <= channel <= 18 else None


def _mapped_pwm(channels: list[Any] | None, mapped: dict[str, Any] | None, key: str) -> int | None:
    channel = _mapped_channel(mapped, key)
    if not channel or not channels or channel > len(channels):
        return None
    return _pwm(channels[channel - 1])


def _near_mid(value: int | None, deadband: int = 120) -> bool | None:
    if value is None:
        return None
    return abs(value - 1500) <= deadband


def _switch_stable(history: list[dict[str, Any]], mapped: dict[str, Any] | None, key: str) -> bool | None:
    channel = _mapped_channel(mapped, key)
    if not channel:
        return None
    values: list[int] = []
    cutoff = _now_ms() - 1800
    for sample in history:
        if int(sample.get("timeMs") or 0) < cutoff:
            continue
        channels = sample.get("channels") or []
        if channel <= len(channels):
            value = _pwm(channels[channel - 1])
            if value is not None:
                values.append(value)
    if len(values) < 4:
        return None
    buckets = [round(value / 100) for value in values]
    transitions = sum(1 for left, right in zip(buckets, buckets[1:]) if left != right)
    return transitions <= 3


def _jitter_detected(history: list[dict[str, Any]]) -> bool:
    cutoff = _now_ms() - 1200
    recent = [item for item in history if int(item.get("timeMs") or 0) >= cutoff]
    if len(recent) < 8:
        return False
    for index in range(18):
        values: list[int] = []
        for sample in recent:
            channels = sample.get("channels") or []
            if index < len(channels):
                value = _pwm(channels[index])
                if value is not None:
                    values.append(value)
        if len(values) >= 8:
            span = max(values) - min(values)
            if 10 <= span <= 80 and pstdev(values) >= 12:
                return True
    return False


def _latest_rc_warning(statustexts: list[dict[str, Any]] | None, warnings: list[str] | None) -> str | None:
    candidates: list[str] = []
    for item in reversed(statustexts or []):
        text = str(item.get("text") or "")
        if any(keyword in text.lower() for keyword in RC_WARNING_KEYWORDS):
            candidates.append(text)
            break
    if not candidates:
        for item in reversed(warnings or []):
            text = str(item or "")
            if any(keyword in text.lower() for keyword in RC_WARNING_KEYWORDS):
                candidates.append(text)
                break
    return candidates[0] if candidates else None


def analyze_rc_link(
    state: dict[str, Any],
    message_stats: dict[str, Any] | None = None,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    history = history or []
    message_stats = message_stats or {}
    rates = message_stats.get("rates") or {}
    ages = message_stats.get("ages") or {}
    now = _now_ms()

    raw_channels = state.get("rcRawChannels") or state.get("rcChannels") or []
    valid_channels = _valid_channels(raw_channels)
    mapped = state.get("rcMapped") or {}
    connected = bool(state.get("connected"))
    rc_update_time_ms = state.get("rcUpdateTimeMs")
    rc_age_ms = None
    if _finite_number(rc_update_time_ms):
        rc_age_ms = max(0, now - int(float(rc_update_time_ms)))
    elif _finite_number(ages.get("RC_CHANNELS")):
        rc_age_ms = int(float(ages["RC_CHANNELS"]) * 1000)

    rc_rate = None
    if _finite_number(rates.get("RC_CHANNELS")):
        rc_rate = round(float(rates["RC_CHANNELS"]), 1)

    rssi_raw = state.get("rcSignalRaw")
    rssi_percent = state.get("rcSignal")
    if rssi_percent is None and _finite_number(rssi_raw):
        rssi_percent = round(max(0, min(100, float(rssi_raw) / 255 * 100)), 1)

    received_rc = bool(valid_channels) and rc_age_ms is not None
    rc_lost = bool(connected and (not received_rc or rc_age_ms > 3000))
    rc_stale = bool(connected and received_rc and rc_age_ms > 1000)
    rc_connected = bool(connected and received_rc and rc_age_ms <= 1000)

    if not connected:
        status = "unknown"
        status_text = "Unknown"
        signal_quality = "unknown"
        level = "grey"
    elif rc_lost:
        status = "lost"
        status_text = "RC Lost"
        signal_quality = "lost"
        level = "red"
    elif rc_stale:
        status = "stale"
        status_text = "RC Stale"
        signal_quality = "weak"
        level = "yellow"
    elif rc_connected:
        weak = (
            (rssi_percent is not None and _finite_number(rssi_percent) and float(rssi_percent) < 35)
            or (rc_rate is not None and rc_rate < 8)
        )
        status = "connected"
        status_text = "RC Connected"
        signal_quality = "weak" if weak else "normal"
        level = "yellow" if weak else "green"
    else:
        status = "unknown"
        status_text = "Unknown"
        signal_quality = "unknown"
        level = "grey"

    throttle_pwm = _mapped_pwm(raw_channels, mapped, "throttle")
    roll_mid = _near_mid(_mapped_pwm(raw_channels, mapped, "roll"))
    pitch_mid = _near_mid(_mapped_pwm(raw_channels, mapped, "pitch"))
    yaw_mid = _near_mid(_mapped_pwm(raw_channels, mapped, "yaw"))
    throttle_low = None if throttle_pwm is None else throttle_pwm <= 1120
    mode_switch_stable = _switch_stable(history, mapped, "flightMode")
    arm_switch_stable = _switch_stable(history, mapped, "armSwitch")
    jitter = _jitter_detected(history)
    latest_warning = _latest_rc_warning(state.get("statustexts"), state.get("warnings"))
    manual_available = bool(state.get("manualControl")) and bool(state.get("manualControlTimeMs"))

    warnings: list[str] = []
    if not connected:
        warnings.append("未连接飞控，RC 链路状态不可用")
    elif rc_lost:
        warnings.append("RC Lost：超过 3 秒没有收到 RC_CHANNELS")
    elif not received_rc:
        warnings.append("未收到 RC_CHANNELS")
        warnings.append("QGC 正常但本 UI 未收到 RC_CHANNELS，请检查后端是否请求 RC_CHANNELS 消息频率、是否正确解析 MAVLink、是否连接到正确链路。")
    elif rc_stale:
        warnings.append("RC signal stale：超过 1 秒没有 RC_CHANNELS 更新")
    if rssi_percent is not None and _finite_number(rssi_percent) and float(rssi_percent) < 35:
        warnings.append("RC Weak：RSSI 较低")
    if throttle_low is False:
        warnings.append("Throttle not low：油门未在最低位")
    if arm_switch_stable is False:
        warnings.append("Arm switch unstable：解锁开关不稳定")
    if mode_switch_stable is False:
        warnings.append("Mode switch unstable：飞行模式开关不稳定")
    if jitter:
        warnings.append("RC channel jitter detected：通道存在抖动")
    if latest_warning:
        warnings.append(latest_warning)
    if rc_connected and not warnings:
        warnings.append("RC 信号正常")

    return {
        "activeFixedProfile": "default",
        "rc_connected": rc_connected,
        "rc_lost": rc_lost,
        "rc_stale": rc_stale,
        "rc_status": status,
        "rc_status_text": status_text,
        "ui_level": level,
        "rc_update_rate_hz": rc_rate,
        "rc_last_update_age_ms": rc_age_ms,
        "rc_last_update_time_ms": rc_update_time_ms,
        "rc_channel_count": len(valid_channels),
        "rc_rssi_raw": rssi_raw,
        "rc_rssi_percent": rssi_percent,
        "rc_signal_quality": signal_quality,
        "rc_valid": bool(rc_connected and len(valid_channels) >= 4),
        "rc_source": state.get("rcSource") or ("RC_CHANNELS" if received_rc else ("MANUAL_CONTROL" if manual_available else "unavailable")),
        "rc_map_status": "received" if state.get("rcMapAvailable") else "unavailable",
        "mapping_confidence": "yes" if state.get("rcMapAvailable") and len(valid_channels) >= 4 else ("partial" if len(valid_channels) else "no"),
        "manual_control_available": manual_available,
        "rc_jitter_detected": jitter,
        "mode_switch_stable": mode_switch_stable,
        "arm_switch_stable": arm_switch_stable,
        "throttle_low": throttle_low,
        "roll_centered": roll_mid,
        "pitch_centered": pitch_mid,
        "yaw_centered": yaw_mid,
        "failsafe": {
            "rc_lost": rc_lost,
            "manual_control_lost": bool(latest_warning and "manual" in latest_warning.lower() and "lost" in latest_warning.lower()),
            "datalink_lost": bool(latest_warning and "datalink" in latest_warning.lower() and "lost" in latest_warning.lower()),
        },
        "latest_rc_warning": latest_warning,
        "warnings": warnings,
    }

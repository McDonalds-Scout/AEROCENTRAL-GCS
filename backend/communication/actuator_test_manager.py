"""PX4 actuator, servo, and motor test helpers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pymavlink import mavutil

from backend.communication.command_queue import is_ack_timeout, write_command_status
from backend.communication.command_sender import wait_for_command_ack
from backend.communication.message_bus import MessageBus


MAV_CMD_ACTUATOR_TEST = getattr(mavutil.mavlink, "MAV_CMD_ACTUATOR_TEST", 310)
PX4_MOTOR_OUTPUT_FUNCTION_BASE = 101
PX4_SERVO_OUTPUT_FUNCTION_BASE = 33
ACCEPTED_RESULTS = {"ACCEPTED", "IN_PROGRESS"}


def mavlink_servo_test_function(channel: int, output_function: Any = None) -> int:
    if output_function not in (None, ""):
        value = int(output_function)
        if 201 <= value <= 208:
            return 33 + (value - 201)
        if 33 <= value <= 40:
            return value
    return PX4_SERVO_OUTPUT_FUNCTION_BASE + int(channel) - 1


def mavlink_motor_test_function(motor: int, output_function: Any = None) -> int:
    if output_function not in (None, ""):
        value = int(output_function)
        if 101 <= value <= 112:
            return value - 100
        if 1 <= value <= 12:
            return value
    return int(motor)


def send_px4_actuator_test(
    master: Any,
    channel: int,
    pwm: int,
    timeout_s: float = 2.0,
    output_function: Any = None,
    *,
    ack_history: list[dict[str, Any]] | None = None,
    result_names: dict[int, str] | None = None,
) -> dict[str, Any]:
    normalized = max(-1.0, min(1.0, (int(pwm) - 1500) / 500))
    test_function = mavlink_servo_test_function(channel, output_function)
    bus = MessageBus.for_master(master)
    cursor = bus.current_sequence() if bus is not None else None
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
    return wait_for_command_ack(
        master,
        MAV_CMD_ACTUATOR_TEST,
        timeout=1.0,
        ack_history=ack_history,
        result_names=result_names,
        cursor=cursor,
    )


def send_px4_motor_actuator_test(
    master: Any,
    motor: int,
    throttle_percent: float,
    timeout_s: float = 2.0,
    output_function: Any = None,
    *,
    ack_history: list[dict[str, Any]] | None = None,
    result_names: dict[int, str] | None = None,
) -> dict[str, Any]:
    normalized = max(0.0, min(1.0, float(throttle_percent) / 100))
    test_function = mavlink_motor_test_function(motor, output_function)
    bus = MessageBus.for_master(master)
    cursor = bus.current_sequence() if bus is not None else None
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
    return wait_for_command_ack(
        master,
        MAV_CMD_ACTUATOR_TEST,
        timeout=1.0,
        ack_history=ack_history,
        result_names=result_names,
        cursor=cursor,
    )


def send_legacy_servo(
    master: Any,
    channel: int,
    pwm: int,
    *,
    ack_history: list[dict[str, Any]] | None = None,
    result_names: dict[int, str] | None = None,
) -> dict[str, Any]:
    bus = MessageBus.for_master(master)
    cursor = bus.current_sequence() if bus is not None else None
    master.mav.command_long_send(
        master.target_system,
        master.target_component,
        mavutil.mavlink.MAV_CMD_DO_SET_SERVO,
        0,
        int(channel),
        int(pwm),
        0,
        0,
        0,
        0,
        0,
    )
    return wait_for_command_ack(
        master,
        mavutil.mavlink.MAV_CMD_DO_SET_SERVO,
        timeout=0.8,
        ack_history=ack_history,
        result_names=result_names,
        cursor=cursor,
    )


def send_servo_outputs(
    master: Any,
    outputs: list[dict[str, Any]],
    command_id: str | None = None,
    *,
    ack_history: list[dict[str, Any]] | None = None,
    result_names: dict[int, str] | None = None,
) -> None:
    write_command_status(
        command_id,
        status="running",
        message="Servo test command is being sent to the flight controller",
        results=[],
    )
    results = []
    for item in outputs or []:
        channel = int(item.get("channel", 0))
        pwm = int(item.get("pwm", 1500))
        output_function = item.get("outputFunction")
        if not 1 <= channel <= 16:
            print(f"Ignoring invalid servo channel: {channel}")
            continue
        pwm = max(800, min(2200, pwm))
        actuator_ack = send_px4_actuator_test(
            master,
            channel,
            pwm,
            output_function=output_function,
            ack_history=ack_history,
            result_names=result_names,
        )
        method = "PX4_ACTUATOR_TEST"
        ack = actuator_ack
        if actuator_ack["resultText"] not in ACCEPTED_RESULTS:
            legacy_ack = send_legacy_servo(
                master,
                channel,
                pwm,
                ack_history=ack_history,
                result_names=result_names,
            )
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
        print(f"Servo test command sent: channel={channel}, pwm={pwm}, method={method}, ack={ack['resultText']}")

    accepted = any(item["ack"]["resultText"] in ACCEPTED_RESULTS for item in results)
    no_ack = results and all(is_ack_timeout(item["ack"]) for item in results)
    unsupported = results and all(item["ack"]["resultText"] == "UNSUPPORTED" for item in results)
    if accepted:
        status = "accepted"
        message = "Flight controller confirmed servo test command; if the servo does not move, check PX4 output function and servo power."
    elif no_ack:
        status = "sent_no_ack"
        message = "Command was sent, but no ACK was received; check MAVLink routing and whether PX4 allows actuator tests."
    elif unsupported:
        status = "unsupported"
        message = "Flight controller reported the current servo test command is unsupported; check PX4 output function mapping."
    else:
        status = "rejected"
        message = "Flight controller did not accept the servo test command; confirm the vehicle is disarmed and in a safe test state."
    write_command_status(command_id, status=status, message=message, results=results)


def send_servo_test(
    master: Any,
    channel: int,
    pwm: int = 1500,
    command_id: str | None = None,
    output_function: Any = None,
    *,
    ack_history: list[dict[str, Any]] | None = None,
    result_names: dict[int, str] | None = None,
) -> None:
    send_servo_outputs(
        master,
        [{"channel": channel, "pwm": pwm, "outputFunction": output_function}],
        command_id=command_id,
        ack_history=ack_history,
        result_names=result_names,
    )


def send_motor_test(
    master: Any,
    motor: int,
    throttle_percent: float,
    duration: float,
    command_id: str | None = None,
    output_function: Any = None,
    output: Any = None,
    *,
    ack_history: list[dict[str, Any]] | None = None,
    result_names: dict[int, str] | None = None,
) -> None:
    write_command_status(
        command_id,
        status="running",
        message="Motor test command is being sent to the flight controller",
        results=[],
    )
    motor = max(1, min(12, int(motor)))
    throttle_percent = max(0.0, min(10.0, float(throttle_percent)))
    duration = max(0.2, min(5.0, float(duration)))
    actuator_ack = send_px4_motor_actuator_test(
        master,
        motor,
        throttle_percent,
        duration,
        output_function=output_function,
        ack_history=ack_history,
        result_names=result_names,
    )
    ack = actuator_ack
    legacy_ack = None
    method = "PX4_ACTUATOR_TEST"
    if ack["resultText"] not in ACCEPTED_RESULTS:
        bus = MessageBus.for_master(master)
        cursor = bus.current_sequence() if bus is not None else None
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
        legacy_ack = wait_for_command_ack(
            master,
            mavutil.mavlink.MAV_CMD_DO_MOTOR_TEST,
            timeout=1.2,
            ack_history=ack_history,
            result_names=result_names,
            cursor=cursor,
        )
        ack = legacy_ack
        method = "DO_MOTOR_TEST"
    accepted = ack["resultText"] in ACCEPTED_RESULTS
    status = "accepted" if accepted else ("sent_no_ack" if is_ack_timeout(ack) else "rejected")
    message = (
        f"Flight controller confirmed motor {motor} test command"
        if accepted
        else f"Motor {motor} test was not confirmed by flight controller: {ack['resultText']}"
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
        "Motor test command sent: "
        f"motor={motor}, outputFunction={output_function}, testFunction={mavlink_motor_test_function(motor, output_function)}, "
        f"throttle={throttle_percent}%, duration={duration}s, method={method}, ack={ack['resultText']}"
    )


def send_actuator_test(
    master: Any,
    output: dict[str, Any],
    command_id: str | None = None,
    *,
    ack_history: list[dict[str, Any]] | None = None,
    result_names: dict[int, str] | None = None,
) -> None:
    output_type = str(output.get("type") or output.get("kind") or "").lower()
    if output_type == "motor" or "motor" in output:
        send_motor_test(
            master,
            output.get("motor", 1),
            output.get("throttlePercent", output.get("throttle", 0)),
            output.get("duration", 1),
            command_id=command_id,
            output_function=output.get("outputFunction"),
            output=output.get("output"),
            ack_history=ack_history,
            result_names=result_names,
        )
        return
    send_servo_test(
        master,
        output.get("channel", 1),
        output.get("pwm", 1500),
        command_id=command_id,
        output_function=output.get("outputFunction"),
        ack_history=ack_history,
        result_names=result_names,
    )

# RC Setup Tutorial

This tutorial explains how to use the RC Setup page in AeroCentral. The workflow is inspired by QGroundControl Radio Setup, but the safety boundary is strict: the UI reads `RC_CHANNELS` and PX4 RC parameters, requires human confirmation before parameter writes, and does not send `RC_OVERRIDE`, `MANUAL_CONTROL`, or automatic Arm commands.

## 1. Preparation

1. Remove all propellers.
2. Turn on the transmitter.
3. Confirm that the receiver is connected to the flight controller.
4. Connect the flight controller through USB, telemetry radio, or UDP MAVLink.
5. Open the UI: `http://127.0.0.1:8080/`.
6. Go to Connection Settings and confirm that MAVLink is connected and vehicle Heartbeat is received.
7. Open the RC Setup page.

## 2. Compare with QGroundControl

Before using AeroCentral RC Setup, confirm that the transmitter works correctly in QGroundControl Radio Setup.

Comparison points:

- QGroundControl CH1-CH18 values should broadly match AeroCentral CH1-CH18 values.
- Moving Roll, Pitch, Throttle, and Yaw should change the same channels in both tools.
- If QGroundControl shows RC values but AeroCentral does not, check whether AeroCentral is receiving `RC_CHANNELS`.
- If mapped controls are incorrect, check `RC_MAP_ROLL`, `RC_MAP_PITCH`, `RC_MAP_THROTTLE`, and `RC_MAP_YAW`.

Important indexing rule:

```text
RC_MAP_ROLL=1 means CH1.
In code, CH1 corresponds to channels[0].
```

## 3. RC Monitor

RC Monitor displays:

- Raw CH1 to CH18 PWM values
- Percentage for each channel
- `MIN / MAX / TRIM / REV` values when available
- Mapped Roll, Pitch, Throttle, and Yaw
- Flight Mode channel
- Arm Switch channel
- RC Link Status
- RSSI when MAVLink provides it
- Update rate and last update age
- Channel stability and possible jitter

If the page shows `No RC`:

1. Confirm that the flight controller is connected.
2. Confirm that the transmitter is powered on.
3. Confirm that QGroundControl can see RC channels.
4. Confirm that AeroCentral is connected to the correct MAVLink link.
5. Check whether `RC_CHANNELS` is requested and received.

## 4. RC Calibration Wizard

Open RC Setup and select the Calibration Wizard.

Recommended workflow:

1. Start the RC calibration session.
2. Detection: confirm that at least five valid channels are available.
3. Center sampling: release all sticks, keep throttle at minimum, and sample the current step.
4. Roll sampling: move Roll fully left and right, return to center, then sample.
5. Pitch sampling: move Pitch fully forward and backward, return to center, then sample.
6. Throttle sampling: move throttle from minimum to maximum and back to minimum, then sample.
7. Yaw sampling: move Yaw fully left and right, return to center, then sample.
8. Switch sampling: toggle mode and arm switches as instructed.
9. Review detected mapping and parameter diff.
10. Write parameters only after confirming that the mapping is correct.

## 5. Parameter Write Safety

Before writing RC parameters:

- Aircraft must be Disarmed.
- Propellers must be removed.
- Throttle must be at minimum.
- Mapping preview must be reviewed.
- Parameter diff must be checked.
- Operator confirmation text must be entered exactly as required by the UI.

AeroCentral does not automatically Arm the vehicle, does not send RC override commands, and does not control the aircraft from the RC calibration page.

## 6. Backup and Rollback

Before writing new RC parameters:

1. Read the current parameter list.
2. Save or review the current `RC_MAP_*` and `RCx_*` values.
3. Confirm the generated diff.
4. Write only the intended parameters.
5. Refresh RC Monitor after the queue is executed.

If the result is incorrect, use the saved values to restore the previous configuration.

## 7. Safety Notes

- RC calibration and parameter writes are blocked when the vehicle is Armed.
- Parameter writes during flight are not allowed.
- Always inspect the diff before writing.
- The UI does not automatically Arm the aircraft.
- The UI does not send `RC_OVERRIDE`.
- The UI does not send `MANUAL_CONTROL`.
- The UI does not directly control motors or servos from RC Setup.
- If the mapping result is uncertain, do not write parameters. Repeat sampling or inspect values manually.

## 8. Common Issues

### QGroundControl Works but AeroCentral Shows No RC Data

Check whether AeroCentral receives `RC_CHANNELS`. If attitude and GPS update but `RC_CHANNELS` does not, the message interval may not be requested correctly or the link may differ from the one used by QGroundControl.

### Channels Do Not Match QGroundControl

Confirm that both applications are connected to the same flight controller and the same MAVLink path. Raw RC input must come from `RC_CHANNELS`, not from `SERVO_OUTPUT_RAW` or actuator output topics.

### Write Button Does Not Work

Check:

- Current mode allows parameter writing.
- The flight controller is Disarmed.
- The required confirmation text is entered exactly.
- The browser loaded the latest UI version. Use `Ctrl + F5` if necessary.

### Throttle Minimum Is Not 0 Percent

Check whether `RC_MAP_THROTTLE` points to the correct channel and whether the corresponding `RCx_MIN`, `RCx_MAX`, and `RCx_TRIM` values are reasonable. For throttle, `TRIM` is usually close to `MIN`.

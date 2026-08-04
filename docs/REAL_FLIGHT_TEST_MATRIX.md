# AeroCentral Real-Flight Test Matrix

This matrix upgrades validation from "the UI is visible" to "the flight controller command loop is verified." Each test item should record date, flight controller model, PX4 version, connection type, propeller removal status, operator, pass/fail result, failure STATUSTEXT, COMMAND_ACK, screenshots, and logs.

## 0. Pre-Test Safety Conditions

| ID | Check Item | Pass Criteria | Record |
| --- | --- | --- | --- |
| S-01 | Propellers | Propellers removed before motor, servo, Arm, Disarm, or calibration tests. |  |
| S-02 | Aircraft state | Disarmed, throttle at minimum, safety switch status known. |  |
| S-03 | Power | Battery voltage normal; USB or telemetry link stable. |  |
| S-04 | Test area | Indoor calibration away from metal and magnetic interference; outdoor test area controlled. |  |
| S-05 | Fallback | QGroundControl available for comparison and emergency takeover if needed. |  |

## 1. Aircraft and Flight Controller Coverage

| ID | Aircraft Type | Flight Controller | Firmware | Connection | Required Modules |
| --- | --- | --- | --- | --- | --- |
| A-01 | Multicopter | Pixhawk 6C / PX6C | Current PX4 version | USB serial | Connection, attitude, RC, Arm, mode switching, motor test, calibration |
| A-02 | Fixed-wing | Pixhawk 6C / PX6C | Current PX4 version | USB serial | Connection, attitude, GPS, airspeed, servo test, Mission |
| A-03 | VTOL | Pixhawk 6C / PX6C | Current PX4 version | USB serial | VTOL mode, transition phases, servo/motor mapping |
| A-04 | Multicopter | Pixhawk 6C / PX6C | Current PX4 version | UDP telemetry | Link recovery, message frequency, RC, mode switching, alerts |
| A-05 | Fixed-wing | Pixhawk 6C / PX6C | Current PX4 version | UDP telemetry | Mission upload/download, airspeed, map trajectory |

## 2. Link Stability Tests

| ID | Scenario | Operation | Pass Criteria | Key Records |
| --- | --- | --- | --- | --- |
| L-01 | Normal USB connection | Select serial port and connect. | `target_system` and `target_component` identified within 5 seconds. | HEARTBEAT, target |
| L-02 | UDP target connection | Connect using target flight controller IP and port. | UI shows GCS Heartbeat at 1 Hz and receives vehicle Heartbeat. | GCS Heartbeat, Vehicle Heartbeat |
| L-03 | UDP listener | Listen on `0.0.0.0:14550`. | MAVLink packets are received and the target is identified. | Connection status |
| L-04 | Short USB disconnect | Disconnect USB for 5 seconds, then reconnect. | UI shows link loss and recovers without crashing. | Loss duration, recovery time |
| L-05 | Short telemetry loss | Interrupt telemetry for 5 seconds, then restore. | No backend crash, no duplicate connection process, target remains stable. | Connection status |
| L-06 | Long-duration idle run | Keep connection active for 30 minutes. | Backend remains stable; message frequency and Heartbeat remain consistent. | CPU, memory, logs |
| L-07 | Shared-link comparison | Connect QGroundControl and AeroCentral sequentially to the same flight controller. | AeroCentral does not classify QGroundControl Heartbeat as the vehicle target. | `target_system` |

## 3. Real-Time Telemetry Tests

| ID | Data | MAVLink Source | Operation | Pass Criteria |
| --- | --- | --- | --- | --- |
| T-01 | Attitude indicator | ATTITUDE | Move the flight controller by hand. | Roll, Pitch, and Yaw update with low latency. |
| T-02 | Compass | ATTITUDE / GLOBAL_POSITION_INT / VFR_HUD | Rotate the flight controller. | Heading changes continuously without incorrect wrap artifacts. |
| T-03 | Ground speed | VFR_HUD / GLOBAL_POSITION_INT | Move outdoors or use simulation. | Ground speed changes in real time. |
| T-04 | Altitude | GLOBAL_POSITION_INT / VFR_HUD | Raise the aircraft or simulate climb. | Altitude changes clearly. |
| T-05 | Airspeed | VFR_HUD / airspeed sensor | Connect fixed-wing airspeed sensor. | Shows real airspeed when available; shows N/A when unavailable. |
| T-06 | Battery | SYS_STATUS / BATTERY_STATUS | Connect battery. | Voltage, current, and remaining capacity reflect real data. |
| T-07 | GPS | GPS_RAW_INT / GLOBAL_POSITION_INT | Test outdoors with GPS lock. | Fix type, satellites, latitude, and longitude are real. |
| T-08 | Warnings | STATUSTEXT | Trigger a preflight failure. | Warning center shows original PX4 text and local explanation. |

## 4. Command Closed-Loop Tests

| ID | Command | Operation | Pass Criteria | Failure Display Must Include |
| --- | --- | --- | --- | --- |
| C-01 | Request parameters | Click parameter list request. | PARAM_VALUE data returned; missing items listed. | Timeout or missing parameter details |
| C-02 | Arm | Confirm propellers removed, then Arm. | COMMAND_ACK shown and HEARTBEAT armed state changes. | MAV_RESULT and STATUSTEXT |
| C-03 | Disarm | Disarm after Arm. | COMMAND_ACK shown and HEARTBEAT disarmed state changes. | MAV_RESULT and STATUSTEXT |
| C-04 | MANUAL mode | Click Manual mode. | ACK or HEARTBEAT mode read-back confirms mode. | Rejection reason |
| C-05 | POSCTL mode | Click Position mode. | Mode changes correctly. | GPS, RC, or sensor reason |
| C-06 | ALTCTL mode | Click Altitude mode. | Mode changes correctly. | Rejection reason |
| C-07 | LAND mode | Click Land mode. | Mode command is acknowledged. | Rejection reason |
| C-08 | MISSION mode | Click Mission mode after mission upload. | AUTO.MISSION mode is entered when mission and positioning are valid. | No mission or no position reason |
| C-09 | Servo test | Test each output with propellers removed. | Expected servo moves independently and ACK is visible. | Unsupported or denied reason |
| C-10 | Motor test | Test each motor with propellers removed. | Expected motor responds and ACK is visible. | Safety or unsupported reason |
| C-11 | Gyro calibration | Keep aircraft still and confirm. | ACK ACCEPTED and STATUSTEXT progress/completion shown. | Preflight failure reason |
| C-12 | Accelerometer calibration | Follow six-orientation prompts. | PX4 prompts and progress are visible. | Orientation or movement failure reason |
| C-13 | Magnetometer calibration | Rotate aircraft away from metal objects. | MAG_CAL_PROGRESS / MAG_CAL_REPORT visible when supported. | Missing compass or magnetic interference reason |
| C-14 | Airspeed calibration | Keep airspeed sensor still. | ACK and STATUSTEXT visible. | Invalid airspeed reason |

## 5. Mission GCS Tests

| ID | Flow | Pass Criteria |
| --- | --- | --- |
| M-01 | Read mission from flight controller | Receives MISSION_COUNT and each MISSION_ITEM_INT / MISSION_ITEM. |
| M-02 | Clear mission | Receives MISSION_ACK ACCEPTED. |
| M-03 | Upload 3-waypoint mission | COUNT -> REQUEST -> ITEM -> ACK sequence completes. |
| M-04 | Upload mission with TAKEOFF and LAND | Command types and waypoint count are correct. |
| M-05 | Upload retry after timeout | REQUEST timeout triggers retry without freezing. |
| M-06 | Read-back verification after upload | Read-back mission matches UI waypoint plan. |
| M-07 | Mission mode switching | Mission mode can be entered when mission and position are valid. |
| M-08 | Mission progress display | Current waypoint and total waypoint count are visible. |

## 6. Report and Log Tests

| ID | Function | Pass Criteria |
| --- | --- | --- |
| R-01 | USB ULog download | Download does not disconnect the flight controller and supports resumable behavior where implemented. |
| R-02 | Algorithm Engineering Report | Uses `verified_summary` and does not generate known stale false positives. |
| R-03 | AI Engineering Report | OpenAI failure is explained clearly and does not overwrite the Algorithm Engineering Report. |
| R-04 | Fixed-wing log | Identifies fixed-wing operation and avoids hover or rotor takeoff/landing phase labels. |
| R-05 | VTOL log | Includes rotor takeoff, front transition, cruise, back transition, and rotor landing when evidence supports those phases. |

## 7. Acceptance Record

| Date | Flight Controller / Aircraft | Connection | Passed Items | Failed Items | Blocking Reason | Next Step |
| --- | --- | --- | --- | --- | --- | --- |
|  |  |  |  |  |  |  |

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from pymavlink import mavutil

from backend.communication import (
    actuator_test_manager,
    calibration_manager,
    command_queue,
    command_sender,
    connection_manager,
    flight_log_manager,
    heartbeat_manager,
    mavlink_receiver,
    message_bus,
    parameter_manager,
    mission_manager,
    telemetry_state_manager,
)


class FakeMessage:
    def __init__(self, message_type: str, vehicle_type: int = 0, autopilot: int = 0, **fields):
        self._src_system = int(fields.pop("_src_system", 1))
        self._src_component = int(fields.pop("_src_component", 1))
        self.type = vehicle_type
        self.autopilot = autopilot
        self._message_type = message_type
        for key, value in fields.items():
            setattr(self, key, value)

    def get_type(self) -> str:
        return self._message_type

    def get_srcSystem(self):
        return self._src_system

    def get_srcComponent(self):
        return self._src_component

    def to_dict(self):
        return {
            key: value
            for key, value in self.__dict__.items()
            if not key.startswith("_")
        }


class FakeMav:
    def __init__(self):
        self.heartbeats = []
        self.command_longs = []
        self.set_modes = []
        self.param_requests = []
        self.param_sets = []
        self.data_stream_requests = []
        self.mission_clear_all = []
        self.mission_counts = []
        self.mission_items = []
        self.mission_item_ints = []
        self.mission_request_lists = []
        self.mission_requests = []
        self.mission_request_ints = []
        self.log_request_lists = []
        self.log_request_data = []
        self.log_request_ends = []

    def heartbeat_send(self, *args):
        self.heartbeats.append(args)

    def command_long_send(self, *args):
        self.command_longs.append(args)

    def set_mode_send(self, *args):
        self.set_modes.append(args)

    def request_data_stream_send(self, *args):
        self.data_stream_requests.append(args)

    def param_request_read_send(self, *args):
        self.param_requests.append(args)

    def param_set_send(self, *args):
        self.param_sets.append(args)

    def mission_clear_all_send(self, *args):
        self.mission_clear_all.append(args)

    def mission_count_send(self, *args):
        self.mission_counts.append(args)

    def mission_item_send(self, *args):
        self.mission_items.append(args)

    def mission_item_int_send(self, *args):
        self.mission_item_ints.append(args)

    def mission_request_list_send(self, *args):
        self.mission_request_lists.append(args)

    def mission_request_send(self, *args):
        self.mission_requests.append(args)

    def mission_request_int_send(self, *args):
        self.mission_request_ints.append(args)

    def log_request_list_send(self, *args):
        self.log_request_lists.append(args)

    def log_request_data_send(self, *args):
        self.log_request_data.append(args)

    def log_request_end_send(self, *args):
        self.log_request_ends.append(args)


class FakeMaster:
    def __init__(self, messages=None):
        self.mav = FakeMav()
        self.source_system = 255
        self.source_component = mavutil.mavlink.MAV_COMP_ID_MISSIONPLANNER
        self.target_system = 1
        self.target_component = 1
        self._messages = list(messages or [])
        self.closed = False

    def recv_match(self, *args, **kwargs):
        if not self._messages:
            return None
        requested_type = kwargs.get("type")
        for index, message in enumerate(self._messages):
            if requested_type is None or message.get_type() == requested_type:
                return self._messages.pop(index)
        return None

    def close(self):
        self.closed = True


class FakeHeartbeat:
    def __init__(self, system=42, component=99, autopilot=None):
        self.autopilot = mavutil.mavlink.MAV_AUTOPILOT_PX4 if autopilot is None else autopilot
        self._system = system
        self._component = component

    def get_srcSystem(self):
        return self._system

    def get_srcComponent(self):
        return self._component


class FakePort:
    def __init__(self, device, description):
        self.device = device
        self.description = description


class CommunicationRefactorTests(unittest.TestCase):
    def setUp(self):
        self._original_command_status = command_queue.COMMAND_STATUS
        self._temporary_directory = tempfile.TemporaryDirectory()
        command_queue.COMMAND_STATUS = Path(self._temporary_directory.name) / "px6c_command_status.json"

    def tearDown(self):
        command_queue.COMMAND_STATUS = self._original_command_status
        self._temporary_directory.cleanup()

    def test_send_gcs_heartbeat_uses_gcs_identity(self):
        master = FakeMaster()
        state = {}

        self.assertTrue(heartbeat_manager.send_gcs_heartbeat(master, state, force=True))

        heartbeat = master.mav.heartbeats[-1]
        self.assertEqual(heartbeat[0], mavutil.mavlink.MAV_TYPE_GCS)
        self.assertEqual(heartbeat[1], mavutil.mavlink.MAV_AUTOPILOT_INVALID)
        self.assertTrue(state["gcsHeartbeat"]["sending"])
        self.assertEqual(state["gcsHeartbeat"]["ourSystemId"], 255)

    def test_send_gcs_heartbeat_is_cadenced(self):
        master = FakeMaster()
        state = {}

        self.assertTrue(heartbeat_manager.send_gcs_heartbeat(master, state, force=True))
        self.assertFalse(heartbeat_manager.send_gcs_heartbeat(master, state))
        self.assertEqual(len(master.mav.heartbeats), 1)

    def test_vehicle_heartbeat_filters_out_gcs_heartbeat(self):
        gcs = FakeMessage(
            "HEARTBEAT",
            mavutil.mavlink.MAV_TYPE_GCS,
            mavutil.mavlink.MAV_AUTOPILOT_INVALID,
        )
        vehicle = FakeMessage(
            "HEARTBEAT",
            mavutil.mavlink.MAV_TYPE_FIXED_WING,
            mavutil.mavlink.MAV_AUTOPILOT_PX4,
        )

        self.assertFalse(heartbeat_manager.is_vehicle_heartbeat(gcs))
        self.assertTrue(heartbeat_manager.is_vehicle_heartbeat(vehicle))

    def test_command_queue_preserves_status_json_contract(self):
        command_queue.write_command_status("cmd-1", status="queued", message="pending")
        command_queue.write_command_status("cmd-1", status="accepted", resultText="ACCEPTED")

        statuses = command_queue.load_command_statuses()
        self.assertEqual(statuses["cmd-1"]["status"], "accepted")
        self.assertEqual(statuses["cmd-1"]["message"], "pending")
        self.assertEqual(command_queue.command_status_text("cmd-1", statuses), "accepted")

    def test_command_result_status_maps_ack_results(self):
        self.assertEqual(command_queue.command_result_status({"resultText": "ACCEPTED"}), "accepted")
        self.assertEqual(command_queue.command_result_status({"resultText": "TIMEOUT"}), "timeout")
        self.assertEqual(command_queue.command_result_status({"resultText": "NO_ACK"}), "sent_no_ack")

    def test_connection_manager_detects_single_serial_port(self):
        ports = [
            FakePort("COM3", "Bluetooth Device"),
            FakePort("COM10", "USB Serial Device"),
        ]

        selected = connection_manager.detect_serial_port(ports_provider=lambda: ports)

        self.assertEqual(selected, "COM10")

    def test_connection_manager_udp_connection_sets_target_from_heartbeat(self):
        created = {}

        def factory(connection, **kwargs):
            created["connection"] = connection
            created["kwargs"] = kwargs
            return FakeMaster()

        def wait(master, connection, timeout):
            created["wait"] = (master, connection, timeout)
            return FakeHeartbeat(system=2, component=3)

        master, heartbeat = connection_manager.connect(
            "udp:0.0.0.0:14550",
            57600,
            source_system=250,
            source_component=190,
            connection_factory=factory,
            heartbeat_waiter=wait,
            heartbeat_timeout=7,
        )

        self.assertEqual(created["connection"], "udp:0.0.0.0:14550")
        self.assertEqual(created["kwargs"]["baud"], 57600)
        self.assertTrue(created["kwargs"]["autoreconnect"])
        self.assertEqual(created["kwargs"]["source_system"], 250)
        self.assertEqual(created["kwargs"]["source_component"], 190)
        self.assertEqual(master.target_system, 2)
        self.assertEqual(master.target_component, 3)
        self.assertEqual(heartbeat.get_srcSystem(), 2)

    def test_connection_manager_serial_connection_uses_same_factory_contract(self):
        calls = []

        def factory(connection, **kwargs):
            calls.append((connection, kwargs))
            return FakeMaster()

        master, _heartbeat = connection_manager.connect(
            "COM10",
            115200,
            connection_factory=factory,
            heartbeat_waiter=lambda master, connection, timeout: FakeHeartbeat(system=11, component=22),
        )

        self.assertEqual(calls[0][0], "COM10")
        self.assertEqual(calls[0][1]["baud"], 115200)
        self.assertEqual(master.target_system, 11)
        self.assertEqual(master.target_component, 22)

    def test_connection_manager_raises_when_no_heartbeat(self):
        with self.assertRaises(TimeoutError):
            connection_manager.connect(
                "COM10",
                57600,
                connection_factory=lambda connection, **kwargs: FakeMaster(),
                heartbeat_waiter=lambda master, connection, timeout: None,
                heartbeat_timeout=0.01,
            )

    def test_connection_manager_disconnect_and_reconnect(self):
        previous = FakeMaster()
        created = []

        def factory(connection, **kwargs):
            created.append(connection)
            return FakeMaster()

        master, _heartbeat = connection_manager.reconnect(
            "COM11",
            57600,
            previous_master=previous,
            connection_factory=factory,
            heartbeat_waiter=lambda master, connection, timeout: FakeHeartbeat(system=7, component=8),
        )

        self.assertTrue(previous.closed)
        self.assertEqual(created, ["COM11"])
        self.assertEqual(master.target_system, 7)
        self.assertEqual(master.target_component, 8)

    def test_px6c_connect_compat_entrypoint_keeps_message_frequency_requests(self):
        import px6c_connector

        original_connect = px6c_connector.connection_manager.connect
        master = FakeMaster()

        def fake_connect(connection, baud, source_system=255, source_component=None):
            self.assertEqual(connection, "COM12")
            self.assertEqual(baud, 57600)
            master.target_system = 5
            master.target_component = 6
            return master, FakeHeartbeat(system=5, component=6)

        px6c_connector.connection_manager.connect = fake_connect
        try:
            connected, _heartbeat = px6c_connector.connect("COM12", 57600)
        finally:
            px6c_connector.connection_manager.connect = original_connect

        self.assertIs(connected, master)
        self.assertTrue(master.mav.command_longs)
        self.assertTrue(master.mav.data_stream_requests)
        self.assertTrue(master.mav.param_requests)

    def test_wait_for_command_ack_records_matching_ack(self):
        command = mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM
        master = FakeMaster([
            FakeMessage("COMMAND_ACK", command=command, result=mavutil.mavlink.MAV_RESULT_ACCEPTED),
        ])
        history = []

        ack = command_sender.wait_for_command_ack(master, command, timeout=0.2, ack_history=history)

        self.assertEqual(ack["resultText"], "ACCEPTED")
        self.assertEqual(history[-1]["command"], command)

    def test_arm_disarm_sends_component_arm_command(self):
        command = mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM
        master = FakeMaster([
            FakeMessage("COMMAND_ACK", command=command, result=mavutil.mavlink.MAV_RESULT_ACCEPTED),
            FakeMessage(
                "HEARTBEAT",
                mavutil.mavlink.MAV_TYPE_QUADROTOR,
                mavutil.mavlink.MAV_AUTOPILOT_PX4,
                base_mode=mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED,
            ),
        ])
        history = []

        command_sender.send_arm_disarm(master, True, ack_history=history)

        sent = master.mav.command_longs[-1]
        self.assertEqual(sent[2], command)
        self.assertEqual(sent[4], 1)
        self.assertEqual(history[-1]["resultText"], "ACCEPTED")

    def test_flight_mode_uses_command_long_and_set_mode_fallback(self):
        master = FakeMaster([
            FakeMessage("COMMAND_ACK", command=mavutil.mavlink.MAV_CMD_DO_SET_MODE, result=mavutil.mavlink.MAV_RESULT_UNSUPPORTED),
        ])
        modes = {"manual": {"label": "Manual", "px4": "MANUAL", "mainMode": 1, "subMode": 0}}

        command_sender.send_flight_mode(
            master,
            "manual",
            flight_mode_commands=modes,
            mode_encoder=lambda main, sub=0: (int(main) << 16) | (int(sub) << 24),
            mode_decoder=lambda custom: str(custom),
        )

        sent = master.mav.command_longs[-1]
        self.assertEqual(sent[2], mavutil.mavlink.MAV_CMD_DO_SET_MODE)
        self.assertTrue(master.mav.set_modes)

    def test_rtl_flight_mode_uses_px4_auto_rtl_submode(self):
        master = FakeMaster([
            FakeMessage("COMMAND_ACK", command=mavutil.mavlink.MAV_CMD_DO_SET_MODE, result=mavutil.mavlink.MAV_RESULT_ACCEPTED),
        ])
        modes = {"rtl": {"label": "RTL", "px4": "AUTO RTL", "mainMode": 4, "subMode": 5}}

        command_sender.send_flight_mode(
            master,
            "rtl",
            flight_mode_commands=modes,
            mode_encoder=lambda main, sub=0: (int(main) << 16) | (int(sub) << 24),
            mode_decoder=lambda custom: str(custom),
        )

        sent = master.mav.command_longs[-1]
        self.assertEqual(sent[2], mavutil.mavlink.MAV_CMD_DO_SET_MODE)
        self.assertEqual(sent[5], 4)
        self.assertEqual(sent[6], 5)

    def test_parameter_read_requests_and_collects_param_value(self):
        master = FakeMaster([
            FakeMessage(
                "PARAM_VALUE",
                param_id=b"FW_RR_P",
                param_value=0.12,
                param_index=0,
                param_count=1,
                param_type=mavutil.mavlink.MAV_PARAM_TYPE_REAL32,
            ),
        ])
        seen = []

        parameter_manager.request_parameter_values(
            master,
            ["FW_RR_P"],
            on_message=seen.append,
            default_parameter_requests=["FW_RR_P"],
        )

        self.assertEqual(master.mav.param_requests[-1][2], b"FW_RR_P")
        self.assertEqual(seen[-1].param_id, b"FW_RR_P")

    def test_parameter_write_sends_param_set_and_confirms(self):
        master = FakeMaster([
            FakeMessage(
                "PARAM_VALUE",
                param_id=b"FW_RR_P",
                param_value=0.2,
                param_type=mavutil.mavlink.MAV_PARAM_TYPE_REAL32,
            ),
        ])

        parameter_manager.send_parameter(master, "FW_RR_P", 0.2)

        sent = master.mav.param_sets[-1]
        self.assertEqual(sent[2], b"FW_RR_P")
        self.assertAlmostEqual(sent[3], 0.2)
        self.assertEqual(sent[4], mavutil.mavlink.MAV_PARAM_TYPE_REAL32)

    def test_calibration_command_sends_preflight_calibration(self):
        command = mavutil.mavlink.MAV_CMD_PREFLIGHT_CALIBRATION
        master = FakeMaster([
            FakeMessage("COMMAND_ACK", command=command, result=mavutil.mavlink.MAV_RESULT_ACCEPTED),
        ])
        history = []

        calibration_manager.send_calibration(master, "gyro", ack_history=history)

        sent = master.mav.command_longs[-1]
        self.assertEqual(sent[2], command)
        self.assertEqual(sent[4], 1)
        self.assertEqual(sent[5], 0)
        self.assertEqual(history[-1]["resultText"], "ACCEPTED")

    def test_calibration_evidence_reads_statustext(self):
        message = FakeMessage("STATUSTEXT", text=b"[cal] calibration started: 2 gyro")

        evidence = calibration_manager.calibration_evidence_text(message)

        self.assertIn("calibration started", evidence)

    def test_mission_upload_sends_count_item_and_verifies_readback(self):
        waypoint = {"command": "WAYPOINT", "lat": 31.2, "lon": 121.5, "altitude": 120}
        master = FakeMaster([
            FakeMessage("MISSION_REQUEST_INT", seq=0),
            FakeMessage("MISSION_ACK", type=mavutil.mavlink.MAV_MISSION_ACCEPTED),
            FakeMessage("MISSION_COUNT", count=1),
            FakeMessage(
                "MISSION_ITEM_INT",
                seq=0,
                frame=getattr(mavutil.mavlink, "MAV_FRAME_GLOBAL_RELATIVE_ALT_INT", 6),
                command=mavutil.mavlink.MAV_CMD_NAV_WAYPOINT,
                current=1,
                autocontinue=0,
                param1=0,
                param2=0,
                param3=0,
                param4=0,
                x=int(round(31.2 * 1e7)),
                y=int(round(121.5 * 1e7)),
                z=120,
            ),
        ])

        mission_manager.upload_mission(master, [waypoint], command_id="mission-1", clear_existing=False)

        self.assertEqual(master.mav.mission_counts[-1][2], 1)
        self.assertEqual(master.mav.mission_item_ints[-1][2], 0)
        self.assertEqual(master.mav.mission_request_lists[-1][0], 1)
        self.assertEqual(master.mav.mission_request_ints[-1][2], 0)
        status = command_queue.load_command_statuses()["mission-1"]
        self.assertEqual(status["status"], "accepted")
        self.assertTrue(status["results"][0]["verified"])

    def test_px6c_mission_upload_compat_entrypoint(self):
        import px6c_connector

        waypoint = {"command": "WAYPOINT", "lat": 31.2, "lon": 121.5, "altitude": 120}
        master = FakeMaster([
            FakeMessage("MISSION_REQUEST_INT", seq=0),
            FakeMessage("MISSION_ACK", type=mavutil.mavlink.MAV_MISSION_ACCEPTED),
            FakeMessage("MISSION_COUNT", count=1),
            FakeMessage(
                "MISSION_ITEM_INT",
                seq=0,
                frame=getattr(mavutil.mavlink, "MAV_FRAME_GLOBAL_RELATIVE_ALT_INT", 6),
                command=mavutil.mavlink.MAV_CMD_NAV_WAYPOINT,
                current=1,
                autocontinue=0,
                param1=0,
                param2=0,
                param3=0,
                param4=0,
                x=int(round(31.2 * 1e7)),
                y=int(round(121.5 * 1e7)),
                z=120,
            ),
        ])

        px6c_connector.upload_mission(master, [waypoint], command_id="mission-compat", clear_existing=False)

        status = command_queue.load_command_statuses()["mission-compat"]
        self.assertEqual(status["status"], "accepted")
        self.assertTrue(status["results"][0]["verified"])

    def test_mission_ack_and_clear_flow(self):
        master = FakeMaster([
            FakeMessage("MISSION_ACK", type=mavutil.mavlink.MAV_MISSION_ACCEPTED),
        ])

        mission_manager.clear_mission(master, command_id="clear-1")

        self.assertTrue(master.mav.mission_clear_all)
        status = command_queue.load_command_statuses()["clear-1"]
        self.assertEqual(status["status"], "accepted")
        self.assertEqual(status["results"][0]["ack"]["resultText"], "ACCEPTED")

    def test_read_mission_requests_list_and_items(self):
        master = FakeMaster([
            FakeMessage("MISSION_COUNT", count=1),
            FakeMessage(
                "MISSION_ITEM_INT",
                seq=0,
                frame=getattr(mavutil.mavlink, "MAV_FRAME_GLOBAL_RELATIVE_ALT_INT", 6),
                command=mavutil.mavlink.MAV_CMD_NAV_WAYPOINT,
                current=1,
                autocontinue=0,
                param1=0,
                param2=0,
                param3=0,
                param4=0,
                x=int(round(31.2 * 1e7)),
                y=int(round(121.5 * 1e7)),
                z=120,
            ),
        ])

        readback = mission_manager.read_mission_items_from_vehicle(master, command_id="read-1")

        self.assertEqual(readback["status"], "accepted")
        self.assertEqual(readback["items"][0]["commandName"], "WAYPOINT")
        self.assertTrue(master.mav.mission_request_lists)
        self.assertTrue(master.mav.mission_request_ints)

    def test_px4_actuator_test_sends_mav_cmd_actuator_test(self):
        command = actuator_test_manager.MAV_CMD_ACTUATOR_TEST
        master = FakeMaster([
            FakeMessage("COMMAND_ACK", command=command, result=mavutil.mavlink.MAV_RESULT_ACCEPTED),
        ])

        ack = actuator_test_manager.send_px4_actuator_test(master, channel=3, pwm=1600)

        sent = master.mav.command_longs[-1]
        self.assertEqual(sent[2], command)
        self.assertAlmostEqual(sent[4], 0.2)
        self.assertEqual(sent[8], actuator_test_manager.PX4_SERVO_OUTPUT_FUNCTION_BASE + 2)
        self.assertEqual(ack["resultText"], "ACCEPTED")

    def test_servo_test_falls_back_to_do_set_servo_when_actuator_test_rejected(self):
        actuator_command = actuator_test_manager.MAV_CMD_ACTUATOR_TEST
        servo_command = mavutil.mavlink.MAV_CMD_DO_SET_SERVO
        master = FakeMaster([
            FakeMessage("COMMAND_ACK", command=actuator_command, result=mavutil.mavlink.MAV_RESULT_UNSUPPORTED),
            FakeMessage("COMMAND_ACK", command=servo_command, result=mavutil.mavlink.MAV_RESULT_ACCEPTED),
        ])

        actuator_test_manager.send_servo_outputs(master, [{"channel": 2, "pwm": 1700}], command_id="servo-1")

        self.assertEqual(master.mav.command_longs[0][2], actuator_command)
        self.assertEqual(master.mav.command_longs[1][2], servo_command)
        self.assertEqual(master.mav.command_longs[1][4], 2)
        self.assertEqual(master.mav.command_longs[1][5], 1700)
        status = command_queue.load_command_statuses()["servo-1"]
        self.assertEqual(status["status"], "accepted")
        self.assertEqual(status["results"][0]["method"], "DO_SET_SERVO")

    def test_motor_test_sends_actuator_test_and_records_ack(self):
        command = actuator_test_manager.MAV_CMD_ACTUATOR_TEST
        master = FakeMaster([
            FakeMessage("COMMAND_ACK", command=command, result=mavutil.mavlink.MAV_RESULT_ACCEPTED),
        ])

        actuator_test_manager.send_motor_test(master, 1, 5, 2, command_id="motor-1")

        sent = master.mav.command_longs[-1]
        self.assertEqual(sent[2], command)
        self.assertAlmostEqual(sent[4], 0.05)
        self.assertEqual(sent[8], 1)
        status = command_queue.load_command_statuses()["motor-1"]
        self.assertEqual(status["status"], "accepted")
        self.assertEqual(status["results"][0]["method"], "PX4_ACTUATOR_TEST")

    def test_motor_test_falls_back_to_do_motor_test(self):
        actuator_command = actuator_test_manager.MAV_CMD_ACTUATOR_TEST
        motor_command = mavutil.mavlink.MAV_CMD_DO_MOTOR_TEST
        master = FakeMaster([
            FakeMessage("COMMAND_ACK", command=actuator_command, result=mavutil.mavlink.MAV_RESULT_UNSUPPORTED),
            FakeMessage("COMMAND_ACK", command=motor_command, result=mavutil.mavlink.MAV_RESULT_ACCEPTED),
        ])

        actuator_test_manager.send_motor_test(master, 2, 4, 3, command_id="motor-2")

        self.assertEqual(master.mav.command_longs[0][2], actuator_command)
        self.assertEqual(master.mav.command_longs[1][2], motor_command)
        self.assertEqual(master.mav.command_longs[1][4], 2)
        self.assertEqual(master.mav.command_longs[1][6], 4)
        status = command_queue.load_command_statuses()["motor-2"]
        self.assertEqual(status["status"], "accepted")
        self.assertEqual(status["results"][0]["method"], "DO_MOTOR_TEST")

    def test_px6c_actuator_compat_entrypoints(self):
        import px6c_connector

        command = actuator_test_manager.MAV_CMD_ACTUATOR_TEST
        master = FakeMaster([
            FakeMessage("COMMAND_ACK", command=command, result=mavutil.mavlink.MAV_RESULT_ACCEPTED),
            FakeMessage("COMMAND_ACK", command=command, result=mavutil.mavlink.MAV_RESULT_ACCEPTED),
        ])

        px6c_connector.send_servo_test(master, 1, pwm=1500, command_id="servo-compat")
        px6c_connector.send_motor_test(master, 1, 5, 2, command_id="motor-compat")

        statuses = command_queue.load_command_statuses()
        self.assertEqual(statuses["servo-compat"]["status"], "accepted")
        self.assertEqual(statuses["motor-compat"]["status"], "accepted")

    def test_flight_log_list_reads_log_entries(self):
        master = FakeMaster([
            FakeMessage("LOG_ENTRY", id=1, num_logs=2, last_log_num=2, time_utc=100, size=12),
            FakeMessage("LOG_ENTRY", id=2, num_logs=2, last_log_num=2, time_utc=200, size=24),
        ])

        flight_log_manager.request_flight_log_entries(master, command_id="logs-1")

        self.assertEqual(master.mav.log_request_lists[-1], (1, 1, 0, 0xFFFF))
        status = command_queue.load_command_statuses()["logs-1"]
        self.assertEqual(status["status"], "accepted")
        self.assertEqual([item["id"] for item in status["results"]], [1, 2])

    def test_flight_log_download_writes_file_and_status(self):
        with tempfile.TemporaryDirectory() as output_dir:
            master = FakeMaster([
                FakeMessage("LOG_DATA", id=7, ofs=0, count=6, data=b"ABCDEF"),
            ])

            flight_log_manager.download_flight_log(
                master,
                7,
                size=6,
                time_utc=1,
                output_dir=output_dir,
                command_id="download-1",
            )

            status = command_queue.load_command_statuses()["download-1"]
            output_path = Path(status["results"][0]["path"])
            self.assertEqual(status["status"], "accepted")
            self.assertEqual(output_path.read_bytes(), b"ABCDEF")
            self.assertEqual(master.mav.log_request_data[-1], (1, 1, 7, 0, 6))
            self.assertEqual(master.mav.log_request_ends[-1], (1, 1))

    def test_px6c_download_flight_log_compat_entrypoint(self):
        import px6c_connector

        with tempfile.TemporaryDirectory() as output_dir:
            master = FakeMaster([
                FakeMessage("LOG_DATA", id=8, ofs=0, count=4, data=b"ULOG"),
            ])

            px6c_connector.download_flight_log(
                master,
                8,
                size=4,
                time_utc=1,
                output_dir=output_dir,
                command_id="download-compat",
            )

            status = command_queue.load_command_statuses()["download-compat"]
            self.assertEqual(status["status"], "accepted")
            self.assertEqual(Path(status["results"][0]["path"]).read_bytes(), b"ULOG")

    def test_flight_log_download_interrupted_status_is_resumable(self):
        with tempfile.TemporaryDirectory() as output_dir:
            master = FakeMaster([])

            flight_log_manager.download_flight_log(
                master,
                9,
                size=4,
                time_utc=1,
                output_dir=output_dir,
                command_id="download-timeout",
                chunk_size=2,
            )

            status = command_queue.load_command_statuses()["download-timeout"]
            self.assertEqual(status["status"], "interrupted")
            self.assertTrue(status["results"][0]["resumable"])
            self.assertEqual(master.mav.log_request_ends[-1], (1, 1))

    def test_flight_log_file_validation(self):
        with tempfile.TemporaryDirectory() as output_dir:
            path = Path(output_dir) / "sample.ulg"
            path.write_bytes(b"12345")

            valid = flight_log_manager.validate_downloaded_log(path, expected_size=5)
            invalid = flight_log_manager.validate_downloaded_log(path, expected_size=6)

            self.assertTrue(valid["sizeOk"])
            self.assertFalse(invalid["sizeOk"])

    def test_mavlink_receiver_publishes_vehicle_heartbeat(self):
        master = FakeMaster([
            FakeMessage(
                "HEARTBEAT",
                mavutil.mavlink.MAV_TYPE_FIXED_WING,
                mavutil.mavlink.MAV_AUTOPILOT_PX4,
                base_mode=0,
                custom_mode=0,
                system_status=4,
                mavlink_version=3,
                _src_system=12,
                _src_component=1,
            )
        ])
        bus = message_bus.MessageBus()
        cursor = bus.current_sequence()
        receiver = mavlink_receiver.MavlinkReceiver(
            master,
            bus,
            heartbeat_filter=heartbeat_manager.is_vehicle_heartbeat,
            timeout_s=0.001,
        )
        receiver.start()
        try:
            message, _cursor = bus.wait_for(message_types=("HEARTBEAT",), timeout=0.5, cursor=cursor)
        finally:
            receiver.stop()
            bus.close()

        self.assertIsNotNone(message)
        self.assertEqual(receiver.last_message_type, "HEARTBEAT")
        self.assertIsNotNone(receiver.last_vehicle_heartbeat_mono)

    def test_command_ack_can_be_waited_from_message_bus(self):
        command = mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM
        master = FakeMaster([])
        bus = message_bus.MessageBus()
        bus.attach_master(master)
        cursor = bus.current_sequence()

        master.mav.command_long_send(1, 1, command, 0, 1, 0, 0, 0, 0, 0, 0)
        bus.publish(FakeMessage("COMMAND_ACK", command=command, result=mavutil.mavlink.MAV_RESULT_ACCEPTED))
        ack = command_sender.wait_for_command_ack(master, command, timeout=0.2, cursor=cursor)

        self.assertEqual(ack["resultText"], "ACCEPTED")
        self.assertEqual(master._messages, [])

    def test_realtime_attitude_continues_during_mission_param_log_and_actuator_transactions(self):
        import px6c_connector

        class CapturePublisher:
            def __init__(self):
                self.lock = threading.Lock()
                self.snapshots = []

            def publish(self, state, full=False):
                with self.lock:
                    self.snapshots.append({
                        "roll": state.get("roll"),
                        "speed": state.get("speed"),
                        "full": full,
                    })

        master = FakeMaster([])
        bus = message_bus.MessageBus()
        bus.attach_master(master)
        state = px6c_connector.create_state("TEST")
        publisher = CapturePublisher()
        telemetry = telemetry_state_manager.TelemetryStateManager(
            state=state,
            message_bus=bus,
            update_state=px6c_connector.update_state,
            publisher=publisher,
            full_publish_message_types=px6c_connector.FULL_PUBLISH_MESSAGE_TYPES,
            heartbeat_filter=heartbeat_manager.is_vehicle_heartbeat,
            publish_hz=50,
        )
        telemetry.start()
        stop_event = threading.Event()

        def feed_attitude():
            index = 0
            while not stop_event.is_set() and index < 80:
                bus.publish(FakeMessage(
                    "ATTITUDE",
                    roll=0.01 * index,
                    pitch=0.005 * index,
                    yaw=0.02 * index,
                    rollspeed=0.0,
                    pitchspeed=0.0,
                    yawspeed=0.0,
                ))
                bus.publish(FakeMessage("VFR_HUD", groundspeed=float(index), airspeed=float(index) + 1, climb=0.0, heading=90))
                index += 1
                time.sleep(0.02)

        def feed_mission():
            while not master.mav.mission_counts:
                time.sleep(0.005)
            bus.publish(FakeMessage("MISSION_REQUEST_INT", seq=0))
            while not master.mav.mission_item_ints:
                time.sleep(0.005)
            bus.publish(FakeMessage("MISSION_ACK", type=mavutil.mavlink.MAV_MISSION_ACCEPTED))
            while not master.mav.mission_request_lists:
                time.sleep(0.005)
            bus.publish(FakeMessage("MISSION_COUNT", count=1))
            while not master.mav.mission_request_ints:
                time.sleep(0.005)
            bus.publish(FakeMessage(
                "MISSION_ITEM_INT",
                seq=0,
                frame=getattr(mavutil.mavlink, "MAV_FRAME_GLOBAL_RELATIVE_ALT_INT", 6),
                command=mavutil.mavlink.MAV_CMD_NAV_WAYPOINT,
                current=1,
                autocontinue=0,
                param1=0,
                param2=0,
                param3=0,
                param4=0,
                x=int(round(31.2 * 1e7)),
                y=int(round(121.5 * 1e7)),
                z=120,
            ))

        def feed_parameter():
            while not master.mav.param_requests:
                time.sleep(0.005)
            bus.publish(FakeMessage(
                "PARAM_VALUE",
                param_id=b"FW_RR_P",
                param_value=0.12,
                param_index=0,
                param_count=1,
                param_type=mavutil.mavlink.MAV_PARAM_TYPE_REAL32,
            ))

        def feed_log():
            while not master.mav.log_request_data:
                time.sleep(0.005)
            bus.publish(FakeMessage("LOG_DATA", id=4, ofs=0, count=4, data=b"ULOG"))

        def feed_actuator_ack():
            while not any(item[2] == actuator_test_manager.MAV_CMD_ACTUATOR_TEST for item in master.mav.command_longs):
                time.sleep(0.005)
            bus.publish(FakeMessage(
                "COMMAND_ACK",
                command=actuator_test_manager.MAV_CMD_ACTUATOR_TEST,
                result=mavutil.mavlink.MAV_RESULT_ACCEPTED,
            ))

        attitude_thread = threading.Thread(target=feed_attitude)
        responders = [
            threading.Thread(target=feed_mission),
            threading.Thread(target=feed_parameter),
            threading.Thread(target=feed_log),
            threading.Thread(target=feed_actuator_ack),
        ]
        attitude_thread.start()
        for responder in responders:
            responder.start()

        with tempfile.TemporaryDirectory() as output_dir:
            workers = [
                threading.Thread(target=mission_manager.upload_mission, args=(master, [{"command": "WAYPOINT", "lat": 31.2, "lon": 121.5, "altitude": 120}], "mission-bus", False)),
                threading.Thread(target=parameter_manager.request_parameter_values, args=(master, ["FW_RR_P"], "params-bus"), kwargs={"default_parameter_requests": ["FW_RR_P"]}),
                threading.Thread(target=flight_log_manager.download_flight_log, args=(master, 4), kwargs={"size": 4, "time_utc": 1, "output_dir": output_dir, "command_id": "log-bus"}),
                threading.Thread(target=actuator_test_manager.send_motor_test, args=(master, 1, 3, 1), kwargs={"command_id": "actuator-bus"}),
            ]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=3.0)
            for worker in workers:
                self.assertFalse(worker.is_alive())

        time.sleep(0.3)
        stop_event.set()
        attitude_thread.join(timeout=1.0)
        for responder in responders:
            responder.join(timeout=1.0)
        time.sleep(0.1)
        telemetry.stop()
        bus.close()

        self.assertGreater(len(publisher.snapshots), 10)
        self.assertIsNotNone(state.get("roll"))
        self.assertGreater(state["roll"], 10.0)
        self.assertIsNotNone(state.get("speed"))


if __name__ == "__main__":
    unittest.main()

import csv
import json
import sqlite3
import threading
import time
import zipfile
from dataclasses import asdict
from datetime import datetime
from pathlib import Path


TELEMETRY_FIELDS = [
    "timestamp", "roll_deg", "pitch_deg", "yaw_deg", "altitude_m",
    "relative_altitude_m", "vertical_speed_mps", "ground_speed_mps",
    "airspeed_mps", "latitude", "longitude", "heading_deg", "gps_fix_type",
    "satellites_visible", "hdop", "battery_voltage_v", "battery_current_a",
    "battery_remaining_percent", "flight_mode", "armed", "rc_signal",
    "telemetry_signal",
] + [f"servo_{index}_pwm" for index in range(1, 17)] + [
    f"motor_{index}_pwm" for index in range(1, 13)
] + ["warning_text"]


class SessionLogger:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.active = False
        self.session_dir = None
        self.connection_losses = 0
        self.rows = 0
        self.last_update = None
        self.db = None
        self.min_telemetry_interval_s = 0.05
        self.last_telemetry_write_monotonic = 0.0
        self.last_db_commit_monotonic = 0.0

    def start(self, name="flight"):
        with self.lock:
            if self.active:
                return self.status()
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            safe_name = "".join(c for c in name if c.isalnum() or c in "-_") or "flight"
            self.session_dir = self.root / f"{stamp}_{safe_name}"
            self.session_dir.mkdir(parents=True, exist_ok=True)
            self._create_csv("telemetry.csv", TELEMETRY_FIELDS)
            self._create_csv("events.csv", ["timestamp", "event_type", "event_message", "source"])
            self._create_csv("warnings.csv", ["timestamp", "severity", "warning_message", "source"])
            self._create_csv("parameters.csv", ["timestamp", "parameter", "old_value", "new_value"])
            (self.session_dir / "mission.json").write_text("[]", encoding="utf-8")
            self.db = sqlite3.connect(self.session_dir / "session.db", check_same_thread=False)
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS telemetry "
                "(id INTEGER PRIMARY KEY, timestamp TEXT, payload TEXT)"
            )
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS events "
                "(id INTEGER PRIMARY KEY, timestamp TEXT, event_type TEXT, message TEXT, source TEXT)"
            )
            self.db.commit()
            self.active = True
            self.rows = 0
            self.connection_losses = 0
            self.last_telemetry_write_monotonic = 0.0
            self.last_db_commit_monotonic = time.monotonic()
            self.log_event("recording_started", "开始记录", "system", locked=True)
            return self.status()

    def stop(self):
        with self.lock:
            if not self.active:
                return self.status()
            self.log_event("recording_stopped", "停止记录", "system", locked=True)
            self.active = False
            if self.db:
                self.db.commit()
                self.db.close()
                self.db = None
            return self.status()

    def _create_csv(self, name, fields):
        with (self.session_dir / name).open("w", newline="", encoding="utf-8-sig") as handle:
            csv.DictWriter(handle, fieldnames=fields).writeheader()

    def append_telemetry(self, state):
        if not self.active:
            return
        now = time.monotonic()
        if now - self.last_telemetry_write_monotonic < self.min_telemetry_interval_s:
            return
        with self.lock:
            if now - self.last_telemetry_write_monotonic < self.min_telemetry_interval_s:
                return
            self.last_telemetry_write_monotonic = now
            data = asdict(state)
            servos = list(state.servo_outputs or []) + [None] * 16
            motors = list(state.motor_outputs or []) + [None] * 12
            row = {key: data.get(key) for key in TELEMETRY_FIELDS}
            for index in range(16):
                row[f"servo_{index + 1}_pwm"] = servos[index]
            for index in range(12):
                row[f"motor_{index + 1}_pwm"] = motors[index]
            row["warning_text"] = " | ".join(state.warnings[-3:])
            with (self.session_dir / "telemetry.csv").open(
                "a", newline="", encoding="utf-8-sig"
            ) as handle:
                csv.DictWriter(handle, fieldnames=TELEMETRY_FIELDS).writerow(row)
            self.db.execute(
                "INSERT INTO telemetry(timestamp, payload) VALUES (?, ?)",
                (state.timestamp, json.dumps(data, ensure_ascii=False)),
            )
            if now - self.last_db_commit_monotonic >= 1.0:
                self.db.commit()
                self.last_db_commit_monotonic = now
            self.rows += 1
            self.last_update = state.timestamp

    def log_event(self, event_type, message, source, locked=False):
        if not self.active:
            return
        context = _NullContext() if locked else self.lock
        with context:
            timestamp = datetime.now().isoformat()
            with (self.session_dir / "events.csv").open(
                "a", newline="", encoding="utf-8-sig"
            ) as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["timestamp", "event_type", "event_message", "source"],
                )
                writer.writerow({
                    "timestamp": timestamp, "event_type": event_type,
                    "event_message": message, "source": source,
                })
            self.db.execute(
                "INSERT INTO events(timestamp, event_type, message, source) VALUES (?, ?, ?, ?)",
                (timestamp, event_type, message, source),
            )
            self.db.commit()

    def log_warning(self, severity, message, source):
        if not self.active:
            return
        with self.lock:
            with (self.session_dir / "warnings.csv").open(
                "a", newline="", encoding="utf-8-sig"
            ) as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["timestamp", "severity", "warning_message", "source"],
                )
                writer.writerow({
                    "timestamp": datetime.now().isoformat(),
                    "severity": severity,
                    "warning_message": message,
                    "source": source,
                })

    def log_parameter(self, name, old_value, new_value):
        if not self.active:
            return
        with self.lock:
            with (self.session_dir / "parameters.csv").open(
                "a", newline="", encoding="utf-8-sig"
            ) as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["timestamp", "parameter", "old_value", "new_value"],
                )
                writer.writerow({
                    "timestamp": datetime.now().isoformat(),
                    "parameter": name,
                    "old_value": old_value,
                    "new_value": new_value,
                })

    def status(self):
        return {
            "active": self.active,
            "sessionName": self.session_dir.name if self.session_dir else None,
            "folder": str(self.session_dir) if self.session_dir else str(self.root),
            "rows": self.rows,
            "lastUpdate": self.last_update,
            "connectionLosses": self.connection_losses,
        }

    def list_sessions(self):
        sessions = []
        for folder in sorted((item for item in self.root.iterdir() if item.is_dir()), reverse=True):
            csv_path = folder / "telemetry.csv"
            if not csv_path.exists():
                continue
            rows = []
            try:
                with csv_path.open("r", encoding="utf-8-sig") as handle:
                    rows = list(csv.DictReader(handle))
            except OSError:
                pass
            def values(name):
                result = []
                for row in rows:
                    try:
                        if row.get(name) not in ("", None):
                            result.append(float(row[name]))
                    except ValueError:
                        pass
                return result
            altitudes = values("relative_altitude_m")
            speeds = values("ground_speed_mps")
            voltages = values("battery_voltage_v")
            warning_path = folder / "warnings.csv"
            warning_count = 0
            if warning_path.exists():
                with warning_path.open("r", encoding="utf-8-sig") as handle:
                    warning_count = max(0, sum(1 for _ in handle) - 1)
            sessions.append({
                "name": folder.name,
                "path": str(folder),
                "size": csv_path.stat().st_size if csv_path.exists() else 0,
                "rows": len(rows),
                "durationSeconds": max(0, len(rows) - 1),
                "maxAltitude": max(altitudes) if altitudes else None,
                "maxSpeed": max(speeds) if speeds else None,
                "minVoltage": min(voltages) if voltages else None,
                "warningCount": warning_count,
            })
        return sessions

    def session_detail(self, name, max_rows=2400):
        folder = self._session_folder(name)
        telemetry = self._read_csv(folder / "telemetry.csv")
        events = self._read_csv(folder / "events.csv")
        warnings = self._read_csv(folder / "warnings.csv")
        mission = []
        mission_path = folder / "mission.json"
        if mission_path.exists():
            try:
                mission = json.loads(mission_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                mission = []
        if len(telemetry) > max_rows:
            step = max(1, len(telemetry) // max_rows)
            telemetry = telemetry[::step]
        return {
            "name": folder.name,
            "path": str(folder),
            "telemetry": telemetry,
            "events": events,
            "warnings": warnings,
            "mission": mission,
            "files": {
                "telemetry": f"/logs/{folder.name}/telemetry.csv",
                "events": f"/logs/{folder.name}/events.csv",
                "warnings": f"/logs/{folder.name}/warnings.csv",
                "mission": f"/logs/{folder.name}/mission.json",
            },
        }

    def export_session(self, name):
        folder = self._session_folder(name)
        export_path = folder / f"{folder.name}_blackbox.zip"
        with zipfile.ZipFile(export_path, "w", zipfile.ZIP_DEFLATED) as package:
            for filename in ("telemetry.csv", "events.csv", "warnings.csv", "parameters.csv", "mission.json", "session.db"):
                path = folder / filename
                if path.exists():
                    package.write(path, arcname=filename)
        return {
            "name": folder.name,
            "url": f"/logs/{folder.name}/{export_path.name}",
            "path": str(export_path),
            "size": export_path.stat().st_size,
        }

    def _session_folder(self, name):
        folder = (self.root / Path(name or "").name).resolve()
        if self.root.resolve() not in folder.parents or not (folder / "telemetry.csv").exists():
            raise ValueError("未找到指定飞行记录")
        return folder

    @staticmethod
    def _read_csv(path):
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))


class _NullContext:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

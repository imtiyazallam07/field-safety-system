"""
Thread-safe Central State Manager
Maintains in-memory live sensor readings, worker band vitals, rover teleoperation state,
rolling historical data, alert feeds, and dynamic threshold configurations.
"""

import time
import socket
import threading
from collections import deque
from datetime import datetime
from typing import Dict, List, Optional, Any

from .config import (
    HISTORY_MAXLEN,
    ALERTS_MAXLEN,
    DEFAULT_THRESHOLDS,
    UDP_BROADCAST_PORT,
    COMMAND_MAP,
    CODE_TO_COMMAND
)


class SystemStateManager:
    def __init__(self):
        self.lock = threading.Lock()
        
        # In-memory device tables
        self.checkpoints: Dict[int, Dict[str, Any]] = {}
        self.bands: Dict[int, Dict[str, Any]] = {}
        
        # Rover State
        self.rover_state: Dict[str, Any] = {
            "rover_id": 1,
            "current_command": 0,
            "command_name": "STOP",
            "speed": 200,
            "battery_pct": 90,
            "failsafe_triggered": False,
            "last_seen": 0.0,
            "is_connected": False,
            "is_online": False,
            "connected_checkpoint_id": None,
            "connected_checkpoint_location": None,
            "ip": None,
            "last_command_sent_time": time.time()
        }
        
        # Pending command for Rover/Checkpoint polling
        self.pending_rover_command: Dict[str, Any] = {
            "rover_id": 1,
            "command": 0,
            "speed": 0,
            "seq_id": 0,
            "timestamp": time.time()
        }
        
        # Alerts feed
        self.alerts: deque = deque(maxlen=ALERTS_MAXLEN)
        self.alert_counter: int = 0
        
        # Configurable Safety Thresholds
        self.thresholds: Dict[str, float] = dict(DEFAULT_THRESHOLDS)
        
        # Listeners / Observers for internal events (WebSockets, Webhooks)
        self._alert_subscribers: List[Any] = []
        self._telemetry_subscribers: List[Any] = []

    # ================= EVENT SUBSCRIBERS =================

    def register_alert_subscriber(self, callback):
        if callback not in self._alert_subscribers:
            self._alert_subscribers.append(callback)

    def unregister_alert_subscriber(self, callback):
        if callback in self._alert_subscribers:
            self._alert_subscribers.remove(callback)

    def register_telemetry_subscriber(self, callback):
        if callback not in self._telemetry_subscribers:
            self._telemetry_subscribers.append(callback)

    def unregister_telemetry_subscriber(self, callback):
        if callback in self._telemetry_subscribers:
            self._telemetry_subscribers.remove(callback)

    def _notify_subscribers(self, subscribers, data):
        for cb in list(subscribers):
            try:
                cb(data)
            except Exception as e:
                print(f"[State] Error notifying subscriber: {e}")

    # ================= ALERT MANAGEMENT =================

    def log_alert(self, level: str, category: str, device_id: str, message: str) -> Dict[str, Any]:
        """Logs a new safety alert and notifies all active listeners (WebSockets, Webhooks)."""
        with self.lock:
            self.alert_counter += 1
            alert_item = {
                "id": f"ALT-{self.alert_counter:04d}",
                "timestamp": datetime.now().strftime("%H:%M:%S"),
                "level": level,  # "CRITICAL", "WARNING", "INFO"
                "category": category,  # "Vitals", "Gas", "Motion", "Sound", "Environment", "Rover", "Proximity"
                "device_id": str(device_id),
                "message": message,
                "acked": False
            }
            self.alerts.appendleft(alert_item)
            
        print(f"[{level}] {category} alert on {device_id}: {message}")
        self._notify_subscribers(self._alert_subscribers, alert_item)
        return alert_item

    def get_alerts(self, limit: int = 50, level: Optional[str] = None, unacked_only: bool = False) -> List[Dict[str, Any]]:
        with self.lock:
            items = list(self.alerts)
            if level:
                items = [a for a in items if a["level"].upper() == level.upper()]
            if unacked_only:
                items = [a for a in items if not a.get("acked", False)]
            return items[:limit]

    def acknowledge_alerts(self, alert_id: Optional[str] = None) -> int:
        with self.lock:
            count = 0
            if alert_id:
                for a in self.alerts:
                    if a["id"] == alert_id:
                        a["acked"] = True
                        count += 1
            else:
                for a in self.alerts:
                    if not a["acked"]:
                        a["acked"] = True
                        count += 1
            return count

    def clear_alerts(self):
        with self.lock:
            self.alerts.clear()

    # ================= THRESHOLD EVALUATIONS =================

    def evaluate_checkpoint_thresholds(self, cp_data: Dict[str, Any]):
        cp_id = cp_data.get("checkpoint_id", 1)
        loc = cp_data.get("location", f"Checkpoint {cp_id}")

        # MQ5 Combustible Gas (GPIO 33)
        mq5 = cp_data.get("mq5", 0)
        if mq5 >= self.thresholds.get("mq5_critical", 600.0):
            self.log_alert("CRITICAL", "Gas (MQ5)", f"{loc} (CP-{cp_id})", f"Combustible gas buildup critical! Value: {mq5:.1f}")
        elif mq5 >= self.thresholds.get("mq5_warning", 350.0):
            self.log_alert("WARNING", "Gas (MQ5)", f"{loc} (CP-{cp_id})", f"Elevated combustible gas level: {mq5:.1f}")

        # MQ7 Carbon Monoxide (GPIO 34)
        mq7 = cp_data.get("mq7", 0)
        if mq7 >= self.thresholds.get("mq7_critical", 450.0):
            self.log_alert("CRITICAL", "Gas (MQ7)", f"{loc} (CP-{cp_id})", f"Toxic Carbon Monoxide dangerous spike! Value: {mq7:.1f}")
        elif mq7 >= self.thresholds.get("mq7_warning", 250.0):
            self.log_alert("WARNING", "Gas (MQ7)", f"{loc} (CP-{cp_id})", f"Elevated Carbon Monoxide level: {mq7:.1f}")

        # MQ135 Air Quality (GPIO 35)
        mq135 = cp_data.get("mq135", 0)
        if mq135 >= self.thresholds.get("mq135_critical", 550.0):
            self.log_alert("CRITICAL", "Gas (MQ135)", f"{loc} (CP-{cp_id})", f"Hazardous air quality / toxic vapors: {mq135:.1f}")
        elif mq135 >= self.thresholds.get("mq135_warning", 350.0):
            self.log_alert("WARNING", "Gas (MQ135)", f"{loc} (CP-{cp_id})", f"Degraded air quality: {mq135:.1f}")

        # Sound Spike HW-484-V0.2 (GPIO 27)
        sound = cp_data.get("sound_level", 0)
        if sound >= self.thresholds.get("sound_spike_threshold", 350.0):
            self.log_alert("CRITICAL", "Sound", f"{loc} (CP-{cp_id})", f"Sudden loud sound spike! Possible collapse/explosion ({sound:.1f})")

        # Proximity HC-SR04
        proximity = cp_data.get("proximity_cm")
        if proximity is not None and proximity > 0:
            if proximity <= self.thresholds.get("proximity_min_cm", 25.0):
                self.log_alert("WARNING", "Proximity", f"{loc} (CP-{cp_id})", f"Obstacle/person dangerously close: {proximity:.1f} cm")

        # Temperature & Humidity DHT11 (GPIO 15)
        temp = cp_data.get("temperature", 25.0)
        if temp >= self.thresholds.get("temp_max", 38.0):
            self.log_alert("WARNING", "Environment", f"{loc} (CP-{cp_id})", f"High ambient temperature: {temp:.1f}°C")
        elif temp <= self.thresholds.get("temp_min", 0.0):
            self.log_alert("WARNING", "Environment", f"{loc} (CP-{cp_id})", f"Freezing temperature: {temp:.1f}°C")

        hum = cp_data.get("humidity", 50.0)
        if hum >= self.thresholds.get("humidity_max", 85.0):
            self.log_alert("WARNING", "Environment", f"{loc} (CP-{cp_id})", f"High humidity levels: {hum:.1f}%")

    def evaluate_band_thresholds(self, b_data: Dict[str, Any]):
        band_id = b_data.get("band_id", 1)
        worker = b_data.get("worker_name", f"Band-{band_id}")
        spo2 = b_data.get("spo2", 98.0)
        pulse = b_data.get("pulse_bpm", 75.0)

        # Blood Oxygen (SpO2)
        if spo2 <= self.thresholds.get("spo2_critical", 90.0):
            self.log_alert("CRITICAL", "Vitals", f"{worker}", f"Hypoxia alert! Critically low SpO2: {spo2:.1f}%")
        elif spo2 <= self.thresholds.get("spo2_warning", 94.0):
            self.log_alert("WARNING", "Vitals", f"{worker}", f"Low blood oxygen reading: {spo2:.1f}%")

        # Pulse Rate (BPM)
        if pulse >= self.thresholds.get("pulse_max", 120.0):
            self.log_alert("WARNING", "Vitals", f"{worker}", f"Tachycardia / elevated pulse: {pulse:.0f} BPM")
        elif pulse <= self.thresholds.get("pulse_min", 50.0) and pulse > 0:
            self.log_alert("WARNING", "Vitals", f"{worker}", f"Bradycardia / low pulse: {pulse:.0f} BPM")

    # ================= INGESTION HANDLERS =================

    def ingest_checkpoint_telemetry(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Process telemetry from Checkpoints and relay payloads."""
        cp_id = int(data.get("checkpoint_id", 1))
        loc = data.get("location", f"Checkpoint {cp_id}")
        now_ts = time.time()
        now_str = datetime.now().strftime("%H:%M:%S")

        with self.lock:
            if cp_id not in self.checkpoints:
                self.checkpoints[cp_id] = {
                    "checkpoint_id": cp_id,
                    "location": loc,
                    "mq5": 0.0, "mq7": 0.0, "mq135": 0.0,
                    "pir_motion": False, "sound_level": 0.0,
                    "temperature": 25.0, "humidity": 50.0,
                    "proximity_cm": None,
                    "last_seen": now_ts,
                    "history": deque(maxlen=HISTORY_MAXLEN)
                }

            cp = self.checkpoints[cp_id]
            cp["location"] = loc
            cp["mq5"] = float(data.get("mq5", cp["mq5"]))
            cp["mq7"] = float(data.get("mq7", cp["mq7"]))
            cp["mq135"] = float(data.get("mq135", cp["mq135"]))
            cp["pir_motion"] = bool(data.get("pir_motion", False))
            cp["sound_level"] = float(data.get("sound_level", cp["sound_level"]))
            cp["temperature"] = float(data.get("temperature", cp["temperature"]))
            cp["humidity"] = float(data.get("humidity", cp["humidity"]))
            if "proximity_cm" in data and data["proximity_cm"] is not None:
                cp["proximity_cm"] = float(data["proximity_cm"])
            cp["last_seen"] = now_ts

            cp["history"].append({
                "timestamp": now_str,
                "ts": now_ts,
                "mq5": cp["mq5"],
                "mq7": cp["mq7"],
                "mq135": cp["mq135"],
                "sound_level": cp["sound_level"],
                "temperature": cp["temperature"],
                "humidity": cp["humidity"],
                "pir_motion": cp["pir_motion"],
                "proximity_cm": cp["proximity_cm"]
            })

            # Relayed Band Packet
            if "band" in data and isinstance(data["band"], dict):
                b_info = data["band"]
                b_id = int(b_info.get("band_id", 1))
                if b_id not in self.bands:
                    self.bands[b_id] = {
                        "band_id": b_id,
                        "worker_name": b_info.get("worker_name", f"Worker-{b_id}"),
                        "pulse_bpm": 75.0,
                        "spo2": 98.0,
                        "battery_pct": 100,
                        "relayed_by": cp_id,
                        "hops": 1,
                        "last_seen": now_ts,
                        "history": deque(maxlen=HISTORY_MAXLEN)
                    }
                b = self.bands[b_id]
                b["worker_name"] = b_info.get("worker_name", b["worker_name"])
                b["pulse_bpm"] = float(b_info.get("pulse_bpm", b["pulse_bpm"]))
                b["spo2"] = float(b_info.get("spo2", b["spo2"]))
                b["battery_pct"] = int(b_info.get("battery_pct", b["battery_pct"]))
                b["relayed_by"] = cp_id
                b["hops"] = int(b_info.get("hops", 1))
                b["last_seen"] = now_ts
                b["history"].append({
                    "timestamp": now_str,
                    "ts": now_ts,
                    "pulse_bpm": b["pulse_bpm"],
                    "spo2": b["spo2"]
                })
                self.evaluate_band_thresholds(b)

            # Relayed Rover Packet
            if "rover" in data and isinstance(data["rover"], dict):
                r_info = data["rover"]
                cmd_code = int(r_info.get("current_command", 0))
                cmd_names = {0: "STOP", 1: "FORWARD", 2: "BACKWARD", 3: "LEFT", 4: "RIGHT"}
                was_offline = (now_ts - self.rover_state.get("last_seen", 0)) > 6.0 or not self.rover_state.get("is_connected", False)

                self.rover_state["current_command"] = cmd_code
                self.rover_state["command_name"] = cmd_names.get(cmd_code, "STOP")
                self.rover_state["speed"] = int(r_info.get("speed", 0))
                self.rover_state["battery_pct"] = int(r_info.get("battery_pct", self.rover_state["battery_pct"]))
                self.rover_state["failsafe_triggered"] = bool(r_info.get("failsafe_triggered", False))
                self.rover_state["last_seen"] = now_ts
                self.rover_state["is_connected"] = True
                self.rover_state["is_online"] = True
                self.rover_state["connected_checkpoint_id"] = cp_id
                self.rover_state["connected_checkpoint_location"] = loc

                if was_offline:
                    self.log_alert("INFO", "Rover", f"Rover #{self.rover_state['rover_id']}", f"Rover connected through Checkpoint #{cp_id} ({loc})")

        self.evaluate_checkpoint_thresholds(cp)
        self._notify_subscribers(self._telemetry_subscribers, {"type": "checkpoint", "data": cp})

        # Return active rover drive command if present
        resp = {"status": "ok", "checkpoint_id": cp_id}
        if self.pending_rover_command["seq_id"] > 0:
            resp["rover_command"] = self.pending_rover_command

        return resp

    def ingest_band_telemetry(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Ingest direct or relayed Band vitals."""
        b_id = int(data.get("band_id", 1))
        now_ts = time.time()
        now_str = datetime.now().strftime("%H:%M:%S")

        with self.lock:
            if b_id not in self.bands:
                self.bands[b_id] = {
                    "band_id": b_id,
                    "worker_name": data.get("worker_name", f"Worker-{b_id}"),
                    "pulse_bpm": 75.0,
                    "spo2": 98.0,
                    "battery_pct": 100,
                    "relayed_by": int(data.get("relayed_by", 1)),
                    "hops": int(data.get("hops", 1)),
                    "last_seen": now_ts,
                    "history": deque(maxlen=HISTORY_MAXLEN)
                }
            b = self.bands[b_id]
            b["worker_name"] = data.get("worker_name", b["worker_name"])
            b["pulse_bpm"] = float(data.get("pulse_bpm", b["pulse_bpm"]))
            b["spo2"] = float(data.get("spo2", b["spo2"]))
            b["battery_pct"] = int(data.get("battery_pct", b["battery_pct"]))
            b["relayed_by"] = int(data.get("relayed_by", b.get("relayed_by", 1)))
            b["hops"] = int(data.get("hops", 1))
            b["last_seen"] = now_ts
            b["history"].append({
                "timestamp": now_str,
                "ts": now_ts,
                "pulse_bpm": b["pulse_bpm"],
                "spo2": b["spo2"]
            })
            self.evaluate_band_thresholds(b)

        self._notify_subscribers(self._telemetry_subscribers, {"type": "band", "data": b})
        return {"status": "ok", "band_id": b_id}

    def ingest_rover_telemetry(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Ingest direct Wi-Fi telemetry from Rover Driver."""
        now_ts = time.time()
        with self.lock:
            was_offline = (not self.rover_state.get("is_connected", False)) or (now_ts - self.rover_state.get("last_seen", 0) > 6.0)
            self.rover_state["rover_id"] = int(data.get("rover_id", 1))
            self.rover_state["current_command"] = int(data.get("current_command", 0))
            self.rover_state["speed"] = int(data.get("speed", 0))
            self.rover_state["battery_pct"] = int(data.get("battery_pct", 90))
            self.rover_state["failsafe_triggered"] = bool(data.get("failsafe_triggered", False))
            self.rover_state["connected_checkpoint_id"] = int(data.get("connected_checkpoint_id", 0))
            self.rover_state["connected_checkpoint_location"] = data.get("connected_checkpoint_location", "Direct Wi-Fi")
            self.rover_state["last_seen"] = now_ts
            self.rover_state["is_connected"] = True
            self.rover_state["is_online"] = True
            self.rover_state["ip"] = data.get("ip")

            if was_offline:
                self.log_alert("INFO", "Rover", f"Rover #{self.rover_state['rover_id']}", f"Rover directly connected to station (IP: {self.rover_state.get('ip')})")

        self._notify_subscribers(self._telemetry_subscribers, {"type": "rover", "data": self.rover_state})
        return {"status": "ok", "rover_id": self.rover_state["rover_id"]}

    # ================= ROVER CONTROL =================

    def send_rover_command(self, command_str: str, speed: int = 200, rover_id: int = 1) -> Dict[str, Any]:
        cmd_str = command_str.upper()
        cmd_code = COMMAND_MAP.get(cmd_str, 0)
        speed = max(0, min(255, speed))
        seq_id = int(time.time() * 1000) % 1000000

        # Broadcast low-latency UDP packet for sub-millisecond local Wi-Fi control
        self._broadcast_udp_rover(cmd_code, speed)

        with self.lock:
            self.pending_rover_command["rover_id"] = rover_id
            self.pending_rover_command["command"] = cmd_code
            self.pending_rover_command["speed"] = speed
            self.pending_rover_command["seq_id"] = seq_id
            self.pending_rover_command["timestamp"] = time.time()

            self.rover_state["current_command"] = cmd_code
            self.rover_state["command_name"] = cmd_str
            self.rover_state["speed"] = speed
            self.rover_state["last_command_sent_time"] = time.time()

        return {
            "status": "command_queued",
            "command": cmd_str,
            "speed": speed,
            "seq_id": seq_id,
            "timestamp": time.time()
        }

    def _broadcast_udp_rover(self, cmd_code: int, speed: int):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            pkt = bytes([0x04, 1, cmd_code, speed])
            sock.sendto(pkt, ('<broadcast>', UDP_BROADCAST_PORT))
            sock.sendto(pkt, ('192.168.137.255', UDP_BROADCAST_PORT))
            sock.close()
        except Exception:
            pass

    # ================= SNAPSHOTS & QUERIES =================

    def get_full_snapshot(self) -> Dict[str, Any]:
        now_ts = time.time()
        with self.lock:
            cp_list = []
            for cp_id, cp in sorted(self.checkpoints.items()):
                cp_list.append({
                    "checkpoint_id": cp["checkpoint_id"],
                    "location": cp["location"],
                    "mq5": cp["mq5"],
                    "mq7": cp["mq7"],
                    "mq135": cp["mq135"],
                    "pir_motion": cp["pir_motion"],
                    "sound_level": cp["sound_level"],
                    "temperature": cp["temperature"],
                    "humidity": cp["humidity"],
                    "proximity_cm": cp.get("proximity_cm"),
                    "last_seen": cp["last_seen"],
                    "is_online": (now_ts - cp["last_seen"]) < 10.0,
                    "history_count": len(cp["history"])
                })

            band_list = []
            for b_id, b in sorted(self.bands.items()):
                band_list.append({
                    "band_id": b["band_id"],
                    "worker_name": b["worker_name"],
                    "pulse_bpm": b["pulse_bpm"],
                    "spo2": b["spo2"],
                    "battery_pct": b["battery_pct"],
                    "relayed_by": b["relayed_by"],
                    "hops": b["hops"],
                    "last_seen": b["last_seen"],
                    "is_online": (now_ts - b["last_seen"]) < 10.0,
                    "history_count": len(b["history"])
                })

            rover_online = (now_ts - self.rover_state.get("last_seen", 0)) < 6.0 and self.rover_state.get("last_seen", 0) > 0
            self.rover_state["is_online"] = rover_online
            if not rover_online:
                self.rover_state["is_connected"] = False

            alert_list = list(self.alerts)[:30]

            return {
                "checkpoints": cp_list,
                "bands": band_list,
                "rover": dict(self.rover_state),
                "alerts": alert_list,
                "thresholds": dict(self.thresholds),
                "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "device_summary": {
                    "total_checkpoints": len(cp_list),
                    "online_checkpoints": sum(1 for c in cp_list if c["is_online"]),
                    "total_workers": len(band_list),
                    "online_workers": sum(1 for b in band_list if b["is_online"]),
                    "rover_online": rover_online,
                    "active_alerts": len(alert_list)
                }
            }

    def get_summary(self) -> Dict[str, Any]:
        snapshot = self.get_full_snapshot()
        crit_alerts = sum(1 for a in snapshot["alerts"] if a["level"] == "CRITICAL" and not a.get("acked", False))
        warn_alerts = sum(1 for a in snapshot["alerts"] if a["level"] == "WARNING" and not a.get("acked", False))
        
        status = "NORMAL"
        if crit_alerts > 0:
            status = "CRITICAL"
        elif warn_alerts > 0:
            status = "WARNING"

        return {
            "status": status,
            "total_workers": snapshot["device_summary"]["total_workers"],
            "online_workers": snapshot["device_summary"]["online_workers"],
            "total_checkpoints": snapshot["device_summary"]["total_checkpoints"],
            "online_checkpoints": snapshot["device_summary"]["online_checkpoints"],
            "rover_connected": snapshot["rover"]["is_connected"],
            "active_critical_alerts": crit_alerts,
            "active_warning_alerts": warn_alerts,
            "server_time": snapshot["server_time"]
        }


# Singleton Global State Manager instance
state_manager = SystemStateManager()

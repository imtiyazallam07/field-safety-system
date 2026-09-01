#!/usr/bin/env python3
"""
Field Safety Monitoring System - PyQt5 Desktop Station Dashboard
PRD Reference: field-safety-system-prd.md
Pin Config: PIN_Config.txt (MQ5: 33, MQ7: 34, MQ135: 35, HW-416-B: 26, HW-484-V0.2: 27, DHT11: 15)

Features:
- Dedicated real-time graph for EACH sensor (MQ5, MQ7, MQ135, HW-484, Temp, Hum, PIR, Pulse, SpO2)
- Built-in Ingestion Server on Port 3000 for ESP32 Checkpoint telemetry & Rover steering
- Real-time Rover Camera optical feed & WASD / D-Pad joystick teleoperation
- Prominent Rover-to-Checkpoint connection alerts & banners
- Multi-checkpoint & multi-worker live filtering
"""

import sys
import os
import io
import time
import math
import socket
import random
import threading
from collections import deque
from datetime import datetime

# PyQt5 Imports
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QPushButton, QSlider, QComboBox, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame, QSplitter,
    QGroupBox, QScrollArea, QStatusBar, QMessageBox, QLineEdit, QCheckBox
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject, QThread
from PyQt5.QtGui import QImage, QPixmap, QFont, QColor, QPalette, QIcon

# Matplotlib Qt Backend
import matplotlib
matplotlib.use('Qt5Agg')
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import numpy as np
import cv2
import requests
import urllib.request
from PIL import Image, ImageDraw

# Flask for background HTTP ingestion
from flask import Flask, request, jsonify, Response

# ================= GLOBAL STATE & THREAD SAFETY =================
app_flask = Flask(__name__)
state_lock = threading.Lock()
HISTORY_MAXLEN = 120

checkpoints = {}
bands = {}
rover_state = {
    "rover_id": 1,
    "current_command": 0, # 0=STOP, 1=FWD, 2=BACK, 3=LEFT, 4=RIGHT
    "command_name": "STOP",
    "speed": 200,
    "battery_pct": 90,
    "failsafe_triggered": False,
    "last_seen": 0,
    "is_online": False,
    "is_connected": False,
    "connected_checkpoint_id": None,
    "connected_checkpoint_location": None
}

pending_rover_command = {
    "rover_id": 1,
    "command": 0,
    "speed": 200,
    "seq_id": 0,
    "timestamp": time.time()
}

alerts_log = deque(maxlen=200)
latest_camera_frame_bytes = None
camera_chunks_buffer = {}

thresholds = {
    "spo2_critical": 90.0,
    "spo2_warning": 94.0,
    "pulse_min": 50.0,
    "pulse_max": 120.0,
    "mq5_warning": 350.0,
    "mq5_critical": 600.0,
    "mq7_warning": 250.0,
    "mq7_critical": 450.0,
    "mq135_warning": 350.0,
    "mq135_critical": 550.0,
    "sound_spike_threshold": 350.0,
    "temp_max": 38.0,
    "humidity_max": 85.0
}

# ================= FLASK SERVER INGESTION =================

class ServerSignals(QObject):
    telemetry_received = pyqtSignal(dict)
    camera_frame_received = pyqtSignal(bytes)
    alert_logged = pyqtSignal(dict)

server_signals = ServerSignals()

def log_alert_internal(level, category, device_id, message):
    alert_item = {
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "level": level,
        "category": category,
        "device_id": str(device_id),
        "message": message
    }
    alerts_log.appendleft(alert_item)
    server_signals.alert_logged.emit(alert_item)

@app_flask.route('/api/telemetry', methods=['POST'])
def http_receive_telemetry():
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"status": "error"}), 400

    cp_id = data.get("checkpoint_id", 1)
    loc = data.get("location", f"Checkpoint {cp_id}")
    now_ts = time.time()
    now_str = datetime.now().strftime("%H:%M:%S")

    with state_lock:
        if cp_id not in checkpoints:
            checkpoints[cp_id] = {
                "checkpoint_id": cp_id,
                "location": loc,
                "mq5": 0, "mq7": 0, "mq135": 0,
                "pir_motion": False, "sound_level": 0,
                "temperature": 25.0, "humidity": 50.0,
                "last_seen": now_ts,
                "history": deque(maxlen=HISTORY_MAXLEN)
            }

        cp = checkpoints[cp_id]
        cp["location"] = loc
        cp["mq5"] = data.get("mq5", cp["mq5"])
        cp["mq7"] = data.get("mq7", cp["mq7"])
        cp["mq135"] = data.get("mq135", cp["mq135"])
        cp["pir_motion"] = bool(data.get("pir_motion", False))
        cp["sound_level"] = data.get("sound_level", cp["sound_level"])
        cp["temperature"] = data.get("temperature", cp["temperature"])
        cp["humidity"] = data.get("humidity", cp["humidity"])
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
            "pir_motion": cp["pir_motion"]
        })

        # Relayed Band
        if "band" in data and isinstance(data["band"], dict):
            b_info = data["band"]
            b_id = b_info.get("band_id", 1)
            if b_id not in bands:
                bands[b_id] = {
                    "band_id": b_id,
                    "worker_name": b_info.get("worker_name", f"Worker-{b_id}"),
                    "pulse_bpm": 75.0,
                    "spo2": 98.0,
                    "battery_pct": 100,
                    "relayed_by": cp_id,
                    "last_seen": now_ts,
                    "history": deque(maxlen=HISTORY_MAXLEN)
                }
            b = bands[b_id]
            b["worker_name"] = b_info.get("worker_name", b["worker_name"])
            b["pulse_bpm"] = b_info.get("pulse_bpm", b["pulse_bpm"])
            b["spo2"] = b_info.get("spo2", b["spo2"])
            b["battery_pct"] = b_info.get("battery_pct", b["battery_pct"])
            b["relayed_by"] = cp_id
            b["last_seen"] = now_ts
            b["history"].append({
                "timestamp": now_str,
                "ts": now_ts,
                "pulse_bpm": b["pulse_bpm"],
                "spo2": b["spo2"]
            })

            # Check SpO2 alert
            if b["spo2"] < thresholds["spo2_critical"]:
                log_alert_internal("CRITICAL", "Vitals", b["worker_name"], f"Critical Hypoxia! SpO2: {b['spo2']:.1f}%")

        # Relayed Rover
        if "rover" in data and isinstance(data["rover"], dict):
            r_info = data["rover"]
            cmd_code = r_info.get("current_command", 0)
            cmd_names = {0: "STOP", 1: "FORWARD", 2: "BACKWARD", 3: "LEFT", 4: "RIGHT"}

            was_offline = (now_ts - rover_state.get("last_seen", 0)) > 6.0 or not rover_state.get("is_connected", False)

            rover_state["current_command"] = cmd_code
            rover_state["command_name"] = cmd_names.get(cmd_code, "STOP")
            rover_state["speed"] = r_info.get("speed", 0)
            rover_state["battery_pct"] = r_info.get("battery_pct", rover_state["battery_pct"])
            rover_state["last_seen"] = now_ts
            rover_state["is_connected"] = True
            rover_state["is_online"] = True
            rover_state["connected_checkpoint_id"] = cp_id
            rover_state["connected_checkpoint_location"] = loc

            if was_offline:
                log_alert_internal("INFO", "Rover", f"Rover #{rover_state['rover_id']}", f"Rover CONNECTED to Checkpoint #{cp_id} ({loc})")

        # Evaluate gas/sound alert
        if cp["mq5"] >= thresholds["mq5_critical"]:
            log_alert_internal("CRITICAL", "Gas (MQ5)", f"{loc}", f"Combustible gas spike: {cp['mq5']}")
        if cp["sound_level"] >= thresholds["sound_spike_threshold"]:
            log_alert_internal("CRITICAL", "Sound", f"{loc}", f"Sound spike detected: {cp['sound_level']}")

    resp = {"status": "ok"}
    if pending_rover_command["seq_id"] > 0:
        resp["rover_command"] = pending_rover_command

    return jsonify(resp), 200

@app_flask.route('/api/camera_frame', methods=['POST'])
def http_receive_camera_frame():
    global latest_camera_frame_bytes
    img_data = request.get_data()
    if img_data:
        latest_camera_frame_bytes = img_data
        server_signals.camera_frame_received.emit(img_data)
        return jsonify({"status": "ok"}), 200
    return jsonify({"status": "empty"}), 400

@app_flask.route('/api/telemetry/rover', methods=['POST'])
def http_receive_rover_telemetry():
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"status": "error"}), 400

    now_ts = time.time()
    with state_lock:
        was_offline = (not rover_state.get("is_connected", False)) or (now_ts - rover_state.get("last_seen", 0) > 6.0)
        rover_state["rover_id"] = data.get("rover_id", 1)
        rover_state["current_command"] = data.get("current_command", 0)
        rover_state["speed"] = data.get("speed", 0)
        rover_state["battery_pct"] = data.get("battery_pct", 90)
        rover_state["failsafe_triggered"] = data.get("failsafe_triggered", False)
        rover_state["connected_checkpoint_id"] = 0
        rover_state["connected_checkpoint_location"] = "Direct PC Wi-Fi"
        rover_state["last_seen"] = now_ts
        rover_state["is_connected"] = True
        rover_state["is_online"] = True
        rover_state["ip"] = data.get("ip", "192.168.137.x")

        if was_offline:
            log_alert_internal("INFO", "Rover", f"Rover #{rover_state['rover_id']}", f"Rover Driver DIRECTLY LINKED over Wi-Fi (IP: {rover_state['ip']})")

    return jsonify({"status": "ok"}), 200

@app_flask.route('/api/rover/command/poll', methods=['GET'])
def http_poll_rover_command():
    with state_lock:
        return jsonify(pending_rover_command), 200

# ================= DEDICATED SENSOR MATPLOTLIB CANVAS =================

class SingleSensorCanvas(FigureCanvas):
    """A dedicated, dark-themed Matplotlib canvas for a single sensor with live dynamic auto-scaling."""
    def __init__(self, title, unit, line_color='#38bdf8', threshold=None, threshold_label=None, is_step=False):
        self.fig = Figure(figsize=(4.0, 2.4), dpi=90)
        self.fig.patch.set_facecolor('#0f172a') # Dark slate background
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor('#1e293b') # Dark slate plot area

        super().__init__(self.fig)
        self.title_text = title
        self.unit_text = unit
        self.line_color = line_color
        self.threshold = threshold
        self.threshold_label = threshold_label
        self.is_step = is_step

        self.setup_plot()

    def setup_plot(self):
        self.ax.tick_params(colors='#94a3b8', labelsize=7)
        for spine in self.ax.spines.values():
            spine.set_color('#334155')
        self.ax.grid(True, color='#334155', linestyle='--', alpha=0.5)
        self.ax.set_title(self.title_text, color='#f8fafc', fontsize=9, fontweight='bold', pad=6)
        self.ax.set_ylabel(self.unit_text, color='#94a3b8', fontsize=7)
        self.fig.tight_layout()

    def update_data(self, x_data, y_data, latest_val_str=""):
        self.ax.clear()
        self.ax.set_facecolor('#1e293b')
        self.ax.tick_params(colors='#94a3b8', labelsize=7)
        for spine in self.ax.spines.values():
            spine.set_color('#334155')
        self.ax.grid(True, color='#334155', linestyle='--', alpha=0.5)

        title = f"{self.title_text}: {latest_val_str}" if latest_val_str else self.title_text
        self.ax.set_title(title, color='#f8fafc', fontsize=9, fontweight='bold', pad=6)
        self.ax.set_ylabel(self.unit_text, color='#94a3b8', fontsize=7)

        if len(x_data) > 0 and len(y_data) > 0:
            x_list = list(x_data)
            y_list = [float(v) for v in y_data]

            if self.is_step:
                self.ax.step(x_list, y_list, color=self.line_color, where='mid', linewidth=2.0)
                self.ax.fill_between(x_list, y_list, step='mid', color=self.line_color, alpha=0.25)
                self.ax.set_yticks([0, 1])
                self.ax.set_yticklabels(['Clear', 'Motion'])
                self.ax.set_ylim(-0.2, 1.2)
            else:
                # 1. Dynamic Auto-scaling calculation based directly on live sensor input
                d_min = min(y_list)
                d_max = max(y_list)
                d_range = d_max - d_min

                if d_range < 1e-5:
                    # Sensor is currently steady / constant value
                    if abs(d_min) < 1e-5:
                        y_lower = 0.0
                        y_upper = 5.0
                    elif d_min > 0:
                        margin = max(d_min * 0.12, 1.5)
                        y_lower = max(0.0, d_min - margin)
                        y_upper = d_max + margin
                    else:
                        margin = max(abs(d_min) * 0.12, 1.5)
                        y_lower = d_min - margin
                        y_upper = d_max + margin
                else:
                    # Dynamic padding (12% of data span above and below)
                    pad = max(d_range * 0.12, 0.5)
                    y_lower = d_min - pad
                    y_upper = d_max + pad

                    # Floor at 0 for non-negative sensor readings
                    if d_min >= 0:
                        if d_min <= pad * 2:
                            y_lower = 0.0
                        else:
                            y_lower = max(0.0, y_lower)

                # 2. Adaptive safety threshold inclusion (if readings approach danger zone)
                threshold_visible = False
                if self.threshold is not None:
                    thresh = float(self.threshold)
                    # Expand Y-axis if sensor reading is within 40% of threshold or has crossed it
                    if thresh > d_max:
                        if d_max >= thresh * 0.60:
                            y_upper = max(y_upper, thresh * 1.10)
                            threshold_visible = True
                    else:
                        # Danger threshold exceeded!
                        y_upper = max(y_upper, d_max * 1.08)
                        y_lower = min(y_lower, thresh * 0.90)
                        threshold_visible = True

                    # For lower thresholds (like SpO2 hypoxia < 90%)
                    if thresh < d_min:
                        if d_min <= thresh * 1.40:
                            y_lower = min(y_lower, max(0.0, thresh * 0.90))
                            threshold_visible = True
                    elif thresh >= d_min and thresh <= d_max:
                        threshold_visible = True

                self.ax.plot(x_list, y_list, color=self.line_color, linewidth=1.8, marker='o', markersize=2.5, alpha=0.95)
                self.ax.fill_between(x_list, y_list, y2=y_lower, color=self.line_color, alpha=0.15)
                self.ax.set_ylim(y_lower, y_upper)

                # 3. Draw threshold line if within active auto-scaled view
                if self.threshold is not None and (threshold_visible or (y_lower <= self.threshold <= y_upper)):
                    self.ax.axhline(y=self.threshold, color='#ef4444', linestyle='--', linewidth=1.3,
                                    label=self.threshold_label or f"Danger ({self.threshold})")
                    self.ax.legend(loc='upper right', facecolor='#0f172a', edgecolor='#334155', fontsize=6.5, labelcolor='#e2e8f0')

            # X-Axis Auto-scaling to span the full width
            if len(x_data) > 1:
                self.ax.set_xlim(min(x_data), max(x_data))
            elif len(x_data) == 1:
                self.ax.set_xlim(0, 1)
        else:
            self.ax.text(0.5, 0.5, 'Waiting for sensor telemetry...', color='#64748b', ha='center', va='center', transform=self.ax.transAxes, fontsize=8)

        self.fig.tight_layout()
        self.draw()

# ================= BACKGROUND SIMULATOR THREAD =================

class SimulatorThread(QThread):
    def __init__(self):
        super().__init__()
        self.running = True

    def run(self):
        step = 0
        while self.running:
            step += 1
            now_ts = time.time()
            now_str = datetime.now().strftime("%H:%M:%S")

            with state_lock:
                # 3 Checkpoints Simulation
                for cp_id, loc in [(1, "Zone-A (Main Shaft)"), (2, "Zone-B (Extraction)"), (3, "Zone-C (Deep Mine)")]:
                    if cp_id not in checkpoints:
                        checkpoints[cp_id] = {
                            "checkpoint_id": cp_id, "location": loc,
                            "mq5": 110, "mq7": 45, "mq135": 80,
                            "pir_motion": False, "sound_level": 60,
                            "temperature": 24.5, "humidity": 52.0,
                            "last_seen": now_ts,
                            "history": deque(maxlen=HISTORY_MAXLEN)
                        }

                    cp = checkpoints[cp_id]
                    cp["mq5"] = max(50, min(800, cp["mq5"] + random.randint(-4, 4)))
                    cp["mq7"] = max(20, min(600, cp["mq7"] + random.randint(-3, 3)))
                    cp["mq135"] = max(40, min(700, cp["mq135"] + random.randint(-3, 3)))
                    cp["temperature"] = round(cp["temperature"] + random.uniform(-0.1, 0.1), 1)
                    cp["humidity"] = round(cp["humidity"] + random.uniform(-0.2, 0.2), 1)
                    cp["sound_level"] = random.randint(20, 85)
                    cp["pir_motion"] = (random.random() < 0.20)
                    cp["last_seen"] = now_ts

                    # Occasional Gas Spike Scenario
                    if cp_id == 2 and 15 <= (step % 50) <= 25:
                        cp["mq5"] = 650 + random.randint(-20, 30)
                        cp["mq7"] = 480 + random.randint(-15, 20)

                    # Sound Spike Scenario
                    if cp_id == 1 and 35 <= (step % 50) <= 40:
                        cp["sound_level"] = 450 + random.randint(0, 80)
                        cp["pir_motion"] = True

                    cp["history"].append({
                        "timestamp": now_str, "ts": now_ts,
                        "mq5": cp["mq5"], "mq7": cp["mq7"], "mq135": cp["mq135"],
                        "sound_level": cp["sound_level"],
                        "temperature": cp["temperature"], "humidity": cp["humidity"],
                        "pir_motion": cp["pir_motion"]
                    })

                # Worker Bands Simulation
                for b_id, name in [(1, "Worker-101 (Alex Mercer)"), (2, "Worker-102 (Sarah Connor)")]:
                    if b_id not in bands:
                        bands[b_id] = {
                            "band_id": b_id, "worker_name": name,
                            "pulse_bpm": 74.0, "spo2": 98.5,
                            "battery_pct": 95, "relayed_by": 1,
                            "last_seen": now_ts,
                            "history": deque(maxlen=HISTORY_MAXLEN)
                        }

                    b = bands[b_id]
                    b["pulse_bpm"] = round(max(55.0, min(130.0, b["pulse_bpm"] + random.uniform(-1.5, 1.5))), 1)
                    b["spo2"] = round(max(85.0, min(99.5, b["spo2"] + random.uniform(-0.3, 0.3))), 1)
                    b["last_seen"] = now_ts

                    # Hypoxia Scenario for Worker 1
                    if b_id == 1 and 25 <= (step % 50) <= 35:
                        b["spo2"] = 87.5 + random.uniform(-0.5, 0.5)
                        b["pulse_bpm"] = 126.0

                    b["history"].append({
                        "timestamp": now_str, "ts": now_ts,
                        "pulse_bpm": b["pulse_bpm"], "spo2": b["spo2"]
                    })

                # Rover Status
                was_offline = (now_ts - rover_state.get("last_seen", 0)) > 6.0 or not rover_state.get("is_connected", False)
                rover_state["last_seen"] = now_ts
                rover_state["is_connected"] = True
                rover_state["is_online"] = True
                rover_state["connected_checkpoint_id"] = 1
                rover_state["connected_checkpoint_location"] = "Zone-A (Main Shaft)"

                if was_offline:
                    log_alert_internal("INFO", "Rover", "Rover #1", "Rover CONNECTED to Checkpoint #1 (Zone-A)")

            time.sleep(1.0)

    def stop(self):
        self.running = False
        self.wait()

# ================= MJPEG STREAM THREAD (OPENCV / TEST.PY ENGINE) =================

class MjpegStreamThread(QThread):
    frame_ready = pyqtSignal(QImage)
    stream_error = pyqtSignal(str)
    stream_stopped = pyqtSignal()

    def __init__(self, cam_ip):
        super().__init__()
        # Clean IP address
        cleaned_ip = cam_ip.replace("http://", "").replace("https://", "").strip().rstrip("/")
        if ":" in cleaned_ip:
            cleaned_ip = cleaned_ip.split(":")[0]
        self.cam_ip = cleaned_ip
        self.stream_url = f"http://{self.cam_ip}:81/stream"
        self.running = True

    def run(self):
        try:
            # Open HTTP MJPEG Connection matching Test.py
            stream = urllib.request.urlopen(self.stream_url, timeout=5)
            bytes_data = b''

            while self.running:
                chunk = stream.read(2048)
                if not chunk:
                    break
                bytes_data += chunk

                # Search for JPEG Start (\xff\xd8) and End (\xff\xd9)
                a = bytes_data.find(b'\xff\xd8')
                b = bytes_data.find(b'\xff\xd9')

                if a != -1 and b != -1 and b > a:
                    jpg = bytes_data[a:b+2]
                    bytes_data = bytes_data[b+2:]

                    # Decode frame with OpenCV
                    frame = cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)
                    if frame is not None and self.running:
                        # Convert BGR to RGB
                        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        h, w, ch = rgb_frame.shape
                        bytes_per_line = ch * w
                        q_img = QImage(rgb_frame.data, w, h, bytes_per_line, QImage.Format_RGB888).copy()
                        self.frame_ready.emit(q_img)
        except Exception as e:
            if self.running:
                self.stream_error.emit(str(e))
        finally:
            self.running = False
            self.stream_stopped.emit()

    def stop(self):
        self.running = False
        self.wait(1000)

# ================= MAIN PYQT5 WINDOW =================

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Field Safety Monitoring System - PyQt5 Dashboard Station")
        self.resize(1400, 920)
        self.setStyleSheet(self.get_dark_stylesheet())

        self.sim_thread = None
        self.cam_stream_thread = None
        self.current_motor_cmd = "stop"

        # Build UI Layout
        self.init_ui()

        # Update Timers
        self.graph_timer = QTimer(self)
        self.graph_timer.timeout.connect(self.update_all_sensor_graphs)
        self.graph_timer.start(1000) # Update graphs every 1s

        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self.update_ui_state)
        self.ui_timer.start(500)

        # Connect background signals
        server_signals.alert_logged.connect(self.on_new_alert)
        server_signals.camera_frame_received.connect(self.on_camera_frame_pushed)

    def get_dark_stylesheet(self):
        return """
            QMainWindow {
                background-color: #090d16;
                color: #f1f5f9;
            }
            QWidget {
                font-family: 'Segoe UI', Arial, sans-serif;
                color: #e2e8f0;
            }
            QTabWidget::pane {
                border: 1px solid #1e293b;
                background: #0b1120;
                border-radius: 8px;
            }
            QTabBar::tab {
                background: #1e293b;
                color: #94a3b8;
                padding: 8px 18px;
                margin-right: 4px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                font-weight: bold;
                font-size: 11px;
            }
            QTabBar::tab:selected {
                background: #0284c7;
                color: #ffffff;
            }
            QGroupBox {
                border: 1px solid #1e293b;
                border-radius: 8px;
                margin-top: 10px;
                font-weight: bold;
                font-size: 11px;
                color: #38bdf8;
                background-color: #0f172a;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
            QPushButton {
                background-color: #1e293b;
                border: 1px solid #334155;
                color: #f8fafc;
                padding: 8px 14px;
                border-radius: 6px;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #0284c7;
                border-color: #38bdf8;
            }
            QPushButton:pressed {
                background-color: #0369a1;
            }
            QComboBox {
                background-color: #0f172a;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 4px 10px;
                color: #f8fafc;
                font-size: 11px;
            }
            QTableWidget {
                background-color: #0f172a;
                border: 1px solid #1e293b;
                gridline-color: #1e293b;
                border-radius: 6px;
                color: #f8fafc;
            }
            QHeaderView::section {
                background-color: #1e293b;
                color: #94a3b8;
                font-weight: bold;
                font-size: 10px;
                border: 1px solid #334155;
                padding: 4px;
            }
        """

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_vbox = QVBoxLayout(central_widget)
        main_vbox.setContentsMargins(10, 10, 10, 10)
        main_vbox.setSpacing(8)

        # 1. Header Bar
        main_vbox.addLayout(self.create_header())

        # 2. Main Tabs
        self.tabs = QTabWidget()
        self.tabs.addTab(self.create_individual_sensor_graphs_tab(), "📊 Dedicated Sensor Graphs (9 Live Charts)")
        self.tabs.addTab(self.create_rover_console_tab(), "🤖 Rover Video Feed & Control Console")
        self.tabs.addTab(self.create_overview_tab(), "📋 Checkpoint & Personnel Summary")
        main_vbox.addWidget(self.tabs, stretch=4)

        # 3. Bottom Alert Feed Panel
        main_vbox.addWidget(self.create_alert_panel(), stretch=1)

    def create_header(self):
        header_hbox = QHBoxLayout()
        header_hbox.setSpacing(10)

        # Title
        title_vbox = QVBoxLayout()
        title_lbl = QLabel("FIELD SAFETY MONITORING STATION")
        title_lbl.setStyleSheet("font-size: 16px; font-weight: 800; color: #38bdf8; letter-spacing: 1px;")
        sub_lbl = QLabel("PyQt5 Multi-Sensor Live Telemetry, Dedicated Graphs & Rover Control")
        sub_lbl.setStyleSheet("font-size: 10px; color: #94a3b8;")
        title_vbox.addWidget(title_lbl)
        title_vbox.addWidget(sub_lbl)
        header_hbox.addLayout(title_vbox)

        header_hbox.addStretch()

        # Status Badges
        self.server_status_badge = QLabel("● SERVER ACTIVE : 3000")
        self.server_status_badge.setStyleSheet("background: #064e3b; color: #34d399; padding: 5px 10px; border-radius: 6px; font-weight: bold; font-size: 10px; border: 1px solid #059669;")
        header_hbox.addWidget(self.server_status_badge)

        self.rover_header_badge = QLabel("ROVER: OFFLINE")
        self.rover_header_badge.setStyleSheet("background: #1e293b; color: #94a3b8; padding: 5px 10px; border-radius: 6px; font-weight: bold; font-size: 10px; border: 1px solid #334155;")
        header_hbox.addWidget(self.rover_header_badge)

        # Simulator Toggle Button
        self.btn_sim = QPushButton("▶ Start Built-in Simulator")
        self.btn_sim.setStyleSheet("background: #0284c7; color: white; padding: 6px 14px; font-size: 11px;")
        self.btn_sim.clicked.connect(self.toggle_simulator)
        header_hbox.addWidget(self.btn_sim)

        return header_hbox

    # ================= TAB 1: 9 DEDICATED SENSOR GRAPHS =================

    def create_individual_sensor_graphs_tab(self):
        tab_widget = QWidget()
        vbox = QVBoxLayout(tab_widget)
        vbox.setContentsMargins(6, 6, 6, 6)
        vbox.setSpacing(6)

        # Filter Bar
        filter_hbox = QHBoxLayout()
        filter_hbox.addWidget(QLabel("Select Checkpoint:"))
        self.cb_checkpoint = QComboBox()
        self.cb_checkpoint.addItems(["Checkpoint 1 (Zone-A Main Shaft)", "Checkpoint 2 (Zone-B Extraction)", "Checkpoint 3 (Zone-C Deep Mine)"])
        self.cb_checkpoint.currentIndexChanged.connect(self.update_all_sensor_graphs)
        filter_hbox.addWidget(self.cb_checkpoint)

        filter_hbox.addSpacing(20)
        filter_hbox.addWidget(QLabel("Select Worker:"))
        self.cb_worker = QComboBox()
        self.cb_worker.addItems(["Worker 101 (Alex Mercer)", "Worker 102 (Sarah Connor)"])
        self.cb_worker.currentIndexChanged.connect(self.update_all_sensor_graphs)
        filter_hbox.addWidget(self.cb_worker)

        filter_hbox.addStretch()
        btn_refresh = QPushButton("🔄 Refresh Graphs")
        btn_refresh.clicked.connect(self.update_all_sensor_graphs)
        filter_hbox.addWidget(btn_refresh)
        vbox.addLayout(filter_hbox)

        # 3x3 Grid of 9 Independent Sensor Canvases
        grid = QGridLayout()
        grid.setSpacing(8)

        # 1. MQ5 Combustible Gas
        self.canvas_mq5 = SingleSensorCanvas(
            title="MQ5 Combustible Gas (GPIO 33)", unit="ADC Level",
            line_color="#f97316", threshold=600, threshold_label="MQ5 Danger (600)"
        )
        grid.addWidget(self.canvas_mq5, 0, 0)

        # 2. MQ7 Carbon Monoxide
        self.canvas_mq7 = SingleSensorCanvas(
            title="MQ7 Carbon Monoxide (GPIO 34)", unit="ADC Level",
            line_color="#ef4444", threshold=450, threshold_label="MQ7 Danger (450)"
        )
        grid.addWidget(self.canvas_mq7, 0, 1)

        # 3. MQ135 Air Quality
        self.canvas_mq135 = SingleSensorCanvas(
            title="MQ135 Air Quality (GPIO 35)", unit="ADC Level",
            line_color="#38bdf8", threshold=550, threshold_label="Air Toxic (550)"
        )
        grid.addWidget(self.canvas_mq135, 0, 2)

        # 4. Sound Peak HW-484-V0.2
        self.canvas_sound = SingleSensorCanvas(
            title="HW-484 Acoustic Sound Peak (GPIO 27)", unit="Peak Amplitude",
            line_color="#eab308", threshold=350, threshold_label="Explosion Spike (350)"
        )
        grid.addWidget(self.canvas_sound, 1, 0)

        # 5. DHT11 Temperature
        self.canvas_temp = SingleSensorCanvas(
            title="DHT11 Temperature (GPIO 15)", unit="°Celsius",
            line_color="#f43f5e", threshold=38.0, threshold_label="Heat Limit (38°C)"
        )
        grid.addWidget(self.canvas_temp, 1, 1)

        # 6. DHT11 Humidity
        self.canvas_hum = SingleSensorCanvas(
            title="DHT11 Humidity (GPIO 15)", unit="% Relative",
            line_color="#0ea5e9", threshold=85.0, threshold_label="Max Humidity (85%)"
        )
        grid.addWidget(self.canvas_hum, 1, 2)

        # 7. PIR Motion HW-416-B (true/false)
        self.canvas_pir = SingleSensorCanvas(
            title="HW-416-B PIR Motion State (GPIO 26)", unit="State",
            line_color="#a855f7", is_step=True
        )
        grid.addWidget(self.canvas_pir, 2, 0)

        # 8. Worker Pulse Rate (BPM)
        self.canvas_pulse = SingleSensorCanvas(
            title="Personnel Heart Rate (MAX30100)", unit="BPM",
            line_color="#fbbf24", threshold=120.0, threshold_label="High Pulse (120)"
        )
        grid.addWidget(self.canvas_pulse, 2, 1)

        # 9. Worker Blood Oxygen (SpO2 %)
        self.canvas_spo2 = SingleSensorCanvas(
            title="Personnel Oxygen SpO2 % (MAX30100)", unit="SpO2 %",
            line_color="#10b981", threshold=90.0, threshold_label="Hypoxia Critical (90%)"
        )
        grid.addWidget(self.canvas_spo2, 2, 2)

        vbox.addLayout(grid)
        return tab_widget

    # ================= TAB 2: ROVER VIDEO & CONTROLS =================

    def create_rover_console_tab(self):
        tab_widget = QWidget()
        vbox_main = QVBoxLayout(tab_widget)
        vbox_main.setContentsMargins(8, 8, 8, 8)
        vbox_main.setSpacing(8)

        # --- Top Connection Panel (Matching Test.py) ---
        conn_group = QGroupBox("ESP32 Direct Connection & Hardware Configuration (Test.py Mode)")
        conn_layout = QHBoxLayout(conn_group)
        conn_layout.setContentsMargins(10, 8, 10, 8)
        conn_layout.setSpacing(10)

        # Dev ESP32 IP
        lbl_dev_ip = QLabel("Dev ESP32 IP:")
        lbl_dev_ip.setStyleSheet("font-weight: bold; color: #f8fafc;")
        conn_layout.addWidget(lbl_dev_ip)

        self.txt_dev_ip = QLineEdit("192.168.137.9")
        self.txt_dev_ip.setPlaceholderText("e.g. 192.168.137.9")
        self.txt_dev_ip.setStyleSheet("background: #0f172a; border: 1px solid #334155; border-radius: 4px; padding: 4px 8px; font-size: 11px; font-family: monospace; color: #38bdf8;")
        self.txt_dev_ip.setFixedWidth(130)
        conn_layout.addWidget(self.txt_dev_ip)

        # CAM ESP32 IP
        lbl_cam_ip = QLabel("CAM ESP32 IP:")
        lbl_cam_ip.setStyleSheet("font-weight: bold; color: #f8fafc;")
        conn_layout.addWidget(lbl_cam_ip)

        self.txt_cam_ip = QLineEdit("192.168.137.106")
        self.txt_cam_ip.setPlaceholderText("e.g. 192.168.137.106")
        self.txt_cam_ip.setStyleSheet("background: #0f172a; border: 1px solid #334155; border-radius: 4px; padding: 4px 8px; font-size: 11px; font-family: monospace; color: #38bdf8;")
        self.txt_cam_ip.setFixedWidth(130)
        conn_layout.addWidget(self.txt_cam_ip)

        # Start/Stop Video Button (styled with #00adb5 like Test.py)
        self.btn_toggle_video = QPushButton("Start Video")
        self.btn_toggle_video.setStyleSheet("background: #00adb5; color: white; padding: 6px 14px; font-weight: bold; border-radius: 6px; font-size: 11px;")
        self.btn_toggle_video.clicked.connect(self.toggle_video_stream)
        conn_layout.addWidget(self.btn_toggle_video)

        # Ping Test Button
        self.btn_ping_dev = QPushButton("⚡ Ping Dev")
        self.btn_ping_dev.setStyleSheet("background: #1e293b; color: #38bdf8; border: 1px solid #334155; padding: 6px 10px; font-size: 10px;")
        self.btn_ping_dev.clicked.connect(self.test_dev_ip_connection)
        conn_layout.addWidget(self.btn_ping_dev)

        # Motor Inversion Checkbox (default True to match Test.py)
        self.chk_invert_motors = QCheckBox("Inverted Wiring (Test.py Default: W=backward, S=forward)")
        self.chk_invert_motors.setChecked(True)
        self.chk_invert_motors.setStyleSheet("color: #94a3b8; font-size: 10px;")
        conn_layout.addWidget(self.chk_invert_motors)

        conn_layout.addStretch()
        vbox_main.addWidget(conn_group)

        # --- Content Area (Video on Left, Steering on Right) ---
        hbox_content = QHBoxLayout()
        hbox_content.setSpacing(12)

        # Left: Video Screen Display & Banner
        video_vbox = QVBoxLayout()
        self.rover_banner = QLabel("SEARCHING FOR ROVER LINK OVER WI-FI / HOTSPOT...")
        self.rover_banner.setStyleSheet("background: #451a03; color: #fde047; padding: 8px 14px; border-radius: 6px; font-weight: bold; font-size: 11px; border: 1px solid #d97706;")
        video_vbox.addWidget(self.rover_banner)

        self.video_screen = QLabel("[ Video Stream Offline ]")
        self.video_screen.setMinimumSize(540, 400)
        self.video_screen.setAlignment(Qt.AlignCenter)
        self.video_screen.setStyleSheet("background: #000000; color: #ffffff; font-size: 14px; font-weight: bold; border: 2px solid #1e293b; border-radius: 8px;")
        video_vbox.addWidget(self.video_screen, stretch=1)

        hbox_content.addLayout(video_vbox, stretch=3)

        # Right: Steering & Controls
        ctrl_group = QGroupBox("Rover Teleoperation & Motion Console")
        ctrl_vbox = QVBoxLayout(ctrl_group)
        ctrl_vbox.setSpacing(10)

        # Prominent Forward / Backward Action Buttons (matching Test.py)
        action_vbox = QVBoxLayout()
        action_vbox.setSpacing(8)

        self.btn_fwd_large = QPushButton("▲ FORWARD (W)")
        self.btn_fwd_large.setFixedHeight(48)
        self.btn_fwd_large.setStyleSheet("background: #00adb5; color: white; font-size: 13px; font-weight: bold; border-radius: 8px;")
        self.btn_fwd_large.pressed.connect(lambda: self.send_motor_cmd("forward"))
        self.btn_fwd_large.released.connect(lambda: self.send_motor_cmd("stop"))
        action_vbox.addWidget(self.btn_fwd_large)

        self.btn_back_large = QPushButton("▼ BACKWARD (S)")
        self.btn_back_large.setFixedHeight(48)
        self.btn_back_large.setStyleSheet("background: #00adb5; color: white; font-size: 13px; font-weight: bold; border-radius: 8px;")
        self.btn_back_large.pressed.connect(lambda: self.send_motor_cmd("backward"))
        self.btn_back_large.released.connect(lambda: self.send_motor_cmd("stop"))
        action_vbox.addWidget(self.btn_back_large)

        ctrl_vbox.addLayout(action_vbox)

        # Steering D-Pad (Left, Stop, Right)
        dpad_grid = QGridLayout()
        dpad_grid.setSpacing(6)

        btn_left = QPushButton("◄ LEFT (A)")
        btn_left.setStyleSheet("background: #1e293b; color: white; font-size: 11px; padding: 10px;")
        btn_left.pressed.connect(lambda: self.send_motor_cmd("left"))
        btn_left.released.connect(lambda: self.send_motor_cmd("stop"))
        dpad_grid.addWidget(btn_left, 0, 0)

        btn_stop = QPushButton("■ STOP (Space)")
        btn_stop.setStyleSheet("background: #dc2626; color: white; font-weight: bold; font-size: 11px; padding: 10px;")
        btn_stop.clicked.connect(lambda: self.send_motor_cmd("stop"))
        dpad_grid.addWidget(btn_stop, 0, 1)

        btn_right = QPushButton("RIGHT (D) ►")
        btn_right.setStyleSheet("background: #1e293b; color: white; font-size: 11px; padding: 10px;")
        btn_right.pressed.connect(lambda: self.send_motor_cmd("right"))
        btn_right.released.connect(lambda: self.send_motor_cmd("stop"))
        dpad_grid.addWidget(btn_right, 0, 2)

        ctrl_vbox.addLayout(dpad_grid)

        # Speed PWM Slider
        self.lbl_rover_speed_text = QLabel("Motor PWM Speed: 220 / 255")
        self.lbl_rover_speed_text.setStyleSheet("color: #94a3b8; font-size: 10px; font-weight: bold;")
        ctrl_vbox.addWidget(self.lbl_rover_speed_text)

        self.speed_slider = QSlider(Qt.Horizontal)
        self.speed_slider.setRange(100, 255)
        self.speed_slider.setValue(220)
        self.speed_slider.valueChanged.connect(self.on_speed_changed)
        ctrl_vbox.addWidget(self.speed_slider)

        # Status HUD Info
        self.lbl_rover_link = QLabel("Status: Standby | Dev ESP32: 192.168.137.9")
        self.lbl_rover_link.setStyleSheet("font-weight: bold; color: #38bdf8; font-size: 10px;")
        ctrl_vbox.addWidget(self.lbl_rover_link)

        self.lbl_active_cmd = QLabel("Current State: STOP")
        self.lbl_active_cmd.setStyleSheet("font-weight: bold; color: #34d399; font-size: 10px;")
        ctrl_vbox.addWidget(self.lbl_active_cmd)

        hint_lbl = QLabel("🎮 Hold W/S or Buttons to Drive | Release to Stop | Space for Emergency Brake")
        hint_lbl.setStyleSheet("color: #64748b; font-size: 10px; font-family: monospace;")
        hint_lbl.setAlignment(Qt.AlignCenter)
        ctrl_vbox.addWidget(hint_lbl)
        ctrl_vbox.addStretch()

        hbox_content.addWidget(ctrl_group, stretch=2)
        vbox_main.addLayout(hbox_content, stretch=1)

        return tab_widget

    # ================= TAB 3: OVERVIEW SUMMARY =================

    def create_overview_tab(self):
        tab_widget = QWidget()
        vbox = QVBoxLayout(tab_widget)
        vbox.setSpacing(10)

        # Checkpoint Table
        vbox.addWidget(QLabel("Checkpoint Sensor Telemetry Table (PIN_Config.txt)"))
        self.tbl_checkpoints = QTableWidget(3, 8)
        self.tbl_checkpoints.setHorizontalHeaderLabels([
            "CP ID", "Location", "MQ5 (GPIO 33)", "MQ7 (GPIO 34)", "MQ135 (GPIO 35)", "PIR (GPIO 26)", "Sound (GPIO 27)", "DHT11 (GPIO 15)"
        ])
        self.tbl_checkpoints.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        vbox.addWidget(self.tbl_checkpoints)

        # Worker Table
        vbox.addWidget(QLabel("Personnel Biometric Vitals (ESP32 Band)"))
        self.tbl_workers = QTableWidget(2, 6)
        self.tbl_workers.setHorizontalHeaderLabels([
            "Band ID", "Personnel Name", "Heart Rate (BPM)", "Blood Oxygen (SpO2 %)", "Battery", "Relayed By"
        ])
        self.tbl_workers.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        vbox.addWidget(self.tbl_workers)

        return tab_widget

    # ================= BOTTOM ALERT PANEL =================

    def create_alert_panel(self):
        group = QGroupBox("Active Site Incidents & Alerts Stream")
        vbox = QVBoxLayout(group)
        vbox.setContentsMargins(6, 6, 6, 6)

        self.tbl_alerts = QTableWidget(0, 5)
        self.tbl_alerts.setHorizontalHeaderLabels(["Time", "Severity", "Category", "Device / Location", "Message"])
        self.tbl_alerts.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        vbox.addWidget(self.tbl_alerts)

        btn_ack = QPushButton("✓ Acknowledge All Alerts")
        btn_ack.clicked.connect(self.clear_alerts)
        vbox.addWidget(btn_ack, alignment=Qt.AlignRight)

        return group

    # ================= DATA UPDATE METHODS =================

    def update_all_sensor_graphs(self):
        selected_cp_id = self.cb_checkpoint.currentIndex() + 1
        selected_worker_id = self.cb_worker.currentIndex() + 1

        with state_lock:
            # 1. Update Checkpoint Sensor Graphs
            cp = checkpoints.get(selected_cp_id)
            if cp and len(cp["history"]) > 0:
                hist = list(cp["history"])
                x_indices = range(len(hist))
                loc = cp.get("location", f"CP-{selected_cp_id}")

                mq5_vals = [h["mq5"] for h in hist]
                mq7_vals = [h["mq7"] for h in hist]
                mq135_vals = [h["mq135"] for h in hist]
                sound_vals = [h["sound_level"] for h in hist]
                temp_vals = [h["temperature"] for h in hist]
                hum_vals = [h["humidity"] for h in hist]
                pir_vals = [1 if h["pir_motion"] else 0 for h in hist]

                self.canvas_mq5.update_data(x_indices, mq5_vals, f"{mq5_vals[-1]} ADC ({loc})")
                self.canvas_mq7.update_data(x_indices, mq7_vals, f"{mq7_vals[-1]} ADC")
                self.canvas_mq135.update_data(x_indices, mq135_vals, f"{mq135_vals[-1]} ADC")
                self.canvas_sound.update_data(x_indices, sound_vals, f"{sound_vals[-1]} ADC")
                self.canvas_temp.update_data(x_indices, temp_vals, f"{temp_vals[-1]:.1f}°C")
                self.canvas_hum.update_data(x_indices, hum_vals, f"{hum_vals[-1]:.0f}%")
                self.canvas_pir.update_data(x_indices, pir_vals, "MOTION DETECTED" if pir_vals[-1] == 1 else "CLEAR")
            else:
                for c in [self.canvas_mq5, self.canvas_mq7, self.canvas_mq135, self.canvas_sound, self.canvas_temp, self.canvas_hum, self.canvas_pir]:
                    c.update_data([], [])

            # 2. Update Worker Vitals Graphs
            b = bands.get(selected_worker_id)
            if b and len(b["history"]) > 0:
                hist = list(b["history"])
                x_indices = range(len(hist))
                name = b.get("worker_name", f"Band-{selected_worker_id}")

                pulse_vals = [h["pulse_bpm"] for h in hist]
                spo2_vals = [h["spo2"] for h in hist]

                self.canvas_pulse.update_data(x_indices, pulse_vals, f"{pulse_vals[-1]:.0f} BPM ({name})")
                self.canvas_spo2.update_data(x_indices, spo2_vals, f"{spo2_vals[-1]:.1f}%")
            else:
                self.canvas_pulse.update_data([], [])
                self.canvas_spo2.update_data([], [])

    def update_ui_state(self):
        with state_lock:
            # Update Rover Connection Banner & Badges
            is_online = (time.time() - rover_state.get("last_seen", 0)) < 6.0 and rover_state.get("last_seen", 0) > 0
            rover_state["is_online"] = is_online

            dev_ip = self.txt_dev_ip.text().strip() if hasattr(self, 'txt_dev_ip') else "192.168.137.9"

            if is_online or (self.cam_stream_thread and self.cam_stream_thread.isRunning()):
                cp_id = rover_state.get("connected_checkpoint_id", 0)
                loc = rover_state.get("connected_checkpoint_location", "Direct Dev/CAM ESP32")
                ip = rover_state.get("ip", dev_ip)
                self.rover_header_badge.setText(f"ROVER: ONLINE ({ip or loc})")
                self.rover_header_badge.setStyleSheet("background: #064e3b; color: #34d399; padding: 5px 10px; border-radius: 6px; font-weight: bold; font-size: 10px; border: 1px solid #059669;")

                self.rover_banner.setText(f"✔ ROVER CONNECTED ({loc} | Dev IP: {dev_ip}) - STEERING ACTIVE")
                self.rover_banner.setStyleSheet("background: #064e3b; color: #6ee7b7; padding: 8px 14px; border-radius: 6px; font-weight: bold; font-size: 11px; border: 1px solid #059669;")
                self.lbl_rover_link.setText(f"Status: Online | Dev ESP32: {dev_ip}")
            else:
                self.rover_header_badge.setText("ROVER: OFFLINE / STANDBY")
                self.rover_header_badge.setStyleSheet("background: #1e293b; color: #94a3b8; padding: 5px 10px; border-radius: 6px; font-weight: bold; font-size: 10px; border: 1px solid #334155;")

                self.rover_banner.setText("⚠ WAITING FOR ROVER TELEMETRY / STREAM...")
                self.rover_banner.setStyleSheet("background: #451a03; color: #fde047; padding: 8px 14px; border-radius: 6px; font-weight: bold; font-size: 11px; border: 1px solid #d97706;")
                self.lbl_rover_link.setText(f"Status: Standby | Dev ESP32: {dev_ip}")

            # Update Tables
            for row, (cp_id, cp) in enumerate(sorted(checkpoints.items())):
                if row < self.tbl_checkpoints.rowCount():
                    self.tbl_checkpoints.setItem(row, 0, QTableWidgetItem(f"#{cp['checkpoint_id']}"))
                    self.tbl_checkpoints.setItem(row, 1, QTableWidgetItem(str(cp['location'])))
                    self.tbl_checkpoints.setItem(row, 2, QTableWidgetItem(str(cp['mq5'])))
                    self.tbl_checkpoints.setItem(row, 3, QTableWidgetItem(str(cp['mq7'])))
                    self.tbl_checkpoints.setItem(row, 4, QTableWidgetItem(str(cp['mq135'])))
                    self.tbl_checkpoints.setItem(row, 5, QTableWidgetItem("MOTION" if cp['pir_motion'] else "CLEAR"))
                    self.tbl_checkpoints.setItem(row, 6, QTableWidgetItem(str(cp['sound_level'])))
                    self.tbl_checkpoints.setItem(row, 7, QTableWidgetItem(f"{cp['temperature']:.1f}°C / {cp['humidity']:.0f}%"))

            for row, (b_id, b) in enumerate(sorted(bands.items())):
                if row < self.tbl_workers.rowCount():
                    self.tbl_workers.setItem(row, 0, QTableWidgetItem(f"#{b['band_id']}"))
                    self.tbl_workers.setItem(row, 1, QTableWidgetItem(str(b['worker_name'])))
                    self.tbl_workers.setItem(row, 2, QTableWidgetItem(f"{b['pulse_bpm']:.0f} BPM"))
                    self.tbl_workers.setItem(row, 3, QTableWidgetItem(f"{b['spo2']:.1f}%"))
                    self.tbl_workers.setItem(row, 4, QTableWidgetItem(f"{b['battery_pct']}%"))
                    self.tbl_workers.setItem(row, 5, QTableWidgetItem(f"CP #{b['relayed_by']}"))

            # Render fallback if not streaming
            if self.cam_stream_thread is None and self.sim_thread and self.sim_thread.isRunning():
                self.render_rover_video()

    def render_rover_video(self):
        global latest_camera_frame_bytes
        if latest_camera_frame_bytes:
            image = QImage.fromData(latest_camera_frame_bytes)
            if not image.isNull():
                pixmap = QPixmap.fromImage(image)
                self.video_screen.setPixmap(pixmap.scaled(self.video_screen.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
                return

        # Generate synthetic HUD frame
        width, height = 480, 360
        pil_img = Image.new('RGB', (width, height), color=(15, 23, 42))
        draw = ImageDraw.Draw(pil_img)
        for x in range(0, width, 40):
            draw.line([(x, 0), (x, height)], fill=(30, 41, 59), width=1)
        for y in range(0, height, 40):
            draw.line([(0, y), (width, y)], fill=(30, 41, 59), width=1)
        cx, cy = width // 2, height // 2
        draw.line([(cx - 25, cy), (cx + 25, cy)], fill=(16, 185, 129), width=2)
        draw.line([(cx, cy - 25), (cx, cy + 25)], fill=(16, 185, 129), width=2)
        draw.text((12, 12), "ROVER CAM FEED [SIMULATOR ACTIVE]", fill=(56, 189, 248))
        draw.text((12, height - 25), f"COMMAND: {rover_state['command_name']} | SPEED: {rover_state['speed']}", fill=(56, 189, 248))
        buf = io.BytesIO()
        pil_img.save(buf, format='JPEG')
        image = QImage.fromData(buf.getvalue())
        pixmap = QPixmap.fromImage(image)
        self.video_screen.setPixmap(pixmap.scaled(self.video_screen.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def on_new_alert(self, alert):
        row = self.tbl_alerts.rowCount()
        self.tbl_alerts.insertRow(0)
        self.tbl_alerts.setItem(0, 0, QTableWidgetItem(alert["timestamp"]))
        
        sev_item = QTableWidgetItem(alert["level"])
        if alert["level"] == "CRITICAL":
            sev_item.setForeground(QColor("#f43f5e"))
        elif alert["level"] == "WARNING":
            sev_item.setForeground(QColor("#fbbf24"))
        else:
            sev_item.setForeground(QColor("#38bdf8"))
        self.tbl_alerts.setItem(0, 1, sev_item)

        self.tbl_alerts.setItem(0, 2, QTableWidgetItem(alert["category"]))
        self.tbl_alerts.setItem(0, 3, QTableWidgetItem(alert["device_id"]))
        self.tbl_alerts.setItem(0, 4, QTableWidgetItem(alert["message"]))

    def clear_alerts(self):
        alerts_log.clear()
        self.tbl_alerts.setRowCount(0)

    # ================= VIDEO STREAMING METHODS (TEST.PY ENGINE) =================

    def toggle_video_stream(self):
        if self.cam_stream_thread is None or not self.cam_stream_thread.isRunning():
            raw_ip = self.txt_cam_ip.text().strip()
            cam_ip = raw_ip.replace("http://", "").replace("https://", "").split(":")[0]

            if not cam_ip:
                QMessageBox.critical(self, "Error", "Please enter ESP32-CAM IP")
                return

            self.cam_stream_thread = MjpegStreamThread(cam_ip)
            self.cam_stream_thread.frame_ready.connect(self.on_mjpeg_frame_ready)
            self.cam_stream_thread.stream_error.connect(self.on_mjpeg_stream_error)
            self.cam_stream_thread.stream_stopped.connect(self.on_mjpeg_stream_stopped)
            self.cam_stream_thread.start()

            self.btn_toggle_video.setText("Stop Video")
            self.btn_toggle_video.setStyleSheet("background: #ff4d4d; color: white; padding: 6px 14px; font-weight: bold; border-radius: 6px; font-size: 11px;")
            log_alert_internal("INFO", "Rover", "ESP32-CAM", f"Connecting to live video stream at http://{cam_ip}:81/stream")
        else:
            self.stop_video_stream()

    def stop_video_stream(self):
        if self.cam_stream_thread:
            self.cam_stream_thread.stop()
            self.cam_stream_thread = None
        self.on_mjpeg_stream_stopped()

    def on_mjpeg_frame_ready(self, q_img):
        pixmap = QPixmap.fromImage(q_img)
        self.video_screen.setPixmap(pixmap.scaled(self.video_screen.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def on_mjpeg_stream_error(self, err_msg):
        log_alert_internal("WARNING", "Rover", "ESP32-CAM", f"Stream Error: {err_msg}")
        QMessageBox.critical(self, "Stream Error", f"Cannot connect to stream.\nMake sure Chrome tab is CLOSED!\n\nDetails: {err_msg}")
        self.stop_video_stream()

    def on_mjpeg_stream_stopped(self):
        self.btn_toggle_video.setText("Start Video")
        self.btn_toggle_video.setStyleSheet("background: #00adb5; color: white; padding: 6px 14px; font-weight: bold; border-radius: 6px; font-size: 11px;")
        if self.video_screen and (self.sim_thread is None or not self.sim_thread.isRunning()):
            self.video_screen.setPixmap(QPixmap())
            self.video_screen.setText("[ Video Stream Offline ]")

    def on_camera_frame_pushed(self, frame_bytes):
        if self.cam_stream_thread and self.cam_stream_thread.isRunning():
            return
        global latest_camera_frame_bytes
        latest_camera_frame_bytes = frame_bytes
        image = QImage.fromData(frame_bytes)
        if not image.isNull():
            pixmap = QPixmap.fromImage(image)
            self.video_screen.setPixmap(pixmap.scaled(self.video_screen.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    # ================= MOTOR CONTROL METHODS (TEST.PY ENGINE) =================

    def on_speed_changed(self, val):
        self.lbl_rover_speed_text.setText(f"Motor PWM Speed: {val} / 255")
        with state_lock:
            pending_rover_command["speed"] = val
            rover_state["speed"] = val

    def test_dev_ip_connection(self):
        dev_ip = self.txt_dev_ip.text().strip().replace("http://", "").replace("https://", "").rstrip("/")
        if not dev_ip:
            QMessageBox.warning(self, "Dev ESP32", "Please enter Dev ESP32 IP address.")
            return

        def _ping_worker():
            try:
                res = requests.get(f"http://{dev_ip}/", timeout=1.5)
                if res.status_code == 200:
                    log_alert_internal("INFO", "Rover", f"Dev ESP32 ({dev_ip})", "Ping Successful! Motor Controller Responded 200 OK")
                else:
                    log_alert_internal("WARNING", "Rover", f"Dev ESP32 ({dev_ip})", f"Dev ESP32 responded with HTTP status {res.status_code}")
            except Exception as e:
                log_alert_internal("WARNING", "Rover", f"Dev ESP32 ({dev_ip})", f"Ping failed: {e}")

        threading.Thread(target=_ping_worker, daemon=True).start()

    def send_motor_cmd(self, action):
        """
        Sends HTTP motor control command to Dev ESP32.
        Handles motor direction wiring inversion (Test.py default: Forward sends 'backward', Backward sends 'forward').
        """
        dev_ip = self.txt_dev_ip.text().strip().replace("http://", "").replace("https://", "").rstrip("/")
        if not dev_ip:
            return

        # Determine wire mapping
        inverted = self.chk_invert_motors.isChecked() if hasattr(self, 'chk_invert_motors') else True

        if action == "forward":
            http_cmd = "backward" if inverted else "forward"
            cmd_code = 1
        elif action == "backward":
            http_cmd = "forward" if inverted else "backward"
            cmd_code = 2
        elif action == "left":
            http_cmd = "left"
            cmd_code = 3
        elif action == "right":
            http_cmd = "right"
            cmd_code = 4
        else: # stop
            http_cmd = "stop"
            cmd_code = 0

        # Avoid redundant HTTP requests when holding keys
        if self.current_motor_cmd == http_cmd:
            return
        self.current_motor_cmd = http_cmd

        speed = self.speed_slider.value() if hasattr(self, 'speed_slider') else 220

        # Update UI Labels
        if hasattr(self, 'lbl_active_cmd'):
            self.lbl_active_cmd.setText(f"Current State: {action.upper()} (HTTP: /{http_cmd})")

        # Update global state
        with state_lock:
            rover_state["current_command"] = cmd_code
            rover_state["command_name"] = action.upper()
            rover_state["speed"] = speed if cmd_code != 0 else 0
            rover_state["last_seen"] = time.time()
            rover_state["is_online"] = True
            rover_state["ip"] = dev_ip

            pending_rover_command["rover_id"] = 1
            pending_rover_command["command"] = cmd_code
            pending_rover_command["speed"] = speed
            pending_rover_command["seq_id"] = int(time.time() * 1000) % 1000000
            pending_rover_command["timestamp"] = time.time()

        # 1. Asynchronous Direct HTTP Request (Test.py Engine)
        def _send_http_request(url_cmd):
            try:
                url = f"http://{dev_ip}/{url_cmd}"
                requests.get(url, timeout=0.5)
            except Exception:
                pass

        threading.Thread(target=_send_http_request, args=(http_cmd,), daemon=True).start()

        # 2. Direct UDP Broadcast (< 1ms)
        def _send_udp():
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                pkt = bytes([0x04, 1, cmd_code, speed])
                sock.sendto(pkt, ('<broadcast>', 4210))
                sock.sendto(pkt, ('192.168.137.255', 4210))
                if dev_ip and dev_ip not in ['<broadcast>', '192.168.137.255']:
                    sock.sendto(pkt, (dev_ip, 4210))
                sock.close()
            except Exception:
                pass

        threading.Thread(target=_send_udp, daemon=True).start()

    def keyPressEvent(self, event):
        if event.isAutoRepeat():
            return
        key = event.key()
        if key in (Qt.Key_W, Qt.Key_Up):
            self.send_motor_cmd("forward")
        elif key in (Qt.Key_S, Qt.Key_Down):
            self.send_motor_cmd("backward")
        elif key in (Qt.Key_A, Qt.Key_Left):
            self.send_motor_cmd("left")
        elif key in (Qt.Key_D, Qt.Key_Right):
            self.send_motor_cmd("right")
        elif key == Qt.Key_Space:
            self.send_motor_cmd("stop")
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.isAutoRepeat():
            return
        key = event.key()
        if key in (Qt.Key_W, Qt.Key_S, Qt.Key_A, Qt.Key_D, Qt.Key_Up, Qt.Key_Down, Qt.Key_Left, Qt.Key_Right):
            self.send_motor_cmd("stop")
        else:
            super().keyReleaseEvent(event)

    def toggle_simulator(self):
        if self.sim_thread is None or not self.sim_thread.isRunning():
            self.sim_thread = SimulatorThread()
            self.sim_thread.start()
            self.btn_sim.setText("⏹ Stop Simulator")
            self.btn_sim.setStyleSheet("background: #991b1b; color: white; padding: 6px 14px; font-size: 11px;")
        else:
            self.sim_thread.stop()
            self.sim_thread = None
            self.btn_sim.setText("▶ Start Built-in Simulator")
            self.btn_sim.setStyleSheet("background: #0284c7; color: white; padding: 6px 14px; font-size: 11px;")

    def closeEvent(self, event):
        try:
            self.send_motor_cmd("stop")
        except Exception:
            pass
        if self.cam_stream_thread:
            self.cam_stream_thread.stop()
            self.cam_stream_thread = None
        if self.sim_thread:
            self.sim_thread.stop()
            self.sim_thread = None
        event.accept()

# ================= APPLICATION ENTRY POINT =================

def run_flask_background():
    """Runs the Flask ingestion server on Port 3000 in background daemon thread."""
    try:
        app_flask.run(host='0.0.0.0', port=3000, debug=False, threaded=True, use_reloader=False)
    except Exception as e:
        print(f"[SERVER ERROR] Flask background server failed: {e}")

if __name__ == '__main__':
    # Start Flask background server on port 3000
    server_thread = threading.Thread(target=run_flask_background, daemon=True)
    server_thread.start()

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())

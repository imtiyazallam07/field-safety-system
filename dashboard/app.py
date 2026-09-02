#!/usr/bin/env python3
"""
Field Safety Monitoring System - Central Station Dashboard & Backend
Author: Field Safety Engineering Team
PRD Reference: field-safety-system-prd.md
Pin Config: PIN_Config.txt (MQ5: 33, MQ7: 34, MQ135: 35, HW-416-B: 26, HW-484-V0.2: 27, DHT11: 15)
"""

import os
import io
import time
import json
import threading
from collections import deque
from datetime import datetime

from flask import Flask, render_template, request, jsonify, send_file, Response
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from PIL import Image, ImageDraw, ImageFont
import numpy as np
import report_service

app = Flask(__name__, static_folder='static', template_folder='templates')

# ================= GLOBAL THREAD-SAFE STATE =================
state_lock = threading.RLock()

# Rolling history length (data points per metric)
HISTORY_MAXLEN = 120

# Active Checkpoints Data
checkpoints = {}

# Active Worker Bands Data
bands = {}

# Rover Status
rover_state = {
    "rover_id": 1,
    "current_command": 0, # 0=STOP, 1=FWD, 2=BACK, 3=LEFT, 4=RIGHT
    "command_name": "STOP",
    "speed": 0,
    "battery_pct": 90,
    "failsafe_triggered": False,
    "last_seen": 0,
    "is_connected": False,
    "is_online": False,
    "connected_checkpoint_id": None,
    "connected_checkpoint_location": None,
    "last_command_sent_time": time.time()
}

# Pending Rover Command to send on next poll/telemetry response
pending_rover_command = {
    "rover_id": 1,
    "command": 0, # CMD_STOP
    "speed": 0,
    "seq_id": 0,
    "timestamp": time.time()
}

# Alerts Feed
alerts = deque(maxlen=250)
alert_counter = 0
alert_cooldowns = {}
ALERT_COOLDOWN_SECONDS = 8.0

# Configurable Safety Thresholds
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

# Camera Frame Storage
latest_camera_frame_bytes = None
camera_chunks_buffer = {}
default_camera_frame_cache = None
default_camera_frame_last_text = ""

# Chart Image Cache
chart_cache = {}
CHART_CACHE_TTL = 1.5

# ================= HELPER FUNCTIONS =================

def log_alert(level, category, device_id, message):
    global alert_counter
    now = time.time()
    dedup_key = (category, str(device_id), level)

    with state_lock:
        last_logged = alert_cooldowns.get(dedup_key, 0)
        cooldown = 4.0 if level == "CRITICAL" else ALERT_COOLDOWN_SECONDS
        if now - last_logged < cooldown:
            return
        alert_cooldowns[dedup_key] = now
        alert_counter += 1
        alert_item = {
            "id": f"ALT-{alert_counter:04d}",
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "level": level, # "CRITICAL", "WARNING", "INFO"
            "category": category, # "Vitals", "Gas", "Motion", "Sound", "Environment", "Rover"
            "device_id": str(device_id),
            "message": message,
            "acked": False
        }
        alerts.appendleft(alert_item)
        print(f"[{level}] {category} alert on {device_id}: {message}")

def evaluate_thresholds_for_checkpoint(cp_data):
    cp_id = cp_data.get("checkpoint_id", 1)
    loc = cp_data.get("location", f"Checkpoint {cp_id}")

    # MQ5 Combustible Gas (GPIO 33)
    mq5 = cp_data.get("mq5", 0)
    if mq5 >= thresholds["mq5_critical"]:
        log_alert("CRITICAL", "Gas (MQ5)", f"{loc} (CP-{cp_id})", f"Combustible gas buildup spike! Raw: {mq5} (Threshold: {thresholds['mq5_critical']})")
    elif mq5 >= thresholds["mq5_warning"]:
        log_alert("WARNING", "Gas (MQ5)", f"{loc} (CP-{cp_id})", f"Elevated combustible gas level: {mq5}")

    # MQ7 Carbon Monoxide (GPIO 34)
    mq7 = cp_data.get("mq7", 0)
    if mq7 >= thresholds["mq7_critical"]:
        log_alert("CRITICAL", "Gas (MQ7)", f"{loc} (CP-{cp_id})", f"Toxic Carbon Monoxide dangerous level! Raw: {mq7}")
    elif mq7 >= thresholds["mq7_warning"]:
        log_alert("WARNING", "Gas (MQ7)", f"{loc} (CP-{cp_id})", f"Elevated Carbon Monoxide: {mq7}")

    # MQ135 Air Quality (GPIO 35)
    mq135 = cp_data.get("mq135", 0)
    if mq135 >= thresholds["mq135_critical"]:
        log_alert("CRITICAL", "Gas (MQ135)", f"{loc} (CP-{cp_id})", f"Hazardous air quality / toxic vapor detected: {mq135}")
    elif mq135 >= thresholds["mq135_warning"]:
        log_alert("WARNING", "Gas (MQ135)", f"{loc} (CP-{cp_id})", f"Degraded air quality: {mq135}")

    # Sound Spike HW-484-V0.2 (GPIO 27)
    sound = cp_data.get("sound_level", 0)
    if sound >= thresholds["sound_spike_threshold"]:
        log_alert("CRITICAL", "Sound", f"{loc} (CP-{cp_id})", f"Sudden loud sound spike! Possible explosion / collapse ({sound})")

    # PIR Motion HW-416-B (GPIO 26 - true/false)
    pir_motion = cp_data.get("pir_motion", False)
    if pir_motion:
        log_alert("INFO", "Motion", f"{loc} (CP-{cp_id})", "Personnel presence / motion detected in zone (HW-416-B)")

    # Temperature & Humidity DHT11 (GPIO 15)
    temp = cp_data.get("temperature", 25.0)
    if temp >= thresholds["temp_max"]:
        log_alert("WARNING", "Environment", f"{loc} (CP-{cp_id})", f"High ambient temperature: {temp:.1f}°C")

def evaluate_thresholds_for_band(b_data):
    band_id = b_data.get("band_id", 1)
    worker = b_data.get("worker_name", f"Band-{band_id}")
    spo2 = b_data.get("spo2", 98.0)
    pulse = b_data.get("pulse_bpm", 75.0)

    if spo2 <= thresholds["spo2_critical"]:
        log_alert("CRITICAL", "Vitals", f"{worker}", f"Hypoxia alert! Blood oxygen critically low: {spo2:.1f}% (Min Safe: {thresholds['spo2_critical']}%)")
    elif spo2 <= thresholds["spo2_warning"]:
        log_alert("WARNING", "Vitals", f"{worker}", f"Low blood oxygen reading: {spo2:.1f}%")

    if pulse >= thresholds["pulse_max"]:
        log_alert("WARNING", "Vitals", f"{worker}", f"Tachycardia / Elevated pulse rate: {pulse:.0f} BPM")
    elif pulse <= thresholds["pulse_min"] and pulse > 0:
        log_alert("WARNING", "Vitals", f"{worker}", f"Bradycardia / Abnormally low pulse rate: {pulse:.0f} BPM")

def generate_default_camera_frame():
    global default_camera_frame_cache, default_camera_frame_last_text
    status_text = f"{rover_state['command_name']}_{rover_state['speed']}"
    if default_camera_frame_cache and status_text == default_camera_frame_last_text:
        return default_camera_frame_cache

    width, height = 320, 240
    img = Image.new('RGB', (width, height), color=(15, 23, 42))
    draw = ImageDraw.Draw(img)

    for x in range(0, width, 40):
        draw.line([(x, 0), (x, height)], fill=(30, 41, 59), width=1)
    for y in range(0, height, 40):
        draw.line([(0, y), (width, y)], fill=(30, 41, 59), width=1)

    cx, cy = width // 2, height // 2
    draw.line([(cx - 20, cy), (cx + 20, cy)], fill=(16, 185, 129), width=2)
    draw.line([(cx, cy - 20), (cx, cy + 20)], fill=(16, 185, 129), width=2)
    draw.ellipse([(cx - 30, cy - 30), (cx + 30, cy + 30)], outline=(16, 185, 129), width=1)

    draw.text((10, 10), "ROVER CAM FEED [STANDBY]", fill=(244, 63, 94))
    draw.text((10, 25), f"TIME: {datetime.now().strftime('%H:%M:%S')}", fill=(148, 163, 184))
    draw.text((10, height - 25), f"STATUS: {rover_state['command_name']} | SPEED: {rover_state['speed']}", fill=(56, 189, 248))

    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=85)
    frame_bytes = buf.getvalue()
    default_camera_frame_cache = frame_bytes
    default_camera_frame_last_text = status_text
    return frame_bytes

# ================= API ENDPOINTS =================

@app.route('/')
@app.route('/landing')
def landing_page():
    """Serves the CoalGov AI SIH Landing Page."""
    return render_template('landing.html')

@app.route('/dashboard')
@app.route('/station')
def dashboard_page():
    """Serves the Central Command Center Overview."""
    return render_template('index.html', initial_view='overview')

@app.route('/incidents')
def incidents_page():
    """Serves the Dedicated Incident Stream View."""
    return render_template('index.html', initial_view='incidents')

@app.route('/workers')
def workers_page():
    """Serves the Dedicated Worker Vitals View."""
    return render_template('index.html', initial_view='workers')

@app.route('/checkpoints')
def checkpoints_page():
    """Serves the Dedicated Checkpoint Suite View."""
    return render_template('index.html', initial_view='checkpoints')

@app.route('/telemetry')
def telemetry_page():
    """Serves the Dedicated Trend Telemetry View."""
    return render_template('index.html', initial_view='charts')

@app.route('/rover')
def rover_page():
    """Serves the Dedicated Recon Rover Console View."""
    return render_template('index.html', initial_view='rover')

@app.route('/network')
def network_page():
    """Serves the Dedicated Mesh Network Topology View."""
    return render_template('index.html', initial_view='topology')

@app.route('/reports')
def reports_page():
    """Serves the AI Safety Analysis Report page."""
    return render_template('reports.html')

@app.route('/api/report/generate', methods=['POST'])
def generate_report():
    """Generates an AI-powered safety report from existing data (read-only)."""
    with state_lock:
        snapshot = report_service.build_report_snapshot(
            checkpoints, bands, rover_state, alerts, thresholds
        )
    gemini_analysis = report_service.call_gemini_analysis(snapshot)
    full_report = report_service.generate_full_report(snapshot, gemini_analysis)
    return jsonify(full_report), 200

@app.route('/api/telemetry', methods=['POST'])
def receive_telemetry():
    """Main checkpoint telemetry ingest."""
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"status": "error", "message": "Invalid JSON"}), 400

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

        # Relayed Band Vitals
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
                    "hops": 1,
                    "last_seen": now_ts,
                    "history": deque(maxlen=HISTORY_MAXLEN)
                }
            b = bands[b_id]
            b["worker_name"] = b_info.get("worker_name", b["worker_name"])
            b["pulse_bpm"] = b_info.get("pulse_bpm", b["pulse_bpm"])
            b["spo2"] = b_info.get("spo2", b["spo2"])
            b["battery_pct"] = b_info.get("battery_pct", b["battery_pct"])
            b["relayed_by"] = cp_id
            b["hops"] = 1
            b["last_seen"] = now_ts
            b["history"].append({
                "timestamp": now_str,
                "ts": now_ts,
                "pulse_bpm": b["pulse_bpm"],
                "spo2": b["spo2"]
            })
            evaluate_thresholds_for_band(b)

        # Relayed Rover Status
        if "rover" in data and isinstance(data["rover"], dict):
            r_info = data["rover"]
            cmd_code = r_info.get("current_command", 0)
            cmd_names = {0: "STOP", 1: "FORWARD", 2: "BACKWARD", 3: "LEFT", 4: "RIGHT"}
            
            was_offline = (now_ts - rover_state.get("last_seen", 0)) > 6.0 or not rover_state.get("is_connected", False)

            rover_state["current_command"] = cmd_code
            rover_state["command_name"] = cmd_names.get(cmd_code, "STOP")
            rover_state["speed"] = r_info.get("speed", 0)
            rover_state["battery_pct"] = r_info.get("battery_pct", rover_state["battery_pct"])
            rover_state["failsafe_triggered"] = bool(r_info.get("failsafe_triggered", False))
            rover_state["last_seen"] = now_ts
            rover_state["is_connected"] = True
            rover_state["is_online"] = True
            rover_state["connected_checkpoint_id"] = cp_id
            rover_state["connected_checkpoint_location"] = loc

            if was_offline:
                log_alert("INFO", "Rover", f"Rover #{rover_state['rover_id']}", f"Rover successfully CONNECTED to Checkpoint #{cp_id} ({loc})")

    evaluate_thresholds_for_checkpoint(cp)

    # Return pending command to checkpoint to relay down to rover
    resp = {"status": "ok"}
    if pending_rover_command["seq_id"] > 0:
        resp["rover_command"] = pending_rover_command

    return jsonify(resp), 200

@app.route('/api/telemetry/band', methods=['POST'])
def receive_band_telemetry():
    """Receives direct/relayed worker band vitals."""
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"status": "error"}), 400

    b_id = data.get("band_id", 1)
    now_ts = time.time()
    now_str = datetime.now().strftime("%H:%M:%S")

    with state_lock:
        if b_id not in bands:
            bands[b_id] = {
                "band_id": b_id,
                "worker_name": data.get("worker_name", f"Worker-{b_id}"),
                "pulse_bpm": 75.0,
                "spo2": 98.0,
                "battery_pct": 100,
                "relayed_by": data.get("relayed_by", 1),
                "hops": data.get("hops", 1),
                "last_seen": now_ts,
                "history": deque(maxlen=HISTORY_MAXLEN)
            }
        b = bands[b_id]
        b["worker_name"] = data.get("worker_name", b["worker_name"])
        b["pulse_bpm"] = data.get("pulse_bpm", b["pulse_bpm"])
        b["spo2"] = data.get("spo2", b["spo2"])
        b["battery_pct"] = data.get("battery_pct", b["battery_pct"])
        b["relayed_by"] = data.get("relayed_by", b.get("relayed_by", 1))
        b["hops"] = data.get("hops", 1)
        b["last_seen"] = now_ts
        b["history"].append({
            "timestamp": now_str,
            "ts": now_ts,
            "pulse_bpm": b["pulse_bpm"],
            "spo2": b["spo2"]
        })
        evaluate_thresholds_for_band(b)

    return jsonify({"status": "ok"}), 200

@app.route('/api/telemetry/rover', methods=['POST'])
def receive_rover_telemetry():
    """Receives direct telemetry from Rover Driver over Wi-Fi."""
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
            log_alert("INFO", "Rover", f"Rover #{rover_state['rover_id']}", f"Rover Driver DIRECTLY CONNECTED to PC over Wi-Fi (IP: {rover_state['ip']})")

    return jsonify({"status": "ok"}), 200

@app.route('/api/camera_frame', methods=['POST'])
def receive_camera_frame():
    """Direct JPEG frame upload from rover camera."""
    global latest_camera_frame_bytes
    img_data = request.get_data()
    if img_data:
        with state_lock:
            latest_camera_frame_bytes = img_data
        return jsonify({"status": "ok", "bytes": len(img_data)}), 200
    return jsonify({"status": "empty payload"}), 400

@app.route('/api/camera_chunk', methods=['POST'])
def receive_camera_chunk():
    """Reassembles chunked ESP-NOW camera frames."""
    global latest_camera_frame_bytes
    chunk_raw = request.get_data()
    if len(chunk_raw) < 6:
        return jsonify({"status": "invalid chunk"}), 400

    frame_id = int.from_bytes(chunk_raw[1:3], byteorder='little')
    chunk_idx = chunk_raw[3]
    total_chunks = chunk_raw[4]
    chunk_len = chunk_raw[5]
    payload = chunk_raw[6:6+chunk_len]

    with state_lock:
        if frame_id not in camera_chunks_buffer:
            camera_chunks_buffer[frame_id] = {"total": total_chunks, "chunks": {}}

        buf = camera_chunks_buffer[frame_id]
        buf["chunks"][chunk_idx] = payload

        if len(buf["chunks"]) == total_chunks:
            full_frame = bytearray()
            for idx in range(total_chunks):
                if idx in buf["chunks"]:
                    full_frame.extend(buf["chunks"][idx])
            latest_camera_frame_bytes = bytes(full_frame)
            del camera_chunks_buffer[frame_id]
            if len(camera_chunks_buffer) > 5:
                oldest = min(camera_chunks_buffer.keys())
                del camera_chunks_buffer[oldest]

    return jsonify({"status": "chunk received"}), 200

@app.route('/api/camera/latest.jpg')
def get_camera_latest():
    """Returns the latest rover camera frame."""
    with state_lock:
        frame_data = latest_camera_frame_bytes

    if not frame_data:
        frame_data = generate_default_camera_frame()

    return Response(frame_data, mimetype='image/jpeg')

import socket

def broadcast_low_latency_udp_rover(cmd_code, speed):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        pkt = bytes([0x04, 1, cmd_code, speed])
        sock.sendto(pkt, ('<broadcast>', 4210))
        sock.sendto(pkt, ('192.168.137.255', 4210))
        sock.close()
    except Exception:
        pass

@app.route('/api/rover/command', methods=['POST'])
def send_rover_command():
    """Sends a steering command to the rover with instant sub-millisecond UDP broadcast."""
    data = request.get_json(force=True, silent=True) or {}
    cmd_str = str(data.get("command", "STOP")).upper()
    speed = int(data.get("speed", 200))
    speed = max(0, min(255, speed))

    cmd_map = {
        "STOP": 0,
        "FORWARD": 1,
        "BACKWARD": 2,
        "LEFT": 3,
        "RIGHT": 4
    }
    cmd_code = cmd_map.get(cmd_str, 0)

    # 1. Fire ultra-fast UDP broadcast to Checkpoint (Port 4210) in < 1ms
    broadcast_low_latency_udp_rover(cmd_code, speed)

    with state_lock:
        pending_rover_command["rover_id"] = 1
        pending_rover_command["command"] = cmd_code
        pending_rover_command["speed"] = speed
        pending_rover_command["seq_id"] = int(time.time() * 1000) % 1000000
        pending_rover_command["timestamp"] = time.time()

        rover_state["current_command"] = cmd_code
        rover_state["command_name"] = cmd_str
        rover_state["speed"] = speed
        rover_state["last_command_sent_time"] = time.time()

    return jsonify({
        "status": "command_queued",
        "command": cmd_str,
        "speed": speed,
        "seq_id": pending_rover_command["seq_id"]
    }), 200

@app.route('/api/rover/command/poll', methods=['GET'])
def poll_rover_command():
    """Allows Rover/Checkpoint to poll for active drive command."""
    with state_lock:
        return jsonify(pending_rover_command), 200

@app.route('/api/data')
def get_all_data():
    """Returns complete system snapshot."""
    with state_lock:
        cp_list = []
        for cp_id, cp in sorted(checkpoints.items()):
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
                "last_seen": cp["last_seen"],
                "is_online": (time.time() - cp["last_seen"]) < 10
            })

        band_list = []
        for b_id, b in sorted(bands.items()):
            band_list.append({
                "band_id": b["band_id"],
                "worker_name": b["worker_name"],
                "pulse_bpm": b["pulse_bpm"],
                "spo2": b["spo2"],
                "battery_pct": b["battery_pct"],
                "relayed_by": b["relayed_by"],
                "hops": b["hops"],
                "last_seen": b["last_seen"],
                "is_online": (time.time() - b["last_seen"]) < 10
            })

        # Calculate live rover online connection status
        rover_online = (time.time() - rover_state.get("last_seen", 0)) < 6.0 and rover_state.get("last_seen", 0) > 0
        rover_state["is_online"] = rover_online
        if not rover_online:
            rover_state["is_connected"] = False

        alert_list = list(alerts)[:30]

        return jsonify({
            "checkpoints": cp_list,
            "bands": band_list,
            "rover": rover_state,
            "alerts": alert_list,
            "thresholds": thresholds,
            "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })

@app.route('/api/alerts/ack', methods=['POST'])
def ack_alerts():
    """Clears / acknowledges alerts."""
    with state_lock:
        alerts.clear()
    return jsonify({"status": "alerts cleared"}), 200

@app.route('/api/thresholds', methods=['GET', 'POST'])
def handle_thresholds():
    """Get or update thresholds."""
    if request.method == 'POST':
        data = request.get_json(force=True, silent=True) or {}
        with state_lock:
            for k, v in data.items():
                if k in thresholds:
                    thresholds[k] = float(v)
        return jsonify({"status": "updated", "thresholds": thresholds})
    return jsonify(thresholds)

# ================= DYNAMIC MATPLOTLIB CHARTS =================

@app.route('/api/charts/<chart_type>.png')
def generate_chart(chart_type):
    now = time.time()
    cached = chart_cache.get(chart_type)
    if cached and (now - cached["timestamp"]) < CHART_CACHE_TTL:
        return Response(cached["bytes"], mimetype='image/png')

    with state_lock:
        cp_snapshot = {
            cp_id: {
                "location": cp.get("location", f"CP-{cp_id}"),
                "history": list(cp.get("history", []))
            }
            for cp_id, cp in checkpoints.items()
        }
        band_snapshot = {
            b_id: {
                "worker_name": b.get("worker_name", f"Band-{b_id}"),
                "history": list(b.get("history", []))
            }
            for b_id, b in bands.items()
        }
        th_snapshot = dict(thresholds)

    fig, ax = plt.subplots(figsize=(7.5, 3.8), dpi=100)
    fig.patch.set_facecolor('#0A0A0B')
    ax.set_facecolor('#131314')

    ax.tick_params(colors='#d3c5ac', labelsize=8)
    for spine in ax.spines.values():
        spine.set_color('#2D2E32')
    ax.grid(True, color='#2D2E32', linestyle='--', alpha=0.6)

    if chart_type == 'gas':
        ax.set_title('Live Gas Levels (MQ5 / MQ7 / MQ135)', color='#FBBF24', fontsize=11, fontweight='bold', pad=10)
        ax.set_ylabel('Sensor Reading (ADC / PPM)', color='#d3c5ac', fontsize=9)
        has_data = False

        for cp_id, cp in cp_snapshot.items():
            hist = cp["history"]
            if not hist:
                continue
            has_data = True
            indices = range(len(hist))
            loc = cp.get("location", f"CP-{cp_id}")

            mq5_vals = [h["mq5"] for h in hist]
            mq7_vals = [h["mq7"] for h in hist]
            mq135_vals = [h["mq135"] for h in hist]

            ax.plot(indices, mq5_vals, label=f'{loc} - MQ5 (Combustible)', color='#FBBF24', linewidth=1.8)
            ax.plot(indices, mq7_vals, label=f'{loc} - MQ7 (CO)', color='#FF0000', linewidth=1.8, linestyle='--')
            ax.plot(indices, mq135_vals, label=f'{loc} - MQ135 (Air Quality)', color='#F97316', linewidth=1.8, linestyle=':')

        ax.axhline(y=th_snapshot["mq5_critical"], color='#FF0000', linestyle='-.', linewidth=1.5, label=f'MQ5 Danger ({th_snapshot["mq5_critical"]:.0f})')
        ax.axhline(y=th_snapshot["mq7_critical"], color='#93000a', linestyle='-.', linewidth=1.5, label=f'MQ7 Danger ({th_snapshot["mq7_critical"]:.0f})')

        if not has_data:
            ax.text(0.5, 0.5, 'Waiting for Checkpoint Gas Telemetry...', color='#9c8f79', ha='center', va='center', transform=ax.transAxes)

    elif chart_type == 'noise':
        ax.set_title('Site Noise & Sound Peak Levels (HW-484-V0.2)', color='#FBBF24', fontsize=11, fontweight='bold', pad=10)
        ax.set_ylabel('Sound Peak Amplitude (ADC)', color='#d3c5ac', fontsize=9)
        has_data = False
        colors = ['#FBBF24', '#F97316', '#22C55E', '#FF0000']

        for i, (cp_id, cp) in enumerate(cp_snapshot.items()):
            hist = cp["history"]
            if not hist:
                continue
            has_data = True
            indices = range(len(hist))
            sound_vals = [h["sound_level"] for h in hist]
            loc = cp.get("location", f"CP-{cp_id}")
            ax.plot(indices, sound_vals, label=f'{loc} Sound', color=colors[i % len(colors)], linewidth=2.0)

        ax.axhline(y=th_snapshot["sound_spike_threshold"], color='#FF0000', linestyle='--', linewidth=1.6, label=f'Explosion Spike Threshold ({th_snapshot["sound_spike_threshold"]:.0f})')

        if not has_data:
            ax.text(0.5, 0.5, 'Waiting for Acoustic Sensor Telemetry...', color='#9c8f79', ha='center', va='center', transform=ax.transAxes)

    elif chart_type == 'environment':
        ax.set_title('Environmental Conditions (DHT11 Temp & Humidity)', color='#FBBF24', fontsize=11, fontweight='bold', pad=10)
        ax.set_ylabel('Temp (°C) / Humidity (%)', color='#d3c5ac', fontsize=9)
        has_data = False

        for cp_id, cp in cp_snapshot.items():
            hist = cp["history"]
            if not hist:
                continue
            has_data = True
            indices = range(len(hist))
            loc = cp.get("location", f"CP-{cp_id}")
            t_vals = [h["temperature"] for h in hist]
            h_vals = [h["humidity"] for h in hist]

            ax.plot(indices, t_vals, label=f'{loc} Temp (°C)', color='#F97316', linewidth=1.8)
            ax.plot(indices, h_vals, label=f'{loc} Humidity (%)', color='#22C55E', linewidth=1.8, linestyle='--')

        ax.axhline(y=th_snapshot["temp_max"], color='#FF0000', linestyle=':', linewidth=1.4, label=f'Max Safe Temp ({th_snapshot["temp_max"]:.0f}°C)')

        if not has_data:
            ax.text(0.5, 0.5, 'Waiting for DHT11 Telemetry...', color='#9c8f79', ha='center', va='center', transform=ax.transAxes)

    elif chart_type == 'vitals':
        ax.set_title('Personnel Vitals (SpO2 & Heart Rate)', color='#FBBF24', fontsize=11, fontweight='bold', pad=10)
        ax.set_ylabel('SpO2 (%) / Pulse (BPM)', color='#d3c5ac', fontsize=9)
        has_data = False
        colors = ['#22C55E', '#FBBF24', '#F97316']

        for i, (b_id, b) in enumerate(band_snapshot.items()):
            hist = b["history"]
            if not hist:
                continue
            has_data = True
            indices = range(len(hist))
            name = b.get("worker_name", f"Band-{b_id}")
            spo2_vals = [h["spo2"] for h in hist]
            pulse_vals = [h["pulse_bpm"] for h in hist]

            ax.plot(indices, spo2_vals, label=f'{name} SpO2 (%)', color=colors[i % len(colors)], linewidth=2.0)
            ax.plot(indices, pulse_vals, label=f'{name} Pulse (BPM)', color='#FBBF24', linewidth=1.5, linestyle='--')

        ax.axhline(y=th_snapshot["spo2_critical"], color='#FF0000', linestyle='--', linewidth=1.6, label=f'Critical SpO2 ({th_snapshot["spo2_critical"]:.0f}%)')
        ax.axhline(y=th_snapshot["spo2_warning"], color='#F97316', linestyle=':', linewidth=1.4, label=f'Warning SpO2 ({th_snapshot["spo2_warning"]:.0f}%)')

        if not has_data:
            ax.text(0.5, 0.5, 'Waiting for Worker Band Telemetry...', color='#9c8f79', ha='center', va='center', transform=ax.transAxes)

    ax.legend(loc='upper right', facecolor='#0A0A0B', edgecolor='#2D2E32', fontsize=7, labelcolor='#e5e2e3')

    fig.tight_layout()
    img_buf = io.BytesIO()
    fig.savefig(img_buf, format='png', facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close(fig)
    img_buf.seek(0)
    chart_bytes = img_buf.getvalue()

    chart_cache[chart_type] = {
        "bytes": chart_bytes,
        "timestamp": time.time()
    }

    return Response(chart_bytes, mimetype='image/png')

# ================= SERVER INITIALIZATION =================
if __name__ == '__main__':
    print("==========================================================")
    print("  FIELD SAFETY MONITORING SYSTEM - CENTRAL STATION")
    print("  Server running on: http://0.0.0.0:3000")
    print("  PIN Config: MQ5(33), MQ7(34), MQ135(35), HW-416-B PIR(26), HW-484 Sound(27), DHT11(15)")
    print("==========================================================")
    app.run(host='0.0.0.0', port=3000, debug=False, threaded=True)

#!/usr/bin/env python3
"""
Field Safety Monitoring System - Telemetry & Sensor Mesh Simulator
Soldered Configuration (matches PIN_Config.txt):
- MQ5: 33, MQ7: 34, MQ135: 35, HW-416-B PIR: 26 (true/false), HW-484-V0.2 Sound: 27, DHT11: 15
"""

import time
import random
import io
import math
import requests
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

STATION_URL = "http://127.0.0.1:3000"

# Simulation State
workers = [
    {"band_id": 1, "name": "Worker-101 (Alex Mercer)", "pulse": 72.0, "spo2": 98.5, "batt": 94, "assigned_cp": 1},
    {"band_id": 2, "name": "Worker-102 (Sarah Connor)", "pulse": 78.0, "spo2": 99.0, "batt": 89, "assigned_cp": 2},
    {"band_id": 3, "name": "Worker-103 (David Vance)", "pulse": 68.0, "spo2": 97.8, "batt": 96, "assigned_cp": 3}
]

checkpoints = [
    {"id": 1, "location": "Zone-A (Main Shaft)", "wifi_direct": True, "mq5": 110, "mq7": 45, "mq135": 80, "temp": 24.2, "hum": 52.0},
    {"id": 2, "location": "Zone-B (Tunnel Junction)", "wifi_direct": False, "relays_through": 1, "mq5": 140, "mq7": 55, "mq135": 95, "temp": 26.8, "hum": 61.0},
    {"id": 3, "location": "Zone-C (Deep Extraction)", "wifi_direct": False, "relays_through": 2, "mq5": 160, "mq7": 70, "mq135": 110, "temp": 29.5, "hum": 74.0}
]

rover_sim = {
    "rover_id": 1,
    "current_command": 0, # 0=STOP, 1=FWD, 2=BACK, 3=LEFT, 4=RIGHT
    "speed": 0,
    "batt": 91,
    "x_pos": 0.0,
    "heading_deg": 0.0
}

frame_counter = 0
simulation_step = 0

def generate_simulated_camera_frame(cmd_name, speed, heading):
    """Generates synthetic hazardous tunnel video frames with dynamic motion perspective."""
    global frame_counter
    frame_counter += 1
    width, height = 320, 240
    img = Image.new('RGB', (width, height), color=(15, 20, 30))
    draw = ImageDraw.Draw(img)

    vanishing_x = width // 2 + int(math.sin(math.radians(heading)) * 30)
    vanishing_y = height // 2 - 10

    line_color = (60, 75, 95)
    floor_color = (25, 30, 40)

    draw.polygon([(0, height), (width, height), (vanishing_x + 30, vanishing_y + 20), (vanishing_x - 30, vanishing_y + 20)], fill=floor_color)
    draw.line([(0, 0), (vanishing_x - 30, vanishing_y - 20)], fill=line_color, width=2)
    draw.line([(width, 0), (vanishing_x + 30, vanishing_y - 20)], fill=line_color, width=2)
    draw.line([(0, height), (vanishing_x - 30, vanishing_y + 20)], fill=line_color, width=2)
    draw.line([(width, height), (vanishing_x + 30, vanishing_y + 20)], fill=line_color, width=2)

    offset = (frame_counter * 5) % 40 if cmd_name == "FORWARD" else ((-frame_counter * 5) % 40 if cmd_name == "BACKWARD" else 0)
    for dist_step in range(1, 6):
        frac = dist_step / 6.0
        y_top = int(vanishing_y - 20 - (vanishing_y - 20) * frac)
        y_bot = int(vanishing_y + 20 + (height - vanishing_y - 20) * frac)
        x_left = int(vanishing_x - 30 - (vanishing_x - 30) * frac)
        x_right = int(vanishing_x + 30 + (width - vanishing_x - 30) * frac)
        draw.rectangle([(x_left, y_top), (x_right, y_bot)], outline=(45, 60, 80), width=1)

    cx, cy = width // 2, height // 2
    draw.line([(cx - 15, cy), (cx + 15, cy)], fill=(16, 185, 129), width=1)
    draw.line([(cx, cy - 15), (cx, cy + 15)], fill=(16, 185, 129), width=1)
    draw.rectangle([(cx - 25, cy - 25), (cx + 25, cy + 25)], outline=(16, 185, 129), width=1)

    draw.text((8, 8), f"ROVER CAM #01 | FPS: 5.0 | {datetime.now().strftime('%H:%M:%S')}", fill=(244, 63, 94))
    draw.text((8, 22), f"HEADING: {heading:.1f}° | SPEED: {speed}", fill=(56, 189, 248))
    draw.text((8, height - 20), f"COMMAND: {cmd_name}", fill=(250, 204, 21))

    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=80)
    return buf.getvalue()

def run_simulation_tick():
    global simulation_step, rover_sim
    simulation_step += 1

    # 1. Poll Station for Active Rover Drive Command
    try:
        r = requests.get(f"{STATION_URL}/api/rover/command/poll", timeout=1.0)
        if r.status_code == 200:
            cmd_data = r.json()
            rover_sim["current_command"] = cmd_data.get("command", 0)
            rover_sim["speed"] = cmd_data.get("speed", 0)
            cmd_names = {0: "STOP", 1: "FORWARD", 2: "BACKWARD", 3: "LEFT", 4: "RIGHT"}
            cmd_name = cmd_names.get(rover_sim["current_command"], "STOP")

            if cmd_name == "LEFT":
                rover_sim["heading_deg"] = (rover_sim["heading_deg"] - 10) % 360
            elif cmd_name == "RIGHT":
                rover_sim["heading_deg"] = (rover_sim["heading_deg"] + 10) % 360
    except Exception:
        cmd_name = "STOP"

    # 2. Transmit Simulated Camera Frame
    try:
        frame_bytes = generate_simulated_camera_frame(cmd_name, rover_sim["speed"], rover_sim["heading_deg"])
        requests.post(f"{STATION_URL}/api/camera_frame", data=frame_bytes, headers={"Content-Type": "image/jpeg"}, timeout=1.0)
    except Exception:
        pass

    # 3. Simulate Worker Biometrics
    for w in workers:
        w["pulse"] += random.uniform(-1.5, 1.5)
        w["pulse"] = max(55.0, min(115.0, w["pulse"]))

        w["spo2"] += random.uniform(-0.3, 0.3)
        w["spo2"] = max(95.0, min(99.5, w["spo2"]))

        # Periodic Scenario: Worker 101 hypoxia drop between step 25 and 35
        if w["band_id"] == 1 and 25 <= (simulation_step % 60) <= 35:
            w["spo2"] = 87.5 + random.uniform(-1.0, 1.0)
            w["pulse"] = 125.0 + random.uniform(-3.0, 3.0)

    # 4. Simulate Checkpoints (Soldered Sensors: MQ5, MQ7, MQ135, PIR HW-416-B, Sound HW-484, DHT11)
    for cp in checkpoints:
        cp["mq5"] += random.randint(-4, 4)
        cp["mq7"] += random.randint(-3, 3)
        cp["mq135"] += random.randint(-3, 3)
        cp["temp"] += random.uniform(-0.1, 0.1)
        cp["hum"] += random.uniform(-0.2, 0.2)

        cp["mq5"] = max(60, min(250, cp["mq5"]))
        cp["mq7"] = max(20, min(150, cp["mq7"]))
        cp["mq135"] = max(50, min(200, cp["mq135"]))

        # PIR Motion HW-416-B (true/false only)
        pir_motion = random.random() < 0.20

        # Sound Sensor HW-484-V0.2
        sound_peak = random.randint(20, 95)

        # Scenarios:
        # Gas Leak at Checkpoint 2 (Zone-B)
        if cp["id"] == 2 and 15 <= (simulation_step % 60) <= 25:
            cp["mq5"] = 680 + random.randint(-20, 30)
            cp["mq7"] = 490 + random.randint(-15, 20)

        # Sound Spike at Checkpoint 1 (Zone-A)
        if cp["id"] == 1 and 40 <= (simulation_step % 60) <= 45:
            sound_peak = 450 + random.randint(0, 80)
            pir_motion = True

        assigned_worker = next((w for w in workers if w["assigned_cp"] == cp["id"]), None)

        payload = {
            "checkpoint_id": cp["id"],
            "location": cp["location"],
            "mq5": cp["mq5"],
            "mq7": cp["mq7"],
            "mq135": cp["mq135"],
            "pir_motion": pir_motion, # HW-416-B: boolean true/false
            "sound_level": sound_peak, # HW-484-V0.2
            "temperature": round(cp["temp"], 1),
            "humidity": round(cp["hum"], 1),
            "uptime_ms": int(time.time() * 1000)
        }

        if assigned_worker:
            payload["band"] = {
                "band_id": assigned_worker["band_id"],
                "worker_name": assigned_worker["name"],
                "pulse_bpm": round(assigned_worker["pulse"], 1),
                "spo2": round(assigned_worker["spo2"], 1),
                "battery_pct": assigned_worker["batt"]
            }

        if cp["id"] == 1:
            payload["rover"] = {
                "rover_id": rover_sim["rover_id"],
                "current_command": rover_sim["current_command"],
                "speed": rover_sim["speed"],
                "battery_pct": rover_sim["batt"],
                "failsafe_triggered": False
            }

        try:
            requests.post(f"{STATION_URL}/api/telemetry", json=payload, timeout=1.5)
        except Exception as e:
            print(f"[SIMULATOR] Error sending telemetry for CP-{cp['id']}: {e}")

def main():
    print("=================================================================")
    print("  FIELD SAFETY MONITORING SYSTEM - TELEMETRY MESH SIMULATOR")
    print(f"  Target Station URL: {STATION_URL}")
    print("  Checkpoints: MQ5(33), MQ7(34), MQ135(35), HW-416-B PIR(26: true/false), HW-484 Sound(27), DHT11(15)")
    print("  Simulating: 3 Checkpoints, 3 Worker Bands, 1 Rover (Cam+Driver)")
    print("=================================================================")

    while True:
        try:
            run_simulation_tick()
            print(f"[SIMULATOR Step {simulation_step:03d}] Emitted Telemetry (PIR HW-416-B true/false). Rover Cmd: {rover_sim['current_command']}")
            time.sleep(1.0)
        except KeyboardInterrupt:
            print("\nSimulation stopped.")
            break
        except Exception as e:
            print(f"Error in simulation loop: {e}")
            time.sleep(1.0)

if __name__ == '__main__':
    main()

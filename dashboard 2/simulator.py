#!/usr/bin/env python3
"""
Field Safety Monitoring System - External Simulator for Dashboard 2
Sends telemetry to http://localhost:3000/api/telemetry
"""

import time
import random
import requests
from datetime import datetime

STATION_URL = "http://127.0.0.1:3000/api/telemetry"

def run_simulation():
    print("=====================================================")
    print("  FIELD SAFETY SYSTEM - DASHBOARD 2 TELEMETRY SIMULATOR")
    print(f"  Target Station: {STATION_URL}")
    print("=====================================================")

    step = 0
    while True:
        step += 1
        for cp_id, loc in [(1, "Zone-A_Shaft1"), (2, "Zone-B_Extraction"), (3, "Zone-C_DeepMine")]:
            # Generate realistic sensor values
            mq5 = random.randint(100, 220)
            mq7 = random.randint(30, 95)
            mq135 = random.randint(70, 160)
            sound = random.randint(20, 80)
            pir = random.random() < 0.25
            temp = round(24.0 + random.uniform(-0.5, 0.5), 1)
            hum = round(52.0 + random.uniform(-1.0, 1.0), 1)

            # Scenario 1: Gas Leak in Zone B
            if cp_id == 2 and 15 <= (step % 40) <= 25:
                mq5 = 620 + random.randint(0, 50)
                mq7 = 470 + random.randint(0, 40)

            # Scenario 2: Sound / Collapse Spike in Zone A
            if cp_id == 1 and 30 <= (step % 40) <= 34:
                sound = 420 + random.randint(0, 90)
                pir = True

            payload = {
                "checkpoint_id": cp_id,
                "location": loc,
                "mq5": mq5,
                "mq7": mq7,
                "mq135": mq135,
                "pir_motion": pir,
                "sound_level": sound,
                "temperature": temp,
                "humidity": hum,
                "uptime_ms": int(time.time() * 1000)
            }

            # Attach Band
            if cp_id == 1:
                spo2 = round(98.2 + random.uniform(-0.4, 0.4), 1)
                pulse = round(74.0 + random.uniform(-2.0, 2.0), 1)
                if 20 <= (step % 40) <= 28:
                    spo2 = 88.0 + random.uniform(-0.5, 0.5)
                    pulse = 125.0

                payload["band"] = {
                    "band_id": 1,
                    "worker_name": "Worker-101 (Alex)",
                    "pulse_bpm": pulse,
                    "spo2": spo2,
                    "battery_pct": 94
                }

            # Attach Rover Status
            if cp_id == 1:
                payload["rover"] = {
                    "rover_id": 1,
                    "current_command": 0,
                    "speed": 200,
                    "battery_pct": 91,
                    "failsafe_triggered": False
                }

            try:
                res = requests.post(STATION_URL, json=payload, timeout=1.0)
                if res.status_code == 200:
                    resp_json = res.json()
                    cmd_info = resp_json.get("rover_command")
                    if cmd_info:
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] CP-{cp_id} -> Dispatched Rover Cmd: {cmd_info}")
            except Exception as e:
                print(f"[ERROR] Could not connect to Station: {e}")

        time.sleep(1.5)

if __name__ == '__main__':
    run_simulation()

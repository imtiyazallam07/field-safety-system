"""
Field Safety Monitoring System - Python Client & Integration Example
Demonstrates how external Python platforms, data science pipelines,
or IoT controllers can connect to the API via REST, WebSockets, and Webhooks.
"""

import time
import requests
import json

BASE_URL = "http://localhost:8000"


def test_rest_queries():
    print("\n--- 1. Testing REST Queries ---")
    
    # 1. System Summary
    resp = requests.get(f"{BASE_URL}/api/v1/system/summary")
    print("System Summary:", json.dumps(resp.json(), indent=2))

    # 2. Checkpoints
    resp = requests.get(f"{BASE_URL}/api/v1/sensors/checkpoints")
    cps = resp.json()
    print(f"Discovered {len(cps)} Checkpoints:")
    for cp in cps:
        print(f"  - Checkpoint #{cp['checkpoint_id']} ({cp['location']}): MQ5={cp['mq5']:.1f}, MQ7={cp['mq7']:.1f}, Temp={cp['temperature']:.1f}°C")

    # 3. Worker Bands
    resp = requests.get(f"{BASE_URL}/api/v1/sensors/bands")
    bands = resp.json()
    print(f"Discovered {len(bands)} Worker Bands:")
    for b in bands:
        print(f"  - Worker '{b['worker_name']}': Heart Rate={b['pulse_bpm']:.0f} BPM, SpO2={b['spo2']:.1f}%")

    # 4. Rover Telemetry
    resp = requests.get(f"{BASE_URL}/api/v1/rover/status")
    print("Rover Status:", resp.json())


def test_rover_control():
    print("\n--- 2. Testing Rover Teleoperation via API ---")
    print("Sending FORWARD command (Speed: 210)...")
    resp = requests.post(
        f"{BASE_URL}/api/v1/rover/command",
        json={"command": "FORWARD", "speed": 210, "rover_id": 1}
    )
    print("Command response:", resp.json())

    time.sleep(1.0)
    print("Sending STOP command...")
    resp = requests.post(
        f"{BASE_URL}/api/v1/rover/command",
        json={"command": "STOP", "speed": 0, "rover_id": 1}
    )
    print("Command response:", resp.json())


def test_webhook_registration():
    print("\n--- 3. Testing External Platform Webhook Registration ---")
    webhook_payload = {
        "url": "https://webhook.site/sample-endpoint",
        "events": ["alert.critical", "alert.warning"],
        "description": "Enterprise Incident Response Platform"
    }
    resp = requests.post(f"{BASE_URL}/api/v1/webhooks", json=webhook_payload)
    print("Webhook Created:", json.dumps(resp.json(), indent=2))


if __name__ == "__main__":
    try:
        test_rest_queries()
        test_rover_control()
        test_webhook_registration()
    except Exception as e:
        print(f"Connection failed (Ensure API server is running on {BASE_URL}): {e}")

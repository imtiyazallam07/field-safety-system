# Field Safety Monitoring System - JSON Data Format Specifications

This document defines the complete JSON schemas, payload structures, field descriptions, units of measurement, and data contracts for all REST endpoints, WebSockets, Server-Sent Events (SSE), and Webhooks across the Field Safety Monitoring System API.

---

## 📑 Table of Contents

1. [General Conventions & Standard Envelope](#1-general-conventions--standard-envelope)
2. [Hardware Ingestion Payloads (ESP32 / Mesh -> API)](#2-hardware-ingestion-payloads)
   - [2.1 Checkpoint Environmental Telemetry](#21-checkpoint-environmental-telemetry)
   - [2.2 Worker Monitoring Band Vitals](#22-worker-monitoring-band-vitals)
   - [2.3 Rover Telemetry](#23-rover-telemetry)
3. [Camera & Video JSON Payloads](#3-camera--video-json-payloads)
   - [3.1 Camera Base64 Snapshot](#31-camera-base64-snapshot)
   - [3.2 Camera WebSocket Frame Packet](#32-camera-websocket-frame-packet)
4. [Rover Teleoperation & Commands](#4-rover-teleoperation--commands)
   - [4.1 Send Steering Command](#41-send-steering-command)
   - [4.2 Command Polling Response](#42-command-polling-response)
5. [Frontend Query & System State Responses](#5-frontend-query--system-state-responses)
   - [5.1 Full System Snapshot (`GET /api/v1/data`)](#51-full-system-snapshot)
   - [5.2 High-Level System Summary (`GET /api/v1/system/summary`)](#52-high-level-system-summary)
   - [5.3 Checkpoint Detail & Time-Series History](#53-checkpoint-detail--time-series-history)
   - [5.4 Worker Band Detail & Time-Series History](#54-worker-band-detail--time-series-history)
6. [Safety Alerts & Thresholds](#6-safety-alerts--thresholds)
   - [6.1 Safety Alert Object](#61-safety-alert-object)
   - [6.2 Threshold Configuration Object](#62-threshold-configuration-object)
7. [Real-Time WebSocket Protocol](#7-real-time-websocket-protocol)
   - [7.1 Server -> Client Telemetry Broadcast](#71-server---client-telemetry-broadcast)
   - [7.2 Server -> Client Alert Broadcast](#72-server---client-alert-broadcast)
   - [7.3 Client -> Server Rover Steering Action](#73-client---server-rover-steering-action)
8. [External Platform Webhooks](#8-external-platform-webhooks)
   - [8.1 Webhook Subscription Registration](#81-webhook-subscription-registration)
   - [8.2 Outbound Webhook Delivery Envelope](#82-outbound-webhook-delivery-envelope)

---

## 1. General Conventions & Standard Envelope

* **Character Encoding**: UTF-8
* **Date & Time Formats**:
  * ISO-8601 Strings: `"YYYY-MM-DDTHH:MM:SSZ"` (e.g. `"2026-09-01T17:30:00Z"`)
  * Local Display Strings: `"HH:MM:SS"` or `"YYYY-MM-DD HH:MM:SS"`
  * Epoch Timestamps: Floating-point Unix timestamp in seconds (`1788264600.123`) or device uptime in milliseconds (`timestamp_ms`)
* **Physical Units**:
  * **Temperature**: Degrees Celsius (`°C`)
  * **Relative Humidity**: Percentage (`%`, 0.0 to 100.0)
  * **Gas Levels** (MQ5, MQ7, MQ135): Analog ADC raw value / calibrated PPM
  * **Sound Peak** (HW-484): Analog ADC peak amplitude (0 to 1023 / 4095)
  * **Proximity** (HC-SR04): Distance in centimeters (`cm`)
  * **Heart Rate**: Beats Per Minute (`BPM`)
  * **Blood Oxygen** (SpO2): Percentage (`%`, 0.0 to 100.0)
  * **Motor Speed**: PWM value (`0` to `255`)

---

## 2. Hardware Ingestion Payloads

### 2.1 Checkpoint Environmental Telemetry
* **Endpoints**: `POST /api/v1/ingest/telemetry` or `POST /api/telemetry`
* **Sender**: Checkpoint ESP32 directly via Wi-Fi hotspot or mesh gateway.

```json
{
  "checkpoint_id": 1,
  "location": "Zone-A (Main Shaft)",
  "mq5": 124.5,
  "mq7": 42.0,
  "mq135": 85.2,
  "pir_motion": true,
  "sound_level": 68.0,
  "temperature": 24.5,
  "humidity": 52.0,
  "proximity_cm": 145.0,
  "timestamp_ms": 348290,
  "band": {
    "band_id": 1,
    "worker_name": "Worker-101 (Alex Mercer)",
    "pulse_bpm": 74.0,
    "spo2": 98.5,
    "battery_pct": 94,
    "hops": 1
  },
  "rover": {
    "rover_id": 1,
    "current_command": 1,
    "speed": 200,
    "battery_pct": 91,
    "failsafe_triggered": false
  }
}
```

#### Field Descriptions:
| Field | Type | Required | Units / Values | Description |
|---|---|---|---|---|
| `checkpoint_id` | `integer` | Yes | 1, 2, 3... | Unique ID of the reporting checkpoint |
| `location` | `string` | No | Text | Human-readable zone/location label |
| `mq5` | `float` | No | ADC / ppm | Combustible Gas sensor reading (GPIO 33) |
| `mq7` | `float` | No | ADC / ppm | Carbon Monoxide (CO) sensor reading (GPIO 34) |
| `mq135` | `float` | No | ADC / ppm | Air Quality / Toxic Vapor sensor reading (GPIO 35) |
| `pir_motion` | `boolean` | No | `true`/`false` | Motion detection state HW-416-B (GPIO 26) |
| `sound_level` | `float` | No | ADC amplitude | Sound peak reading HW-484-V0.2 (GPIO 27) |
| `temperature` | `float` | No | `°C` | Ambient temperature DHT11 (GPIO 15) |
| `humidity` | `float` | No | `%` | Ambient relative humidity DHT11 (GPIO 15) |
| `proximity_cm` | `float` | No | `cm` | Distance to nearest obstacle HC-SR04 |
| `timestamp_ms` | `integer` | No | `ms` | Hardware uptime in milliseconds |
| `band` | `object` | No | Object | Optional piggybacked worker band packet relayed over ESP-NOW |
| `rover` | `object` | No | Object | Optional piggybacked rover status packet relayed over ESP-NOW |

#### Successful Response (`200 OK`):
```json
{
  "status": "ok",
  "checkpoint_id": 1,
  "rover_command": {
    "rover_id": 1,
    "command": 1,
    "speed": 200,
    "seq_id": 982341,
    "timestamp": 1788264612.45
  }
}
```
*(Note: `rover_command` is returned only when a pending drive command needs to be relayed down to the rover).*

---

### 2.2 Worker Monitoring Band Vitals
* **Endpoints**: `POST /api/v1/ingest/band` or `POST /api/telemetry/band`
* **Sender**: Monitoring Band ESP32 (direct Wi-Fi or forwarded packet).

```json
{
  "band_id": 1,
  "worker_name": "Worker-101 (Alex Mercer)",
  "pulse_bpm": 74.5,
  "spo2": 98.2,
  "battery_pct": 92,
  "relayed_by": 1,
  "hops": 1,
  "timestamp_ms": 194820
}
```

#### Field Descriptions:
| Field | Type | Required | Units / Values | Description |
|---|---|---|---|---|
| `band_id` | `integer` | Yes | 1, 2, 3... | Unique ID of the worker monitoring band |
| `worker_name` | `string` | No | Text | Assigned worker full name / badge tag |
| `pulse_bpm` | `float` | Yes | `BPM` (30.0 - 220.0) | Heart rate from MAX30100 / MAX30102 SPO2 sensor |
| `spo2` | `float` | Yes | `%` (70.0 - 100.0) | Blood oxygen saturation |
| `battery_pct` | `integer` | No | `%` (0 - 100) | Band battery percentage |
| `relayed_by` | `integer` | No | Checkpoint ID | Nearest checkpoint that forwarded this packet |
| `hops` | `integer` | No | 1 - 5 | Number of mesh relay hops taken to reach station |

#### Successful Response (`200 OK`):
```json
{
  "status": "ok",
  "band_id": 1
}
```

---

### 2.3 Rover Telemetry
* **Endpoints**: `POST /api/v1/ingest/rover` or `POST /api/telemetry/rover`
* **Sender**: Rover Driver ESP32 (Direct Wi-Fi).

```json
{
  "rover_id": 1,
  "current_command": 1,
  "speed": 220,
  "battery_pct": 89,
  "failsafe_triggered": false,
  "ip": "192.168.137.106",
  "connected_checkpoint_id": 0,
  "connected_checkpoint_location": "Direct Wi-Fi"
}
```

#### Command Code Mapping:
* `0` = `STOP`
* `1` = `FORWARD`
* `2` = `BACKWARD`
* `3` = `LEFT`
* `4` = `RIGHT`

---

## 3. Camera & Video JSON Payloads

### 3.1 Camera Base64 Snapshot
* **Endpoint**: `GET /api/v1/camera/snapshot`

```json
{
  "timestamp": "2026-09-01 17:35:12",
  "image_base64": "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEASABIAAD...",
  "content_type": "image/jpeg",
  "width": 320,
  "height": 240,
  "source": "live_esp32_cam"
}
```

#### Field Descriptions:
| Field | Type | Description |
|---|---|---|
| `timestamp` | `string` | Capture timestamp formatted as `"YYYY-MM-DD HH:MM:SS"` |
| `image_base64` | `string` | Complete Base64 Data URI ready for HTML `<img src="..." />` |
| `content_type` | `string` | MIME type (e.g. `"image/jpeg"`) |
| `width` | `integer` | Image horizontal resolution (320 or 640) |
| `height` | `integer` | Image vertical resolution (240 or 480) |
| `source` | `string` | `"live_esp32_cam"` if active, or `"standby_hud"` if offline |

---

### 3.2 Camera WebSocket Frame Packet
* **Endpoint**: `ws://localhost:8000/api/v1/ws/camera`

```json
{
  "type": "camera_frame",
  "image": "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEASABIAAD..."
}
```

---

## 4. Rover Teleoperation & Commands

### 4.1 Send Steering Command
* **Endpoint**: `POST /api/v1/rover/command`

#### Request Body:
```json
{
  "command": "FORWARD",
  "speed": 220,
  "rover_id": 1
}
```

| Field | Type | Allowed Values | Default | Description |
|---|---|---|---|---|
| `command` | `string` | `"STOP"`, `"FORWARD"`, `"BACKWARD"`, `"LEFT"`, `"RIGHT"` | `"STOP"` | Steering direction |
| `speed` | `integer` | `0` to `255` | `200` | Motor PWM speed |
| `rover_id` | `integer` | `1` | `1` | Target rover unit |

#### Response (`200 OK`):
```json
{
  "status": "command_queued",
  "command": "FORWARD",
  "speed": 220,
  "seq_id": 847291,
  "timestamp": 1788264625.12
}
```

---

### 4.2 Command Polling Response
* **Endpoint**: `GET /api/v1/rover/command/poll`

```json
{
  "rover_id": 1,
  "command": 1,
  "speed": 220,
  "seq_id": 847291,
  "timestamp": 1788264625.12
}
```

---

## 5. Frontend Query & System State Responses

### 5.1 Full System Snapshot
* **Endpoint**: `GET /api/v1/data` (or legacy `/api/data`)

```json
{
  "checkpoints": [
    {
      "checkpoint_id": 1,
      "location": "Zone-A (Main Shaft)",
      "mq5": 120.4,
      "mq7": 41.2,
      "mq135": 82.0,
      "pir_motion": true,
      "sound_level": 72.0,
      "temperature": 24.2,
      "humidity": 51.5,
      "proximity_cm": 140.0,
      "last_seen": 1788264610.12,
      "is_online": true,
      "history_count": 60
    },
    {
      "checkpoint_id": 2,
      "location": "Zone-B (Tunnel Junction)",
      "mq5": 148.1,
      "mq7": 64.0,
      "mq135": 102.5,
      "pir_motion": false,
      "sound_level": 45.0,
      "temperature": 27.1,
      "humidity": 63.8,
      "proximity_cm": 180.0,
      "last_seen": 1788264609.84,
      "is_online": true,
      "history_count": 60
    }
  ],
  "bands": [
    {
      "band_id": 1,
      "worker_name": "Worker-101 (Alex Mercer)",
      "pulse_bpm": 74.0,
      "spo2": 98.4,
      "battery_pct": 92,
      "relayed_by": 1,
      "hops": 1,
      "last_seen": 1788264610.12,
      "is_online": true,
      "history_count": 60
    }
  ],
  "rover": {
    "rover_id": 1,
    "current_command": 0,
    "command_name": "STOP",
    "speed": 0,
    "battery_pct": 90,
    "failsafe_triggered": false,
    "last_seen": 1788264610.0,
    "is_connected": true,
    "is_online": true,
    "connected_checkpoint_id": 1,
    "connected_checkpoint_location": "Zone-A (Main Shaft)",
    "ip": "192.168.137.106"
  },
  "alerts": [
    {
      "id": "ALT-0001",
      "timestamp": "17:30:15",
      "level": "CRITICAL",
      "category": "Vitals",
      "device_id": "Worker-101 (Alex Mercer)",
      "message": "Hypoxia alert! Blood oxygen critically low: 89.2%",
      "acked": false
    }
  ],
  "thresholds": {
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
    "temp_min": 0.0,
    "humidity_max": 85.0,
    "proximity_min_cm": 25.0
  },
  "server_time": "2026-09-01 17:35:12",
  "device_summary": {
    "total_checkpoints": 2,
    "online_checkpoints": 2,
    "total_workers": 1,
    "online_workers": 1,
    "rover_online": true,
    "active_alerts": 1
  }
}
```

---

### 5.2 High-Level System Summary
* **Endpoint**: `GET /api/v1/system/summary`

```json
{
  "status": "CRITICAL",
  "total_workers": 3,
  "online_workers": 3,
  "total_checkpoints": 3,
  "online_checkpoints": 3,
  "rover_connected": true,
  "active_critical_alerts": 1,
  "active_warning_alerts": 2,
  "server_time": "2026-09-01 17:35:12"
}
```

---

### 5.3 Checkpoint Detail & Time-Series History
* **Endpoint**: `GET /api/v1/sensors/checkpoints/1`

```json
{
  "checkpoint_id": 1,
  "location": "Zone-A (Main Shaft)",
  "mq5": 120.4,
  "mq7": 41.2,
  "mq135": 82.0,
  "pir_motion": true,
  "sound_level": 72.0,
  "temperature": 24.2,
  "humidity": 51.5,
  "proximity_cm": 140.0,
  "last_seen": 1788264610.12,
  "is_online": true,
  "history_count": 3,
  "history": [
    {
      "timestamp": "17:34:50",
      "ts": 1788264590.12,
      "mq5": 118.0,
      "mq7": 40.0,
      "mq135": 80.5,
      "sound_level": 65.0,
      "temperature": 24.1,
      "humidity": 51.0,
      "pir_motion": false,
      "proximity_cm": 142.0
    },
    {
      "timestamp": "17:35:00",
      "ts": 1788264600.12,
      "mq5": 119.2,
      "mq7": 40.8,
      "mq135": 81.2,
      "sound_level": 68.0,
      "temperature": 24.1,
      "humidity": 51.2,
      "pir_motion": true,
      "proximity_cm": 141.0
    },
    {
      "timestamp": "17:35:10",
      "ts": 1788264610.12,
      "mq5": 120.4,
      "mq7": 41.2,
      "mq135": 82.0,
      "sound_level": 72.0,
      "temperature": 24.2,
      "humidity": 51.5,
      "pir_motion": true,
      "proximity_cm": 140.0
    }
  ]
}
```

---

### 5.4 Worker Band Detail & Time-Series History
* **Endpoint**: `GET /api/v1/sensors/bands/1`

```json
{
  "band_id": 1,
  "worker_name": "Worker-101 (Alex Mercer)",
  "pulse_bpm": 74.0,
  "spo2": 98.4,
  "battery_pct": 92,
  "relayed_by": 1,
  "hops": 1,
  "last_seen": 1788264610.12,
  "is_online": true,
  "history_count": 3,
  "history": [
    {
      "timestamp": "17:34:50",
      "ts": 1788264590.12,
      "pulse_bpm": 73.0,
      "spo2": 98.5
    },
    {
      "timestamp": "17:35:00",
      "ts": 1788264600.12,
      "pulse_bpm": 73.8,
      "spo2": 98.5
    },
    {
      "timestamp": "17:35:10",
      "ts": 1788264610.12,
      "pulse_bpm": 74.0,
      "spo2": 98.4
    }
  ]
}
```

---

## 6. Safety Alerts & Thresholds

### 6.1 Safety Alert Object
* **Endpoint**: `GET /api/v1/alerts?limit=50&level=CRITICAL&unacked_only=false`

```json
[
  {
    "id": "ALT-0001",
    "timestamp": "17:30:15",
    "level": "CRITICAL",
    "category": "Gas (MQ5)",
    "device_id": "Zone-A (Main Shaft) (CP-1)",
    "message": "Combustible gas buildup critical! Value: 615.0",
    "acked": false
  },
  {
    "id": "ALT-0002",
    "timestamp": "17:31:02",
    "level": "WARNING",
    "category": "Vitals",
    "device_id": "Worker-102 (Sarah Connor)",
    "message": "Low blood oxygen reading: 93.2%",
    "acked": true
  }
]
```

#### Alert Categories & Levels:
* **Levels**: `"CRITICAL"`, `"WARNING"`, `"INFO"`
* **Categories**:
  * `"Vitals"` (SpO2 drop, Tachycardia, Bradycardia)
  * `"Gas (MQ5)"` (Methane / Combustible gas)
  * `"Gas (MQ7)"` (Carbon Monoxide)
  * `"Gas (MQ135)"` (Air quality / toxic fumes)
  * `"Sound"` (Sudden noise spike / explosion / collapse)
  * `"Motion"` (Personnel presence detected)
  * `"Proximity"` (Obstacle / vehicle danger distance)
  * `"Environment"` (Extreme high/low temp, extreme humidity)
  * `"Rover"` (Connection, disconnect, failsafe)

---

### 6.2 Threshold Configuration Object
* **Endpoints**: `GET /api/v1/thresholds` and `POST /api/v1/thresholds`

```json
{
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
  "temp_min": 0.0,
  "humidity_max": 85.0,
  "proximity_min_cm": 25.0
}
```

---

## 7. Real-Time WebSocket Protocol

**Endpoint**: `ws://localhost:8000/api/v1/ws/live`

### 7.1 Server -> Client Telemetry Broadcast
Sent upon initial connection (`type: "initial_state"`) and every second (`type: "telemetry_update"`):

```json
{
  "type": "telemetry_update",
  "data": {
    "checkpoints": [ ... ],
    "bands": [ ... ],
    "rover": { ... },
    "alerts": [ ... ],
    "thresholds": { ... },
    "server_time": "2026-09-01 17:35:12",
    "device_summary": { ... }
  }
}
```

---

### 7.2 Server -> Client Alert Broadcast
Dispatched immediately whenever a safety violation is detected:

```json
{
  "type": "alert",
  "data": {
    "id": "ALT-0005",
    "timestamp": "17:35:12",
    "level": "CRITICAL",
    "category": "Sound",
    "device_id": "Zone-B (Tunnel Junction) (CP-2)",
    "message": "Sudden loud sound spike! Possible collapse/explosion (390.0)",
    "acked": false
  }
}
```

---

### 7.3 Client -> Server Rover Steering Action
Frontend sends JSON action packet over WebSocket:

```json
{
  "action": "rover_command",
  "command": "FORWARD",
  "speed": 220
}
```

#### Server Acknowledgment:
```json
{
  "type": "command_ack",
  "command": "FORWARD",
  "speed": 220
}
```

---

## 8. External Platform Webhooks

### 8.1 Webhook Subscription Registration
* **Endpoint**: `POST /api/v1/webhooks`

#### Request Body:
```json
{
  "url": "https://api.incident-platform.com/v1/safety-webhook",
  "events": [
    "alert.critical",
    "alert.warning",
    "telemetry.checkpoint"
  ],
  "secret": "my_secret_token_12345",
  "description": "Enterprise Operations Incident Gateway"
}
```

| Field | Type | Description |
|---|---|---|
| `url` | `string` | External platform HTTP/HTTPS endpoint |
| `events` | `array[string]` | Event topics (`alert.critical`, `alert.warning`, `alert.info`, `telemetry.checkpoint`, `telemetry.band`, `rover.status`, `*`) |
| `secret` | `string` | (Optional) HMAC secret key used to compute `X-FieldSafety-Signature` |
| `description` | `string` | Label for the webhook integration |

#### Response (`200 OK`):
```json
{
  "id": "wh_0001_1788264600",
  "url": "https://api.incident-platform.com/v1/safety-webhook",
  "events": [
    "alert.critical",
    "alert.warning",
    "telemetry.checkpoint"
  ],
  "secret": "my_secret_token_12345",
  "description": "Enterprise Operations Incident Gateway",
  "created_at": "2026-09-01 17:30:00",
  "last_delivered_at": null,
  "total_deliveries": 0,
  "failed_deliveries": 0
}
```

---

### 8.2 Outbound Webhook Delivery Envelope
Dispatched via HTTP `POST` to registered URLs.

#### HTTP Headers:
```http
POST /v1/safety-webhook HTTP/1.1
Host: api.incident-platform.com
Content-Type: application/json
User-Agent: FieldSafetySystem-Webhook/1.0
X-FieldSafety-Event: alert.critical
X-FieldSafety-Timestamp: 2026-09-01T17:35:12.450123
X-FieldSafety-Signature: sha256=d3b07384d113edec49eaa6238ad5ff00...
```

#### JSON Payload:
```json
{
  "event": "alert.critical",
  "timestamp": "2026-09-01T17:35:12.450123",
  "data": {
    "id": "ALT-0008",
    "timestamp": "17:35:12",
    "level": "CRITICAL",
    "category": "Vitals",
    "device_id": "Worker-103 (David Vance)",
    "message": "Hypoxia alert! Critically low SpO2: 87.5%",
    "acked": false
  }
}
```

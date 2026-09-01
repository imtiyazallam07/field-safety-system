# Field Safety Monitoring System - Universal API

The **Field Safety Monitoring System API** is a high-performance, asynchronous interface built with **FastAPI** designed to connect any frontend interface (React, Vue, Angular, Mobile Apps, Electron, Web Dashboards) and external third-party platforms (Incident response platforms, Cloud IoT, Slack, Discord, MQTT bridges) with live sensor telemetry and real-time rover camera video feeds.

---

## 🌟 Key Capabilities

* **Environmental Sensors**: Real-time readings from Checkpoint sensors (MQ5 combustible gas, MQ7 carbon monoxide, MQ135 air quality, HW-416-B PIR motion, HW-484 sound peak, DHT11 temp/humidity, HC-SR04 proximity).
* **Worker Vitals**: Live pulse rate (BPM) and blood oxygen (SpO2) tracking with multi-hop ESP-NOW mesh relay topologies.
* **Universal Live Video Stream**: High-performance multipart MJPEG streaming at `/api/v1/camera/stream` that can be plugged directly into standard HTML/React `<img src="..." />` tags on any platform without custom decoders.
* **Rover Teleoperation**: Directional steering controls (`FORWARD`, `BACKWARD`, `LEFT`, `RIGHT`, `STOP`) with sub-millisecond UDP broadcast and PWM speed modulation.
* **Bi-directional WebSockets**: Sub-second live data push (`/api/v1/ws/live`) and video frame streaming (`/api/v1/ws/camera`).
* **External Platform Webhooks**: Outbound webhooks that push JSON safety alerts directly to external systems.
* **Universal CORS Enabled**: Open CORS support (`*`) allowing seamless cross-origin communication with any frontend.
* **Interactive OpenAPI/Swagger**: Full interactive documentation and testing playground at `/docs`.
* **Built-in Simulator**: Offline development mode with animated synthetic video feeds and realistic sensor noise.

---

## 🚀 Quick Start

### 1. Install Dependencies
```bash
pip install -r api/requirements.txt
```

### 2. Start the API Server

#### Hardware Mode (Waiting for ESP32s):
```bash
python api/run_api.py --port 8000
```

#### Simulation Mode (For Instant Frontend Testing Without Hardware):
```bash
python api/run_api.py --port 8000 --simulate
```

### 3. Access in Browser

* **Interactive Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
* **Alternative Redoc**: [http://localhost:8000/redoc](http://localhost:8000/redoc)
* **Universal Test Dashboard**: [http://localhost:8000/test](http://localhost:8000/test)
* **Live Camera Stream**: [http://localhost:8000/api/v1/camera/stream](http://localhost:8000/api/v1/camera/stream)
* **Complete JSON Schema & Formats Guide**: [`JSON_DATA_FORMATS.md`](./JSON_DATA_FORMATS.md)

---

## 📡 API Endpoint Reference

### 1. System & Overview
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v1/data` | Complete live snapshot of all checkpoints, bands, rover, and alerts |
| `GET` | `/api/v1/system/summary` | High-level system health status (`NORMAL`, `WARNING`, `CRITICAL`) |
| `GET` | `/api/v1/events/sse` | Server-Sent Events (SSE) live sensor push stream |

### 2. Environmental Checkpoints
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v1/sensors/checkpoints` | List all active checkpoints with current gas, sound, and temp readings |
| `GET` | `/api/v1/sensors/checkpoints/{id}` | Detailed checkpoint metrics and rolling historical data |

### 3. Worker Monitoring Bands
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v1/sensors/bands` | List all active worker bands with heart rate, SpO2, and mesh hop path |
| `GET` | `/api/v1/sensors/bands/{id}` | Detailed worker vitals and rolling graph history |

### 4. Camera & Video Streaming
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v1/camera/stream` | Multipart MJPEG stream (`multipart/x-mixed-replace`) for `<img src="...">` |
| `GET` | `/api/v1/camera/latest.jpg` | Single latest JPEG frame (image/jpeg) |
| `GET` | `/api/v1/camera/snapshot` | Latest frame as Base64 Data URI with metadata |
| `POST`| `/api/v1/camera/upload` | Upload binary JPEG frame directly from ESP32-CAM |
| `POST`| `/api/v1/camera/chunk` | Ingest chunked binary packet from ESP-NOW mesh relay |

### 5. Rover Teleoperation
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v1/rover/status` | Current rover speed, direction, battery, and failsafe status |
| `POST`| `/api/v1/rover/command` | Send directional drive command (`FORWARD`, `BACKWARD`, `LEFT`, `RIGHT`, `STOP`) |
| `GET` | `/api/v1/rover/command/poll` | Poll active drive command (for hardware rover driver) |

### 6. Safety Alerts & Thresholds
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v1/alerts` | List active safety alerts with level (`CRITICAL`, `WARNING`) filtering |
| `POST`| `/api/v1/alerts/ack` | Acknowledge safety alerts |
| `DELETE`| `/api/v1/alerts` | Clear all alerts from history |
| `GET` | `/api/v1/thresholds` | Retrieve current hazard thresholds |
| `POST`| `/api/v1/thresholds` | Dynamically update safety thresholds (gas levels, min SpO2, etc.) |

### 7. External Platform Webhooks
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v1/webhooks` | List all registered external platform webhooks |
| `POST`| `/api/v1/webhooks` | Register a webhook URL to receive live JSON event push notifications |
| `DELETE`| `/api/v1/webhooks/{id}` | Unsubscribe webhook |
| `POST`| `/api/v1/webhooks/{id}/test` | Send test ping payload to verified endpoint |

### 8. Historical Data Export
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v1/export/json` | Complete historical time-series export in JSON format |
| `GET` | `/api/v1/export/csv/checkpoints` | Downloadable CSV of all checkpoint environmental readings |
| `GET` | `/api/v1/export/csv/bands` | Downloadable CSV of worker vitals history |

---

## 🔌 Frontend Integration Examples

### Embedding Live Camera in React / HTML
Because the API serves a standard multipart MJPEG stream, you can display live camera feed with zero custom libraries:
```jsx
// React Component
function RoverCamera() {
  return (
    <div className="camera-card">
      <h3>Live Rover Camera</h3>
      <img 
        src="http://localhost:8000/api/v1/camera/stream" 
        alt="Live Rover Feed" 
        style={{ width: "100%", borderRadius: "8px" }}
      />
    </div>
  );
}
```

### Real-Time Telemetry via WebSocket (React Example)
```jsx
import { useEffect, useState } from "react";

export function LiveSensorDashboard() {
  const [telemetry, setTelemetry] = useState(null);

  useEffect(() => {
    const ws = new WebSocket("ws://localhost:8000/api/v1/ws/live");

    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.type === "telemetry_update" || msg.type === "initial_state") {
        setTelemetry(msg.data);
      }
    };

    return () => ws.close();
  }, []);

  return (
    <div>
      <h2>Active Checkpoints: {telemetry?.device_summary?.online_checkpoints || 0}</h2>
      {telemetry?.checkpoints?.map(cp => (
        <div key={cp.checkpoint_id}>
          <h4>{cp.location}</h4>
          <p>MQ5 Gas: {cp.mq5} | Temp: {cp.temperature}°C | PIR: {cp.pir_motion ? "Active" : "Clear"}</p>
        </div>
      ))}
    </div>
  );
}
```

### Steering the Rover via API (Fetch / Axios)
```javascript
async function moveRover(direction, speed = 200) {
  await fetch("http://localhost:8000/api/v1/rover/command", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ command: direction, speed: speed })
  });
}

// Example usage:
moveRover("FORWARD", 220);
setTimeout(() => moveRover("STOP"), 1000);
```

---

## 🌐 External Platform Integration (Webhooks)

External platforms (e.g., Slack bot, PagerDuty, enterprise monitoring cloud) can register a webhook:

```bash
curl -X POST "http://localhost:8000/api/v1/webhooks" \
     -H "Content-Type: application/json" \
     -d '{
       "url": "https://your-platform.com/api/field-safety-webhook",
       "events": ["alert.critical", "alert.warning"],
       "secret": "your_optional_hmac_secret",
       "description": "Enterprise Incident Manager"
     }'
```

Whenever a worker's SpO2 drops critically or gas spikes, your server will receive an instant HTTP POST:
```json
{
  "event": "alert.critical",
  "timestamp": "2026-09-01T17:30:00Z",
  "data": {
    "id": "ALT-0012",
    "level": "CRITICAL",
    "category": "Vitals",
    "device_id": "Worker-101 (Alex Mercer)",
    "message": "Hypoxia alert! Blood oxygen critically low: 88.5%",
    "acked": false
  }
}
```

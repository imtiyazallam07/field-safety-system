"""
Field Safety Monitoring System - Main Universal API Application
High-performance asynchronous API for connecting any frontend interface (React, Vue, Web, Mobile)
and external platforms to real-time sensor telemetry, worker vitals, camera video streaming, and rover teleoperation.
"""

import os
import time
import json
import asyncio
import math
import random
from typing import Dict, Any, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import DEFAULT_HOST, DEFAULT_PORT
from .state import state_manager
from .camera import camera_manager
from .webhooks import webhook_manager
from .websocket_manager import ws_manager

# Import Modular Routers
from .routes.telemetry import router as telemetry_router
from .routes.camera import router as camera_router
from .routes.rover import router as rover_router
from .routes.alerts import router as alerts_router
from .routes.webhooks import router as webhooks_router
from .routes.export import router as export_router


# ================= SIMULATOR ENGINE (FOR OFFLINE / FRONTEND TESTING) =================

class SensorSimulator:
    def __init__(self):
        self.is_running = False
        self.task: Optional[asyncio.Task] = None
        self.step = 0
        self.heading = 0.0

    async def run_loop(self):
        print("[Simulator] Background sensor & camera simulator started.")
        while self.is_running:
            self.step += 1
            now_ts = time.time()
            
            # 1. Simulate 3 Checkpoints with realistic noise
            cp_definitions = [
                {"id": 1, "loc": "Zone-A (Main Shaft)", "mq5_base": 120, "mq7_base": 40, "mq135_base": 80, "temp_base": 24.0, "hum_base": 52.0},
                {"id": 2, "loc": "Zone-B (Tunnel Junction)", "mq5_base": 150, "mq7_base": 65, "mq135_base": 105, "temp_base": 27.0, "hum_base": 64.0},
                {"id": 3, "loc": "Zone-C (Deep Extraction)", "mq5_base": 180, "mq7_base": 85, "mq135_base": 130, "temp_base": 30.5, "hum_base": 78.0}
            ]

            for cp in cp_definitions:
                noise = math.sin(self.step * 0.1 + cp["id"]) * 15 + random.uniform(-3, 3)
                state_manager.ingest_checkpoint_telemetry({
                    "checkpoint_id": cp["id"],
                    "location": cp["loc"],
                    "mq5": max(20.0, cp["mq5_base"] + noise),
                    "mq7": max(10.0, cp["mq7_base"] + noise * 0.5),
                    "mq135": max(30.0, cp["mq135_base"] + noise * 0.8),
                    "sound_level": 70.0 + (random.uniform(0, 290) if random.random() < 0.05 else random.uniform(0, 30)),
                    "pir_motion": (self.step % 10 < 4),
                    "temperature": cp["temp_base"] + math.sin(self.step * 0.05) * 2.0,
                    "humidity": cp["hum_base"] + math.cos(self.step * 0.05) * 3.0,
                    "proximity_cm": 150.0 - (self.step % 40) * 3.0
                })

            # 2. Simulate 3 Worker Bands
            worker_defs = [
                {"id": 1, "name": "Worker-101 (Alex M.)", "pulse_base": 74.0, "spo2_base": 98.6, "cp": 1},
                {"id": 2, "name": "Worker-102 (Sarah C.)", "pulse_base": 82.0, "spo2_base": 99.0, "cp": 2},
                {"id": 3, "name": "Worker-103 (David V.)", "pulse_base": 69.0, "spo2_base": 97.4, "cp": 3}
            ]

            for w in worker_defs:
                vitals_noise = random.uniform(-1.5, 1.5)
                state_manager.ingest_band_telemetry({
                    "band_id": w["id"],
                    "worker_name": w["name"],
                    "pulse_bpm": max(55.0, min(140.0, w["pulse_base"] + vitals_noise)),
                    "spo2": max(88.0, min(100.0, w["spo2_base"] + random.uniform(-0.3, 0.3))),
                    "battery_pct": max(20, 100 - (self.step // 30)),
                    "relayed_by": w["cp"],
                    "hops": w["cp"]
                })

            # 3. Simulate Rover Status
            with state_manager.lock:
                current_cmd = state_manager.rover_state.get("command_name", "STOP")
                cmd_code = state_manager.rover_state.get("current_command", 0)
                speed = state_manager.rover_state.get("speed", 200)

            if current_cmd == "LEFT":
                self.heading = (self.heading - 10) % 360
            elif current_cmd == "RIGHT":
                self.heading = (self.heading + 10) % 360

            state_manager.ingest_rover_telemetry({
                "rover_id": 1,
                "current_command": cmd_code,
                "speed": speed if current_cmd != "STOP" else 0,
                "battery_pct": 92,
                "failsafe_triggered": False,
                "connected_checkpoint_id": 1,
                "connected_checkpoint_location": "Zone-A (Main Shaft)",
                "ip": "192.168.137.106"
            })

            # Broadcast live state to all connected WebSockets
            snapshot = state_manager.get_full_snapshot()
            await ws_manager.broadcast_telemetry({
                "type": "telemetry_update",
                "data": snapshot
            })

            # Sleep interval
            await asyncio.sleep(1.0)

    def start(self):
        if not self.is_running:
            self.is_running = True
            self.task = asyncio.create_task(self.run_loop())

    def stop(self):
        if self.is_running:
            self.is_running = False
            if self.task:
                self.task.cancel()
                self.task = None
            print("[Simulator] Background simulator stopped.")


simulator = SensorSimulator()


# ================= LIFESPAN & EVENT BRIDGING =================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup state manager event listeners to forward to WebSockets and Webhooks
    def on_alert_generated(alert_item):
        event_name = f"alert.{alert_item['level'].lower()}"
        webhook_manager.dispatch_event(event_name, alert_item)
        asyncio.create_task(ws_manager.broadcast_telemetry({
            "type": "alert",
            "data": alert_item
        }))

    def on_telemetry_updated(telemetry_item):
        t_type = telemetry_item.get("type", "unknown")
        webhook_manager.dispatch_event(f"telemetry.{t_type}", telemetry_item.get("data"))

    state_manager.register_alert_subscriber(on_alert_generated)
    state_manager.register_telemetry_subscriber(on_telemetry_updated)

    # Check if simulation mode is enabled via environment variable
    if os.getenv("ENABLE_SIMULATOR", "false").lower() in ("true", "1", "yes"):
        simulator.start()

    yield

    # Teardown
    simulator.stop()
    state_manager.unregister_alert_subscriber(on_alert_generated)
    state_manager.unregister_telemetry_subscriber(on_telemetry_updated)


# ================= FASTAPI APPLICATION =================

app = FastAPI(
    title="Field Safety Monitoring System API",
    version="1.0.0",
    description="""
# Field Safety Monitoring & Rover Teleoperation Platform API

Universal API to connect external platforms, mobile applications, cloud services,
and any web frontend (React, Vue, Angular, Flutter, Electron, Desktop) to live sensor data and rover controls.

### Features & Capabilities:
* **Real-Time Sensor Telemetry**: Continuous readings from MQ5, MQ7, MQ135 gas sensors, PIR motion, sound level, temperature, humidity, and proximity.
* **Worker Vitals Monitoring**: Live pulse rate (BPM) and blood oxygen (SpO2) tracking with multi-hop ESP-NOW mesh relay topologies.
* **Live Video Streaming**: Real-time multipart MJPEG video stream at `/api/v1/camera/stream` (embeddable directly into any HTML `<img src="..." />`), single JPEG snapshots, and Base64 Data URIs.
* **Rover Teleoperation**: WASD/D-Pad directional control (FORWARD, BACKWARD, LEFT, RIGHT, STOP) with sub-millisecond UDP broadcast and PWM speed control.
* **Bi-directional WebSockets**: Connect to `/api/v1/ws/live` for high-frequency telemetry & control, and `/api/v1/ws/camera` for video frames.
* **Server-Sent Events (SSE)**: Stream live sensor events directly into web browsers at `/api/v1/events/sse`.
* **Outbound Webhooks**: External systems (Slack, Discord, PagerDuty, cloud IoT hubs) can subscribe at `/api/v1/webhooks` for real-time safety alert dispatches.
* **Open Universal CORS**: Connect from any domain, port, or client framework without CORS issues.
    """,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)

# ================= UNIVERSAL CORS CONFIGURATION =================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Routers
app.include_router(telemetry_router)
app.include_router(camera_router)
app.include_router(rover_router)
app.include_router(alerts_router)
app.include_router(webhooks_router)
app.include_router(export_router)


# ================= WEBSOCKETS FOR REAL-TIME FRONTENDS =================

@app.websocket("/api/v1/ws/live")
@app.websocket("/ws/live")
async def websocket_live_telemetry(websocket: WebSocket):
    """
    Real-time bidirectional WebSocket for frontend applications.
    Receives continuous telemetry updates, alerts, and accepts rover drive commands:
    `{"action": "rover_command", "command": "FORWARD", "speed": 220}`
    """
    await ws_manager.connect_telemetry(websocket)
    # Send initial snapshot immediately upon connection
    initial_snapshot = state_manager.get_full_snapshot()
    await websocket.send_text(json.dumps({
        "type": "initial_state",
        "data": initial_snapshot
    }))

    try:
        while True:
            text = await websocket.receive_text()
            try:
                msg = json.loads(text)
                action = msg.get("action")
                
                # Handle incoming rover steering commands from frontend WebSocket
                if action == "rover_command":
                    cmd_str = msg.get("command", "STOP").upper()
                    speed = int(msg.get("speed", 200))
                    state_manager.send_rover_command(cmd_str, speed)
                    await websocket.send_text(json.dumps({
                        "type": "command_ack",
                        "command": cmd_str,
                        "speed": speed
                    }))
                elif action == "ping":
                    await websocket.send_text(json.dumps({"type": "pong", "time": time.time()}))
            except Exception as e:
                await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
    except WebSocketDisconnect:
        await ws_manager.disconnect_telemetry(websocket)
    except Exception:
        await ws_manager.disconnect_telemetry(websocket)


@app.websocket("/api/v1/ws/camera")
@app.websocket("/ws/camera")
async def websocket_live_camera(websocket: WebSocket):
    """
    High-frequency WebSocket dedicated to streaming real-time camera frames (Base64 JSON).
    """
    await ws_manager.connect_camera(websocket)
    try:
        while True:
            # Keep connection alive while broadcasting in background
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        await ws_manager.disconnect_camera(websocket)
    except Exception:
        await ws_manager.disconnect_camera(websocket)


# ================= SERVER-SENT EVENTS (SSE) =================

@app.get(
    "/api/v1/events/sse",
    summary="Server-Sent Events (SSE) Live Sensor Stream",
    description="One-way real-time push stream for web browsers using standard EventSource API."
)
async def sse_sensor_stream(request: Request):
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            snapshot = state_manager.get_full_snapshot()
            yield f"event: telemetry\ndata: {json.dumps(snapshot)}\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


# ================= SIMULATOR CONTROL & TEST CLIENT =================

@app.post(
    "/api/v1/system/simulator/start",
    tags=["System & Simulation"],
    summary="Start In-Memory Sensor & Telemetry Simulator",
    description="Starts generating synthetic live sensor readings and rover camera frames for frontend testing without hardware."
)
async def start_simulator():
    simulator.start()
    return {"status": "simulator_started", "is_running": simulator.is_running}


@app.post(
    "/api/v1/system/simulator/stop",
    tags=["System & Simulation"],
    summary="Stop In-Memory Sensor Simulator",
    description="Stops generating synthetic simulator data."
)
async def stop_simulator():
    simulator.stop()
    return {"status": "simulator_stopped", "is_running": simulator.is_running}


@app.get(
    "/api/v1/system/simulator/status",
    tags=["System & Simulation"],
    summary="Get Simulator Running Status"
)
async def get_simulator_status():
    return {"is_running": simulator.is_running, "simulation_step": simulator.step}


# ================= UNIVERSAL TEST FRONTEND INTERFACE =================

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
@app.get("/test", response_class=HTMLResponse, include_in_schema=False)
async def serve_test_frontend():
    test_client_path = os.path.join(STATIC_DIR, "test_client.html")
    if os.path.exists(test_client_path):
        with open(test_client_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="""
    <html>
        <head><title>Field Safety System API</title></head>
        <body style="font-family: sans-serif; background: #0f172a; color: white; padding: 40px; text-align: center;">
            <h1>Field Safety Monitoring System API</h1>
            <p>API is running and ready for frontend and external platform connections.</p>
            <p><a href="/docs" style="color: #38bdf8; font-size: 18px;">Open Interactive Swagger API Documentation (/docs)</a></p>
        </body>
    </html>
    """)

"""
WebSocket Connection & Broadcast Manager
Allows any frontend interface (React, Vue, Angular, Flutter, Electron, Web) to connect
via real-time WebSockets for sub-second telemetry updates and camera frame streaming.
"""

import json
import asyncio
import base64
from typing import List, Set, Dict, Any
from starlette.websockets import WebSocket, WebSocketState


class WebSocketConnectionManager:
    def __init__(self):
        # Connected clients for live telemetry (sensors, vitals, rover, alerts)
        self.telemetry_connections: Set[WebSocket] = set()
        # Connected clients for live video frames
        self.camera_connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    # ================= TELEMETRY WEBSOCKETS =================

    async def connect_telemetry(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self.telemetry_connections.add(websocket)

    async def disconnect_telemetry(self, websocket: WebSocket):
        async with self._lock:
            self.telemetry_connections.discard(websocket)

    async def broadcast_telemetry(self, message: Dict[str, Any]):
        """Broadcasts a JSON telemetry payload to all connected frontend clients."""
        text_data = json.dumps(message)
        async with self._lock:
            dead_sockets = set()
            for ws in self.telemetry_connections:
                if ws.client_state == WebSocketState.CONNECTED:
                    try:
                        await ws.send_text(text_data)
                    except Exception:
                        dead_sockets.add(ws)
                else:
                    dead_sockets.add(ws)
            
            for ws in dead_sockets:
                self.telemetry_connections.discard(ws)

    # ================= CAMERA WEBSOCKETS =================

    async def connect_camera(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self.camera_connections.add(websocket)

    async def disconnect_camera(self, websocket: WebSocket):
        async with self._lock:
            self.camera_connections.discard(websocket)

    async def broadcast_camera_frame(self, frame_bytes: bytes, as_base64: bool = True):
        """Broadcasts a camera JPEG frame to video subscribers."""
        if not self.camera_connections:
            return

        async with self._lock:
            dead_sockets = set()
            if as_base64:
                b64_frame = base64.b64encode(frame_bytes).decode('utf-8')
                msg = json.dumps({"type": "camera_frame", "image": f"data:image/jpeg;base64,{b64_frame}"})
                for ws in self.camera_connections:
                    if ws.client_state == WebSocketState.CONNECTED:
                        try:
                            await ws.send_text(msg)
                        except Exception:
                            dead_sockets.add(ws)
                    else:
                        dead_sockets.add(ws)
            else:
                for ws in self.camera_connections:
                    if ws.client_state == WebSocketState.CONNECTED:
                        try:
                            await ws.send_bytes(frame_bytes)
                        except Exception:
                            dead_sockets.add(ws)
                    else:
                        dead_sockets.add(ws)

            for ws in dead_sockets:
                self.camera_connections.discard(ws)


ws_manager = WebSocketConnectionManager()

"""
Camera and Video Stream Engine
Provides MJPEG multipart video streaming, latest frame capture, chunk reassembly
for ESP-NOW mesh camera packets, base64 snapshots, and fallback HUD test generators.
"""

import io
import time
import base64
import threading
from datetime import datetime
from typing import Optional, Dict, Any, List
from PIL import Image, ImageDraw, ImageFont


class CameraStreamManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.latest_frame_bytes: Optional[bytes] = None
        self.last_frame_timestamp: float = 0.0
        self.frame_counter: int = 0
        self.camera_chunks_buffer: Dict[int, Dict[str, Any]] = {}
        self.frame_subscribers: List[Any] = []
        
        # Stream parameters
        self.stream_fps: int = 15
        self.fallback_frame_cache: Optional[bytes] = None
        self.last_fallback_gen_time: float = 0.0

    def register_frame_subscriber(self, callback):
        if callback not in self.frame_subscribers:
            self.frame_subscribers.append(callback)

    def unregister_frame_subscriber(self, callback):
        if callback in self.frame_subscribers:
            self.frame_subscribers.remove(callback)

    def _notify_subscribers(self, frame_data: bytes):
        for cb in list(self.frame_subscribers):
            try:
                cb(frame_data)
            except Exception as e:
                print(f"[Camera] Error notifying frame subscriber: {e}")

    def update_frame(self, frame_bytes: bytes, source: str = "rover_cam") -> int:
        """Stores a new raw JPEG frame and notifies streaming listeners."""
        with self.lock:
            self.latest_frame_bytes = frame_bytes
            self.last_frame_timestamp = time.time()
            self.frame_counter += 1
            frame_len = len(frame_bytes)

        self._notify_subscribers(frame_bytes)
        return frame_len

    def ingest_chunk(self, chunk_raw: bytes) -> Optional[bytes]:
        """Reassembles chunked ESP-NOW camera frames into full JPEG images."""
        if len(chunk_raw) < 6:
            return None

        # Chunk header format: [pkt_type(1), frame_id(2 LE), chunk_idx(1), total_chunks(1), chunk_len(1), data...]
        frame_id = int.from_bytes(chunk_raw[1:3], byteorder='little')
        chunk_idx = chunk_raw[3]
        total_chunks = chunk_raw[4]
        chunk_len = chunk_raw[5]
        payload = chunk_raw[6:6+chunk_len]

        completed_frame: Optional[bytes] = None

        with self.lock:
            if frame_id not in self.camera_chunks_buffer:
                self.camera_chunks_buffer[frame_id] = {
                    "total": total_chunks,
                    "chunks": {},
                    "first_seen": time.time()
                }

            buf = self.camera_chunks_buffer[frame_id]
            buf["chunks"][chunk_idx] = payload

            if len(buf["chunks"]) == total_chunks:
                full_frame = bytearray()
                for idx in range(total_chunks):
                    if idx in buf["chunks"]:
                        full_frame.extend(buf["chunks"][idx])
                
                completed_frame = bytes(full_frame)
                self.latest_frame_bytes = completed_frame
                self.last_frame_timestamp = time.time()
                self.frame_counter += 1

                # Clean up completed and stale frame buffers
                del self.camera_chunks_buffer[frame_id]
                now = time.time()
                stale_ids = [fid for fid, b in self.camera_chunks_buffer.items() if now - b["first_seen"] > 5.0]
                for fid in stale_ids:
                    del self.camera_chunks_buffer[fid]

        if completed_frame:
            self._notify_subscribers(completed_frame)

        return completed_frame

    def generate_fallback_frame(self, rover_status: Optional[Dict[str, Any]] = None) -> bytes:
        """Generates dynamic synthetic HUD telemetry overlay when physical camera is offline."""
        now = time.time()
        # Rate limit HUD redraws to 10 FPS
        if self.fallback_frame_cache and (now - self.last_fallback_gen_time) < 0.1:
            return self.fallback_frame_cache

        width, height = 320, 240
        img = Image.new('RGB', (width, height), color=(15, 23, 42))
        draw = ImageDraw.Draw(img)

        # Tactical HUD Grid
        for x in range(0, width, 40):
            draw.line([(x, 0), (x, height)], fill=(30, 41, 59), width=1)
        for y in range(0, height, 40):
            draw.line([(0, y), (width, y)], fill=(30, 41, 59), width=1)

        # Center Reticle
        cx, cy = width // 2, height // 2
        draw.line([(cx - 20, cy), (cx + 20, cy)], fill=(16, 185, 129), width=2)
        draw.line([(cx, cy - 20), (cx, cy + 20)], fill=(16, 185, 129), width=2)
        draw.ellipse([(cx - 30, cy - 30), (cx + 30, cy + 30)], outline=(16, 185, 129), width=1)

        # HUD Text Readouts
        draw.text((10, 10), "ROVER CAM FEED [STANDBY]", fill=(244, 63, 94))
        draw.text((10, 26), f"TIME: {datetime.now().strftime('%H:%M:%S.%f')[:10]}", fill=(148, 163, 184))
        
        cmd_name = rover_status.get("command_name", "STOP") if rover_status else "STOP"
        speed = rover_status.get("speed", 0) if rover_status else 0
        batt = rover_status.get("battery_pct", 90) if rover_status else 90
        
        draw.text((10, height - 38), f"TELEOP: {cmd_name} | SPEED: {speed} PWM", fill=(56, 189, 248))
        draw.text((10, height - 20), f"BATTERY: {batt}% | SATELLITE/MESH: ACTIVE", fill=(34, 197, 94))

        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=85)
        raw_bytes = buf.getvalue()

        self.fallback_frame_cache = raw_bytes
        self.last_fallback_gen_time = now
        return raw_bytes

    def get_latest_frame(self, rover_status: Optional[Dict[str, Any]] = None) -> bytes:
        """Returns the most recent camera frame or fallback HUD image."""
        with self.lock:
            # Check if camera frame is recent (within 10 seconds)
            if self.latest_frame_bytes and (time.time() - self.last_frame_timestamp) < 10.0:
                return self.latest_frame_bytes
        
        return self.generate_fallback_frame(rover_status)

    def get_snapshot_base64(self, rover_status: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Returns the latest image formatted as a Base64 Data URI."""
        frame_bytes = self.get_latest_frame(rover_status)
        b64 = base64.b64encode(frame_bytes).decode('utf-8')
        is_live = (self.latest_frame_bytes is not None) and ((time.time() - self.last_frame_timestamp) < 10.0)

        return {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "image_base64": f"data:image/jpeg;base64,{b64}",
            "content_type": "image/jpeg",
            "width": 320,
            "height": 240,
            "source": "live_esp32_cam" if is_live else "standby_hud"
        }

    def generate_mjpeg_stream(self, rover_status_fn=None, fps: int = 15):
        """
        Continuous Generator that yields multipart MJPEG stream frames
        suitable for direct browser <img> src or external video consumers.
        """
        frame_interval = 1.0 / max(1, fps)
        while True:
            t_start = time.time()
            rover_stat = rover_status_fn() if rover_status_fn else None
            frame = self.get_latest_frame(rover_stat)

            yield (
                b'--frame\r\n'
                b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n'
            )

            elapsed = time.time() - t_start
            sleep_time = max(0.01, frame_interval - elapsed)
            time.sleep(sleep_time)


camera_manager = CameraStreamManager()

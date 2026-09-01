"""
Camera Video Streaming & Frame Ingestion Endpoints
Provides standard MJPEG streaming (usable directly in HTML/React <img> tags),
single JPEG snapshots, Base64 Data URI payloads, and frame upload/reassembly.
"""

from fastapi import APIRouter, Request, Response, HTTPException
from fastapi.responses import StreamingResponse
from typing import Dict, Any

from ..camera import camera_manager
from ..state import state_manager
from ..models import CameraSnapshotResponse

router = APIRouter(tags=["Camera & Video Streaming"])


@router.get(
    "/api/v1/camera/stream",
    summary="Live Multipart MJPEG Video Stream",
    description="Streams real-time MJPEG video feed. Can be plugged directly into any frontend <img> tag: `<img src='http://localhost:8000/api/v1/camera/stream' />`."
)
async def get_camera_stream():
    """
    Continuous MJPEG video stream.
    Compatible with React, Vue, Angular, Flutter, iOS, Android, and Web browsers natively.
    """
    return StreamingResponse(
        camera_manager.generate_mjpeg_stream(rover_status_fn=lambda: state_manager.rover_state),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


@router.get(
    "/api/v1/camera/latest.jpg",
    summary="Get Latest Single JPEG Frame",
    description="Returns the most recent camera frame as an image/jpeg binary response."
)
@router.get("/api/camera/latest.jpg", include_in_schema=False)  # Legacy alias
async def get_latest_jpeg():
    frame_bytes = camera_manager.get_latest_frame(state_manager.rover_state)
    return Response(content=frame_bytes, media_type="image/jpeg")


@router.get(
    "/api/v1/camera/snapshot",
    response_model=CameraSnapshotResponse,
    summary="Get Latest Frame Snapshot (Base64)",
    description="Returns the latest camera snapshot encoded as a Base64 Data URI with image dimensions and metadata."
)
async def get_camera_snapshot():
    return camera_manager.get_snapshot_base64(state_manager.rover_state)


@router.post(
    "/api/v1/camera/upload",
    summary="Upload Raw JPEG Frame from Camera",
    description="Receives raw binary JPEG frame payload from ESP32-CAM over Wi-Fi."
)
@router.post("/api/camera_frame", include_in_schema=False)  # Legacy firmware alias
async def upload_camera_frame(request: Request):
    frame_bytes = await request.body()
    if not frame_bytes or len(frame_bytes) < 10:
        raise HTTPException(status_code=400, detail="Empty or invalid image payload")

    byte_len = camera_manager.update_frame(frame_bytes, source="esp32_cam")
    return {"status": "ok", "bytes_received": byte_len}


@router.post(
    "/api/v1/camera/chunk",
    summary="Upload ESP-NOW Reassembly Chunk",
    description="Receives binary chunks sent over ESP-NOW mesh relay and reassembles them into full JPEG frames."
)
@router.post("/api/camera_chunk", include_in_schema=False)  # Legacy firmware alias
async def upload_camera_chunk(request: Request):
    chunk_raw = await request.body()
    if len(chunk_raw) < 6:
        raise HTTPException(status_code=400, detail="Invalid chunk header")

    completed_frame = camera_manager.ingest_chunk(chunk_raw)
    return {
        "status": "chunk_received",
        "frame_assembled": completed_frame is not None,
        "frame_bytes": len(completed_frame) if completed_frame else 0
    }

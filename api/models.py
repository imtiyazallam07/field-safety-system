"""
Data Models and Schemas for Field Safety Monitoring System API
Used for OpenAPI validation, documentation, and serialization.
"""

from typing import List, Dict, Optional, Any, Union
from pydantic import BaseModel, Field
import time


# ================= SENSOR MODELS =================

class CheckpointTelemetryIn(BaseModel):
    """Payload sent by Checkpoint ESP32 or Mesh Relay."""
    checkpoint_id: int = Field(1, description="Unique checkpoint identifier")
    location: Optional[str] = Field("Checkpoint 1", description="Location name/zone label")
    mq5: Optional[float] = Field(0.0, description="MQ5 combustible gas reading (ADC / ppm) - GPIO 33")
    mq7: Optional[float] = Field(0.0, description="MQ7 Carbon Monoxide reading (ADC / ppm) - GPIO 34")
    mq135: Optional[float] = Field(0.0, description="MQ135 Air Quality reading (ADC / ppm) - GPIO 35")
    pir_motion: Optional[bool] = Field(False, description="PIR motion detection state (HW-416-B) - GPIO 26")
    sound_level: Optional[float] = Field(0.0, description="Peak sound level (HW-484-V0.2) - GPIO 27")
    temperature: Optional[float] = Field(25.0, description="Ambient temperature (°C, DHT11) - GPIO 15")
    humidity: Optional[float] = Field(50.0, description="Relative humidity (%, DHT11) - GPIO 15")
    proximity_cm: Optional[float] = Field(None, description="Proximity to nearest object (cm, HC-SR04)")
    band: Optional[Dict[str, Any]] = Field(None, description="Relayed Band data if piggybacked")
    rover: Optional[Dict[str, Any]] = Field(None, description="Relayed Rover data if piggybacked")
    timestamp_ms: Optional[int] = Field(None, description="Device uptime timestamp in milliseconds")


class BandTelemetryIn(BaseModel):
    """Payload sent by Worker Monitoring Band ESP32 (direct or relayed)."""
    band_id: int = Field(1, description="Worker Band identifier")
    worker_name: Optional[str] = Field("Worker-1", description="Worker name or personnel tag")
    pulse_bpm: float = Field(75.0, description="Heart rate in BPM (SPO2 sensor)")
    spo2: float = Field(98.0, description="Blood oxygen percentage (%)")
    battery_pct: Optional[int] = Field(100, description="Battery percentage (0-100%)")
    relayed_by: Optional[int] = Field(1, description="Checkpoint ID that relayed this packet")
    hops: Optional[int] = Field(1, description="Number of mesh hops traversed")
    timestamp_ms: Optional[int] = Field(None, description="Device uptime timestamp in milliseconds")


class RoverTelemetryIn(BaseModel):
    """Telemetry sent by Rover Driver ESP32."""
    rover_id: int = Field(1, description="Rover identifier")
    current_command: Optional[int] = Field(0, description="Active command code (0=STOP, 1=FWD, 2=BACK, 3=LEFT, 4=RIGHT)")
    speed: Optional[int] = Field(0, description="Motor PWM speed (0-255)")
    battery_pct: Optional[int] = Field(90, description="Rover battery level (0-100%)")
    failsafe_triggered: Optional[bool] = Field(False, description="Whether safety timeout failsafe is active")
    ip: Optional[str] = Field(None, description="Rover IP address if connected via Wi-Fi")
    connected_checkpoint_id: Optional[int] = Field(None, description="Checkpoint ID rover is communicating through")


# ================= ROVER CONTROL =================

class RoverCommandIn(BaseModel):
    """Steering command sent to the Rover."""
    command: str = Field("STOP", description="Steering direction: STOP, FORWARD, BACKWARD, LEFT, RIGHT")
    speed: Optional[int] = Field(200, ge=0, le=255, description="Motor speed (0 to 255 PWM)")
    rover_id: Optional[int] = Field(1, description="Target Rover ID")


class RoverCommandResponse(BaseModel):
    status: str
    command: str
    speed: int
    seq_id: int
    timestamp: float


# ================= OUTPUT / STATUS MODELS =================

class SensorHistoryPoint(BaseModel):
    timestamp: str
    ts: float
    mq5: Optional[float] = None
    mq7: Optional[float] = None
    mq135: Optional[float] = None
    sound_level: Optional[float] = None
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    pir_motion: Optional[bool] = None
    proximity_cm: Optional[float] = None


class CheckpointStatus(BaseModel):
    checkpoint_id: int
    location: str
    mq5: float
    mq7: float
    mq135: float
    pir_motion: bool
    sound_level: float
    temperature: float
    humidity: float
    proximity_cm: Optional[float] = None
    last_seen: float
    is_online: bool
    history_count: int = 0


class CheckpointDetail(CheckpointStatus):
    history: List[Dict[str, Any]] = []


class BandHistoryPoint(BaseModel):
    timestamp: str
    ts: float
    pulse_bpm: float
    spo2: float


class BandStatus(BaseModel):
    band_id: int
    worker_name: str
    pulse_bpm: float
    spo2: float
    battery_pct: int
    relayed_by: int
    hops: int
    last_seen: float
    is_online: bool
    history_count: int = 0


class BandDetail(BandStatus):
    history: List[Dict[str, Any]] = []


class RoverStatus(BaseModel):
    rover_id: int
    current_command: int
    command_name: str
    speed: int
    battery_pct: int
    failsafe_triggered: bool
    last_seen: float
    is_connected: bool
    is_online: bool
    connected_checkpoint_id: Optional[int] = None
    connected_checkpoint_location: Optional[str] = None
    ip: Optional[str] = None


class AlertItem(BaseModel):
    id: str
    timestamp: str
    level: str  # "CRITICAL", "WARNING", "INFO"
    category: str  # "Vitals", "Gas", "Motion", "Sound", "Environment", "Rover", "Proximity"
    device_id: str
    message: str
    acked: bool = False


class FullSystemSnapshot(BaseModel):
    checkpoints: List[CheckpointStatus]
    bands: List[BandStatus]
    rover: RoverStatus
    alerts: List[AlertItem]
    thresholds: Dict[str, float]
    server_time: str
    device_summary: Dict[str, int]


class SystemSummary(BaseModel):
    status: str  # "NORMAL", "WARNING", "CRITICAL"
    total_workers: int
    online_workers: int
    total_checkpoints: int
    online_checkpoints: int
    rover_connected: bool
    active_critical_alerts: int
    active_warning_alerts: int
    server_time: str


class CameraSnapshotResponse(BaseModel):
    timestamp: str
    image_base64: str
    content_type: str = "image/jpeg"
    width: int
    height: int
    source: str


# ================= WEBHOOK MODELS =================

class WebhookSubscriptionIn(BaseModel):
    url: str = Field(..., description="External webhook endpoint URL to deliver HTTP POST events to")
    events: List[str] = Field(
        default=["alert.critical", "alert.warning"],
        description="Event types to subscribe to: 'alert.critical', 'alert.warning', 'alert.info', 'telemetry.checkpoint', 'telemetry.band', 'rover.status', '*'"
    )
    secret: Optional[str] = Field(None, description="Optional secret token for HMAC SHA256 signature verification")
    description: Optional[str] = Field(None, description="Descriptive label for this webhook consumer platform")


class WebhookSubscription(BaseModel):
    id: str
    url: str
    events: List[str]
    description: Optional[str]
    created_at: str
    last_delivered_at: Optional[str] = None
    total_deliveries: int = 0
    failed_deliveries: int = 0

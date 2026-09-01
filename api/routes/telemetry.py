"""
Sensor Telemetry and Data Query Endpoints
Handles real-time sensor metrics (MQ5, MQ7, MQ135, PIR, Sound, Temp, Hum, Proximity),
worker band vitals (SpO2, Pulse), mesh topologies, and ingestion from hardware/simulators.
"""

from fastapi import APIRouter, HTTPException, Request, Response
from typing import List, Dict, Any, Optional

from ..state import state_manager
from ..models import (
    FullSystemSnapshot,
    SystemSummary,
    CheckpointStatus,
    CheckpointDetail,
    BandStatus,
    BandDetail,
    CheckpointTelemetryIn,
    BandTelemetryIn,
    RoverTelemetryIn
)

router = APIRouter(tags=["Sensors & Telemetry"])


# ================= SYSTEM OVERVIEW & SNAPSHOT =================

@router.get(
    "/api/v1/data",
    response_model=FullSystemSnapshot,
    summary="Get Full System Snapshot",
    description="Returns live state of all checkpoints, worker bands, rover telemetry, active safety alerts, and thresholds."
)
@router.get("/api/data", include_in_schema=False)  # Legacy alias
async def get_all_data():
    return state_manager.get_full_snapshot()


@router.get(
    "/api/v1/system/summary",
    response_model=SystemSummary,
    summary="Get High-Level System Summary",
    description="Quick overview of system health status (NORMAL/WARNING/CRITICAL), active workers, and alerts count."
)
async def get_system_summary():
    return state_manager.get_summary()


# ================= CHECKPOINTS & ENVIRONMENTAL SENSORS =================

@router.get(
    "/api/v1/sensors/checkpoints",
    response_model=List[CheckpointStatus],
    summary="List All Checkpoints & Environmental Sensors",
    description="Retrieves current gas readings (MQ5, MQ7, MQ135), sound level, PIR motion, temperature, and humidity for all checkpoints."
)
async def list_checkpoints():
    snapshot = state_manager.get_full_snapshot()
    return snapshot["checkpoints"]


@router.get(
    "/api/v1/sensors/checkpoints/{checkpoint_id}",
    response_model=CheckpointDetail,
    summary="Get Checkpoint Details & History",
    description="Retrieves current sensor data and rolling time-series history for a specific checkpoint."
)
async def get_checkpoint_detail(checkpoint_id: int):
    with state_manager.lock:
        if checkpoint_id not in state_manager.checkpoints:
            raise HTTPException(status_code=404, detail=f"Checkpoint #{checkpoint_id} not found")
        
        cp = state_manager.checkpoints[checkpoint_id]
        return {
            "checkpoint_id": cp["checkpoint_id"],
            "location": cp["location"],
            "mq5": cp["mq5"],
            "mq7": cp["mq7"],
            "mq135": cp["mq135"],
            "pir_motion": cp["pir_motion"],
            "sound_level": cp["sound_level"],
            "temperature": cp["temperature"],
            "humidity": cp["humidity"],
            "proximity_cm": cp.get("proximity_cm"),
            "last_seen": cp["last_seen"],
            "is_online": (state_manager.rover_state.get("last_seen", 0) > 0),
            "history_count": len(cp["history"]),
            "history": list(cp["history"])
        }


# ================= WORKER MONITORING BANDS =================

@router.get(
    "/api/v1/sensors/bands",
    response_model=List[BandStatus],
    summary="List All Worker Bands & Vitals",
    description="Returns live SpO2 blood oxygen, pulse BPM, battery level, relay mesh checkpoint, and online status for all worker bands."
)
async def list_bands():
    snapshot = state_manager.get_full_snapshot()
    return snapshot["bands"]


@router.get(
    "/api/v1/sensors/bands/{band_id}",
    response_model=BandDetail,
    summary="Get Worker Band Details & History",
    description="Retrieves current vital signs and rolling time-series graph history for a specific worker band."
)
async def get_band_detail(band_id: int):
    with state_manager.lock:
        if band_id not in state_manager.bands:
            raise HTTPException(status_code=404, detail=f"Worker Band #{band_id} not found")
        
        b = state_manager.bands[band_id]
        return {
            "band_id": b["band_id"],
            "worker_name": b["worker_name"],
            "pulse_bpm": b["pulse_bpm"],
            "spo2": b["spo2"],
            "battery_pct": b["battery_pct"],
            "relayed_by": b["relayed_by"],
            "hops": b["hops"],
            "last_seen": b["last_seen"],
            "is_online": True,
            "history_count": len(b["history"]),
            "history": list(b["history"])
        }


# ================= HARDWARE INGESTION ENDPOINTS =================

@router.post(
    "/api/v1/ingest/telemetry",
    summary="Ingest Checkpoint Telemetry (ESP32 / Mesh)",
    description="Ingest endpoint for Checkpoints to post sensor readings, relayed worker band vitals, and relayed rover status."
)
@router.post("/api/telemetry", include_in_schema=False)  # Legacy firmware alias
async def ingest_telemetry(payload: CheckpointTelemetryIn):
    result = state_manager.ingest_checkpoint_telemetry(payload.model_dump())
    return result


@router.post(
    "/api/v1/ingest/band",
    summary="Ingest Worker Band Vitals Directly",
    description="Direct or mesh endpoint for Worker Bands to send pulse BPM and SpO2 readings."
)
@router.post("/api/telemetry/band", include_in_schema=False)  # Legacy firmware alias
async def ingest_band(payload: BandTelemetryIn):
    result = state_manager.ingest_band_telemetry(payload.model_dump())
    return result


@router.post(
    "/api/v1/ingest/rover",
    summary="Ingest Direct Rover Telemetry",
    description="Direct Wi-Fi telemetry endpoint for Rover Driver ESP32."
)
@router.post("/api/telemetry/rover", include_in_schema=False)  # Legacy firmware alias
async def ingest_rover(payload: RoverTelemetryIn):
    result = state_manager.ingest_rover_telemetry(payload.model_dump())
    return result

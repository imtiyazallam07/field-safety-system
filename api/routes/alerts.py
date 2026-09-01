"""
Safety Alerts and Threshold Management Endpoints
Handles safety threshold violations, real-time alert logs, acknowledgments, and threshold adjustments.
"""

from fastapi import APIRouter, Query, HTTPException
from typing import List, Dict, Any, Optional

from ..state import state_manager
from ..models import AlertItem

router = APIRouter(tags=["Safety Alerts & Thresholds"])


@router.get(
    "/api/v1/alerts",
    response_model=List[AlertItem],
    summary="Get Safety Alerts Feed",
    description="Retrieves list of safety alerts triggered by gas spikes, hypoxia, sound spikes, proximity violations, or disconnects."
)
async def get_alerts(
    limit: int = Query(50, ge=1, le=250, description="Max alerts to retrieve"),
    level: Optional[str] = Query(None, description="Filter by level: CRITICAL, WARNING, INFO"),
    unacked_only: bool = Query(False, description="Return only unacknowledged alerts")
):
    return state_manager.get_alerts(limit=limit, level=level, unacked_only=unacked_only)


@router.post(
    "/api/v1/alerts/ack",
    summary="Acknowledge Alerts",
    description="Marks alerts as acknowledged by safety personnel."
)
@router.post("/api/alerts/ack", include_in_schema=False)  # Legacy alias
async def acknowledge_alerts(alert_id: Optional[str] = Query(None, description="Specific alert ID to ack, or omit to ack all")):
    count = state_manager.acknowledge_alerts(alert_id=alert_id)
    return {"status": "ok", "acknowledged_count": count}


@router.delete(
    "/api/v1/alerts",
    summary="Clear Alert Feed",
    description="Flushes all stored alerts from memory."
)
async def clear_alerts():
    state_manager.clear_alerts()
    return {"status": "alerts_cleared"}


@router.get(
    "/api/v1/thresholds",
    summary="Get Safety Threshold Configurations",
    description="Returns current threshold values for SpO2, Pulse, MQ5, MQ7, MQ135, Sound, Temp, Hum, and Proximity."
)
@router.get("/api/thresholds", include_in_schema=False)  # Legacy alias
async def get_thresholds():
    with state_manager.lock:
        return state_manager.thresholds


@router.post(
    "/api/v1/thresholds",
    summary="Update Safety Threshold Configurations",
    description="Dynamically updates safety thresholds used for automated alert evaluation."
)
@router.post("/api/thresholds", include_in_schema=False)  # Legacy alias
async def update_thresholds(threshold_updates: Dict[str, float]):
    with state_manager.lock:
        updated = {}
        for key, value in threshold_updates.items():
            if key in state_manager.thresholds:
                state_manager.thresholds[key] = float(value)
                updated[key] = float(value)
        return {
            "status": "thresholds_updated",
            "updated_thresholds": updated,
            "all_thresholds": state_manager.thresholds
        }

"""
Rover Teleoperation and Control Endpoints
Handles steering commands (FORWARD, BACKWARD, LEFT, RIGHT, STOP), speed adjustment,
failsafe status, and command polling for hardware/simulators.
"""

from fastapi import APIRouter, HTTPException
from typing import Dict, Any

from ..state import state_manager
from ..models import RoverStatus, RoverCommandIn, RoverCommandResponse

router = APIRouter(tags=["Rover Teleoperation"])


@router.get(
    "/api/v1/rover/status",
    response_model=RoverStatus,
    summary="Get Rover Telemetry & Connection Status",
    description="Returns current speed, heading, battery, failsafe state, and whether connected directly or via checkpoint mesh."
)
async def get_rover_status():
    snapshot = state_manager.get_full_snapshot()
    return snapshot["rover"]


@router.post(
    "/api/v1/rover/command",
    response_model=RoverCommandResponse,
    summary="Send Steering Command to Rover",
    description="Sends a drive command (STOP, FORWARD, BACKWARD, LEFT, RIGHT) and speed (0-255) to the Rover. Instantly fires UDP broadcast for local Wi-Fi and queues mesh packet."
)
@router.post("/api/rover/command", include_in_schema=False)  # Legacy alias
async def send_rover_command(cmd: RoverCommandIn):
    valid_cmds = ["STOP", "FORWARD", "BACKWARD", "LEFT", "RIGHT"]
    command_str = cmd.command.upper()
    if command_str not in valid_cmds:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid command '{cmd.command}'. Must be one of: {', '.join(valid_cmds)}"
        )

    res = state_manager.send_rover_command(
        command_str=command_str,
        speed=cmd.speed or 200,
        rover_id=cmd.rover_id or 1
    )
    return res


@router.get(
    "/api/v1/rover/command/poll",
    summary="Poll Active Rover Command",
    description="Allows Checkpoint or Rover Driver ESP32 to poll for active queued commands."
)
@router.get("/api/rover/command/poll", include_in_schema=False)  # Legacy firmware alias
async def poll_rover_command():
    with state_manager.lock:
        return state_manager.pending_rover_command

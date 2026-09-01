"""
Data Export Endpoints
Allows external analytics platforms to export historical sensor metrics,
vitals, and safety incident logs in JSON and CSV formats.
"""

import io
import csv
from fastapi import APIRouter, Response
from ..state import state_manager

router = APIRouter(tags=["Data Export & Analytics"])


@router.get(
    "/api/v1/export/json",
    summary="Export Full Historical Data (JSON)",
    description="Dumps all in-memory rolling time series data for all checkpoints, bands, and alerts."
)
async def export_json():
    with state_manager.lock:
        data = {
            "checkpoints": {cid: list(cp["history"]) for cid, cp in state_manager.checkpoints.items()},
            "bands": {bid: list(b["history"]) for bid, b in state_manager.bands.items()},
            "alerts": list(state_manager.alerts)
        }
    return data


@router.get(
    "/api/v1/export/csv/checkpoints",
    summary="Export Checkpoint Sensors (CSV)",
    description="Generates a downloadable CSV containing all time-series environmental readings."
)
async def export_checkpoints_csv():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["checkpoint_id", "location", "timestamp", "ts", "mq5", "mq7", "mq135", "sound_level", "temp_c", "hum_pct", "pir_motion", "proximity_cm"])

    with state_manager.lock:
        for cid, cp in state_manager.checkpoints.items():
            loc = cp.get("location", f"Checkpoint {cid}")
            for pt in cp.get("history", []):
                writer.writerow([
                    cid,
                    loc,
                    pt.get("timestamp"),
                    pt.get("ts"),
                    pt.get("mq5"),
                    pt.get("mq7"),
                    pt.get("mq135"),
                    pt.get("sound_level"),
                    pt.get("temperature"),
                    pt.get("humidity"),
                    pt.get("pir_motion"),
                    pt.get("proximity_cm", "")
                ])

    csv_data = output.getvalue()
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=checkpoint_sensors.csv"}
    )


@router.get(
    "/api/v1/export/csv/bands",
    summary="Export Worker Vitals (CSV)",
    description="Generates a downloadable CSV containing pulse and SpO2 readings for all worker bands."
)
async def export_bands_csv():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["band_id", "worker_name", "timestamp", "ts", "pulse_bpm", "spo2_pct"])

    with state_manager.lock:
        for bid, b in state_manager.bands.items():
            name = b.get("worker_name", f"Worker-{bid}")
            for pt in b.get("history", []):
                writer.writerow([
                    bid,
                    name,
                    pt.get("timestamp"),
                    pt.get("ts"),
                    pt.get("pulse_bpm"),
                    pt.get("spo2")
                ])

    csv_data = output.getvalue()
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=worker_vitals.csv"}
    )

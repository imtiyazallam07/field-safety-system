"""
CoalGov AI — Safety Analysis Report Service
============================================
Standalone module for generating AI-powered safety reports.
Reads EXISTING backend state (read-only) and calls Google Gemini for analysis.

HARDWARE ISOLATION:
    This module does NOT import, modify, or interact with any hardware
    communication, sensor processing, or device control code.
    It only reads from the in-memory state dicts passed to it.
"""

import os
import json
import time
import traceback
from datetime import datetime


# ================= GEMINI CLIENT (LAZY INIT) =================

_gemini_model = None
_gemini_init_attempted = False
_active_model_name = "gemini-2.5-flash"

CANDIDATE_MODELS = [
    "gemini-flash-latest",
    "gemini-3.6-flash",
    "gemini-3.7-flash",
    "gemini-2.5-flash-lite",
    "gemini-pro-latest"
]


def _get_gemini_model():
    """Lazily initializes the Gemini generative model. Returns None if unavailable."""
    global _gemini_model, _gemini_init_attempted, _active_model_name

    if _gemini_init_attempted and _gemini_model is not None:
        return _gemini_model

    _gemini_init_attempted = True
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()

    if not api_key:
        print("[ReportService] GEMINI_API_KEY not set. AI analysis will be unavailable.")
        return None

    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        
        # Test candidate models in order
        for model_name in CANDIDATE_MODELS:
            try:
                _gemini_model = genai.GenerativeModel(model_name)
                _active_model_name = model_name
                print(f"[ReportService] Gemini model initialized successfully ({model_name}).")
                return _gemini_model
            except Exception as me:
                print(f"[ReportService] Candidate {model_name} failed: {me}")
                continue

        print("[ReportService] No candidate model could be initialized.")
        return None
    except Exception as e:
        print(f"[ReportService] Failed to initialize Gemini: {e}")
        return None


# ================= READ-ONLY DATA SNAPSHOT =================

def build_report_snapshot(checkpoints_dict, bands_dict, rover_state, alerts_deque, thresholds_dict):
    """
    Builds a read-only JSON-serializable snapshot of the current system state.
    This function does NOT modify any state — it only reads.

    Args:
        checkpoints_dict: The existing checkpoints dict (under state_lock).
        bands_dict: The existing bands dict (under state_lock).
        rover_state: The existing rover_state dict (under state_lock).
        alerts_deque: The existing alerts deque (under state_lock).
        thresholds_dict: The existing thresholds dict (under state_lock).

    Returns:
        dict: A structured snapshot suitable for report generation and Gemini analysis.
    """
    now = time.time()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # --- Checkpoint Snapshot ---
    checkpoint_snapshots = []
    for cp_id, cp in sorted(checkpoints_dict.items()):
        age_seconds = now - cp.get("last_seen", 0)
        is_online = age_seconds < 10

        # Extract latest history stats (min/max/avg) if available
        hist = list(cp.get("history", []))
        stats = {}
        if hist:
            stats = {
                "data_points": len(hist),
                "mq5_avg": round(sum(h["mq5"] for h in hist) / len(hist), 1),
                "mq5_max": max(h["mq5"] for h in hist),
                "mq7_avg": round(sum(h["mq7"] for h in hist) / len(hist), 1),
                "mq7_max": max(h["mq7"] for h in hist),
                "mq135_avg": round(sum(h["mq135"] for h in hist) / len(hist), 1),
                "mq135_max": max(h["mq135"] for h in hist),
                "sound_avg": round(sum(h["sound_level"] for h in hist) / len(hist), 1),
                "sound_max": max(h["sound_level"] for h in hist),
                "temp_avg": round(sum(h["temperature"] for h in hist) / len(hist), 1),
                "temp_max": max(h["temperature"] for h in hist),
                "humidity_avg": round(sum(h["humidity"] for h in hist) / len(hist), 1),
                "motion_events": sum(1 for h in hist if h.get("pir_motion", False)),
            }

        checkpoint_snapshots.append({
            "checkpoint_id": cp.get("checkpoint_id", cp_id),
            "location": cp.get("location", f"Checkpoint {cp_id}"),
            "is_online": is_online,
            "age_seconds": round(age_seconds, 1),
            "current": {
                "mq5": cp.get("mq5", 0),
                "mq7": cp.get("mq7", 0),
                "mq135": cp.get("mq135", 0),
                "pir_motion": cp.get("pir_motion", False),
                "sound_level": cp.get("sound_level", 0),
                "temperature": cp.get("temperature", 25.0),
                "humidity": cp.get("humidity", 50.0),
            },
            "history_stats": stats,
        })

    # --- Worker Band Snapshot ---
    worker_snapshots = []
    for b_id, b in sorted(bands_dict.items()):
        age_seconds = now - b.get("last_seen", 0)
        is_online = age_seconds < 10

        hist = list(b.get("history", []))
        stats = {}
        if hist:
            stats = {
                "data_points": len(hist),
                "spo2_avg": round(sum(h["spo2"] for h in hist) / len(hist), 1),
                "spo2_min": min(h["spo2"] for h in hist),
                "pulse_avg": round(sum(h["pulse_bpm"] for h in hist) / len(hist), 1),
                "pulse_max": max(h["pulse_bpm"] for h in hist),
                "pulse_min": min(h["pulse_bpm"] for h in hist),
            }

        worker_snapshots.append({
            "band_id": b.get("band_id", b_id),
            "worker_name": b.get("worker_name", f"Worker-{b_id}"),
            "is_online": is_online,
            "age_seconds": round(age_seconds, 1),
            "current": {
                "spo2": b.get("spo2", 0),
                "pulse_bpm": b.get("pulse_bpm", 0),
                "battery_pct": b.get("battery_pct", 0),
            },
            "relayed_by": b.get("relayed_by", None),
            "hops": b.get("hops", 0),
            "history_stats": stats,
        })

    # --- Rover Snapshot ---
    rover_age = now - rover_state.get("last_seen", 0)
    rover_snapshot = {
        "is_online": rover_state.get("is_online", False),
        "is_connected": rover_state.get("is_connected", False),
        "age_seconds": round(rover_age, 1),
        "command_name": rover_state.get("command_name", "STOP"),
        "speed": rover_state.get("speed", 0),
        "battery_pct": rover_state.get("battery_pct", 90),
        "connected_checkpoint_id": rover_state.get("connected_checkpoint_id"),
        "connected_checkpoint_location": rover_state.get("connected_checkpoint_location"),
        "failsafe_triggered": rover_state.get("failsafe_triggered", False),
    }

    # --- Alerts Snapshot ---
    alert_list = []
    critical_count = 0
    warning_count = 0
    info_count = 0
    for a in list(alerts_deque)[:50]:
        level = a.get("level", "INFO")
        if level == "CRITICAL":
            critical_count += 1
        elif level == "WARNING":
            warning_count += 1
        else:
            info_count += 1
        alert_list.append({
            "id": a.get("id"),
            "timestamp": a.get("timestamp"),
            "level": level,
            "category": a.get("category"),
            "device_id": a.get("device_id"),
            "message": a.get("message"),
        })

    # --- Full Snapshot ---
    snapshot = {
        "generated_at": now_str,
        "generated_ts": now,
        "station_id": "STATION-01",
        "checkpoints": checkpoint_snapshots,
        "checkpoint_count": len(checkpoint_snapshots),
        "workers": worker_snapshots,
        "worker_count": len(worker_snapshots),
        "rover": rover_snapshot,
        "alerts": alert_list,
        "alert_summary": {
            "total": len(alert_list),
            "critical": critical_count,
            "warning": warning_count,
            "info": info_count,
        },
        "thresholds": dict(thresholds_dict),
        "data_availability": {
            "checkpoints_reporting": len(checkpoint_snapshots) > 0,
            "workers_reporting": len(worker_snapshots) > 0,
            "rover_connected": rover_snapshot["is_online"],
            "alerts_present": len(alert_list) > 0,
            "hardware_connected": any(cp["is_online"] for cp in checkpoint_snapshots) or any(w["is_online"] for w in worker_snapshots),
        },
    }

    return snapshot


# ================= GEMINI AI ANALYSIS =================

GEMINI_SYSTEM_PROMPT = """You are a coal mine safety analysis AI integrated into the CoalGov AI underground mine monitoring system. You receive a JSON snapshot of real-time telemetry data from an underground coal mine safety monitoring station.

Your role is to analyze the data and provide a professional safety assessment report.

CRITICAL RULES:
1. Base your analysis ONLY on the data provided in the snapshot. Never invent or assume data.
2. Clearly distinguish between MEASURED DATA (facts from sensors), AI INTERPRETATION (your analysis), and RECOMMENDATIONS (suggested actions).
3. If data is insufficient or unavailable, say so explicitly. Never present assumptions as facts.
4. If hardware is disconnected (no checkpoints/workers reporting), state that live telemetry is unavailable and limit your analysis to whatever historical or alert data exists.
5. Use professional mine safety terminology.
6. Prioritize life safety above all else.

Respond with a JSON object containing exactly these fields:
{
    "overall_safety_status": "NOMINAL" | "CAUTION" | "WARNING" | "CRITICAL" | "INSUFFICIENT_DATA",
    "overall_summary": "A 2-3 sentence summary of the overall safety situation.",
    "observations": [
        {"observation": "text", "severity": "LOW" | "MEDIUM" | "HIGH" | "CRITICAL", "category": "text"}
    ],
    "risk_patterns": [
        {"pattern": "text", "evidence": "text", "risk_level": "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"}
    ],
    "recommendations": [
        {"action": "text", "priority": "LOW" | "MEDIUM" | "HIGH" | "CRITICAL", "reasoning": "text"}
    ],
    "data_limitations": ["text"],
    "ai_confidence": "LOW" | "MEDIUM" | "HIGH"
}

Respond with ONLY the JSON object, no markdown fences or extra text."""


def call_gemini_analysis(snapshot_dict):
    """
    Sends the read-only data snapshot to Gemini for AI safety analysis.
    Returns a structured dict, or a fallback dict if Gemini is unavailable.
    """
    model = _get_gemini_model()

    if model is None:
        return _gemini_unavailable_fallback("GEMINI_API_KEY environment variable not set.")

    # Build a compact version of the snapshot for the prompt (exclude raw alert list to save tokens)
    compact = {
        "generated_at": snapshot_dict.get("generated_at"),
        "station_id": snapshot_dict.get("station_id"),
        "checkpoint_count": snapshot_dict.get("checkpoint_count"),
        "checkpoints": snapshot_dict.get("checkpoints", []),
        "worker_count": snapshot_dict.get("worker_count"),
        "workers": snapshot_dict.get("workers", []),
        "rover": snapshot_dict.get("rover"),
        "alert_summary": snapshot_dict.get("alert_summary"),
        "recent_alerts": snapshot_dict.get("alerts", [])[:15],
        "thresholds": snapshot_dict.get("thresholds"),
        "data_availability": snapshot_dict.get("data_availability"),
    }

    user_prompt = f"Analyze the following coal mine safety telemetry snapshot and provide your assessment:\n\n{json.dumps(compact, indent=2)}"

    import google.generativeai as genai

    last_error = None
    for model_name in CANDIDATE_MODELS:
        try:
            curr_model = genai.GenerativeModel(model_name)
            response = curr_model.generate_content(
                [
                    {"role": "user", "parts": [{"text": GEMINI_SYSTEM_PROMPT + "\n\n" + user_prompt}]}
                ],
                generation_config={
                    "temperature": 0.2,
                    "max_output_tokens": 2048,
                    "response_mime_type": "application/json"
                }
            )

            response_text = response.text.strip()

            # Strip markdown fences if present
            if response_text.startswith("```"):
                lines = response_text.split("\n")
                lines = [l for l in lines if not l.strip().startswith("```")]
                response_text = "\n".join(lines).strip()

            analysis = json.loads(response_text)

            # Validate required fields
            required_fields = ["overall_safety_status", "overall_summary", "observations", "recommendations"]
            for field in required_fields:
                if field not in analysis:
                    analysis[field] = [] if field in ["observations", "recommendations"] else "Data unavailable"

            analysis["ai_source"] = f"Google Gemini ({model_name})"
            analysis["ai_available"] = True
            return analysis

        except json.JSONDecodeError as e:
            print(f"[ReportService] Model {model_name} returned non-JSON response: {e}")
            last_error = f"Model {model_name} returned unparseable JSON"
            continue
        except Exception as e:
            print(f"[ReportService] Model {model_name} error: {e}")
            last_error = str(e)
            continue

    return _gemini_unavailable_fallback(f"Gemini API error: {last_error}")


def _gemini_unavailable_fallback(reason):
    """Returns a structured fallback when Gemini is unavailable."""
    return {
        "ai_available": False,
        "ai_source": "Unavailable",
        "overall_safety_status": "INSUFFICIENT_DATA",
        "overall_summary": f"AI analysis temporarily unavailable. Reason: {reason}",
        "observations": [],
        "risk_patterns": [],
        "recommendations": [],
        "data_limitations": [reason, "AI-powered analysis requires a valid GEMINI_API_KEY environment variable."],
        "ai_confidence": "NONE",
    }


# ================= FULL REPORT ASSEMBLY =================

def generate_full_report(snapshot, gemini_analysis):
    """
    Merges the real data snapshot and Gemini's AI analysis into a unified report.
    Clearly separates measured data from AI interpretation.
    """
    da = snapshot.get("data_availability", {})

    report = {
        "meta": {
            "title": "CoalGov AI — Underground Mine Safety Analysis Report",
            "generated_at": snapshot.get("generated_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            "station_id": snapshot.get("station_id", "STATION-01"),
            "report_type": "COMPREHENSIVE_SAFETY_ANALYSIS",
        },
        "data_summary": {
            "checkpoints_active": snapshot.get("checkpoint_count", 0),
            "workers_monitored": snapshot.get("worker_count", 0),
            "rover_online": snapshot.get("rover", {}).get("is_online", False),
            "total_alerts": snapshot.get("alert_summary", {}).get("total", 0),
            "critical_alerts": snapshot.get("alert_summary", {}).get("critical", 0),
            "warning_alerts": snapshot.get("alert_summary", {}).get("warning", 0),
            "info_alerts": snapshot.get("alert_summary", {}).get("info", 0),
            "hardware_connected": da.get("hardware_connected", False),
        },
        "checkpoints": snapshot.get("checkpoints", []),
        "workers": snapshot.get("workers", []),
        "rover": snapshot.get("rover", {}),
        "alerts": snapshot.get("alerts", []),
        "thresholds": snapshot.get("thresholds", {}),
        "ai_analysis": gemini_analysis,
        "data_availability": da,
    }

    return report

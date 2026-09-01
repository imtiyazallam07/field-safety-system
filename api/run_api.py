#!/usr/bin/env python3
"""
Field Safety Monitoring System - API Server Launcher
Run with:
    python api/run_api.py
    python api/run_api.py --port 8000 --simulate
"""

import sys
import os
import argparse
import uvicorn

# Ensure project root is in sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


def main():
    parser = argparse.ArgumentParser(description="Field Safety Monitoring System Universal API Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host address to bind to (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    parser.add_argument("--simulate", action="store_true", help="Start with built-in sensor & camera simulation enabled")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload on code change")

    args = parser.parse_args()

    if args.simulate:
        os.environ["ENABLE_SIMULATOR"] = "true"

    print("=" * 65)
    print("  FIELD SAFETY MONITORING SYSTEM - UNIVERSAL API SERVER")
    print("=" * 65)
    print(f"  API Documentation (Swagger) : http://localhost:{args.port}/docs")
    print(f"  API Documentation (Redoc)   : http://localhost:{args.port}/redoc")
    print(f"  Universal Test Interface    : http://localhost:{args.port}/test")
    print(f"  Live Video Stream Endpoint  : http://localhost:{args.port}/api/v1/camera/stream")
    print(f"  WebSocket Live Telemetry    : ws://localhost:{args.port}/api/v1/ws/live")
    print(f"  Simulation Mode             : {'ENABLED' if args.simulate else 'DISABLED (Hardware Mode)'}")
    print("=" * 65)
    print(f"Starting server on {args.host}:{args.port}...\n")

    uvicorn.run(
        "api.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info"
    )


if __name__ == "__main__":
    main()

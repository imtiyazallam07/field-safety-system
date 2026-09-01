"""
API Configuration Settings
Field Safety Monitoring System API
"""

import os
from typing import Dict

# Network & Server Settings
DEFAULT_HOST = os.getenv("API_HOST", "0.0.0.0")
DEFAULT_PORT = int(os.getenv("API_PORT", "8000"))
UDP_BROADCAST_PORT = int(os.getenv("UDP_PORT", "4210"))

# State & History Settings
HISTORY_MAXLEN = int(os.getenv("HISTORY_MAXLEN", "120"))
ALERTS_MAXLEN = int(os.getenv("ALERTS_MAXLEN", "250"))
CAMERA_FRAME_TTL_SEC = 10.0

# Configurable Sensor Thresholds (Matching PIN_Config.txt and Hardware Spec)
DEFAULT_THRESHOLDS = {
    # Worker Vitals
    "spo2_critical": 90.0,
    "spo2_warning": 94.0,
    "pulse_min": 50.0,
    "pulse_max": 120.0,
    
    # Gas Sensors (ADC / ppm)
    "mq5_warning": 350.0,      # Combustible gas (GPIO 33)
    "mq5_critical": 600.0,
    "mq7_warning": 250.0,      # Carbon monoxide (GPIO 34)
    "mq7_critical": 450.0,
    "mq135_warning": 350.0,    # Air quality / toxic vapor (GPIO 35)
    "mq135_critical": 550.0,
    
    # Sound & Environment
    "sound_spike_threshold": 350.0,  # Sound level HW-484-V0.2 (GPIO 27)
    "temp_max": 38.0,                # Ambient temp DHT11 (GPIO 15)
    "temp_min": 0.0,
    "humidity_max": 85.0,            # Humidity DHT11 (GPIO 15)
    "proximity_min_cm": 25.0         # HC-SR04 proximity warning distance
}

# Rover Command Code Mappings
COMMAND_MAP = {
    "STOP": 0,
    "FORWARD": 1,
    "BACKWARD": 2,
    "LEFT": 3,
    "RIGHT": 4
}

CODE_TO_COMMAND = {v: k for k, v in COMMAND_MAP.items()}

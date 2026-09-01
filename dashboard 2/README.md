# Field Safety Monitoring System - PyQt5 Desktop Station (Dashboard 2)

A desktop monitoring command center built in **PyQt5** for the Field Safety System.

---

## Key Features

1. **Dedicated Real-Time Graph for Every Single Sensor (9 Dedicated Charts)**:
   - **MQ5 Combustible Gas**: Analog levels with warning & danger (600 ADC) thresholds.
   - **MQ7 Carbon Monoxide**: Analog levels with warning & danger (450 ADC) thresholds.
   - **MQ135 Air Quality**: Toxic vapor levels with danger (550 ADC) threshold.
   - **HW-484 Acoustic Sound Peak**: Real-time sound amplitude with explosion spike threshold.
   - **DHT11 Temperature**: Live temperature in °C with safety ceiling.
   - **DHT11 Humidity**: Live relative humidity %.
   - **HW-416-B PIR Motion**: Digital timeline step graph (Clear / Motion Detected).
   - **Worker Pulse Rate**: Heart rate (BPM) with tachycardia & bradycardia lines.
   - **Worker Blood Oxygen (SpO2 %)**: Oxygen saturation with critical hypoxia alert line (90%).

2. **Real-Time Rover Camera Feed & Steering Console (Test.py Integrated Engine)**:
   - **Dual ESP32 Hardware Linking**:
     - **Dev ESP32 IP** (default: `192.168.137.9`) for direct motor driver HTTP teleoperation (`/forward`, `/backward`, `/stop`, `/left`, `/right`).
     - **CAM ESP32 IP** (default: `192.168.137.106`) with Port 81 MJPEG video stream.
   - **OpenCV & NumPy Powered Stream Decoding**: Robust frame extraction using SOI/EOI delimiters and zero-latency QImage rendering with error diagnostics.
   - **One-Click Video Toggle**: "Start Video" (teal `#00adb5`) and "Stop Video" (red `#ff4d4d`) toggle with `[ Video Stream Offline ]` standby view.
   - **Interactive Steering Controls**:
     - Prominent action buttons: **▲ FORWARD (W)** and **▼ BACKWARD (S)**.
     - **Virtual D-Pad** (Forward, Backward, Left, Right, Stop) and **WASD / Arrow Keys / Space** keyboard teleoperation with press & release bindings.
     - **Inverted Motor Wiring Toggle**: Seamlessly matches physical wiring polarities (Test.py default: W sends `backward`, S sends `forward`).
     - PWM Speed Slider (100–255).
     - Quick **⚡ Ping Dev** button to verify hardware responsiveness.

3. **Built-in Ingestion Server (Port 3000)**:
   - Automatically runs a background server on `0.0.0.0:3000` so real Checkpoint ESP32 boards can POST telemetry directly over Wi-Fi without needing any other web server.

4. **One-Click Built-in Simulator**:
   - Includes a toggle button `▶ Start Built-in Simulator` in the top toolbar to test and demo all 9 sensor graphs, hypoxia scenarios, and rover connection without hardware.

---

## How to Run

1. Open PowerShell or Command Prompt in the `dashboard 2` directory:
   ```bash
   cd "dashboard 2"
   ```

2. Launch the PyQt5 Dashboard:
   ```bash
   python main.py
   ```

3. Click **"▶ Start Built-in Simulator"** at the top right to start viewing live data across all 9 sensor graphs immediately, or power on your ESP32 Checkpoint on the Hotspot!

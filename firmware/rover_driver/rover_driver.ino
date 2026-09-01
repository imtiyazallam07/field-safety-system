#include <WiFi.h>
#include <WebServer.h>

// WiFi Credentials (Your Laptop Hotspot)
const char* ssid = "Kernel";
const char* password = "MSI_Thin_15.";

// ESP32 Network Device Name (Hostname)
const char* hostname = "ESP_Driver";

// Motor Control Pins
#define IN1 27
#define IN2 26
#define IN3 25
#define IN4 33

WebServer server(80);

// Motor Helper Functions
void stopMotors() {
  digitalWrite(IN1, LOW);
  digitalWrite(IN2, LOW);
  digitalWrite(IN3, LOW);
  digitalWrite(IN4, LOW);
}

void moveForward() {
  digitalWrite(IN1, HIGH);
  digitalWrite(IN2, LOW);
  digitalWrite(IN3, HIGH);
  digitalWrite(IN4, LOW);
}

void moveBackward() {
  digitalWrite(IN1, LOW);
  digitalWrite(IN2, HIGH);
  digitalWrite(IN3, LOW);
  digitalWrite(IN4, HIGH);
}

// HTML Dashboard Interface
const char index_html[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html>
<head>
  <title>ESP32 Rover Control</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body { font-family: Arial, sans-serif; text-align: center; margin: 0; padding: 10px; background: #222; color: #fff; }
    h1 { color: #00adb5; }
    .video-container { margin: 15px auto; max-width: 640px; background: #000; border: 2px solid #00adb5; }
    img { width: 100%; height: auto; display: block; }
    .btn-container { display: flex; flex-direction: column; align-items: center; gap: 15px; margin-top: 20px; }
    .btn { background-color: #00adb5; color: white; border: none; padding: 20px 40px; font-size: 20px; font-weight: bold; border-radius: 10px; width: 200px; touch-action: manipulation; cursor: pointer; }
    .btn:active { background-color: #393e46; }
    .ip-input { margin: 10px; padding: 8px; font-size: 16px; border-radius: 5px; border: none; text-align: center;}
  </style>
</head>
<body>
  <h1>Rover Control Center</h1>
  
  <div>
    <label>ESP32-CAM IP: </label>
    <input type="text" id="camIp" class="ip-input" placeholder="e.g. 192.168.137.50">
    <button onclick="updateStream()">Connect Stream</button>
  </div>

  <div class="video-container">
    <img id="stream" src="" alt="Video Feed Off">
  </div>

  <div class="btn-container">
    <button class="btn" onmousedown="sendCommand('forward')" onmouseup="sendCommand('stop')" ontouchstart="sendCommand('forward')" ontouchend="sendCommand('stop')">FORWARD</button>
    <button class="btn" onmousedown="sendCommand('backward')" onmouseup="sendCommand('stop')" ontouchstart="sendCommand('backward')" ontouchend="sendCommand('stop')">BACKWARD</button>
  </div>

  <script>
    function updateStream() {
      var ip = document.getElementById('camIp').value;
      if (ip) {
        document.getElementById('stream').src = "http://" + ip + ":81/stream";
      }
    }

    function sendCommand(cmd) {
      fetch("/" + cmd);
    }
  </script>
</body>
</html>
)rawliteral";

void setup() {
  Serial.begin(115200);

  // Initialize Motor Pins
  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);
  pinMode(IN3, OUTPUT);
  pinMode(IN4, OUTPUT);
  stopMotors();

  // Set ESP32 Device Name (Hostname) BEFORE connecting
  WiFi.setHostname(hostname);

  // Connect to Laptop Hotspot
  WiFi.begin(ssid, password);
  Serial.print("Connecting to WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWiFi Connected!");
  Serial.print("Device Name: ");
  Serial.println(hostname);
  Serial.print("Motor Controller IP Address: ");
  Serial.println(WiFi.localIP());

  // Web Server Routes
  server.on("/", HTTP_GET, []() {
    server.send(200, "text/html", index_html);
  });
  
  server.on("/forward", HTTP_GET, []() {
    moveForward();
    server.send(200, "text/plain", "OK");
  });
  
  server.on("/backward", HTTP_GET, []() {
    moveBackward();
    server.send(200, "text/plain", "OK");
  });
  
  server.on("/stop", HTTP_GET, []() {
    stopMotors();
    server.send(200, "text/plain", "OK");
  });

  server.begin();
}

void loop() {
  server.handleClient();
}
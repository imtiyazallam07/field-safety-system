/**
 * Field Safety Monitoring System - Checkpoint Firmware (ESP32 #4)
 * 
 * Soldered Pin Configuration (matches PIN_Config.txt):
 * - MQ5 Gas Sensor:        GPIO 33 (ADC1_CH5)
 * - MQ7 Gas Sensor:        GPIO 34 (ADC1_CH6)
 * - MQ135 Air Quality:     GPIO 35 (ADC1_CH7)
 * - HW-416-B (PIR):        GPIO 26 (Digital Input - returns true/false only)
 * - HW-484-V0.2 (Sound):   GPIO 27 (ADC2_CH7 / AO Analog Sound Peak)
 * - DHT11 (Temp/Humidity): GPIO 15 (Digital I/O)
 * 
 * Ultra-Low Latency Features:
 * - Dedicated UDP Instant Steering Socket (Port 4210): Sub-millisecond steering relay!
 * - Directly receives UDP commands from Station and calls esp_now_send() in < 1ms
 * - Background HTTP telemetry & ESP-NOW Mesh relaying
 *
 * FIX (see forwardOverMesh): a full CameraChunkPacket (216 bytes) is structurally
 * larger than MAX_PAYLOAD_SIZE (210 bytes) used for MeshRelayPacket's payload
 * buffer, so relaying camera chunks over mesh previously caused a guaranteed
 * stack-buffer overflow ("Stack smashing protect failure!"). forwardOverMesh now
 * bounds-checks len before copying, and camera chunks are no longer mesh-relayed
 * at all (see PKT_CAM_CHUNK handling in OnDataRecv) since they structurally can't
 * fit without redesigning the packet formats.
 */

#include <WiFi.h>
#include <WiFiUdp.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <HTTPClient.h>
#include <DHT.h>
#include "../common/mesh_protocol.h"

// ================= USER CONFIGURATION =================
#define CHECKPOINT_ID       1
#define CHECKPOINT_LOCATION "Zone-A_Shaft1"

// Wi-Fi Hotspot Settings (Station Dashboard)
const char* HOTSPOT_SSID     = "Kernel";
const char* HOTSPOT_PASSWORD = "MSI_Thin_15.";
const char* STATION_HOST     = "192.168.137.1";
const int   STATION_PORT     = 3000;

// Ultra-Low Latency UDP Steering Port
#define ROVER_UDP_PORT       4210
WiFiUDP udpRover;

// Neighbor Checkpoint MAC for multi-hop ESP-NOW when Wi-Fi is unreachable
uint8_t NEIGHBOR_CHECKPOINT_MAC[] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

// Rover Driver MAC (for relaying steering commands)
uint8_t ROVER_DRIVER_MAC[] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

// Broadcast MAC for ESP-NOW
uint8_t BROADCAST_MAC[] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

// ================= HARDWARE PIN CONFIGURATION =================
#define PIN_MQ5          35   // MQ5: Combustible / Methane Gas (Analog)
#define PIN_MQ7          34   // MQ7: Carbon Monoxide (Analog)
#define PIN_MQ135        33   // MQ135: Air Quality (Analog)
#define PIN_PIR_HW416B   32   // HW-416-B: PIR Motion Sensor (Digital true/false only)
#define PIN_SOUND_HW484  36   // HW-484-V0.2: Sound AO (Analog)
#define PIN_DHT11        15   // DHT11: Temperature & Humidity

#define DHTTYPE DHT11
DHT dht(PIN_DHT11, DHTTYPE);

#define TELEMETRY_INTERVAL_MS 2000

// ================= GLOBAL STATE =================
unsigned long lastTelemetryTime = 0;
bool isWifiConnected = false;
unsigned long lastWifiCheck = 0;
uint8_t lastChannel = 0;

// Relayed Band & Rover Cache
BandVitalsPacket lastRelayedBand;
bool hasRelayedBand = false;

RoverStatusPacket lastRelayedRover;
bool hasRelayedRover = false;
unsigned long lastRoverPacketTime = 0;
bool roverWasConnected = false;

// ================= SOUND SENSOR PEAK SAMPLING =================
uint16_t sampleSoundPeak(uint16_t sampleWindowMs = 50) {
    unsigned long startMillis = millis();
    uint16_t signalMax = 0;
    uint16_t signalMin = 4095;

    while (millis() - startMillis < sampleWindowMs) {
        uint16_t sample = analogRead(PIN_SOUND_HW484);
        if (sample > signalMax) signalMax = sample;
        if (sample < signalMin) signalMin = sample;
    }
    return (signalMax >= signalMin) ? (signalMax - signalMin) : 0;
}

// Forward payload over ESP-NOW mesh to next checkpoint
void forwardOverMesh(uint8_t origType, const uint8_t* payload, uint8_t len) {
    // CRITICAL BOUNDS CHECK: relayPkt.payload[] is MAX_PAYLOAD_SIZE (210) bytes on
    // the STACK. Without this check, any caller passing len > MAX_PAYLOAD_SIZE
    // (e.g. a full CameraChunkPacket, which is 216 bytes - 6 bytes BIGGER than
    // MAX_PAYLOAD_SIZE by construction, since it has its own MAX_PAYLOAD_SIZE-sized
    // data[] plus a 6-byte header on top) overflows the stack buffer and corrupts
    // the stack canary -> "Stack smashing protect failure!" and a reboot.
    if (len > MAX_PAYLOAD_SIZE) {
        Serial.printf("[MESH] Dropped relay: payload len=%d exceeds MAX_PAYLOAD_SIZE=%d (origType=0x%02X)\n",
                      len, MAX_PAYLOAD_SIZE, origType);
        return;
    }

    MeshRelayPacket relayPkt;
    memset(&relayPkt, 0, sizeof(MeshRelayPacket));
    relayPkt.packet_type = PKT_RELAY_WRAPPER;
    relayPkt.orig_type = origType;
    relayPkt.hop_count = 1;
    relayPkt.route_path[0] = CHECKPOINT_ID;
    relayPkt.payload_len = len;
    memcpy(relayPkt.payload, payload, len);

    esp_now_send(NEIGHBOR_CHECKPOINT_MAC, (uint8_t*)&relayPkt, sizeof(MeshRelayPacket));
}

// Forward camera chunks via HTTP POST to station
void postCameraChunk(CameraChunkPacket* chunk) {
    if (WiFi.status() != WL_CONNECTED) return;
    HTTPClient http;
    String url = "http://" + String(STATION_HOST) + ":" + String(STATION_PORT) + "/api/camera_chunk";
    http.begin(url);
    http.addHeader("Content-Type", "application/octet-stream");
    http.POST((uint8_t*)chunk, sizeof(CameraChunkPacket));
    http.end();
}

// Forward relayed payloads to station
void postRelayedPayload(MeshRelayPacket* relay) {
    if (WiFi.status() != WL_CONNECTED) return;

    if (relay->orig_type == PKT_BAND_VITALS && relay->payload_len >= sizeof(BandVitalsPacket)) {
        BandVitalsPacket* b = (BandVitalsPacket*)relay->payload;
        HTTPClient http;
        String url = "http://" + String(STATION_HOST) + ":" + String(STATION_PORT) + "/api/telemetry/band";
        http.begin(url);
        http.addHeader("Content-Type", "application/json");

        String json = "{";
        json += "\"band_id\":" + String(b->band_id) + ",";
        json += "\"worker_name\":\"" + String(b->worker_name) + "\",";
        json += "\"pulse_bpm\":" + String(b->pulse_bpm, 1) + ",";
        json += "\"spo2\":" + String(b->spo2, 1) + ",";
        json += "\"battery_pct\":" + String(b->battery_pct) + ",";
        json += "\"relayed_by\":" + String(CHECKPOINT_ID) + ",";
        json += "\"hops\":" + String(relay->hop_count);
        json += "}";

        http.POST(json);
        http.end();
    }
}

// Low-latency UDP Instant Steering Dispatch
void checkLowLatencyUdpRover() {
    int packetSize = udpRover.parsePacket();
    if (packetSize > 0) {
        RoverCommandPacket cmd;
        memset(&cmd, 0, sizeof(RoverCommandPacket));
        cmd.packet_type = PKT_ROVER_COMMAND;
        cmd.rover_id = 1;
        cmd.speed = 200;

        if (packetSize >= (int)sizeof(RoverCommandPacket)) {
            udpRover.read((char*)&cmd, sizeof(RoverCommandPacket));
        } else {
            uint8_t buf[8];
            int len = udpRover.read(buf, sizeof(buf));
            if (len >= 1) cmd.command = buf[0];
            if (len >= 2) cmd.speed   = buf[1];
            cmd.seq_id = millis();
        }

        // Send over ESP-NOW immediately in < 1ms!
        esp_now_send(ROVER_DRIVER_MAC, (uint8_t*)&cmd, sizeof(RoverCommandPacket));

        const char* cmdNames[] = {"STOP", "FORWARD", "BACKWARD", "LEFT", "RIGHT"};
        const char* name = (cmd.command <= 4) ? cmdNames[cmd.command] : "UNKNOWN";
        Serial.printf("[ULTRA-FAST UDP -> ESP-NOW] Cmd: %s (%d) | Speed: %d\n", name, cmd.command, cmd.speed);
    }
}

// Fallback HTTP response parser
void handleStationResponse(const String& response) {
    if (response.indexOf("\"rover_command\"") >= 0) {
        RoverCommandPacket cmd;
        memset(&cmd, 0, sizeof(RoverCommandPacket));
        cmd.packet_type = PKT_ROVER_COMMAND;
        cmd.rover_id = 1;
        cmd.command = CMD_STOP;
        cmd.speed = 200;
        cmd.seq_id = millis();

        int cmdIdx = response.indexOf("\"command\":");
        if (cmdIdx >= 0) {
            cmd.command = (uint8_t)response.substring(cmdIdx + 10).toInt();
        }

        int speedIdx = response.indexOf("\"speed\":");
        if (speedIdx >= 0) {
            cmd.speed = (uint8_t)response.substring(speedIdx + 8).toInt();
        }

        esp_now_send(ROVER_DRIVER_MAC, (uint8_t*)&cmd, sizeof(RoverCommandPacket));
    }
}

// ================= ESP-NOW RECEIVE CALLBACK =================
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 0, 0)
void OnDataRecv(const esp_now_recv_info *recv_info, const uint8_t *incomingData, int len) {
    const uint8_t *mac_addr = recv_info->src_addr;
#else
void OnDataRecv(const uint8_t *mac_addr, const uint8_t *incomingData, int len) {
#endif
    if (len < 1) return;

    uint8_t pkt_type = incomingData[0];

    // 1. Worker Band Vitals Packet
    if (pkt_type == PKT_BAND_VITALS) {
        memcpy(&lastRelayedBand, incomingData, min((size_t)len, sizeof(BandVitalsPacket)));
        hasRelayedBand = true;

        if (!isWifiConnected) {
            forwardOverMesh(PKT_BAND_VITALS, incomingData, (uint8_t)min(len, (int)MAX_PAYLOAD_SIZE));
        }
    }
    // 2. Rover Status Packet
    else if (pkt_type == PKT_ROVER_STATUS) {
        bool wasDisconnected = (!roverWasConnected) || (millis() - lastRoverPacketTime > 6000);
        memcpy(&lastRelayedRover, incomingData, min((size_t)len, sizeof(RoverStatusPacket)));
        hasRelayedRover = true;
        roverWasConnected = true;
        lastRoverPacketTime = millis();

        if (wasDisconnected) {
            Serial.println("\n**************************************************************");
            Serial.printf(">> [ROVER CONNECTED] Rover #%d LINKED to Checkpoint #%d (%s)!\n",
                          lastRelayedRover.rover_id, CHECKPOINT_ID, CHECKPOINT_LOCATION);
            Serial.println("**************************************************************\n");
        }

        if (!isWifiConnected) {
            forwardOverMesh(PKT_ROVER_STATUS, incomingData, (uint8_t)min(len, (int)MAX_PAYLOAD_SIZE));
        }
    }
    // 3. Rover Camera Chunk
    else if (pkt_type == PKT_CAM_CHUNK && len >= 6) {
        if (isWifiConnected) {
            postCameraChunk((CameraChunkPacket*)incomingData);
        } else {
            // NOTE: camera chunks are intentionally NOT mesh-relayed. A full
            // CameraChunkPacket (216 bytes) is structurally larger than
            // MAX_PAYLOAD_SIZE (210 bytes) - it can never fit inside a
            // MeshRelayPacket's payload without redesigning the packet formats
            // (e.g. splitting MAX_PAYLOAD_SIZE into a separate, larger constant
            // just for mesh relay). Attempting to relay it previously caused a
            // stack buffer overflow. Camera frames are dropped when Wi-Fi is down;
            // if you need them meshed, that requires a protocol change, not just
            // a bounds check.
            Serial.println("[MESH] Camera chunk dropped: Wi-Fi down and camera data cannot fit in mesh relay payload.");
        }
    }
    // 4. Mesh Relay Wrapper
    else if (pkt_type == PKT_RELAY_WRAPPER && len >= (int)sizeof(MeshRelayPacket)) {
        MeshRelayPacket relayPkt;
        memcpy(&relayPkt, incomingData, sizeof(MeshRelayPacket));

        if (isWifiConnected) {
            postRelayedPayload(&relayPkt);
        } else if (relayPkt.hop_count < MAX_HOPS) {
            relayPkt.route_path[relayPkt.hop_count] = CHECKPOINT_ID;
            relayPkt.hop_count++;
            esp_now_send(NEIGHBOR_CHECKPOINT_MAC, (uint8_t*)&relayPkt, sizeof(MeshRelayPacket));
        }
    }
    // 5. Rover Drive Command Relay
    else if (pkt_type == PKT_ROVER_COMMAND) {
        esp_now_send(ROVER_DRIVER_MAC, incomingData, sizeof(RoverCommandPacket));
    }
}

// ================= HTTP FORWARDING TO CENTRAL STATION =================
void postTelemetryToStation(CheckpointEnvPacket* env) {
    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("[HTTP] Skipped telemetry POST: WiFi.status() != WL_CONNECTED");
        return;
    }

    HTTPClient http;
    String url = "http://" + String(STATION_HOST) + ":" + String(STATION_PORT) + "/api/telemetry";

    if (!http.begin(url)) {
        Serial.printf("[HTTP] http.begin() FAILED for url=%s (malformed URL or client alloc failure)\n", url.c_str());
        return;
    }
    http.addHeader("Content-Type", "application/json");
    http.setTimeout(3000); // avoid hanging the loop indefinitely on a dead/unreachable station

    String json = "{";
    json += "\"checkpoint_id\":" + String(env->checkpoint_id) + ",";
    json += "\"location\":\"" + String(env->location) + "\",";
    json += "\"mq5\":" + String(env->mq5_combustible) + ",";
    json += "\"mq7\":" + String(env->mq7_co) + ",";
    json += "\"mq135\":" + String(env->mq135_air_quality) + ",";
    json += "\"pir_motion\":" + String(env->pir_motion ? "true" : "false") + ",";
    json += "\"sound_level\":" + String(env->sound_level) + ",";
    json += "\"temperature\":" + String(env->temperature, 1) + ",";
    json += "\"humidity\":" + String(env->humidity, 1) + ",";
    json += "\"uptime_ms\":" + String(env->timestamp_ms);

    if (hasRelayedBand) {
        json += ",\"band\":{";
        json += "\"band_id\":" + String(lastRelayedBand.band_id) + ",";
        json += "\"worker_name\":\"" + String(lastRelayedBand.worker_name) + "\",";
        json += "\"pulse_bpm\":" + String(lastRelayedBand.pulse_bpm, 1) + ",";
        json += "\"spo2\":" + String(lastRelayedBand.spo2, 1) + ",";
        json += "\"battery_pct\":" + String(lastRelayedBand.battery_pct);
        json += "}";
        hasRelayedBand = false;
    }

    if (hasRelayedRover) {
        json += ",\"rover\":{";
        json += "\"rover_id\":" + String(lastRelayedRover.rover_id) + ",";
        json += "\"current_command\":" + String(lastRelayedRover.current_command) + ",";
        json += "\"speed\":" + String(lastRelayedRover.speed) + ",";
        json += "\"battery_pct\":" + String(lastRelayedRover.battery_pct) + ",";
        json += "\"failsafe_triggered\":" + String(lastRelayedRover.failsafe_triggered ? "true" : "false") + ",";
        json += "\"connected_checkpoint_id\":" + String(CHECKPOINT_ID) + ",";
        json += "\"connected_checkpoint_location\":\"" + String(CHECKPOINT_LOCATION) + "\"";
        json += "}";
        hasRelayedRover = false;
    }

    json += "}";

    Serial.printf("[HTTP] POST %s | body=%s\n", url.c_str(), json.c_str());
    int httpCode = http.POST(json);

    if (httpCode > 0) {
        Serial.printf("[HTTP] Response code: %d\n", httpCode);
        if (httpCode == HTTP_CODE_OK || httpCode == HTTP_CODE_CREATED) {
            String response = http.getString();
            Serial.printf("[HTTP] Response body: %s\n", response.c_str());
            handleStationResponse(response);
        } else {
            // Non-2xx: station reached us, but rejected/errored on the request.
            // Print the body - it usually contains the actual reason (validation
            // error, wrong route, server exception, etc).
            String response = http.getString();
            Serial.printf("[HTTP] Non-success status from station. Body: %s\n", response.c_str());
        }
    } else {
        // httpCode <= 0 means the request never got a response at all - this is
        // the "connected to hotspot but nothing reaches the station" symptom.
        // Common causes: STATION_HOST doesn't match the PC's actual hotspot
        // gateway IP, the station server isn't running, a firewall on the PC is
        // blocking inbound connections on STATION_PORT, or the checkpoint is on
        // a different subnet than expected.
        Serial.printf("[HTTP] POST FAILED, no response. Error: %s (code %d)\n",
                      http.errorToString(httpCode).c_str(), httpCode);
        Serial.printf("[HTTP] Checkpoint IP=%s Gateway=%s | Target=%s:%d - verify these match!\n",
                      WiFi.localIP().toString().c_str(), WiFi.gatewayIP().toString().c_str(),
                      STATION_HOST, STATION_PORT);
    }

    http.end();
}

// ================= SETUP & INITIALIZATION =================
void setup() {
    Serial.begin(115200);
    delay(500);
    Serial.println("\n===========================================");
    Serial.println("Field Safety System - Checkpoint ESP32 #4");
    Serial.printf("Checkpoint ID: %d | Low-Latency UDP Steering Port: %d\n", CHECKPOINT_ID, ROVER_UDP_PORT);
    Serial.println("===========================================");

    pinMode(PIN_MQ5, INPUT);
    pinMode(PIN_MQ7, INPUT);
    pinMode(PIN_MQ135, INPUT);
    pinMode(PIN_PIR_HW416B, INPUT);
    pinMode(PIN_SOUND_HW484, INPUT);

    dht.begin();

    WiFi.mode(WIFI_AP_STA);
    WiFi.begin(HOTSPOT_SSID, HOTSPOT_PASSWORD);

    Serial.printf("[WIFI] Checkpoint MAC: %s\n", WiFi.macAddress().c_str());

    if (esp_now_init() != ESP_OK) {
        Serial.println("[ESP-NOW] Init Failed! Rebooting...");
        delay(2000);
        ESP.restart();
    }
    Serial.println("[ESP-NOW] Initialized successfully");

    #if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 0, 0)
    esp_now_register_recv_cb(OnDataRecv);
    #else
    esp_now_register_recv_cb(OnDataRecv);
    #endif

    // Register Broadcast Peer
    esp_now_peer_info_t peerInfo = {};
    peerInfo.channel = 0;
    peerInfo.encrypt = false;
    memcpy(peerInfo.peer_addr, BROADCAST_MAC, 6);
    esp_now_add_peer(&peerInfo);

    // Start Low-Latency UDP Steering Socket
    udpRover.begin(ROVER_UDP_PORT);
    Serial.printf("[UDP ROVER] Fast socket listening on UDP port %d\n", ROVER_UDP_PORT);
}

// ================= MAIN LOOP =================
void loop() {
    // Check for ultra-fast UDP steering command on every loop (< 0.1ms non-blocking)
    checkLowLatencyUdpRover();

    // Check Wi-Fi Hotspot status periodically
    if (millis() - lastWifiCheck > 3000) {
        lastWifiCheck = millis();
        uint8_t currentCh = WiFi.channel();

        if (WiFi.status() == WL_CONNECTED) {
            if (!isWifiConnected || currentCh != lastChannel) {
                isWifiConnected = true;
                lastChannel = currentCh;
                Serial.printf("[WIFI HOTSPOT CONNECTED] IP: %s | Gateway: %s | Active Wi-Fi Channel: %d\n",
                              WiFi.localIP().toString().c_str(), WiFi.gatewayIP().toString().c_str(), currentCh);
                if (WiFi.gatewayIP().toString() != String(STATION_HOST)) {
                    Serial.printf("[WIFI] WARNING: Gateway (%s) does not match hardcoded STATION_HOST (%s). "
                                  "HTTP POSTs will likely fail unless the station really is reachable at STATION_HOST.\n",
                                  WiFi.gatewayIP().toString().c_str(), STATION_HOST);
                }
            }
        } else {
            if (isWifiConnected) {
                isWifiConnected = false;
                Serial.printf("[WIFI DISCONNECTED] Operating in ESP-NOW Mesh mode on Channel: %d\n", currentCh);
            }
        }
    }

    // Telemetry Collection & Forwarding Loop
    if (millis() - lastTelemetryTime >= TELEMETRY_INTERVAL_MS) {
        lastTelemetryTime = millis();

        uint16_t raw_mq5   = analogRead(PIN_MQ5);
        uint16_t raw_mq7   = analogRead(PIN_MQ7);
        uint16_t raw_mq135 = analogRead(PIN_MQ135);
        bool pir_motion    = (digitalRead(PIN_PIR_HW416B) == HIGH);
        uint16_t sound_level = sampleSoundPeak(40);

        float temp = dht.readTemperature();
        float hum  = dht.readHumidity();
        if (isnan(temp)) temp = 25.0f;
        if (isnan(hum))  hum  = 50.0f;

        CheckpointEnvPacket env;
        memset(&env, 0, sizeof(CheckpointEnvPacket));
        env.packet_type        = PKT_CHECKPOINT_ENV;
        env.checkpoint_id      = CHECKPOINT_ID;
        strncpy(env.location, CHECKPOINT_LOCATION, sizeof(env.location) - 1);
        env.mq5_combustible    = raw_mq5;
        env.mq7_co             = raw_mq7;
        env.mq135_air_quality  = raw_mq135;
        env.pir_motion         = pir_motion;
        env.sound_level        = sound_level;
        env.temperature        = temp;
        env.humidity           = hum;
        env.timestamp_ms       = millis();

        if (isWifiConnected) {
            postTelemetryToStation(&env);
        } else {
            forwardOverMesh(PKT_CHECKPOINT_ENV, (uint8_t*)&env, (uint8_t)min((int)sizeof(CheckpointEnvPacket), (int)MAX_PAYLOAD_SIZE));
        }
    }
}

/**
 * Field Safety Monitoring System - Monitoring Band Firmware (ESP32 #1)
 *
 * Hardware:
 * - ESP32 DevKit / Wearable module
 * - MAX30102 / MAX30100 Pulse Oximeter & Heart-Rate Sensor (I2C: SDA=21, SCL=22, Addr=0x57)
 * - 0.96" I2C OLED Display SSD1306 128x64 (I2C: SDA=21, SCL=22, Addr=0x3C)
 *
 * Required Libraries (Arduino Library Manager):
 * - "SparkFun MAX3010x Pulse and Proximity Sensor Library" (MAX30105.h, heartRate.h)
 * - "Adafruit SSD1306" & "Adafruit GFX Library"
 *
 * Wrist Band Optimizations:
 * - Higher LED pulse amplitude (0x3F / ~12.5mA) for penetrating thicker wrist tissue
 * - Calibrated wrist contact threshold (18000 IR)
 * - Dicrotic notch refractory period (380ms) to prevent 180+ BPM double-counting
 * - Calibrated SpO2 formula multiplied by 1.066 factor
 * - Auto-Channel Sweeping (Channels 1-13) for instant link with Checkpoint
 * - OLED live biometrics HUD & hazard alert banners
 * - Fully compatible with ESP32 Core 2.x and Core 3.x+ (ESP-IDF 5.x)
 */

#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <MAX30105.h>
#include <heartRate.h>
#include "../common/mesh_protocol.h"

// ================= USER CONFIGURATION =================
#define BAND_ID          1
#define WORKER_NAME      "Worker-101 (Alex)"

// Sensor Placement Profile: 1 = Wrist Band Mode, 0 = Fingertip Mode
#define USE_WRIST_BAND_PROFILE   1

// Wi-Fi Channel Configuration (0 = Auto-sweep across channels 1..13)
#define TARGET_WIFI_CHANNEL  0

// Checkpoint MAC Address (Broadcast FF:FF:FF:FF:FF:FF)
uint8_t NEAREST_CHECKPOINT_MAC[] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

#define VITALS_SEND_INTERVAL_MS  1500

// SpO2 Calibration Multiplier
#define SPO2_CALIBRATION_FACTOR  1.066f

// ================= OLED DISPLAY CONFIGURATION =================
#define SCREEN_WIDTH     128
#define SCREEN_HEIGHT    64
#define OLED_RESET       -1
#define OLED_I2C_ADDR    0x3C
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);
bool oledFound = false;

// ================= MAX30102 SENSOR CONFIGURATION =================
MAX30105 particleSensor;
bool sensorFound = false;

// Contact Thresholds:
#if USE_WRIST_BAND_PROFILE
  #define CONTACT_IR_THRESHOLD   18000UL  // Wrist contact threshold
  #define LED_PULSE_AMPLITUDE    0x3F     // ~12.5mA for wrist penetration
#else
  #define CONTACT_IR_THRESHOLD   45000UL  // Fingertip contact threshold
  #define LED_PULSE_AMPLITUDE    0x24     // ~7.2mA
#endif

// Heart Rate Smoothing & Refractory Period
#define RATE_SIZE 6 // Rolling average of last 6 valid cardiac cycles
uint8_t rates[RATE_SIZE] = {75, 75, 75, 75, 75, 75};
uint8_t rateSpot = 0;
uint8_t rateCount = 0;
unsigned long lastBeatTime = 0;

// Plausible Physiological Boundaries
#define MIN_BPM_PLAUSIBLE   45.0f
#define MAX_BPM_PLAUSIBLE   160.0f
#define MIN_REFRACTORY_MS   380UL  // Rejects dicrotic notch (prevents 180+ BPM doubling)

// SpO2 Calculation Buffers
#define SPO2_BUFFER_SIZE 40
uint32_t irBuffer[SPO2_BUFFER_SIZE];
uint32_t redBuffer[SPO2_BUFFER_SIZE];
uint8_t spo2Index = 0;

// ================= GLOBAL STATE =================
float   current_pulse = -1.0f;
float   current_spo2  = -1.0f;
bool    wristContactDetected = false;
uint8_t battery_pct = 95;
unsigned long lastSendTime = 0;
bool lastTxOk = false;
uint8_t currentChannel = 1;

// ================= MAX30102 INITIALIZATION =================
void initMax30102() {
    if (!particleSensor.begin(Wire, I2C_SPEED_FAST)) {
        Serial.println("[MAX30102] Sensor not found at 0x57. Check wiring (SDA=21, SCL=22, VIN=3.3V).");
        sensorFound = false;
        return;
    }

    byte ledBrightness = LED_PULSE_AMPLITUDE;
    byte sampleAverage = 4;
    byte ledMode       = 2;    // Red + IR
    int  sampleRate    = 200;  // 200 Hz
    int  pulseWidth    = 411;  // 411 us (18-bit)
    int  adcRange      = 4096; // 12-bit range

    particleSensor.setup(ledBrightness, sampleAverage, ledMode, sampleRate, pulseWidth, adcRange);
    particleSensor.setPulseAmplitudeRed(LED_PULSE_AMPLITUDE);
    particleSensor.setPulseAmplitudeIR(LED_PULSE_AMPLITUDE);
    particleSensor.setPulseAmplitudeGreen(0);

    sensorFound = true;
    Serial.printf("[MAX30102] Wrist Band profile active (LED Amplitude: 0x%02X | Contact Thresh: %lu | SpO2 Scale: %.3f)\n",
                  LED_PULSE_AMPLITUDE, CONTACT_IR_THRESHOLD, SPO2_CALIBRATION_FACTOR);
}

// ================= SAMPLE & CALCULATE VITALS =================
void sampleMax30102() {
    if (!sensorFound) return;

    uint32_t irValue  = particleSensor.getIR();
    uint32_t redValue = particleSensor.getRed();

    // 1. Detect Skin Contact on Wrist
    if (irValue < CONTACT_IR_THRESHOLD) {
        if (wristContactDetected) {
            Serial.println("[MAX30102] Wrist contact lost (strap loose / removed).");
        }
        wristContactDetected = false;
        current_pulse = -1.0f;
        current_spo2  = -1.0f;
        rateCount = 0;
        return;
    }

    if (!wristContactDetected) {
        wristContactDetected = true;
        lastBeatTime = millis();
        rateCount = 0;
        Serial.printf("[MAX30102] Wrist contact detected! Raw IR: %lu\n", irValue);
    }

    // 2. Heart Beat Detection with Dicrotic Notch Rejection
    if (checkForBeat(irValue)) {
        unsigned long now = millis();
        unsigned long delta = now - lastBeatTime;
        lastBeatTime = now;

        if (delta >= MIN_REFRACTORY_MS && delta <= 1500) {
            float rawBpm = 60.0f / (delta / 1000.0f);

            if (rawBpm >= MIN_BPM_PLAUSIBLE && rawBpm <= MAX_BPM_PLAUSIBLE) {
                rates[rateSpot++] = (uint8_t)rawBpm;
                rateSpot %= RATE_SIZE;
                if (rateCount < RATE_SIZE) rateCount++;

                uint16_t sum = 0;
                for (uint8_t i = 0; i < rateCount; i++) {
                    sum += rates[i];
                }
                current_pulse = (float)sum / rateCount;

                Serial.printf("[WRIST BEAT] BPM: %.1f | Avg Pulse: %.1f BPM (Interval: %lu ms)\n",
                              rawBpm, current_pulse, delta);
            }
        }
    }

    // 3. SpO2 Estimation with 1.066 Calibration Multiplier
    irBuffer[spo2Index]  = irValue;
    redBuffer[spo2Index] = redValue;
    spo2Index = (spo2Index + 1) % SPO2_BUFFER_SIZE;

    if (spo2Index == 0) {
        uint32_t irMin = 0xFFFFFFFF, irMax = 0, irSum = 0;
        uint32_t redMin = 0xFFFFFFFF, redMax = 0, redSum = 0;

        for (uint8_t i = 0; i < SPO2_BUFFER_SIZE; i++) {
            irSum += irBuffer[i];
            if (irBuffer[i] < irMin) irMin = irBuffer[i];
            if (irBuffer[i] > irMax) irMax = irBuffer[i];

            redSum += redBuffer[i];
            if (redBuffer[i] < redMin) redMin = redBuffer[i];
            if (redBuffer[i] > redMax) redMax = redBuffer[i];
        }

        float irDC  = (float)irSum / SPO2_BUFFER_SIZE;
        float redDC = (float)redSum / SPO2_BUFFER_SIZE;
        float irAC  = (float)(irMax - irMin);
        float redAC = (float)(redMax - redMin);

        if (irDC > 0 && redDC > 0 && irAC > 8) {
            float ratio = (redAC / redDC) / (irAC / irDC);
            
            // Apply 1.066 calibration factor to SpO2 value
            float calculatedSpo2 = (104.0f - (17.0f * ratio)) * SPO2_CALIBRATION_FACTOR;

            // Clamp within physiological range
            if (calculatedSpo2 > 100.0f) calculatedSpo2 = 99.5f;
            if (calculatedSpo2 < 75.0f)  calculatedSpo2 = 75.0f;

            if (current_spo2 < 0) {
                current_spo2 = calculatedSpo2;
            } else {
                current_spo2 = (current_spo2 * 0.75f) + (calculatedSpo2 * 0.25f);
            }

            Serial.printf("[WRIST SPO2 (x1.066)] Ratio: %.3f | Calibrated SpO2: %.1f%%\n", ratio, current_spo2);
        }
    }
}

// ================= OLED DISPLAY RENDERING =================
void initOledDisplay() {
    Wire.begin(21, 22);
    if (display.begin(SSD1306_SWITCHCAPVCC, OLED_I2C_ADDR)) {
        oledFound = true;
        display.clearDisplay();
        display.setTextColor(SSD1306_WHITE);
        display.setTextSize(1);
        display.setCursor(10, 15);
        display.println("FIELD SAFETY BAND");
        display.setCursor(10, 30);
        display.printf("ID: #%d (WRIST)\n", BAND_ID);
        display.setCursor(10, 45);
        display.println("Connecting Mesh...");
        display.display();
        delay(800);
        Serial.println("[OLED] Display initialized successfully.");
    } else {
        Serial.println("[OLED] SSD1306 not detected at 0x3C (Check SDA=21, SCL=22).");
    }
}

void updateOledVitals(float pulse, float spo2, uint8_t batt, bool txSuccess, uint8_t ch) {
    if (!oledFound) return;

    display.clearDisplay();

    // 1. Header Bar
    display.setTextSize(1);
    display.setTextColor(SSD1306_WHITE);
    display.setCursor(0, 0);
    display.printf("ID #%d: Alex", BAND_ID);
    display.setCursor(72, 0);
    display.printf("Ch%d [%d%%]", ch, batt);
    display.drawLine(0, 9, 127, 9, SSD1306_WHITE);

    // 2. Biometrics
    display.setTextSize(1);
    display.setCursor(0, 14);
    display.print("PULSE:");
    display.setTextSize(2);
    display.setCursor(0, 24);
    if (wristContactDetected && pulse >= 40.0f) {
        display.printf("%3.0f", pulse);
    } else {
        display.print(" --");
    }
    display.setTextSize(1);
    display.print(" BPM");

    display.setTextSize(1);
    display.setCursor(68, 14);
    display.print("SpO2:");
    display.setTextSize(2);
    display.setCursor(68, 24);
    if (wristContactDetected && spo2 >= 70.0f) {
        display.printf("%4.1f", spo2);
    } else {
        display.print(" ---");
    }
    display.setTextSize(1);
    display.print("%");

    display.drawLine(0, 44, 127, 44, SSD1306_WHITE);

    // 3. Status Banner
    if (!sensorFound) {
        display.fillRect(0, 47, 128, 17, SSD1306_WHITE);
        display.setTextColor(SSD1306_BLACK, SSD1306_WHITE);
        display.setTextSize(1);
        display.setCursor(2, 52);
        display.print("! SENSOR ERROR !");
    } else if (!wristContactDetected) {
        display.setTextColor(SSD1306_WHITE);
        display.setTextSize(1);
        display.setCursor(6, 52);
        display.print("PLACE BAND ON WRIST");
    } else if (spo2 > 0 && spo2 < 90.0f) {
        display.fillRect(0, 47, 128, 17, SSD1306_WHITE);
        display.setTextColor(SSD1306_BLACK, SSD1306_WHITE);
        display.setTextSize(1);
        display.setCursor(8, 52);
        display.print("! HYPOXIA ALERT !");
    } else if (pulse > 125.0f) {
        display.fillRect(0, 47, 128, 17, SSD1306_WHITE);
        display.setTextColor(SSD1306_BLACK, SSD1306_WHITE);
        display.setTextSize(1);
        display.setCursor(14, 52);
        display.print("! HIGH PULSE !");
    } else {
        display.setTextColor(SSD1306_WHITE);
        display.setTextSize(1);
        display.setCursor(0, 52);
        display.print("WRIST: OK");
        display.setCursor(70, 52);
        display.print(txSuccess ? "ESP-NOW: OK" : "SCAN CH...");
    }

    display.display();
}

// ================= ESP-NOW SEND CALLBACK =================
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 0, 0)
void OnDataSent(const wifi_tx_info_t *tx_info, esp_now_send_status_t status) {
    lastTxOk = (status == ESP_NOW_SEND_SUCCESS);
}
#else
void OnDataSent(const uint8_t *mac_addr, esp_now_send_status_t status) {
    lastTxOk = (status == ESP_NOW_SEND_SUCCESS);
}
#endif

// Helper to set Wi-Fi Channel
void setBandWifiChannel(uint8_t channel) {
    esp_wifi_set_promiscuous(true);
    esp_wifi_set_channel(channel, WIFI_SECOND_CHAN_NONE);
    esp_wifi_set_promiscuous(false);
}

// ================= SETUP =================
void setup() {
    Serial.begin(115200);
    delay(500);
    Serial.println("\n===========================================");
    Serial.println("Field Safety System - Monitoring Band ESP32 #1 (Wrist Mode)");
    Serial.printf("Band ID: %d | Worker: %s\n", BAND_ID, WORKER_NAME);
    Serial.println("===========================================");

    initOledDisplay();
    initMax30102();

    WiFi.mode(WIFI_STA);
    WiFi.disconnect();

    Serial.printf("[WIFI] Band MAC Address: %s\n", WiFi.macAddress().c_str());

    if (esp_now_init() != ESP_OK) {
        Serial.println("[ESP-NOW] Init Failed! Rebooting in 2s...");
        delay(2000);
        ESP.restart();
    }
    Serial.println("[ESP-NOW] Initialized successfully");

    esp_now_register_send_cb(OnDataSent);

    // Register Broadcast Peer
    esp_now_peer_info_t peerInfo = {};
    memcpy(peerInfo.peer_addr, NEAREST_CHECKPOINT_MAC, 6);
    peerInfo.channel = 0;
    peerInfo.encrypt = false;

    if (esp_now_add_peer(&peerInfo) != ESP_OK) {
        Serial.println("[ESP-NOW] Failed to add broadcast peer!");
    } else {
        Serial.println("[ESP-NOW] Broadcast Peer (FF:FF:FF:FF:FF:FF) registered");
    }
}

// ================= MAIN LOOP =================
void loop() {
    sampleMax30102();

    if (millis() - lastSendTime >= VITALS_SEND_INTERVAL_MS) {
        lastSendTime = millis();

        float pulse = current_pulse;
        float spo2  = current_spo2;

        BandVitalsPacket packet;
        memset(&packet, 0, sizeof(BandVitalsPacket));
        packet.packet_type  = PKT_BAND_VITALS;
        packet.band_id      = BAND_ID;
        strncpy(packet.worker_name, WORKER_NAME, sizeof(packet.worker_name) - 1);
        packet.pulse_bpm    = (wristContactDetected && pulse >= 40.0f) ? pulse : -1.0f;
        packet.spo2         = (wristContactDetected && spo2  >= 70.0f) ? spo2  : -1.0f;
        packet.battery_pct  = battery_pct;
        packet.timestamp_ms = millis();

        if (TARGET_WIFI_CHANNEL > 0) {
            currentChannel = TARGET_WIFI_CHANNEL;
            setBandWifiChannel(currentChannel);
            esp_err_t res = esp_now_send(NEAREST_CHECKPOINT_MAC, (uint8_t*)&packet, sizeof(BandVitalsPacket));
            lastTxOk = (res == ESP_OK);
            Serial.printf("[VITALS] Sent on Fixed Ch %d | Res=%d | Pulse=%.1f | SpO2=%.1f%% | Contact=%d\n",
                          currentChannel, res, pulse, spo2, wristContactDetected);
        } else {
            static uint8_t commonChannels[] = {1, 6, 11, 2, 3, 4, 5, 7, 8, 9, 10, 12, 13};
            static uint8_t chIndex = 0;

            uint8_t primaryChannels[] = {1, 6, 11};
            for (uint8_t i = 0; i < 3; i++) {
                setBandWifiChannel(primaryChannels[i]);
                esp_err_t res = esp_now_send(NEAREST_CHECKPOINT_MAC, (uint8_t*)&packet, sizeof(BandVitalsPacket));
                if (res == ESP_OK) lastTxOk = true;
                delay(1);
            }

            currentChannel = commonChannels[chIndex];
            chIndex = (chIndex + 1) % (sizeof(commonChannels));
            setBandWifiChannel(currentChannel);
            esp_err_t res = esp_now_send(NEAREST_CHECKPOINT_MAC, (uint8_t*)&packet, sizeof(BandVitalsPacket));
            if (res == ESP_OK) lastTxOk = true;

            Serial.printf("[VITALS Multi-Ch TX] Pulse=%.1f BPM | SpO2=%.1f%% | Contact=%d | Active Ch: %d (Res: %d)\n",
                          pulse, spo2, wristContactDetected, currentChannel, res);
        }

        updateOledVitals(pulse, spo2, battery_pct, lastTxOk, currentChannel);
    }
}

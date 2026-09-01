#ifndef MESH_PROTOCOL_H
#define MESH_PROTOCOL_H

#include <stdint.h>
#include <stdbool.h>

#define MAX_PAYLOAD_SIZE 210
#define MAX_HOPS 5
#define MAX_WORKER_NAME_LEN 16
#define MAX_LOCATION_NAME_LEN 16

// Packet Types
enum PacketType : uint8_t {
    PKT_BAND_VITALS      = 0x01,
    PKT_CHECKPOINT_ENV   = 0x02,
    PKT_ROVER_STATUS     = 0x03,
    PKT_ROVER_COMMAND    = 0x04,
    PKT_CAM_CHUNK        = 0x05,
    PKT_RELAY_WRAPPER    = 0x06
};

// Rover Commands
enum RoverCommandType : uint8_t {
    CMD_STOP             = 0,
    CMD_FORWARD          = 1,
    CMD_BACKWARD         = 2,
    CMD_LEFT             = 3,
    CMD_RIGHT            = 4
};

// 1. Band Vitals Packet (ESP32 #1)
struct __attribute__((packed)) BandVitalsPacket {
    uint8_t  packet_type;        // PKT_BAND_VITALS
    uint8_t  band_id;            // e.g. 1, 2, 3
    char     worker_name[MAX_WORKER_NAME_LEN]; // "Worker-101"
    float    pulse_bpm;          // Heart rate (BPM)
    float    spo2;               // Blood Oxygen (%)
    uint8_t  battery_pct;        // 0 - 100%
    uint32_t timestamp_ms;       // Device uptime in ms
};

// 2. Checkpoint Environmental Packet (ESP32 #4)
// Soldered Configuration (matches PIN_Config.txt):
// MQ5: GPIO 33, MQ7: GPIO 34, MQ135: GPIO 35, HW-416-B (PIR): GPIO 26 (true/false), HW-484-V0.2 (Sound): GPIO 27, DHT11: GPIO 15
struct __attribute__((packed)) CheckpointEnvPacket {
    uint8_t  packet_type;        // PKT_CHECKPOINT_ENV
    uint8_t  checkpoint_id;      // e.g. 1, 2, 3
    char     location[MAX_LOCATION_NAME_LEN]; // "Zone-A_Shaft1"
    uint16_t mq5_combustible;    // Raw ADC / ppm (GPIO 33)
    uint16_t mq7_co;             // Raw ADC / ppm (GPIO 34)
    uint16_t mq135_air_quality;  // Raw ADC / ppm (GPIO 35)
    bool     pir_motion;         // Motion detected: true/false (HW-416-B, GPIO 26)
    uint16_t sound_level;        // Sound peak / raw ADC (HW-484-V0.2, GPIO 27)
    float    temperature;        // Temperature in Celsius (DHT11, GPIO 15)
    float    humidity;           // Humidity in % (DHT11, GPIO 15)
    uint32_t timestamp_ms;       // Checkpoint uptime in ms
};

// 3. Rover Status Packet (ESP32 #3)
struct __attribute__((packed)) RoverStatusPacket {
    uint8_t  packet_type;        // PKT_ROVER_STATUS
    uint8_t  rover_id;           // e.g. 1
    uint8_t  current_command;    // CMD_STOP, CMD_FORWARD, etc.
    uint8_t  speed;              // 0 - 255 (PWM)
    uint8_t  battery_pct;        // 0 - 100%
    bool     failsafe_triggered; // True if stopped due to signal timeout
    uint32_t timestamp_ms;
};

// 4. Rover Steering Command Packet (Dashboard -> Checkpoint -> Rover Driver)
struct __attribute__((packed)) RoverCommandPacket {
    uint8_t  packet_type;        // PKT_ROVER_COMMAND
    uint8_t  rover_id;           // Target Rover ID
    uint8_t  command;            // CMD_FORWARD, CMD_LEFT, etc.
    uint8_t  speed;              // Speed (0-255)
    uint32_t seq_id;             // Sequential command ID for idempotency
};

// 5. Camera Chunk Packet (ESP32 #2 -> Checkpoint -> Station)
struct __attribute__((packed)) CameraChunkPacket {
    uint8_t  packet_type;        // PKT_CAM_CHUNK
    uint16_t frame_id;           // Frame sequential ID
    uint8_t  chunk_index;        // Current chunk (0-indexed)
    uint8_t  total_chunks;       // Total chunks in this frame
    uint8_t  chunk_len;          // Number of valid bytes in data[]
    uint8_t  data[MAX_PAYLOAD_SIZE]; // JPEG chunk bytes
};

// 6. Multi-Hop Mesh Relay Wrapper
struct __attribute__((packed)) MeshRelayPacket {
    uint8_t  packet_type;        // PKT_RELAY_WRAPPER
    uint8_t  orig_type;          // Original packet type (PKT_BAND_VITALS, etc.)
    uint8_t  hop_count;          // Incremented at each relay step
    uint8_t  route_path[MAX_HOPS]; // Checkpoint IDs that relayed this packet
    uint8_t  payload_len;        // Size of payload
    uint8_t  payload[MAX_PAYLOAD_SIZE]; // Serialized inner packet
};

#endif // MESH_PROTOCOL_H

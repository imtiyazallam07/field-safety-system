#include "esp_camera.h"
#include <WiFi.h>
#include "esp_http_server.h"
#include "img_converters.h" // Enables Software JPEG Encoding for GC2145

// WiFi Credentials
const char* ssid = "Kernel";
const char* password = "MSI_Thin_15.";

// Device Hostname
const char* hostname = "ESP_Cam";

// AI-THINKER / GC2145 Pin Definitions
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27
#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

httpd_handle_t stream_httpd = NULL;

static esp_err_t stream_handler(httpd_req_t *req) {
  camera_fb_t * fb = NULL;
  esp_err_t res = ESP_OK;
  char part_buf[64];

  res = httpd_resp_set_type(req, "multipart/x-mixed-replace; boundary=123456789000000000000987654321");
  if (res != ESP_OK) return res;

  while (true) {
    fb = esp_camera_fb_get();
    if (!fb) {
      Serial.println("Camera capture failed");
      res = ESP_FAIL;
    } else {
      uint8_t * jpg_buf = NULL;
      size_t jpg_len = 0;

      // Software JPEG Encoding for GC2145
      bool jpeg_converted = fmt2jpg(fb->buf, fb->len, fb->width, fb->height, fb->format, 15, &jpg_buf, &jpg_len);
      esp_camera_fb_return(fb);

      if (jpeg_converted) {
        size_t hlen = snprintf((char *)part_buf, 64, "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n", jpg_len);
        res = httpd_resp_send_chunk(req, (const char *)part_buf, hlen);
        if (res == ESP_OK) {
          res = httpd_resp_send_chunk(req, (const char *)jpg_buf, jpg_len);
        }
        if (res == ESP_OK) {
          res = httpd_resp_send_chunk(req, "\r\n--123456789000000000000987654321\r\n", 37);
        }
        free(jpg_buf); // Free software JPEG buffer memory
      } else {
        Serial.println("Software JPEG conversion failed");
      }
      if (res != ESP_OK) break;
    }
  }
  return res;
}

void startCameraServer() {
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.server_port = 81;

  httpd_uri_t stream_uri = {
    .uri       = "/stream",
    .method    = HTTP_GET,
    .handler   = stream_handler,
    .user_ctx  = NULL
  };

  if (httpd_start(&stream_httpd, &config) == ESP_OK) {
    httpd_register_uri_handler(stream_httpd, &stream_uri);
  }
}

void setup() {
  Serial.begin(115200);

  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  
  // Set Raw Pixel Format for GC2145
  config.pixel_format = PIXFORMAT_RGB565; 
  config.frame_size = FRAMESIZE_QVGA; // 320x240 for smooth software encoding
  config.fb_count = 1;

  // Init Camera
  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed: 0x%x\n", err);
    return;
  }

  // 1. Static IP Configuration for Windows Hotspot (192.168.137.x subnet)
  // Bypasses buggy Windows Hotspot DHCP delays and connects instantly!
  IPAddress local_IP(192, 168, 137, 106);
  IPAddress gateway(192, 168, 137, 1);
  IPAddress subnet(255, 255, 255, 0);
  IPAddress primaryDNS(192, 168, 137, 1);

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);

  if (!WiFi.config(local_IP, gateway, subnet, primaryDNS)) {
    Serial.println("Static IP configuration failed, falling back to DHCP.");
  }

  // Set Hostname & Connect Wi-Fi
  WiFi.setHostname(hostname);

  Serial.print("Connecting to WiFi (SSID: ");
  Serial.print(ssid);
  Serial.println(")...");

  WiFi.begin(ssid, password);

  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED) {
    delay(300);
    Serial.print(".");
    attempts++;

    // Print status after 15 seconds if still connecting
    if (attempts % 50 == 0) {
      Serial.printf("\n[Status: %d] Re-triggering connection...\n", WiFi.status());
      WiFi.begin(ssid, password);
    }
  }

  Serial.println("\n\n====================================");
  Serial.println("  WiFi Connected Successfully!");
  Serial.print("  Device Name: ");
  Serial.println(hostname);
  Serial.print("  ESP32-CAM Stream IP: ");
  Serial.println(WiFi.localIP());
  Serial.println("====================================\n");

  startCameraServer();
}

void loop() {
  delay(10000);
}
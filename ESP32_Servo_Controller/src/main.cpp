/*
  esp32_dualcore_servo_dir.ino
  - Core0: WebSocket client + command parsing + ACK/STATUS sending
  - Core1: Motion task (absolute + directional modes)
  - Supports:
      * MOVE      -> absolute target
      * CANCEL    -> cancel specific command
      * STATUS_REQ-> immediate status
      * MOVE_DIR  -> continuous directional movement
      * STOP      -> stop directional movement
  Libraries required:
  - WebSocketsClient
  - ArduinoJson (v6)
  - ESP32Servo
*/

#include <WiFi.h>
#include <WebSocketsClient.h>
#include <ArduinoJson.h>
#include <ESP32Servo.h>
#include <deque>

// ---------- CONFIG ----------
const char* WIFI_SSID = "Control_and_Command";
const char* WIFI_PASS = "12345678";

const char* WS_HOST = "192.168.137.1"; // laptop hotspot IP
const uint16_t WS_PORT = 8080;
const char* WS_PATH = "/";

const int SERVO_PAN_PIN = 18;
const int SERVO_TILT_PIN = 19;

const int PAN_MIN = 0;
const int PAN_MAX = 180;
const int TILT_MIN = 0;
const int TILT_MAX = 180;
const int TILT_MIN_SAFE = 45;  // NEW: minimum tilt for safety

const int STEP_INTERVAL_MS = 15;
const int STEP_SIZE = 1;
const unsigned long COMMAND_TIMEOUT_MS = 4000UL;
// --------------------------------

WebSocketsClient webSocket;
Servo servoPan, servoTilt;

// Command struct (for absolute MOVE queue)
struct Cmd {
  String id;
  int pan;
  int tilt;
};

// command queue
std::deque<Cmd> cmdQueue;

// Active command state
volatile bool hasActive = false;
String activeCmdId = "";
volatile uint8_t activeMode = 0; // 0 = NONE, 1 = ABSOLUTE, 2 = DIRECTIONAL

volatile int currentPan = 90;
volatile int currentTilt = 90;
volatile float targetPan = 90.0f;
volatile float targetTilt = 90.0f;
volatile bool cancelFlag = false;
volatile bool preemptFlag = false;
volatile unsigned long cmdStartMillis = 0;

// Directional movement variables
volatile int8_t panDir = 0;    // -1=LEFT, 0=NONE, +1=RIGHT
volatile int8_t tiltDir = 0;   // -1=DOWN, 0=NONE, +1=UP
volatile uint8_t moveSpeed = 1; // degrees per step

// forward declarations
void sendJSON(const JsonDocument &doc);
void sendAck(const String &id);
void sendStatus(const String &id, const char* state, const char* error = nullptr);

// ---------- WebSocket callbacks on core0 ----------
void webSocketEvent(WStype_t type, uint8_t * payload, size_t length) {
  if (type == WStype_CONNECTED) {
    Serial.println("[WS] connected");
    StaticJsonDocument<128> doc;
    doc["type"] = "HELLO";
    doc["node"] = "esp32_sentry";
    sendJSON(doc);

  } else if (type == WStype_TEXT) {
    String msg = String((char*)payload);
    Serial.println("[WS RX] " + msg);

    StaticJsonDocument<512> doc;
    DeserializationError err = deserializeJson(doc, msg);
    if (err) return;

    const char* t = doc["type"] | "";

    // ---------- Absolute MOVE ----------
    if (strcmp(t, "MOVE") == 0) {
      const char* id = doc["id"] | "";
      if (strlen(id) == 0) return;
      int pan = doc["pan"] | currentPan;
      int tilt = doc["tilt"] | currentTilt;

      // Enforce safe minimum tilt
      tilt = max(tilt, TILT_MIN_SAFE);

      pan = constrain(pan, PAN_MIN, PAN_MAX);
      tilt = constrain(tilt, TILT_MIN, TILT_MAX);

      sendAck(String(id));

      noInterrupts();
      Cmd c; c.id = String(id); c.pan = pan; c.tilt = tilt;
      cmdQueue.push_back(c);
      if (hasActive) preemptFlag = true;
      interrupts();

    // ---------- CANCEL ----------
    } else if (strcmp(t, "CANCEL") == 0) {
      const char* id = doc["id"] | "";
      if (strlen(id) == 0) return;
      String sid = String(id);
      bool found = false;

      noInterrupts();
      if (hasActive && activeCmdId == sid) {
        cancelFlag = true;
        found = true;
      } else {
        for (auto it = cmdQueue.begin(); it != cmdQueue.end(); ++it) {
          if (it->id == sid) {
            cmdQueue.erase(it);
            found = true;
            break;
          }
        }
      }
      interrupts();

      sendAck(sid);
      if (found) sendStatus(sid, "CANCELLED", nullptr);
      else sendStatus(sid, "ERROR", "not_active");

    // ---------- STATUS_REQ ----------
    } else if (strcmp(t, "STATUS_REQ") == 0) {
      StaticJsonDocument<256> st;
      st["type"] = "STATUS";
      st["id"] = "";
      st["state"] = hasActive ? "BUSY" : "IDLE";
      st["pan"] = currentPan;
      st["tilt"] = currentTilt;
      if (hasActive) st["cmd_id"] = activeCmdId.c_str();
      sendJSON(st);

    // ---------- MOVE_DIR ----------
    } else if (strcmp(t, "MOVE_DIR") == 0) {
      const char* id = doc["id"] | "";
      if (strlen(id) == 0) return;
      const char* pan_dir = doc["pan_dir"] | "NONE";
      const char* tilt_dir = doc["tilt_dir"] | "NONE";
      int speed = doc["speed"] | 1;
      speed = max(1, min(10, speed));

      int8_t newPanDir = 0;
      int8_t newTiltDir = 0;
      if (strcmp(pan_dir, "LEFT") == 0) newPanDir = -1;
      else if (strcmp(pan_dir, "RIGHT") == 0) newPanDir = 1;
      if (strcmp(tilt_dir, "DOWN") == 0) newTiltDir = -1;
      else if (strcmp(tilt_dir, "UP") == 0) newTiltDir = 1;

      // enforce safe tilt: block downward if at minimum
      if (newTiltDir == -1 && currentTilt <= TILT_MIN_SAFE) newTiltDir = 0;

      sendAck(String(id));
      noInterrupts();
      if (hasActive) sendStatus(activeCmdId, "PREEMPTED", nullptr);
      activeCmdId = String(id);
      activeMode = 2;
      panDir = newPanDir;
      tiltDir = newTiltDir;
      moveSpeed = (uint8_t)speed;
      hasActive = true;
      cancelFlag = false;
      preemptFlag = false;
      cmdStartMillis = millis();
      interrupts();

      sendStatus(activeCmdId, "MOVING", nullptr);

    // ---------- STOP ----------
    } else if (strcmp(t, "STOP") == 0) {
      const char* id = doc["id"] | "";
      String sid = String(id);
      bool stopped = false;

      noInterrupts();
      if (hasActive) {
        if (sid.length() == 0 || activeCmdId == sid) {
          panDir = 0; tiltDir = 0;
          activeMode = 0;
          activeCmdId = "";
          hasActive = false;
          cancelFlag = false;
          preemptFlag = false;
          stopped = true;
        }
      }
      interrupts();

      sendAck(sid);
      if (stopped) sendStatus(sid.length() ? sid : String(""), "STOPPED", nullptr);
      else sendStatus(sid.length() ? sid : String(""), "ERROR", "not_active");
    }
  }
}

void sendJSON(const JsonDocument &doc) {
  String out;
  serializeJson(doc, out);
  webSocket.sendTXT(out);
}

void sendAck(const String &id) {
  StaticJsonDocument<128> d;
  d["type"] = "ACK";
  d["id"] = id;
  sendJSON(d);
}

void sendStatus(const String &id, const char* state, const char* error) {
  StaticJsonDocument<256> d;
  d["type"] = "STATUS";
  d["id"] = id;
  d["state"] = state;
  d["pan"] = currentPan;
  d["tilt"] = currentTilt;
  if (error) d["error"] = error;
  sendJSON(d);
}

// ---------- Core1: motion task ----------
void taskMotion(void* pv) {
  Serial.println("[MOTION] Started on core " + String(xPortGetCoreID()));
  unsigned long lastStep = millis();

  servoPan.write(currentPan);
  servoTilt.write(currentTilt);

  while (true) {
    unsigned long now = millis();

    // pick next absolute command if idle
    if (!hasActive) {
      noInterrupts();
      if (!cmdQueue.empty()) {
        Cmd c = cmdQueue.front();
        cmdQueue.pop_front();
        hasActive = true;
        activeCmdId = c.id;
        activeMode = 1;
        targetPan = c.pan;
        targetTilt = max(c.tilt, TILT_MIN_SAFE); // enforce safe tilt
        cmdStartMillis = now;
        cancelFlag = false;
        preemptFlag = false;
        panDir = 0;
        tiltDir = 0;
        Serial.printf("[MOTION] New ABS cmd id=%s pan=%d tilt=%d\n", c.id.c_str(), c.pan, c.tilt);
      }
      interrupts();
    }

    if (now - lastStep >= STEP_INTERVAL_MS) {
      lastStep = now;

      // --- Directional motion ---
      if (hasActive && activeMode == 2) {
        if (panDir != 0) {
          int nextPan = currentPan + panDir * moveSpeed;
          nextPan = constrain(nextPan, PAN_MIN, PAN_MAX);
          if (nextPan != currentPan) {
            currentPan = nextPan;
            servoPan.write(currentPan);
          }
        }
        if (tiltDir != 0) {
          int nextTilt = currentTilt + tiltDir * moveSpeed;
          nextTilt = max(nextTilt, TILT_MIN_SAFE);
          nextTilt = constrain(nextTilt, TILT_MIN, TILT_MAX);
          if (nextTilt != currentTilt) {
            currentTilt = nextTilt;
            servoTilt.write(currentTilt);
          }
        }

        if (cancelFlag) {
          sendStatus(activeCmdId, "CANCELLED", nullptr);
          noInterrupts();
          hasActive = false; activeCmdId = ""; activeMode = 0;
          cancelFlag = false; panDir = 0; tiltDir = 0;
          interrupts();
        } else if (preemptFlag) {
          sendStatus(activeCmdId, "PREEMPTED", nullptr);
          noInterrupts();
          hasActive = false; activeCmdId = ""; activeMode = 0;
          preemptFlag = false; panDir = 0; tiltDir = 0;
          interrupts();
        } else if (now - cmdStartMillis > COMMAND_TIMEOUT_MS) {
          sendStatus(activeCmdId, "TIMEOUT", nullptr);
          noInterrupts();
          hasActive = false; activeCmdId = ""; activeMode = 0;
          panDir = 0; tiltDir = 0;
          interrupts();
        }

      // --- Absolute motion ---
      } else if (hasActive && activeMode == 1) {
        if (fabs(targetPan - currentPan) > 0.01f) {
          if (targetPan > currentPan) currentPan += min(STEP_SIZE, (int)ceil(targetPan - currentPan));
          else currentPan -= min(STEP_SIZE, (int)ceil(currentPan - targetPan));
          currentPan = constrain(currentPan, PAN_MIN, PAN_MAX);
          servoPan.write(currentPan);
        }
        if (fabs(targetTilt - currentTilt) > 0.01f) {
          if (targetTilt > currentTilt) currentTilt += min(STEP_SIZE, (int)ceil(targetTilt - currentTilt));
          else currentTilt -= min(STEP_SIZE, (int)ceil(currentTilt - targetTilt));
          currentTilt = max(currentTilt, TILT_MIN_SAFE);
          currentTilt = constrain(currentTilt, TILT_MIN, TILT_MAX);
          servoTilt.write(currentTilt);
        }

        bool panReached = (abs(currentPan - (int)round(targetPan)) <= 0);
        bool tiltReached = (abs(currentTilt - (int)round(targetTilt)) <= 0);

        if (cancelFlag) {
          sendStatus(activeCmdId, "CANCELLED", nullptr);
          noInterrupts(); hasActive = false; activeCmdId = ""; activeMode = 0; cancelFlag = false; interrupts();
        } else if (preemptFlag) {
          sendStatus(activeCmdId, "PREEMPTED", nullptr);
          noInterrupts(); hasActive = false; activeCmdId = ""; activeMode = 0; preemptFlag = false; interrupts();
        } else if (panReached && tiltReached) {
          sendStatus(activeCmdId, "SUCCESS", nullptr);
          noInterrupts(); hasActive = false; activeCmdId = ""; activeMode = 0; interrupts();
        } else if (now - cmdStartMillis > COMMAND_TIMEOUT_MS) {
          sendStatus(activeCmdId, "TIMEOUT", nullptr);
          noInterrupts(); hasActive = false; activeCmdId = ""; activeMode = 0; interrupts();
        }
      }
    }
    vTaskDelay(1);
  }
}

// ---------- Setup ----------
void setup() {
  Serial.begin(115200);
  delay(200);

  servoPan.attach(SERVO_PAN_PIN);
  servoTilt.attach(SERVO_TILT_PIN);
  servoPan.write(currentPan);
  servoTilt.write(currentTilt);

  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.printf("[WIFI] Connecting '%s' ...\n", WIFI_SSID);
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 60) {
    delay(250); Serial.print(".");
    attempts++;
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("[WIFI] Connected: " + WiFi.localIP().toString());
  } else {
    Serial.println("[WIFI] Failed to connect (will retry)");
  }

  webSocket.begin(WS_HOST, WS_PORT, WS_PATH);
  webSocket.onEvent(webSocketEvent);
  webSocket.setReconnectInterval(5000);
  webSocket.enableHeartbeat(5000, 2000, 3);

  xTaskCreatePinnedToCore(taskMotion, "MotionTask", 4096, NULL, 2, NULL, 1);
  Serial.println("[SETUP] Done");
}

void loop() {
  webSocket.loop();
  delay(2);
}

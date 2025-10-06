/*
  esp32_dualcore_servo.ino
  - Core0: WebSocket client + command queue + ACK sending
  - Core1: Motion task reading queue head, executing, reporting STATUS
  - Communication uses JSON: MOVE/CANCEL: send ACK, send STATUS on completion

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

const int STEP_INTERVAL_MS = 15;
const int STEP_SIZE = 1;
const unsigned long COMMAND_TIMEOUT_MS = 4000UL;
// --------------------------------

WebSocketsClient webSocket;
Servo servoPan, servoTilt;

// Command struct
struct Cmd {
  String id;
  int pan;
  int tilt;
};

// command queue protected with critical sections
std::deque<Cmd> cmdQueue;

// active command info (used by motion task)
// NOTE: activeCmdId must NOT be volatile because String methods are not allowed on volatile objects.
volatile bool hasActive = false;
String activeCmdId = "";
volatile int currentPan = 90;
volatile int currentTilt = 90;
volatile float targetPan = 90.0f;
volatile float targetTilt = 90.0f;
volatile bool cancelFlag = false;
volatile bool preemptFlag = false;
volatile unsigned long cmdStartMillis = 0;

// forward
void sendJSON(const JsonDocument &doc);
void sendAck(const String &id);
void sendStatus(const String &id, const char* state, const char* error = nullptr);

// ---------- WebSocket callbacks on core0 ----------
void webSocketEvent(WStype_t type, uint8_t * payload, size_t length) {
  if (type == WStype_CONNECTED) {
    Serial.println("[WS] connected");
    // send hello
    StaticJsonDocument<128> doc;
    doc["type"] = "HELLO";
    doc["node"] = "esp32_sentry";
    sendJSON(doc);
  } else if (type == WStype_TEXT) {
    String msg = String((char*)payload);
    Serial.println("[WS RX] " + msg);
    // parse
    StaticJsonDocument<512> doc;
    DeserializationError err = deserializeJson(doc, msg);
    if (err) {
      Serial.println("[ERR] JSON parse");
      return;
    }
    const char* t = doc["type"] | "";
    if (strcmp(t, "MOVE") == 0) {
      const char* id = doc["id"] | "";
      if (strlen(id) == 0) return;
      int pan = doc["pan"] | currentPan;
      int tilt = doc["tilt"] | currentTilt;
      pan = constrain(pan, PAN_MIN, PAN_MAX);
      tilt = constrain(tilt, TILT_MIN, TILT_MAX);

      // ACK
      sendAck(String(id));

      // push to queue (critical)
      noInterrupts();
      Cmd c; c.id = String(id); c.pan = pan; c.tilt = tilt;
      cmdQueue.push_back(c);
      interrupts();

      // If a command is currently active, mark preempt
      noInterrupts();
      if (hasActive) {
        preemptFlag = true;
      }
      interrupts();

    } else if (strcmp(t, "CANCEL") == 0) {
      const char* id = doc["id"] | "";
      if (strlen(id) == 0) return;
      String sid = String(id);
      // remove from queue or cancel active
      bool found = false;
      noInterrupts();
      // check active
      if (hasActive && activeCmdId == sid) {
        cancelFlag = true;
        found = true;
      } else {
        // remove from deque if present
        for (auto it = cmdQueue.begin(); it != cmdQueue.end(); ++it) {
          if (it->id == sid) {
            cmdQueue.erase(it);
            found = true;
            break;
          }
        }
      }
      interrupts();
      // ack cancel and status
      sendAck(sid);
      if (found) {
        sendStatus(sid, "CANCELLED", nullptr);
      } else {
        sendStatus(sid, "ERROR", "not_active");
      }

    } else if (strcmp(t, "STATUS_REQ") == 0) {
      // immediate status
      StaticJsonDocument<256> st;
      st["type"] = "STATUS";
      st["id"] = "";
      st["state"] = hasActive ? "BUSY" : "IDLE";
      st["pan"] = currentPan;
      st["tilt"] = currentTilt;
      if (hasActive) st["cmd_id"] = activeCmdId.c_str();
      sendJSON(st);
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

  // start at safe neutral
  currentPan = constrain(currentPan, PAN_MIN, PAN_MAX);
  currentTilt = constrain(currentTilt, TILT_MIN, TILT_MAX);
  servoPan.write(currentPan);
  servoTilt.write(currentTilt);

  while (true) {
    unsigned long now = millis();
    // handle picking new command if not active
    if (!hasActive) {
      // pop next from queue atomically
      noInterrupts();
      if (!cmdQueue.empty()) {
        Cmd c = cmdQueue.front();
        cmdQueue.pop_front();
        hasActive = true;
        activeCmdId = c.id;
        targetPan = c.pan;
        targetTilt = c.tilt;
        cmdStartMillis = now;
        cancelFlag = false;
        preemptFlag = false;
        Serial.printf("[MOTION] New active cmd id=%s pan=%d tilt=%d\n", c.id.c_str(), c.pan, c.tilt);
      }
      interrupts();
    }

    // stepping
    if (now - lastStep >= STEP_INTERVAL_MS) {
      lastStep = now;
      // move toward target smoothly
      if (fabs(targetPan - currentPan) > 0.01f) {
        if (targetPan > currentPan) currentPan += min(STEP_SIZE, (int)ceil(targetPan - currentPan));
        else currentPan -= min(STEP_SIZE, (int)ceil(currentPan - targetPan));
        currentPan = constrain(currentPan, PAN_MIN, PAN_MAX);
        servoPan.write(currentPan);
      }
      if (fabs(targetTilt - currentTilt) > 0.01f) {
        if (targetTilt > currentTilt) currentTilt += min(STEP_SIZE, (int)ceil(targetTilt - currentTilt));
        else currentTilt -= min(STEP_SIZE, (int)ceil(currentTilt - targetTilt));
        currentTilt = constrain(currentTilt, TILT_MIN, TILT_MAX);
        servoTilt.write(currentTilt);
      }

      // If active command exists, check completion / timeout / cancel / preempt
      if (hasActive) {
        bool panReached = (abs(currentPan - (int)round(targetPan)) <= 0);
        bool tiltReached = (abs(currentTilt - (int)round(targetTilt)) <= 0);

        if (cancelFlag) {
          sendStatus(activeCmdId, "CANCELLED", nullptr);
          // clear active
          noInterrupts();
          hasActive = false;
          activeCmdId = "";
          cancelFlag = false;
          interrupts();
        } else if (preemptFlag) {
          sendStatus(activeCmdId, "PREEMPTED", nullptr);
          noInterrupts();
          hasActive = false;
          activeCmdId = "";
          preemptFlag = false;
          interrupts();
        } else if (panReached && tiltReached) {
          sendStatus(activeCmdId, "SUCCESS", nullptr);
          noInterrupts();
          hasActive = false;
          activeCmdId = "";
          interrupts();
        } else if (now - cmdStartMillis > COMMAND_TIMEOUT_MS) {
          sendStatus(activeCmdId, "TIMEOUT", nullptr);
          noInterrupts();
          hasActive = false;
          activeCmdId = "";
          interrupts();
        }
      }
    }

    vTaskDelay(1); // yield
  }
}

// ---------- Setup core0 tasks, wifi, websockets ----------
void setup() {
  Serial.begin(115200);
  delay(200);

  // Attach servos
  servoPan.attach(SERVO_PAN_PIN);
  servoTilt.attach(SERVO_TILT_PIN);
  servoPan.write(currentPan);
  servoTilt.write(currentTilt);

  // Connect WiFi
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.printf("[WIFI] Connecting '%s' ...\n", WIFI_SSID);
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 60) {
    delay(250);
    Serial.print(".");
    attempts++;
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("[WIFI] Connected: " + WiFi.localIP().toString());
  } else {
    Serial.println("[WIFI] Failed to connect (will retry)");
  }

  // Setup WebSocket (runs on core0 - event callbacks happen in lwip task but we're okay)
  webSocket.begin(WS_HOST, WS_PORT, WS_PATH);
  webSocket.onEvent(webSocketEvent);
  webSocket.setReconnectInterval(5000);
  webSocket.enableHeartbeat(5000, 2000, 3);

  // create motion task pinned to core 1
  xTaskCreatePinnedToCore(taskMotion, "MotionTask", 4096, NULL, 2, NULL, 1);

  Serial.println("[SETUP] Done");
}

void loop() {
  // run websocket loop on core0 (this is ok in main)
  webSocket.loop();
  delay(2);
}

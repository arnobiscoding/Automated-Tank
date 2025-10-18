/*
  esp32_servo_webcontrol_tof.ino
  - WiFi web interface to control pan/tilt servos
  - Displays live VL53L1X distance
  - UTF-8 icons fixed
  - Graceful fallback if sensor not found
*/

#include <WiFi.h>
#include <WebServer.h>
#include <ESP32Servo.h>
#include <Wire.h>
#include <Adafruit_VL53L1X.h>

// ----------- CONFIG -----------
const char* WIFI_SSID = "Control_and_Command";
const char* WIFI_PASS = "12345678";

const int SERVO_PAN_PIN = 18;
const int SERVO_TILT_PIN = 19;

const int PAN_MIN = 0;
const int PAN_MAX = 180;
const int TILT_MIN = 0;
const int TILT_MAX = 180;
const int TILT_MIN_SAFE = 45;

const int STEP_SIZE = 5;

const int SDA_PIN = 21;
const int SCL_PIN = 22;
// ------------------------------

Servo servoPan;
Servo servoTilt;
WebServer server(80);
Adafruit_VL53L1X vl53 = Adafruit_VL53L1X();

int currentPan = 90;
int currentTilt = 90;
int currentDistance = -1;
unsigned long lastRead = 0;
bool tofAvailable = false;

// ---------- HTML PAGE ----------
String htmlPage() {
  String page = R"rawliteral(
  <!DOCTYPE html>
  <html>
  <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>ESP32 Pan-Tilt + ToF</title>
    <style>
      body { font-family: Arial, sans-serif; text-align:center; background:#121212; color:#eee; margin:0; padding:0; }
      h1 { margin-top:20px; }
      button {
        width:100px; height:60px; font-size:24px; margin:10px;
        border:none; border-radius:10px; background-color:#2196F3; color:white;
        cursor:pointer; transition:0.2s;
      }
      button:hover { background-color:#0b7dda; }
      .grid { display:grid; grid-template-columns:1fr 1fr 1fr; justify-items:center; align-items:center; margin-top:40px; }
      #pos, #dist { margin-top:20px; font-size:18px; }
      #distValue { font-weight:bold; font-size:20px; color:#4CAF50; }
    </style>
  </head>
  <body>
    <h1>ESP32 Pan-Tilt + ToF Distance</h1>
    <p>(Use Arrow Keys or Buttons)</p>
    <div class="grid">
      <div></div>
      <button onclick="move('tilt_up')">▲</button>
      <div></div>
      <button onclick="move('pan_left')">◀</button>
      <div></div>
      <button onclick="move('pan_right')">▶</button>
      <div></div>
      <button onclick="move('tilt_down')">▼</button>
      <div></div>
    </div>
    <div id="pos">Pan: <span id="pan">--</span> | Tilt: <span id="tilt">--</span></div>
    <div id="dist">📏 Distance: <span id="distValue">--</span> mm</div>

    <script>
      function move(dir) {
        fetch('/move?dir=' + dir)
          .then(r => r.text())
          .then(update => {
            const data = JSON.parse(update);
            document.getElementById('pan').textContent = data.pan;
            document.getElementById('tilt').textContent = data.tilt;
          });
      }

      function refreshPos() {
        fetch('/pos')
          .then(r => r.text())
          .then(update => {
            const data = JSON.parse(update);
            document.getElementById('pan').textContent = data.pan;
            document.getElementById('tilt').textContent = data.tilt;
          });
      }

      function refreshDist() {
        fetch('/dist')
          .then(r => r.text())
          .then(update => {
            const data = JSON.parse(update);
            document.getElementById('distValue').textContent = data.distance >= 0 ? data.distance : '--';
          });
      }

      document.addEventListener('keydown', (e) => {
        switch(e.key) {
          case 'ArrowUp': move('tilt_up'); break;
          case 'ArrowDown': move('tilt_down'); break;
          case 'ArrowLeft': move('pan_left'); break;
          case 'ArrowRight': move('pan_right'); break;
        }
      });

      setInterval(() => { refreshPos(); refreshDist(); }, 1000);
      window.onload = () => { refreshPos(); refreshDist(); };
    </script>
  </body>
  </html>
  )rawliteral";
  return page;
}

// ---------- ROUTES ----------
void handleRoot() { server.send(200, "text/html; charset=utf-8", htmlPage()); }

void handleMove() {
  if (!server.hasArg("dir")) { server.send(400, "text/plain", "Missing dir"); return; }

  String dir = server.arg("dir");

  if (dir == "pan_left") currentPan = max(PAN_MIN, currentPan - STEP_SIZE);
  else if (dir == "pan_right") currentPan = min(PAN_MAX, currentPan + STEP_SIZE);
  else if (dir == "tilt_up") currentTilt = min(TILT_MAX, currentTilt + STEP_SIZE);
  else if (dir == "tilt_down") currentTilt = max(TILT_MIN_SAFE, currentTilt - STEP_SIZE);

  servoPan.write(currentPan);
  servoTilt.write(currentTilt);
  Serial.printf("Pan: %d | Tilt: %d\n", currentPan, currentTilt);

  String json = "{\"pan\":" + String(currentPan) + ",\"tilt\":" + String(currentTilt) + "}";
  server.send(200, "application/json; charset=utf-8", json);
}

void handlePos() {
  String json = "{\"pan\":" + String(currentPan) + ",\"tilt\":" + String(currentTilt) + "}";
  server.send(200, "application/json; charset=utf-8", json);
}

void handleDist() {
  String json = "{\"distance\":" + String(currentDistance) + "}";
  server.send(200, "application/json; charset=utf-8", json);
}

// ---------- SETUP ----------
void setup() {
  Serial.begin(115200);
  delay(200);

  servoPan.attach(SERVO_PAN_PIN);
  servoTilt.attach(SERVO_TILT_PIN);
  servoPan.write(currentPan);
  servoTilt.write(currentTilt);

  Serial.printf("[WIFI] Connecting to %s...\n", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) { delay(300); Serial.print("."); }
  Serial.println();
  Serial.printf("[WIFI] Connected! IP: %s\n", WiFi.localIP().toString().c_str());

  // --- Initialize ToF ---
  Wire.begin(SDA_PIN, SCL_PIN);
  if (vl53.begin(0x29, &Wire)) {
    vl53.startRanging();
    tofAvailable = true;
    Serial.println("[TOF] VL53L1X started!");
  } else {
    Serial.println("[TOF] Failed to find VL53L1X sensor!");
  }

  // --- Routes ---
  server.on("/", handleRoot);
  server.on("/move", handleMove);
  server.on("/pos", handlePos);
  server.on("/dist", handleDist);
  server.begin();

  Serial.println("[HTTP] Server started.");
  Serial.println("[INFO] Open in browser: http://" + WiFi.localIP().toString());
}

// ---------- LOOP ----------
void loop() {
  server.handleClient();

  if (tofAvailable && vl53.dataReady() && millis() - lastRead > 500) {
    currentDistance = vl53.distance();
    vl53.clearInterrupt();
    lastRead = millis();
    Serial.printf("[TOF] Distance: %d mm\n", currentDistance);
  }
}

#include <ESP8266WiFi.h>
#include <ESP8266WebServer.h>
#include <ESP8266mDNS.h>   // <- Add this for mDNS
#include <Servo.h>
#include <Wire.h>
#include <Adafruit_VL53L0X.h>

// ---------- CONFIG ----------
const char* WIFI_SSID = "Hello1";
const char* WIFI_PASS = "12345678";

// Servo pins
const int SERVO_PAN_PIN = D5;
const int SERVO_TILT_PIN = D6;

// Servo limits
const int PAN_MIN = 0, PAN_MAX = 180;
const int TILT_MIN_SAFE = 45, TILT_MAX = 180, STEP_SIZE = 5;

// Motor pins
const int ENA = D1, ENB = D2;
const int IN1 = D3, IN2 = D4, IN3 = D7, IN4 = D8;

// Gear speeds
const int gearSpeeds[5] = {50,100,150,200,255};

// ---------- GLOBALS ----------
Servo servoPan, servoTilt;
ESP8266WebServer server(80);

int currentPan = 90;
int currentTilt = 90;
int currentGear = 1;
int currentDistance = -1; // placeholder -1 when no sensor
bool tofAvailable = false;
unsigned long lastRead = 0;
Adafruit_VL53L0X lox;

// ---------- MOTOR & SERVO FUNCTIONS ----------
void moveForward(){ 
  digitalWrite(IN1,HIGH); digitalWrite(IN2,LOW); digitalWrite(IN3,HIGH); digitalWrite(IN4,LOW); 
  analogWrite(ENA,gearSpeeds[currentGear-1]); analogWrite(ENB,gearSpeeds[currentGear-1]); 
}
void moveBackward(){ 
  digitalWrite(IN1,LOW); digitalWrite(IN2,HIGH); digitalWrite(IN3,LOW); digitalWrite(IN4,HIGH); 
  analogWrite(ENA,gearSpeeds[currentGear-1]); analogWrite(ENB,gearSpeeds[currentGear-1]); 
}
void turnLeft(){ 
  digitalWrite(IN1,HIGH); digitalWrite(IN2,LOW); digitalWrite(IN3,LOW); digitalWrite(IN4,HIGH); 
  analogWrite(ENA,gearSpeeds[currentGear-1]); analogWrite(ENB,gearSpeeds[currentGear-1]); 
}
void turnRight(){ 
  digitalWrite(IN1,LOW); digitalWrite(IN2,HIGH); digitalWrite(IN3,HIGH); digitalWrite(IN4,LOW); 
  analogWrite(ENA,gearSpeeds[currentGear-1]); analogWrite(ENB,gearSpeeds[currentGear-1]); 
}
void stopMotors(){ 
  analogWrite(ENA,0); analogWrite(ENB,0); 
}

// ---------- ROUTES ----------
void handleMove() {
  Serial.println("Handle /move called");
  server.sendHeader("Access-Control-Allow-Origin","*");
  if(!server.hasArg("dir")) { 
    Serial.println("Missing dir argument"); 
    server.send(400,"text/plain","Missing dir"); 
    return; 
  }
  String d = server.arg("dir");
  Serial.println("Move direction: " + d);
  if(d=="pan_left") currentPan = max(PAN_MIN,currentPan-STEP_SIZE);
  else if(d=="pan_right") currentPan = min(PAN_MAX,currentPan+STEP_SIZE);
  else if(d=="tilt_up") currentTilt = min(TILT_MAX,currentTilt+STEP_SIZE);
  else if(d=="tilt_down") currentTilt = max(TILT_MIN_SAFE,currentTilt-STEP_SIZE);
  servoPan.write(currentPan); 
  servoTilt.write(currentTilt);
  Serial.println("Pan: " + String(currentPan) + " Tilt: " + String(currentTilt));
  server.send(200,"application/json","{\"pan\":"+String(currentPan)+",\"tilt\":"+String(currentTilt)+"}");
}

void handlePos() {
  Serial.println("Handle /pos called");
  server.sendHeader("Access-Control-Allow-Origin","*");
  server.send(200,"application/json","{\"pan\":"+String(currentPan)+",\"tilt\":"+String(currentTilt)+"}");
}

void handleDist() {
  Serial.println("Handle /dist called");
  server.sendHeader("Access-Control-Allow-Origin","*");
  Serial.println("Distance: " + String(currentDistance));
  server.send(200,"application/json","{\"distance\":"+String(currentDistance)+"}");
}

// Returns whether the ToF sensor is present and producing readings
void handleDistReady(){
  Serial.println("Handle /dist_ready called");
  server.sendHeader("Access-Control-Allow-Origin","*");
  server.send(200,"application/json","{\"ready\":"+(tofAvailable?String("true"):String("false"))+"}");
}

void handleCar() {
  Serial.println("Handle /car called");
  server.sendHeader("Access-Control-Allow-Origin","*");
  if(!server.hasArg("cmd")){ 
    Serial.println("Missing cmd argument"); 
    server.send(400,"text/plain","Missing cmd"); 
    return; 
  }
  String c = server.arg("cmd");
  Serial.println("Car command: " + c);
  if(c=="forward") moveForward();
  else if(c=="backward") moveBackward();
  else if(c=="left") turnLeft();
  else if(c=="right") turnRight();
  else stopMotors();
  server.send(200,"text/plain","OK");
}

void handleGear() {
  Serial.println("Handle /gear called");
  server.sendHeader("Access-Control-Allow-Origin","*");
  if(!server.hasArg("value")){ 
    Serial.println("Missing value argument"); 
    server.send(400,"text/plain","Missing value"); 
    return; 
  }
  int g = server.arg("value").toInt();
  Serial.println("Set gear to: " + String(g));
  if(g>=1 && g<=5) currentGear = g;
  server.send(200,"text/plain","Gear set");
}

// ---------- SETUP ----------
void setup() {
  Serial.begin(115200);

  servoPan.attach(SERVO_PAN_PIN);
  servoTilt.attach(SERVO_TILT_PIN);
  servoPan.write(currentPan);
  servoTilt.write(currentTilt);

  pinMode(ENA,OUTPUT); pinMode(ENB,OUTPUT);
  pinMode(IN1,OUTPUT); pinMode(IN2,OUTPUT);
  pinMode(IN3,OUTPUT); pinMode(IN4,OUTPUT);
  stopMotors();

  WiFi.begin(WIFI_SSID,WIFI_PASS);
  Serial.print("Connecting WiFi");
  while(WiFi.status()!=WL_CONNECTED){ delay(300); Serial.print("."); }
  Serial.println("\nConnected! IP: "+WiFi.localIP().toString());

  // ---------- mDNS ----------
  if (MDNS.begin("nodemcu")) {
    Serial.println("mDNS responder started: nodemcu.local");
  } else {
    Serial.println("Error setting up mDNS responder!");
  }

  // ---------- VL53L0X ToF sensor ----------
  Wire.begin();
  if(!lox.begin()){
    Serial.println("VL53L0X not found");
    tofAvailable = false;
  } else {
    tofAvailable = true;
    Serial.println("VL53L0X initialized");
  }

  server.on("/move",handleMove);
  server.on("/pos",handlePos);
  server.on("/dist",handleDist);
  server.on("/dist_ready",handleDistReady);
  server.on("/car",handleCar);
  server.on("/gear",handleGear);
  server.begin();
  Serial.println("HTTP server started");
}

// ---------- LOOP ----------
void loop() {
  server.handleClient();
  MDNS.update(); // <-- keep mDNS alive
  // periodic ToF reading (update roughly every 500ms if available)
  if(tofAvailable && millis()-lastRead > 500){
    VL53L0X_RangingMeasurementData_t m;
    lox.rangingTest(&m, false);
    currentDistance = (m.RangeStatus == 0) ? m.RangeMilliMeter : -1;
    lastRead = millis();
  }
}

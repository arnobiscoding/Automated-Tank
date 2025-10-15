"""
Red Object Tracker + Servo Controller GUI
- Video feed: 640x480
- Logs box on the right
- Sends MOVE_DIR commands via WebSocket to ESP32 based on tracking
"""

import sys
import json
import asyncio
import threading
import time
import cv2
import numpy as np
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QLabel
)
from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot, QTimer
from PyQt5.QtGui import QImage, QPixmap

import websockets

# CONFIG
WS_HOST = "192.168.137.1"   # ESP32 should connect to this IP
WS_PORT = 8080

# --- Server object to run in separate thread and emit signals ---
class WsServer(QObject):
    sig_log = pyqtSignal(str)
    sig_msg = pyqtSignal(str)
    sig_ready = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.loop = None
        self.server = None
        self.clients = set()
        self.out_queue = None
        self._thread = None
        self.running = False

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        async def _init_server():
            self.out_queue = asyncio.Queue()
            server = await websockets.serve(self._handler, "0.0.0.0", WS_PORT)
            return server

        self.server = self.loop.run_until_complete(_init_server())
        self.sig_log.emit(f"[SERVER] Listening on ws://0.0.0.0:{WS_PORT}")
        self.sig_ready.emit()
        self.loop.create_task(self._sender_task())
        self.running = True
        try:
            self.loop.run_forever()
        finally:
            self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            self.loop.close()
            self.sig_log.emit("[SERVER] Loop closed")

    async def _handler(self, websocket, path=None):
        addr = websocket.remote_address
        self.clients.add(websocket)
        self.sig_log.emit(f"[CONNECT] Client connected: {addr}")
        try:
            async for message in websocket:
                self.sig_log.emit(f"[RX] {message}")
                self.sig_msg.emit(message)
        except websockets.ConnectionClosed:
            self.sig_log.emit(f"[DISCONNECT] Client {addr} disconnected")
        finally:
            self.clients.discard(websocket)

    async def _sender_task(self):
        while True:
            msg = await self.out_queue.get()
            if msg is None:
                break
            s = json.dumps(msg)
            dead = []
            if not self.clients:
                self.sig_log.emit("[SENDER] No clients connected; message dropped")
            for c in list(self.clients):
                try:
                    await c.send(s)
                    self.sig_log.emit(f"[TX->{c.remote_address}] {s}")
                except Exception as e:
                    self.sig_log.emit(f"[SENDER ERR] {e}")
                    dead.append(c)
            for d in dead:
                self.clients.discard(d)

    def send_json(self, obj):
        if not self.loop or not self.running:
            self.sig_log.emit("[ERROR] Server not running")
            return
        fut = asyncio.run_coroutine_threadsafe(self.out_queue.put(obj), self.loop)
        try:
            fut.result(timeout=1.0)
        except Exception as e:
            self.sig_log.emit(f"[ERROR] send_json: {e}")

    def stop(self):
        if not self.loop:
            return
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.running = False


# ---------- Main GUI ----------
class TrackerGUI(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Red Object Tracker + Servo Controller")
        self.setGeometry(200, 100, 900, 540)

        self.server = WsServer()
        self.server.sig_log.connect(self.append_log)
        self.server.sig_msg.connect(self.on_incoming_message)
        self.server.sig_ready.connect(self.on_server_ready)
        self.server.start()

        self.cap = cv2.VideoCapture(0)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        self.last_cx, self.last_cy = None, None
        self.last_pan_dir, self.last_tilt_dir = "NONE", "NONE"
        self.last_command_time = 0

        self.DEADZONE_PX = 30
        self.COMMAND_INTERVAL = 0.1  # seconds
        self.SPEED = 2

        self._build_ui()
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_frame)
        self.timer.start(30)  # ~33 FPS

    def _build_ui(self):
        layout = QHBoxLayout(self)

        # Left: Video feed
        left_layout = QVBoxLayout()
        left_layout.addWidget(QLabel("<b>Video Feed</b>"))
        self.video_label = QLabel()
        self.video_label.setFixedSize(640, 480)
        left_layout.addWidget(self.video_label)
        layout.addLayout(left_layout, 2)

        # Right: Logs
        right_layout = QVBoxLayout()
        right_layout.addWidget(QLabel("<b>Logs / Messages</b>"))
        self.logview = QTextEdit()
        self.logview.setReadOnly(True)
        right_layout.addWidget(self.logview)
        layout.addLayout(right_layout, 1)

        self.setLayout(layout)

    @pyqtSlot()
    def on_server_ready(self):
        self.append_log("[GUI] Server ready")

    @pyqtSlot(str)
    def append_log(self, text):
        self.logview.append(text)

    @pyqtSlot(str)
    def on_incoming_message(self, raw):
        try:
            obj = json.loads(raw)
            self.append_log(f"[INCOMING JSON] {json.dumps(obj, indent=2)}")
        except Exception:
            self.append_log(f"[INCOMING RAW] {raw}")

    def update_frame(self):
        ret, frame = self.cap.read()
        if not ret:
            return

        frame_blur = cv2.GaussianBlur(frame, (7, 7), 0)
        hsv = cv2.cvtColor(frame_blur, cv2.COLOR_BGR2HSV)
        h, w, _ = frame.shape
        center_x, center_y = w // 2, h // 2

        # Red color range
        lower_red1 = np.array([0, 100, 70])
        upper_red1 = np.array([10, 255, 255])
        lower_red2 = np.array([170, 170, 120])
        upper_red2 = np.array([180, 255, 255])

        mask1 = cv2.inRange(hsv, lower_red1, upper_red1)
        mask2 = cv2.inRange(hsv, lower_red2, upper_red2)
        mask = cv2.bitwise_or(mask1, mask2)
        kernel = np.ones((5, 5), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        target_contour = None
        max_area = 0

        for contour in contours:
            area = cv2.contourArea(contour)
            if area > 500 and area > max_area:
                max_area = area
                target_contour = contour
            cv2.drawContours(frame, [contour], -1, (0, 255, 0), 1)

        cv2.circle(frame, (center_x, center_y), 6, (0, 255, 255), -1)
        cv2.line(frame, (center_x - 20, center_y), (center_x + 20, center_y), (0, 255, 255), 1)
        cv2.line(frame, (center_x, center_y - 20), (center_x, center_y + 20), (0, 255, 255), 1)

        direction_text = "No Target"
        cx, cy = None, None

        if target_contour is not None:
            x, y, w_box, h_box = cv2.boundingRect(target_contour)
            cv2.rectangle(frame, (x, y), (x + w_box, y + h_box), (255, 0, 0), 3)
            M = cv2.moments(target_contour)
            if M["m00"] != 0:
                cx = int(M["m10"] / M["m00"])
                cy = int(M["m01"] / M["m00"])
                self.last_cx, self.last_cy = cx, cy
                cv2.circle(frame, (cx, cy), 6, (0, 0, 255), -1)
                dx = cx - center_x
                dy = cy - center_y

                # Deadzone logic
                pan_dir, tilt_dir = "NONE", "NONE"
                if dx < -self.DEADZONE_PX:
                    pan_dir = "LEFT"
                elif dx > self.DEADZONE_PX:
                    pan_dir = "RIGHT"
                if dy < -self.DEADZONE_PX:
                    tilt_dir = "UP"
                elif dy > self.DEADZONE_PX:
                    tilt_dir = "DOWN"

                direction_text = f"Pan: {pan_dir} | Tilt: {tilt_dir}"

                # Send command if changed or interval passed
                curr_time = time.time()
                if (pan_dir != self.last_pan_dir or tilt_dir != self.last_tilt_dir or
                        curr_time - self.last_command_time > self.COMMAND_INTERVAL):
                    msg = {
                        "type": "MOVE_DIR",
                        "id": str(int(curr_time * 1000)),
                        "pan_dir": pan_dir,
                        "tilt_dir": tilt_dir,
                        "speed": self.SPEED
                    }
                    self.server.send_json(msg)
                    self.last_pan_dir = pan_dir
                    self.last_tilt_dir = tilt_dir
                    self.last_command_time = curr_time

        elif self.last_cx is not None:
            cv2.circle(frame, (self.last_cx, self.last_cy), 6, (255, 255, 0), -1)
            direction_text = "Last seen"

        # Overlay info
        cv2.putText(frame, direction_text, (30, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

        # Convert to Qt image
        rgb_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        qt_image = QImage(rgb_image.data, w, h, 3 * w, QImage.Format_RGB888)
        self.video_label.setPixmap(QPixmap.fromImage(qt_image))

    def closeEvent(self, event):
        self.cap.release()
        self.server.stop()
        event.accept()


def main():
    app = QApplication(sys.argv)
    gui = TrackerGUI()
    gui.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

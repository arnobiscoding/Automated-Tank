import sys
import json
import uuid
import cv2
import numpy as np
import time
import asyncio
import threading
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QTextEdit, QLabel
)
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtCore import QTimer, Qt, QObject, pyqtSignal, pyqtSlot
import websockets

# CONFIG
WS_HOST = "ws://127.0.0.1:8080"  # adjust to your server
STEP_RADIUS = 50                 # pixels tolerance to consider “centered”
MOVE_SPEED = 2                   # degrees per step for directional MOVE

# ---------------- WebSocket client -----------------
class WsClient(QObject):
    sig_log = pyqtSignal(str)
    
    def __init__(self, uri):
        super().__init__()
        self.uri = uri
        self.loop = asyncio.new_event_loop()
        self.ws = None
        self.thread = threading.Thread(target=self.run_loop, daemon=True)
        self.thread.start()

    def run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.connect())

    async def connect(self):
        try:
            self.ws = await websockets.connect(self.uri)
            self.sig_log.emit(f"[WS] Connected to {self.uri}")
        except Exception as e:
            self.sig_log.emit(f"[WS ERROR] {e}")
            return

    def send_json(self, obj):
        if self.ws and self.ws.open:
            asyncio.run_coroutine_threadsafe(self.ws.send(json.dumps(obj)), self.loop)
            self.sig_log.emit(f"[TX] {obj}")

# ---------------- Main GUI -----------------
class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Red Object Tracker")
        self.setGeometry(200, 100, 700, 600)

        self.vbox = QVBoxLayout()
        self.setLayout(self.vbox)

        # Video display
        self.video_label = QLabel()
        self.video_label.setFixedSize(640, 480)
        self.video_label.setAlignment(Qt.AlignCenter)
        self.vbox.addWidget(self.video_label)

        # Log view
        self.logview = QTextEdit()
        self.logview.setReadOnly(True)
        self.vbox.addWidget(self.logview)

        # WS client
        self.ws_client = WsClient(WS_HOST)
        self.ws_client.sig_log.connect(self.append_log)

        # Video capture
        self.cap = cv2.VideoCapture(0)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        self.last_cx, self.last_cy = None, None
        self.last_pan_dir, self.last_tilt_dir = None, None

        # Timer to grab frames
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_frame)
        self.timer.start(30)  # ~33 fps

    @pyqtSlot()
    def update_frame(self):
        ret, frame = self.cap.read()
        if not ret:
            return

        frame_blur = cv2.GaussianBlur(frame, (7, 7), 0)
        hsv = cv2.cvtColor(frame_blur, cv2.COLOR_BGR2HSV)
        h, w, _ = frame.shape
        center_x, center_y = w // 2, h // 2

        # Relaxed red thresholds
        lower_red1 = np.array([0, 80, 50])
        upper_red1 = np.array([10, 255, 255])
        lower_red2 = np.array([170, 100, 50])
        upper_red2 = np.array([180, 255, 255])

        mask1 = cv2.inRange(hsv, lower_red1, upper_red1)
        mask2 = cv2.inRange(hsv, lower_red2, upper_red2)
        mask = cv2.bitwise_or(mask1, mask2)

        kernel = np.ones((5,5), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        target_contour = None
        max_area = 0

        for contour in contours:
            area = cv2.contourArea(contour)
            if area > 300:  # lowered area threshold
                if area > max_area:
                    max_area = area
                    target_contour = contour
                cv2.drawContours(frame, [contour], -1, (0, 255, 0), 1)

        # Draw camera center
        cv2.circle(frame, (center_x, center_y), 6, (0, 255, 255), -1)

        direction = "No Target"
        pan_dir, tilt_dir = "NONE", "NONE"
        cx, cy = None, None

        if target_contour is not None:
            x, y, w_box, h_box = cv2.boundingRect(target_contour)
            cv2.rectangle(frame, (x, y), (x + w_box, y + h_box), (255, 0, 0), 2)

            M = cv2.moments(target_contour)
            if M["m00"] != 0:
                cx = int(M["m10"] / M["m00"])
                cy = int(M["m01"] / M["m00"])
                self.last_cx, self.last_cy = cx, cy
                cv2.circle(frame, (cx, cy), 6, (0, 0, 255), -1)

                dx, dy = cx - center_x, cy - center_y

                if abs(dx) < STEP_RADIUS and abs(dy) < STEP_RADIUS:
                    direction = "Centered ✅"
                elif abs(dx) > abs(dy):
                    pan_dir = "LEFT" if dx < 0 else "RIGHT"
                    direction = f"⬅ Move LEFT" if dx < 0 else "➡ Move RIGHT"
                else:
                    tilt_dir = "DOWN" if dy < 0 else "UP"
                    direction = f"⬇ Move DOWN" if dy < 0 else "⬆ Move UP"

        elif self.last_cx is not None:
            cv2.circle(frame, (self.last_cx, self.last_cy), 6, (255, 255, 0), -1)
            cv2.putText(frame, "Last seen", (self.last_cx-40, self.last_cy-15),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255,255,0), 2)

        cv2.putText(frame, direction, (30,50),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,255,255), 2, cv2.LINE_AA)

        # Send MOVE_DIR only if changed
        if (pan_dir != self.last_pan_dir) or (tilt_dir != self.last_tilt_dir):
            msg = {
                "type": "MOVE_DIR",
                "id": uuid.uuid4().hex[:12],
                "pan_dir": pan_dir,
                "tilt_dir": tilt_dir,
                "speed": MOVE_SPEED
            }
            self.ws_client.send_json(msg)
            self.last_pan_dir = pan_dir
            self.last_tilt_dir = tilt_dir

        # Display frame in QLabel
        rgb_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb_image.shape
        bytes_per_line = ch * w
        qt_img = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format_RGB888)
        self.video_label.setPixmap(QPixmap.fromImage(qt_img))

    @pyqtSlot(str)
    def append_log(self, text):
        self.logview.append(text)

    def closeEvent(self, event):
        self.cap.release()
        event.accept()

# ---------------- Entry -----------------
def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()

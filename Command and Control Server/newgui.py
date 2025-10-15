"""
server_gui_video.py
PyQt5 GUI + asyncio WebSocket server.

Left panel: Video stream (640x480)
Right panel: Logs / incoming messages
"""

import sys
import json
import asyncio
import threading
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QLabel
)
from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot, Qt
from PyQt5.QtGui import QPixmap, QImage
import websockets
import numpy as np
import cv2

# CONFIG
WS_BIND_HOST = "0.0.0.0"  
WS_BIND_PORT = 8080

# ---------- Server object ----------
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
            server = await websockets.serve(self._handler, WS_BIND_HOST, WS_BIND_PORT)
            return server

        self.server = self.loop.run_until_complete(_init_server())
        self.sig_log.emit(f"[SERVER] Listening on ws://{WS_BIND_HOST}:{WS_BIND_PORT}")
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

# ---------- PyQt GUI ----------
class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sentry Command Center - Video Stream")
        self.setGeometry(200, 100, 960, 520)

        self.server = WsServer()
        self.server.sig_log.connect(self.append_log)
        self.server.sig_msg.connect(self.on_incoming_message)
        self.server.sig_ready.connect(self.on_server_ready)

        self._build_ui()
        self.server.start()

    def _build_ui(self):
        layout = QHBoxLayout(self)

        # Left panel: Video stream
        left = QVBoxLayout()
        left.addWidget(QLabel("<b>Video Stream</b>"))
        self.video_label = QLabel()
        self.video_label.setFixedSize(640, 480)
        self.video_label.setStyleSheet("background-color: black;")
        self.video_label.setAlignment(Qt.AlignCenter)
        left.addWidget(self.video_label)
        layout.addLayout(left, 2)

        # Right panel: Logs
        right = QVBoxLayout()
        right.addWidget(QLabel("<b>Logs / Messages</b>"))
        self.logview = QTextEdit()
        self.logview.setReadOnly(True)
        right.addWidget(self.logview)
        layout.addLayout(right, 1)

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
            pretty = json.dumps(obj, indent=2)
            self.append_log(f"[INCOMING JSON]\n{pretty}")
            typ = obj.get("type", "")
            if typ == "ACK":
                self.append_log(f"[ACK] id={obj.get('id','')}")
            elif typ == "STATUS":
                s = obj.get("state","")
                cid = obj.get("id","")
                self.append_log(f"[STATUS] id={cid} state={s} pan={obj.get('pan', '')} tilt={obj.get('tilt','')}")
        except Exception:
            self.append_log(f"[INCOMING RAW] {raw}")

    def update_frame(self, frame: np.ndarray):
        """Update the video QLabel with a BGR OpenCV frame"""
        rgb_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb_image.shape
        bytes_per_line = ch * w
        qimg = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format_RGB888)
        pixmap = QPixmap.fromImage(qimg)
        self.video_label.setPixmap(pixmap)

    def closeEvent(self, event):
        self.server.stop()
        event.accept()

# ---------- Entry ----------
def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()

"""
server_gui.py
PyQt5 GUI + asyncio websockets server.

Left panel: Send MOVE (pan,tilt) and CANCEL (by id)
Right panel: Logs (incoming messages, ACKs, STATUS, events)

Run on your laptop hotspot IP (example 192.168.137.1).
"""

import sys
import json
import asyncio
import threading
import uuid
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTextEdit, QLineEdit, QLabel, QSpinBox, QFormLayout, QMessageBox
)
from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot

import websockets

# CONFIG
WS_BIND_HOST = "0.0.0.0"   # bind on all interfaces; use 192.168.137.1 if you prefer
WS_BIND_PORT = 8080

# ---------- Server object to run in separate thread and emit signals ----------
class WsServer(QObject):
    sig_log = pyqtSignal(str)
    sig_msg = pyqtSignal(str)
    sig_ready = pyqtSignal()  # signal when server is ready

    def __init__(self):
        super().__init__()
        self.loop = None
        self.server = None
        self.clients = set()  # set of websockets
        self.out_queue = None  # asyncio.Queue (created in loop)
        self._thread = None
        self.running = False

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self):
        # Setup an asyncio event loop in this thread
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        # Create the queue and start the websockets server inside a coroutine
        # executed on the new event loop. This avoids calling asyncio.get_running_loop()
        # (used by asyncio.Queue and websockets) before the loop is running.
        async def _init_server():
            self.out_queue = asyncio.Queue()
            server = await websockets.serve(self._handler, WS_BIND_HOST, WS_BIND_PORT)
            return server

        self.server = self.loop.run_until_complete(_init_server())
        self.sig_log.emit(f"[SERVER] Listening on ws://{WS_BIND_HOST}:{WS_BIND_PORT}")
        self.sig_ready.emit()  # <-- notify GUI server is ready

        # schedule a background sender coroutine
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
                # Log incoming raw
                self.sig_log.emit(f"[RX] {message}")
                # Emit parsed JSON as signal (GUI may show content)
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
            # send to all connected clients
            dead = []
            s = json.dumps(msg)
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

    # thread-safe helper for GUI to push messages
    def send_json(self, obj):
        if not self.loop or not self.running:
            self.sig_log.emit("[ERROR] Server not running")
            return
        # put into asyncio queue from another thread
        fut = asyncio.run_coroutine_threadsafe(self.out_queue.put(obj), self.loop)
        try:
            fut.result(timeout=1.0)
        except Exception as e:
            self.sig_log.emit(f"[ERROR] send_json: {e}")

    def stop(self):
        if not self.loop:
            return
        def _stop_loop():
            self.loop.stop()
        self.loop.call_soon_threadsafe(_stop_loop)
        self.running = False

# ---------- PyQt GUI ----------
class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sentry Command Center")
        self.setGeometry(300, 200, 900, 480)
        self.server = WsServer()
        self.server.sig_log.connect(self.append_log)
        self.server.sig_msg.connect(self.on_incoming_message)
        self.server.sig_ready.connect(self.on_server_ready)

        self._build_ui()
        self.server.start()

        # keep track of last command id
        self.last_cmd_id = None

    def _build_ui(self):
        layout = QHBoxLayout(self)

        # Left: command CLI
        left = QVBoxLayout()
        left.addWidget(QLabel("<b>Command CLI</b>"))

        form = QFormLayout()
        self.spin_pan = QSpinBox()
        self.spin_pan.setRange(0, 180)
        self.spin_pan.setValue(90)
        self.spin_tilt = QSpinBox()
        self.spin_tilt.setRange(0, 180)
        self.spin_tilt.setValue(90)
        form.addRow("Pan (deg):", self.spin_pan)
        form.addRow("Tilt (deg):", self.spin_tilt)

        left.addLayout(form)

        btn_layout = QHBoxLayout()
        self.btn_send = QPushButton("Send MOVE")
        self.btn_send.setEnabled(False)  # initially disabled
        self.btn_send.clicked.connect(self.send_move)
        btn_layout.addWidget(self.btn_send)

        self.btn_cancel = QPushButton("Send CANCEL")
        self.btn_cancel.setEnabled(False)  # initially disabled
        self.btn_cancel.clicked.connect(self.send_cancel)
        btn_layout.addWidget(self.btn_cancel)

        left.addLayout(btn_layout)

        # Cancel ID input
        self.input_cancel_id = QLineEdit()
        self.input_cancel_id.setPlaceholderText("Command ID to cancel (leave empty to cancel last)")
        left.addWidget(self.input_cancel_id)

        # small helper
        left.addSpacing(8)
        left.addWidget(QLabel("Note: server runs on this machine. ESP should connect to this IP."))
        left.addWidget(QLabel(f"Bind: ws://{WS_BIND_HOST}:{WS_BIND_PORT}"))

        # Right: logs
        right = QVBoxLayout()
        right.addWidget(QLabel("<b>Logs / Messages</b>"))
        self.logview = QTextEdit()
        self.logview.setReadOnly(True)
        right.addWidget(self.logview)

        # Combine
        layout.addLayout(left, 1)
        layout.addLayout(right, 2)
        self.setLayout(layout)

    @pyqtSlot()
    def on_server_ready(self):
        self.append_log("[GUI] Server ready")
        self.btn_send.setEnabled(True)
        self.btn_cancel.setEnabled(True)

    @pyqtSlot()
    def send_move(self):
        if not self.server.running:
            self.append_log("[GUI] Server not ready yet!")
            return
        pan = int(self.spin_pan.value())
        tilt = int(self.spin_tilt.value())
        cmd_id = uuid.uuid4().hex[:12]
        self.last_cmd_id = cmd_id
        msg = {
            "type": "MOVE",
            "id": cmd_id,
            "pan": pan,
            "tilt": tilt
        }
        self.append_log(f"[GUI] Sending MOVE id={cmd_id} pan={pan} tilt={tilt}")
        self.server.send_json(msg)

    @pyqtSlot()
    def send_cancel(self):
        if not self.server.running:
            self.append_log("[GUI] Server not ready yet!")
            return
        cid = self.input_cancel_id.text().strip()
        if not cid:
            if not self.last_cmd_id:
                QMessageBox.warning(self, "Cancel", "No command id specified and no last command available")
                return
            cid = self.last_cmd_id
        msg = {"type": "CANCEL", "id": cid}
        self.append_log(f"[GUI] Sending CANCEL id={cid}")
        self.server.send_json(msg)

    @pyqtSlot(str)
    def append_log(self, text):
        self.logview.append(text)

    @pyqtSlot(str)
    def on_incoming_message(self, raw):
        # try parse json for nicer display
        try:
            obj = json.loads(raw)
            pretty = json.dumps(obj, indent=2)
            self.append_log(f"[INCOMING JSON]\n{pretty}")
            # Additional helper displays
            typ = obj.get("type", "")
            if typ == "ACK":
                self.append_log(f"[ACK] id={obj.get('id','')}")
            elif typ == "STATUS":
                s = obj.get("state","")
                cid = obj.get("id","")
                self.append_log(f"[STATUS] id={cid} state={s} pan={obj.get('pan', '')} tilt={obj.get('tilt','')}")
        except Exception:
            self.append_log(f"[INCOMING RAW] {raw}")

    def closeEvent(self, event):
        self.server.stop()
        event.accept()

# ---------- entry ----------
def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()

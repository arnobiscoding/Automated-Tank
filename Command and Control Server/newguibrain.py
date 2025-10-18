import sys, json, uuid, cv2, numpy as np, time, asyncio, threading
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QTextEdit, QLabel
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtCore import QTimer, Qt, QObject, pyqtSignal, pyqtSlot
import websockets

# CONFIG
WS_PORT = 8080
STEP_RADIUS = 50     # pixels tolerance to consider “centered”
MOVE_SPEED = 2       # degrees per step for directional MOVE

# ---------------- WebSocket SERVER -----------------
class WsServer(QObject):
    sig_log = pyqtSignal(str)

    def __init__(self, port):
        super().__init__()
        self.port = port
        self.loop = asyncio.new_event_loop()
        self.connected_clients = set()
        self.thread = threading.Thread(target=self.run_loop, daemon=True)
        self.thread.start()

    def run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.start_server())
        self.loop.run_forever()

    async def start_server(self):
        # FIX: We use a lambda here to correctly accept the (websocket, path)
        # arguments from websockets.serve and pass them to the instance handler method.
        server = await websockets.serve(
            lambda ws, path: self.handler(ws, path), 
            "0.0.0.0",
            self.port,
            ping_interval=5,
            ping_timeout=2
        )
        self.sig_log.emit(f"[SERVER] Listening on ws://0.0.0.0:{self.port}")
        return server

    async def handler(self, websocket, path): # Correctly accepts 'websocket' and 'path'
        self.connected_clients.add(websocket)
        client_ip = websocket.remote_address[0]
        self.sig_log.emit(f"[CONNECT] Client connected: {client_ip} on path {path}")
        try:
            async for message in websocket:
                self.sig_log.emit(f"[RX] {message}")
                data = json.loads(message)
                if data.get("type")=="HELLO":
                    # Simple HELLO ACK response
                    await websocket.send(json.dumps({"type":"HELLO_ACK"}))
                elif data.get("type")=="STATUS":
                    # Simple logging for status updates from ESP32
                    self.sig_log.emit(f"[STATUS] Cmd {data.get('id')} -> {data.get('state')}")

        except Exception as e:
            if not isinstance(e, (websockets.exceptions.ConnectionClosedOK, websockets.exceptions.ConnectionClosedError)):
                self.sig_log.emit(f"[ERROR] {type(e).__name__}: {e}")
        finally:
            if websocket in self.connected_clients:
                self.connected_clients.remove(websocket)
            self.sig_log.emit(f"[DISCONNECT] Client disconnected: {client_ip}")

    def broadcast_json(self, obj):
        msg = json.dumps(obj)
        # Use a copy of the set in case clients disconnect during broadcast
        for ws in list(self.connected_clients):
            if ws.open:
                try: 
                    # Use asyncio.run_coroutine_threadsafe to send from a different thread (PyQt main thread)
                    future = asyncio.run_coroutine_threadsafe(ws.send(msg), self.loop)
                    # Wait briefly for send to schedule/start (timeout=0.1)
                    future.result(timeout=0.1) 
                except Exception as e: 
                    self.sig_log.emit(f"[ERROR] broadcast: {e}")
        self.sig_log.emit(f"[TX] {msg}")

# ---------------- PyQt5 GUI & CV Tracking -----------------
class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Red Object Tracker (Server Mode)")
        self.setGeometry(200,100,700,600)
        self.vbox = QVBoxLayout(); self.setLayout(self.vbox)
        
        # Video Display
        self.video_label = QLabel(); 
        self.video_label.setFixedSize(640,480); 
        self.video_label.setAlignment(Qt.AlignCenter)
        self.vbox.addWidget(self.video_label)
        
        # Log Console
        self.logview = QTextEdit(); 
        self.logview.setReadOnly(True); 
        self.logview.setMaximumHeight(150);
        self.vbox.addWidget(self.logview)
        
        # Server Initialization
        self.ws_server = WsServer(WS_PORT); 
        self.ws_server.sig_log.connect(self.append_log)
        
        # Camera Setup
        self.cap = cv2.VideoCapture(0); 
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH,640); 
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT,480)
        
        # Tracking State
        self.last_cx,self.last_cy=None,None
        self.last_pan_dir,self.last_tilt_dir="NONE","NONE" # Initialize to NONE
        
        # Timer for Frame Update
        self.timer = QTimer(); 
        self.timer.timeout.connect(self.update_frame); 
        self.timer.start(30) # ~33 FPS

    @pyqtSlot()
    def update_frame(self):
        ret, frame = self.cap.read()
        if not ret: return
        
        # Image Processing for Red Tracking
        frame_blur = cv2.GaussianBlur(frame,(7,7),0)
        hsv=cv2.cvtColor(frame_blur,cv2.COLOR_BGR2HSV)
        h,w,_=frame.shape; center_x,center_y=w//2,h//2
        
        # Red color range definition (two ranges needed for Hue wrap-around)
        lower_red1=np.array([0,80,50]); upper_red1=np.array([10,255,255])
        lower_red2=np.array([170,100,50]); upper_red2=np.array([180,255,255])
        mask1=cv2.inRange(hsv,lower_red1,upper_red1); mask2=cv2.inRange(hsv,lower_red2,upper_red2)
        mask=cv2.bitwise_or(mask1,mask2)
        
        # Morphological operations for noise reduction
        kernel=np.ones((5,5),np.uint8); 
        mask=cv2.morphologyEx(mask,cv2.MORPH_CLOSE,kernel); 
        mask=cv2.morphologyEx(mask,cv2.MORPH_OPEN,kernel)
        
        # Find contours
        contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        
        target_contour=None; max_area=0
        for contour in contours:
            area=cv2.contourArea(contour)
            # Find the largest contour over a minimum size
            if area>300 and area>max_area: 
                max_area=area
                target_contour=contour
                cv2.drawContours(frame,[contour],-1,(0,255,0),1)
                
        # Draw center crosshair
        cv2.circle(frame,(center_x,center_y),6,(0,255,255),-1)
        
        direction="No Target"
        pan_dir,tilt_dir="NONE","NONE"
        cx,cy=None,None
        
        if target_contour is not None:
            # Draw bounding box
            x,y,w_box,h_box=cv2.boundingRect(target_contour)
            cv2.rectangle(frame,(x,y),(x+w_box,y+h_box),(255,0,0),2)
            
            # Calculate centroid
            M=cv2.moments(target_contour)
            if M["m00"]!=0:
                cx=int(M["m10"]/M["m00"])
                cy=int(M["m01"]/M["m00"])
                self.last_cx,self.last_cy=cx,cy
                
                cv2.circle(frame,(cx,cy),6,(0,0,255),-1)
                
                dx,dy=cx-center_x,cy-center_y
                
                # Check if centered within the radius
                if abs(dx)<STEP_RADIUS and abs(dy)<STEP_RADIUS: 
                    direction="Centered ✅"
                    pan_dir,tilt_dir="NONE","NONE" # Stop movement
                else:
                    # Determine directional command based on largest offset
                    if abs(dx)>abs(dy): 
                        pan_dir="LEFT" if dx<0 else "RIGHT"
                        tilt_dir="NONE"
                        direction=f"Pan {pan_dir}"
                    else: 
                        tilt_dir="UP" if dy<0 else "DOWN"
                        pan_dir="NONE"
                        direction=f"Tilt {tilt_dir}"
                
                # Only send MOVE_DIR if direction has changed
                if (pan_dir != self.last_pan_dir) or (tilt_dir != self.last_tilt_dir):
                    msg={
                        "type":"MOVE_DIR",
                        "id":uuid.uuid4().hex[:12], # Unique ID for the command
                        "pan_dir":pan_dir,
                        "tilt_dir":tilt_dir,
                        "speed":MOVE_SPEED
                    }
                    self.ws_server.broadcast_json(msg)
                    self.last_pan_dir = pan_dir
                    self.last_tilt_dir = tilt_dir

        # Display the current tracking status
        cv2.putText(frame,direction,(20,30),cv2.FONT_HERSHEY_SIMPLEX,1,(255,255,255),2)
        
        # Convert OpenCV frame to QPixmap for PyQt display
        rgb=cv2.cvtColor(frame,cv2.COLOR_BGR2RGB); 
        h,w,ch=rgb.shape; 
        bytesPerLine=ch*w
        qt_img=QImage(rgb.data,w,h,bytesPerLine,QImage.Format_RGB888); 
        self.video_label.setPixmap(QPixmap.fromImage(qt_img))

    @pyqtSlot(str)
    def append_log(self,msg): 
        # Ensure only the last 100 lines are kept to prevent memory overload
        self.logview.append(msg)
        cursor = self.logview.textCursor()
        cursor.movePosition(cursor.End)
        self.logview.setTextCursor(cursor)

    # Clean up the camera and server on window close
    def closeEvent(self, event):
        self.cap.release()
        self.ws_server.loop.call_soon_threadsafe(self.ws_server.loop.stop)
        self.ws_server.thread.join(timeout=1)
        super().closeEvent(event)

if __name__=="__main__":
    app=QApplication(sys.argv)
    win=MainWindow(); win.show()
    sys.exit(app.exec_())

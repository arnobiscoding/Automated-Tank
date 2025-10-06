# Automated Tank

This repository contains the code and tools for the "Automated Tank" project: a PyQt-based PC Command & Control GUI that communicates with an ESP32-based servo controller over WebSockets.

Contents

- Command and Control Server/: PyQt GUI and websocket server (server_gui.py / server_gui_2.py)
- ESP32 Servo/: example Arduino/ESP32 code
- ESP32_Servo_Controller/: PlatformIO project for the ESP32 controller

Quick overview

- The PC app (PyQt) runs a WebSocket server which the ESP32 client connects to. The GUI sends MOVE and CANCEL commands and displays incoming STATUS/ACK messages.
- The ESP32 project is a PlatformIO project — build and flash with PlatformIO (CLI or VSCode extension).

Requirements (recommended)

- Windows 10/11 (instructions use PowerShell)
- Python 3.11+ in a virtualenv (this repo uses a `.venv`)
- PlatformIO (for building/flashing ESP32)

Python setup (PowerShell)

1. From repo root:

```powershell
Set-Location -Path "D:\Python Projects\Automated Tank"

# create venv if you don't have one
python -m venv .venv

# activate
& ".\.venv\Scripts\Activate.ps1"

# install pinned dependencies
python -m pip install -r requirements.txt
```

Notes

- If you don't want the full pinned set, install only the GUI server dependencies:

```powershell
python -m pip install websockets PyQt5
```


Run the GUI/WebSocket server

```powershell
# activate venv (if not active)
& ".\.venv\Scripts\Activate.ps1"

# run the GUI server
python -u "d:\Python Projects\Automated Tank\Command and Control Server\server_gui_2.py"
```

The GUI should open and the server will bind on the address printed in the GUI log (default ws://0.0.0.0:8080). Use the displayed bind address for the ESP32 to connect.

ESP32 (PlatformIO) build & flash

Open the `ESP32_Servo_Controller` folder in VSCode with the PlatformIO extension, or use the PlatformIO CLI. From the PlatformIO project root:

```powershell
# build
pio run

# build + upload (adjust environment name in platformio.ini if needed)
pio run -t upload
```

If using `esptool` directly you can also flash a compiled .bin file produced by PlatformIO.

Git / repo tips

- Create a good .gitignore (this repo contains `.gitignore` files for Python, PlatformIO and editors). Confirm `.gitignore` exists before running `git add -A`.

Example first-time push (replace REPO_URL):

```powershell
Set-Location -Path "D:\Python Projects\Automated Tank"

git init
git add .gitignore
git add -A
git commit -m "Initial commit"

git branch -M main
git remote add origin REPO_URL

git push -u origin main
```

Troubleshooting

- PyQt not installed: you'll see `ModuleNotFoundError: No module named 'PyQt5'` — install PyQt5 (or PyQt6 if you prefer and adjust imports) into the venv.
- websockets "no running event loop" error: make sure the server creates and sets an asyncio event loop in the same thread that starts the websockets server (some code versions create the queue or server before the loop is running). If you see this error, ensure the server code initializes the asyncio constructs inside the event loop thread.
- websockets handler signature error (TypeError: missing 1 required positional argument: 'path'): different websockets versions call handlers with slightly different signatures. Make the handler accept an optional `path` (for example: `async def _handler(self, websocket, path=None):`) to be compatible.
- Already committed venv or large build files: if you accidentally committed `.venv` or `.pio` directories, remove them from git history with `git rm -r --cached .venv` and commit.

Project notes / next steps

- If you'd like a headless server mode (run without PyQt installed) I can add a `--headless` flag to `server_gui_2.py` that runs only the WebSocket server and logs to the console.
- If you'd like, I can also prepare a smaller `requirements-gui.txt` and `requirements-dev.txt` for faster installs.


License

- Add your preferred license file (LICENSE) in the repo root.


Contact

- Add any additional project documentation or wiring diagrams into a `docs/` directory when convenient.

---
Generated on: 2025-10-07

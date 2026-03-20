# Kivy RTK App Setup for Raspberry Pi with Display

## 1. Install Kivy Dependencies (SDL2, etc.)

```bash
sudo apt update
sudo apt install -y \
  python3-pip \
  python3-dev \
  libsdl2-dev \
  libsdl2-image-dev \
  libsdl2-mixer-dev \
  libsdl2-ttf-dev \
  libportmidi-dev \
  libswscale-dev \
  libavformat-dev \
  libavcodec-dev \
  zlib1g-dev
```

## 2. Install Python Packages in Virtual Environment

```bash
cd /home/berries/Documents/Peripherals
python3 -m venv kivy_app/.venv
source kivy_app/.venv/bin/activate
pip install --upgrade pip
pip install -r kivy_app/requirements.txt
```

## 3. Run the App

### Option A: Local Desktop (with DISPLAY)
```bash
source kivy_app/.venv/bin/activate
cd kivy_app
python3 app.py
```

### Option B: SSH Session (set DISPLAY remotely)
If SSH'd into RPi, forward X11 or use VNC:

```bash
# X11 forwarding (from your laptop):
ssh -X berries@raspberrypi.local

# Then on RPi:
source /path/to/.venv/bin/activate
cd kivy_app
python3 app.py
```

### Option C: VNC (easiest for RPi)
```bash
# Install and enable VNC on RPi:
sudo apt install realvnc-vnc-server
sudo systemctl enable vncserver-x11-serviced
sudo systemctl start vncserver-x11-serviced

# Then connect from your computer with a VNC client
# and run the app normally
```

## 4. Upload Data

While the Kivy app is running:
1. Open browser: `http://<raspberrypi-ip>:5000/`
2. Upload a CSV file with `lat,lon` per line
3. See points rendered on the map in Kivy window

## Troubleshooting

**Error: `No module named 'kivy'`**
- Ensure you activated the venv: `source kivy_app/.venv/bin/activate`

**Error: `DISPLAY not set` or `cannot connect to X server`**
- Use VNC instead (Option C above), or ensure X11 forwarding is enabled

**Error: SDL2 not found**
- Run the apt install command above for SDL2 dev packages

**Flask port already in use**
- Change port in `uploader_server.py` line `run_server(event, '127.0.0.1', 5001)`

Kivy RTK Visualizer

Overview

This small app runs a local Flask uploader and a Kivy UI to visualize uploaded GPS points.

Usage

1. Install dependencies (prefer a virtualenv):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Run the app (requires GUI/display):

```bash
sudo python3 app.py
```

3. In a browser, open the uploader page:

```
http://127.0.0.1:5000/
```

Upload a CSV file with `lat,lon` per line. The app will display points on the left and generate a button for each point on the right.

Notes

- CSV lines beginning with `#` are ignored.
- This is a minimal local testing tool; for headless Raspberry Pi usage consider running with X forwarding or using Kivy's `--no-window` options and remote display.

Autostart on boot (Raspberry Pi)

To run the app automatically every time the device powers on:

```bash
cd ~/Documents/Peripherals/kivy_app
sudo bash install_autostart.sh
```

Useful commands:

```bash
sudo systemctl status kivy_rtk.service
sudo journalctl -u kivy_rtk.service -f
sudo systemctl disable --now kivy_rtk.service
```

RTCM stream defaults

- The app supports direct RTCM HTTP stream URLs via `RTCM_STREAM_URL`.
- Default stream endpoint is `http://192.168.4.1/rtcm/stream`.
- If `BASE_WIFI_SSID` is left as `CHANGE_ME_SSID`, WiFi auto-connect is skipped and RTCM still starts (assumes networking is already up).

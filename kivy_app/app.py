"""
Kivy application that starts a local uploader (Flask) and visualizes uploaded CSV points.

Usage:
  sudo python3 app.py

Upload page (from another device or localhost):
  http://127.0.0.1:5000/

CSV format expected: each line `lat,lon` (decimal degrees). Lines starting with # ignored.
"""
import threading
import os
import sys
import socket
from pathlib import Path
import time
import math

# Force unbuffered output
sys.stdout = open(sys.stdout.fileno(), mode='w', buffering=1)
sys.stderr = open(sys.stderr.fileno(), mode='w', buffering=1)

from uploader_server import run_server, UPLOAD_DIR
from ints import (
    carr_soln_to_text,
    fix_type_to_text,
    rtk_status_text,
    start_rtcm_wifi_bridge,
)
from display import display_ready
from data import parse_uploaded_points
from network import ensure_base_wifi_connection, ensure_hotspot_mode, switch_hotspot_to_base

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.widget import Widget
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.gridlayout import GridLayout
from kivy.clock import Clock
from kivy.graphics import Color, Ellipse, Rectangle, Line
from kivy.metrics import dp

UPLOAD_FILE = UPLOAD_DIR / "latest.csv"

# ZED-F9P Origin Point (lat, lon) - set to B1 coordinates by default
# Update this to your actual origin/reference point
ORIGIN_LAT = 583083.170476  # B1 latitude
ORIGIN_LON = 1212000.342834  # B1 longitude


def _is_placeholder(value: str) -> bool:
    cleaned = str(value or "").strip().upper()
    return cleaned.startswith("CHANGE_ME") or cleaned in {"", "NONE", "NULL"}


class MapContainer(Widget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.points = []  # list of (lat, lon)
        self.selected = None
        self.rover_pos = None  # (x, y) in local coords
        self.bind(pos=self._redraw, size=self._redraw)

    def set_points(self, points):
        self.points = points
        self.selected = None
        self._redraw()

    def select_index(self, idx):
        if idx is None or idx < 0 or idx >= len(self.points):
            self.selected = None
        else:
            self.selected = idx
        self._redraw()

    def _latlon_to_xy(self, lat, lon, w, h, padding=10):
        if not self.points:
            return (w/2, h/2)
        lats = [p['lat'] for p in self.points]
        lons = [p['lon'] for p in self.points]
        min_lat, max_lat = min(lats), max(lats)
        min_lon, max_lon = min(lons), max(lons)
        # avoid zero span
        lat_span = max(1e-6, max_lat - min_lat)
        lon_span = max(1e-6, max_lon - min_lon)
        # map lon -> x, lat -> y (flip y so north is up)
        x = padding + ((lon - min_lon) / lon_span) * (w - 2*padding)
        y = padding + ((lat - min_lat) / lat_span) * (h - 2*padding)
        return (x, y)

    def _redraw(self, *args):
        self.canvas.clear()
        with self.canvas:
            Color(0.95, 0.95, 0.95)
            Rectangle(pos=self.pos, size=self.size)

            # draw survey points
            w, h = self.size
            for i, pt in enumerate(self.points):
                lat, lon = pt['lat'], pt['lon']
                x_rel, y_rel = self._latlon_to_xy(lat, lon, w, h)
                # convert to absolute position
                x = self.x + x_rel
                y = self.y + y_rel
                if self.selected == i:
                    Color(1, 0, 0)
                    r = dp(10)
                    Ellipse(pos=(x - r/2, y - r/2), size=(r, r))
                    # outline
                    Color(0, 0, 0)
                    Line(circle=(x, y, r/2), width=1)
                else:
                    Color(0, 0.4, 0.8)
                    r = dp(6)
                    Ellipse(pos=(x - r/2, y - r/2), size=(r, r))
            
            # draw rover position (green marker)
            if self.rover_pos:
                x_rel, y_rel = self._latlon_to_xy(self.rover_pos[1], self.rover_pos[0], w, h)
                x = self.x + x_rel
                y = self.y + y_rel
                Color(0, 1, 0)  # green
                r = dp(12)
                Ellipse(pos=(x - r/2, y - r/2), size=(r, r))
                # outline
                Color(0, 0, 0)
                Line(circle=(x, y, r/2), width=2)


class RootWidget(BoxLayout):
    pass


class KivyRTKApp(App):
    def build(self):
        print("\n" + "="*60)
        print("🚀 KIVY BUILD START")
        print("="*60 + "\n")
        
        self.server_event = threading.Event()
        self.rover_position = (0, 0)  # Initialize at 0,0 in local coords (green marker)
        self.rover_gnss = None  # (lat_deg, lon_deg)
        self.rover_fix_type = 0
        self.rover_carr_soln = 0
        self.rover_num_sv = 0
        self.rover_h_acc_m = None
        self.rover_speed_mps = None
        self.zed_connected = False
        self.gnss_popup_shown = False
        self.gnss_reference = None  # {'gnss':(lat,lon), 'local':(x,y)}
        self.rtcm_connected = False
        self.rtcm_bytes_total = 0
        self.rtcm_last_error = ''
        self.rtcm_bridge_started = False
        self.rtcm_stop_event = None
        self.base_wifi_connected = False
        self.base_start_requested = False
        self.last_wifi_attempt_ts = 0.0
        self.wifi_retry_interval = 10.0
        self.rtcm_start_min_fix = 3
        self.base_wifi_ssid = os.environ.get('BASE_WIFI_SSID', 'CHANGE_ME_SSID')
        self.base_wifi_password = os.environ.get('BASE_WIFI_PASSWORD', 'CHANGE_ME_PASSWORD')
        self.base_wifi_interface = os.environ.get('BASE_WIFI_IFACE', 'wlan0')
        self.hotspot_ssid = os.environ.get('ROVER_HOTSPOT_SSID', 'Rover-Upload')
        self.hotspot_password = os.environ.get('ROVER_HOTSPOT_PASSWORD', 'RoverUpload123')
        self.hotspot_connection_name = os.environ.get('ROVER_HOTSPOT_CON_NAME', 'RoverHotspot')
        self.rtcm_host = os.environ.get('RTCM_BASE_HOST', '192.168.4.1')
        self.rtcm_transport = os.environ.get('RTCM_TRANSPORT', 'tcp').strip().lower() or 'tcp'
        if self.rtcm_transport not in ('tcp', 'http'):
            print(f"⚠️  Invalid RTCM_TRANSPORT='{self.rtcm_transport}', defaulting to tcp")
            self.rtcm_transport = 'tcp'
        self.rtcm_stream_url = os.environ.get('RTCM_STREAM_URL', '').strip() or None
        self.rtcm_data_log_path = os.environ.get('RTCM_DATA_LOG_PATH', '').strip() or None
        try:
            self.rtcm_http_poll_interval_s = float(os.environ.get('RTCM_HTTP_POLL_INTERVAL_S', '1.0'))
            if self.rtcm_http_poll_interval_s <= 0:
                self.rtcm_http_poll_interval_s = None
        except ValueError:
            self.rtcm_http_poll_interval_s = 1.0

        if self.rtcm_transport == 'http':
            if self.rtcm_stream_url is None and '/' in self.rtcm_host:
                self.rtcm_stream_url = self.rtcm_host
            if not self.rtcm_stream_url:
                print('⚠️  RTCM_TRANSPORT=http requires RTCM_STREAM_URL; falling back to tcp')
                self.rtcm_transport = 'tcp'
        else:
            if self.rtcm_stream_url:
                print('ℹ️  RTCM_STREAM_URL is set but ignored in TCP mode')
            self.rtcm_stream_url = None
            self.rtcm_http_poll_interval_s = None

        self.auto_manage_wifi = not _is_placeholder(self.base_wifi_ssid)
        raw_hosts = os.environ.get('RTCM_BASE_HOSTS', '').strip()
        self.rtcm_hosts = [h.strip() for h in raw_hosts.split(',') if h.strip()]
        if self.rtcm_host and self.rtcm_host not in self.rtcm_hosts:
            self.rtcm_hosts.insert(0, self.rtcm_host)
        try:
            self.rtcm_port = int(os.environ.get('RTCM_BASE_PORT', '2101'))
        except ValueError:
            self.rtcm_port = 2101
        rtcm_mode = 'TCP'
        if self.rtcm_transport == 'http' and self.rtcm_stream_url:
            rtcm_mode = 'POLL' if self.rtcm_http_poll_interval_s is not None else 'STREAM'
        rtcm_target = self.rtcm_stream_url if self.rtcm_transport == 'http' else f'{self.rtcm_host}:{self.rtcm_port}'
        rtcm_data_log = self.rtcm_data_log_path or 'disabled'
        rtcm_poll_text = self.rtcm_http_poll_interval_s if self.rtcm_http_poll_interval_s is not None else 'disabled'
        print(f'ℹ️  RTCM startup: mode={rtcm_mode} target={rtcm_target} poll_interval_s={rtcm_poll_text} data_log={rtcm_data_log}')
        
        # start uploader server thread (auto-pick free port)
        self.upload_host = '127.0.0.1'
        self.upload_port = self._pick_free_port([5000, 5001, 5002, 5003])
        self.upload_url = f'http://{self.upload_host}:{self.upload_port}/'
        t = threading.Thread(
            target=run_server,
            args=(self.server_event, self.upload_host, self.upload_port),
            kwargs={"on_start_action": self._start_base_mode_from_web},
            daemon=True,
        )
        t.start()
        print(f"✅ Flask server started on {self.upload_url}")

        hotspot_enabled = ensure_hotspot_mode(
            ssid=self.hotspot_ssid,
            password=self.hotspot_password,
            interface=self.base_wifi_interface,
            connection_name=self.hotspot_connection_name,
        )
        if hotspot_enabled:
            print(f"✅ Hotspot mode active for upload (SSID '{self.hotspot_ssid}')")
        else:
            print('⚠️  Hotspot mode not active; local upload page is still available')

        if self.auto_manage_wifi:
            print("ℹ️  Waiting for web 'Start' action to switch from hotspot to base WiFi")
        else:
            print("ℹ️  BASE_WIFI_SSID placeholder detected; 'Start' action will likely fail until configured")

        # start ZED-F9P reader thread
        gnss_thread = threading.Thread(target=self._read_zed_f9p_loop, daemon=True)
        gnss_thread.start()
        print("✅ ZED-F9P reader thread started")

        # load kv
        from kivy.lang import Builder
        kv_path = Path(__file__).parent / 'map.kv'
        print(f"📂 Loading KV from: {kv_path}")
        try:
            Builder.load_file(str(kv_path))
            print("✅ KV loaded successfully")
        except Exception as e:
            print(f"❌ Error loading KV: {e}")

        self.root = RootWidget()
        self.map_container = self.root.ids.map_container
        self.buttons_grid = self.root.ids.buttons_grid
        self.status_label = self.root.ids.status_label
        # reference buttons
        try:
            self.set_ref_btn = self.root.ids.set_ref_btn
            self.clear_ref_btn = self.root.ids.clear_ref_btn
            self.set_ref_btn.bind(on_press=lambda _: self._set_gnss_reference())
            self.clear_ref_btn.bind(on_press=lambda _: self._clear_gnss_reference())
        except Exception:
            pass
        print("✅ Kivy widgets initialized")
        
        # Add a TEST button to verify grid works
        print("🧪 Adding test button to verify GridLayout...")
        test_btn = Button(text="TEST BUTTON", size_hint_x=1, size_hint_y=None, height=dp(40))
        test_btn.bind(on_press=lambda inst: print("✅ TEST BUTTON CLICKED"))
        self.buttons_grid.add_widget(test_btn)
        print("✅ Test button added to grid")
        
        # Try loading existing CSV on startup
        print(f"\n📂 CSV file path: {UPLOAD_FILE}")
        print(f"📂 CSV file exists: {UPLOAD_FILE.exists()}")
        if not UPLOAD_FILE.exists():
            print("⚠️  latest.csv is missing. Waiting for user upload.")
            self.status_label.text = f'Status: latest.csv missing. Upload at {self.upload_url}'
            Clock.schedule_once(lambda dt: self._prompt_upload_required(), 0.1)
        else:
            print("📂 Attempting to parse CSV...")
            sys.stdout.flush()
            sys.stderr.flush()

            existing_points = self._parse_uploaded_file()

            print(f"📂 Parse result: {len(existing_points)} points")
            sys.stdout.flush()

            if existing_points:
                print(f"📍 Found {len(existing_points)} points in existing CSV, loading...")
                # Remove test button before loading real data
                self.buttons_grid.clear_widgets()
                self._update_points(existing_points)
                self.status_label.text = f'Status: loaded {len(existing_points)} points (startup)'
            else:
                self.status_label.text = 'Status: latest.csv found, but no valid points'
                print("⚠️  latest.csv exists but no valid points were parsed.")

        # poll for upload event
        Clock.schedule_interval(self._poll_upload_event, 0.5)
        Clock.schedule_interval(self._monitor_gnss_status, 1.0)
        # update rover position on map
        Clock.schedule_interval(self._update_rover_on_map, 1.0)
        
        print("\n" + "="*60)
        print("🚀 KIVY BUILD COMPLETE")
        print("="*60 + "\n")
        return self.root

    def _prompt_upload_required(self):
        content = BoxLayout(orientation='vertical', spacing=dp(8), padding=dp(10))
        message = Label(
            text=f'latest.csv was not found in uploads.\nPlease upload a file at:\n{self.upload_url}',
            halign='center',
            valign='middle'
        )
        message.bind(size=lambda instance, value: setattr(instance, 'text_size', value))
        close_btn = Button(text='OK', size_hint_y=None, height=dp(40))
        content.add_widget(message)
        content.add_widget(close_btn)

        popup = Popup(
            title='Upload Required',
            content=content,
            size_hint=(0.8, 0.45),
            auto_dismiss=False
        )
        close_btn.bind(on_press=popup.dismiss)
        popup.open()

    def _prompt_gnss_required(self):
        content = BoxLayout(orientation='vertical', spacing=dp(8), padding=dp(10))
        message = Label(
            text='ZED-F9P must be ON and in FIX state.\nCheck /dev/ttyACM0 and wait for 3D fix.',
            halign='center',
            valign='middle'
        )
        message.bind(size=lambda instance, value: setattr(instance, 'text_size', value))
        close_btn = Button(text='OK', size_hint_y=None, height=dp(40))
        content.add_widget(message)
        content.add_widget(close_btn)

        popup = Popup(
            title='GNSS Required',
            content=content,
            size_hint=(0.8, 0.4),
            auto_dismiss=False
        )
        close_btn.bind(on_press=popup.dismiss)
        popup.open()

    def _pick_free_port(self, candidates):
        for port in candidates:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    sock.bind((self.upload_host if hasattr(self, 'upload_host') else '127.0.0.1', port))
                    return port
                except OSError:
                    continue
        raise RuntimeError("No free upload port found in candidate list")

    def _has_gnss_fix(self):
        return self.zed_connected and self.rover_fix_type >= 3

    def _rtcm_status_text(self):
        target = self.rtcm_stream_url if self.rtcm_transport == 'http' else f'{self.rtcm_host}:{self.rtcm_port}'
        if not self.rtcm_bridge_started:
            return 'RTCM HOLD (waiting WiFi/base)'
        if self.rtcm_connected:
            return f'RTCM OK ({self.rtcm_bytes_total} B)'
        return f'RTCM WAIT ({target})'

    def _try_start_base_and_rtcm(self, ser):
        if self.rtcm_bridge_started:
            return

        if not self.base_start_requested:
            return

        if self.auto_manage_wifi:
            now = time.time()
            if now - self.last_wifi_attempt_ts >= self.wifi_retry_interval:
                self.last_wifi_attempt_ts = now
                self.base_wifi_connected = ensure_base_wifi_connection(
                    ssid=self.base_wifi_ssid,
                    password=self.base_wifi_password,
                    interface=self.base_wifi_interface,
                )
        else:
            self.base_wifi_connected = True

        if not self.base_wifi_connected:
            return

        self.rtcm_stop_event, _rtcm_thread = start_rtcm_wifi_bridge(
            ser,
            host=self.rtcm_host,
            port=self.rtcm_port,
            stream_url=self.rtcm_stream_url if self.rtcm_transport == 'http' else None,
            http_poll_interval_s=self.rtcm_http_poll_interval_s if self.rtcm_transport == 'http' else None,
            data_log_path=self.rtcm_data_log_path,
            status_callback=self._on_rtcm_status_update,
            hosts=self.rtcm_hosts,
        )
        self.rtcm_bridge_started = True
        print('✅ RTCM bridge started (GNSS connected and base WiFi connected)')

    def _start_base_mode_from_web(self):
        if not self.auto_manage_wifi:
            return False, 'BASE_WIFI_SSID is placeholder. Configure base credentials first.'

        ok, message = switch_hotspot_to_base(
            ssid=self.base_wifi_ssid,
            password=self.base_wifi_password,
            interface=self.base_wifi_interface,
        )
        if ok:
            self.base_start_requested = True
            self.base_wifi_connected = True
            self.last_wifi_attempt_ts = time.time()
            print('✅ Web Start: hotspot disabled and base WiFi requested')
            return True, message

        self.base_wifi_connected = False
        print(f'⚠️  Web Start failed: {message}')
        return False, message

    def _position_status_text(self):
        if not self.rover_gnss:
            return 'Pos: waiting GNSS'

        lat, lon = self.rover_gnss
        metrics = []
        if self.rover_num_sv:
            metrics.append(f'SVs={self.rover_num_sv}')
        if self.rover_h_acc_m is not None:
            metrics.append(f'hAcc={self.rover_h_acc_m:.3f}m')
        if self.rover_speed_mps is not None:
            metrics.append(f'Spd={self.rover_speed_mps:.2f}m/s')
        if metrics:
            return f'Pos: {lat:.7f}, {lon:.7f} | ' + ' '.join(metrics)
        return f'Pos: {lat:.7f}, {lon:.7f}'

    def _on_rtcm_status_update(self, status):
        connected = status.get('connected')
        bytes_total = status.get('bytes_total')
        error = status.get('error')
        host = status.get('host')
        endpoint = status.get('endpoint')
        if connected is not None:
            self.rtcm_connected = bool(connected)
        if bytes_total is not None:
            self.rtcm_bytes_total = int(bytes_total)
        if error is not None:
            self.rtcm_last_error = str(error)
        if host:
            self.rtcm_host = str(host)
        if endpoint:
            self.rtcm_stream_url = str(endpoint)

    def _monitor_gnss_status(self, dt):
        if self._has_gnss_fix():
            self.gnss_popup_shown = False
            position_text = self._position_status_text()
            self.status_label.text = (
                f'Status: {rtk_status_text(self.rover_fix_type, self.rover_carr_soln)} ({fix_type_to_text(self.rover_fix_type)}, {carr_soln_to_text(self.rover_carr_soln)}) | {self._rtcm_status_text()}\n{position_text}'
            )
            return

        if self.zed_connected:
            position_text = self._position_status_text()
            self.status_label.text = (
                f'Status: ZED-F9P connected, waiting fix ({rtk_status_text(self.rover_fix_type, self.rover_carr_soln)}, {fix_type_to_text(self.rover_fix_type)}, {carr_soln_to_text(self.rover_carr_soln)}) | {self._rtcm_status_text()}\n{position_text}'
            )
        else:
            self.status_label.text = 'Status: ZED-F9P not connected. Turn it on and check GNSS_PORTS (default /dev/ttyACM0,/dev/ttyACM1)'

        if not self.gnss_popup_shown:
            self.gnss_popup_shown = True
            Clock.schedule_once(lambda _dt: self._prompt_gnss_required(), 0.1)

    def _poll_upload_event(self, dt):
        if self.server_event.is_set():
            self.server_event.clear()
            print("📡 Upload event detected!")
            self.status_label.text = 'Status: new upload detected'
            points = self._parse_uploaded_file()
            if points:
                self._update_points(points)
                self.status_label.text = f'Status: loaded {len(points)} points'
                print(f"✅ Updated UI with {len(points)} points")
            else:
                self.status_label.text = 'Status: no valid points found'
                print("❌ No valid points parsed from CSV")

    def _parse_uploaded_file(self):
        return parse_uploaded_points(UPLOAD_FILE, UPLOAD_DIR)

    def _update_points(self, points):
        # update map
        self.map_container.set_points(points)
        # regenerate buttons
        self.buttons_grid.clear_widgets()
        for i, pt in enumerate(points):
            name = pt['name']
            lat = pt['lat']
            lon = pt['lon']
            b = Button(
                text=f"{name} ({lat:.5f}, {lon:.5f})",
                size_hint_x=1,
                size_hint_y=None,
                height=dp(40)
            )
            # capture index in default arg to avoid late-binding in lambda
            b.bind(on_press=lambda inst, idx=i: self._on_point_button(idx))
            self.buttons_grid.add_widget(b)
        
        # Trigger layout recalculation
        Clock.schedule_once(lambda dt: setattr(self.buttons_grid, 'height', self.buttons_grid.minimum_height), 0.01)
        print(f"✅ Added {len(points)} buttons to grid")

    def _on_point_button(self, idx):
        self.map_container.select_index(idx)
        if 0 <= idx < len(self.map_container.points):
            point = self.map_container.points[idx]
            name = point.get('name', f'Point {idx + 1}')
            self.status_label.text = f'Selected: {name}'

            rover_pos = self.map_container.rover_pos
            if rover_pos is None:
                print(f"📏 Distance to {name}: unavailable (current rover position not ready)")
                return

            rover_x, rover_y = rover_pos
            point_x = point.get('lon')
            point_y = point.get('lat')
            if point_x is None or point_y is None:
                print(f"📏 Distance to {name}: unavailable (invalid point coordinates)")
                return

            distance = math.hypot(point_x - rover_x, point_y - rover_y)
            print(f"📏 Distance to {name}: {distance:.2f} m")
            self.status_label.text = f'Selected: {name} | Distance: {distance:.2f} m'
            self._show_distance_popup(name, distance)
        else:
            self.status_label.text = f'Selected point {idx + 1}'

    def _show_distance_popup(self, name, distance_m):
        content = BoxLayout(orientation='vertical', spacing=dp(8), padding=dp(10))
        message = Label(
            text=f'{name}\nDistance: {distance_m:.2f} m',
            halign='center',
            valign='middle'
        )
        message.bind(size=lambda instance, value: setattr(instance, 'text_size', value))
        close_btn = Button(text='OK', size_hint_y=None, height=dp(40))
        content.add_widget(message)
        content.add_widget(close_btn)

        popup = Popup(
            title='Point Distance',
            content=content,
            size_hint=(0.65, 0.35),
            auto_dismiss=False
        )
        close_btn.bind(on_press=popup.dismiss)
        popup.open()

    def _read_zed_f9p_loop(self):
        """Background thread: read ZED-F9P position periodically"""
        import serial
        try:
            from pyubx2 import UBXReader
        except ImportError:
            print("⚠️  pyubx2 not installed, skipping ZED-F9P reader")
            return
        
        raw_ports = os.environ.get('GNSS_PORTS', '/dev/ttyACM0,/dev/ttyACM1')
        ports = [port.strip() for port in raw_ports.split(',') if port.strip()]
        if not ports:
            ports = ['/dev/ttyACM0']
        BAUD = 115200
        
        while True:
            ser = None
            try:
                connected_port = None
                for port in ports:
                    try:
                        print(f"📡 Attempting to connect to ZED-F9P on {port}...")
                        ser = serial.Serial(port, BAUD, timeout=1)
                        connected_port = port
                        break
                    except Exception as port_exc:
                        print(f"⚠️  Could not open {port}: {port_exc}")

                if ser is None:
                    raise RuntimeError(f'Unable to open GNSS port(s): {", ".join(ports)}')

                self.rtcm_connected = False
                self.rtcm_bytes_total = 0
                self.rtcm_bridge_started = False
                self.base_wifi_connected = False
                self.last_wifi_attempt_ts = 0.0
                self.rtcm_stop_event = None
                ubr = UBXReader(ser, protfilter=3)
                self.zed_connected = True
                print(f"✅ Connected to ZED-F9P on {connected_port}")
                
                while True:
                    try:
                        _raw, msg = ubr.read()
                        if msg and msg.identity == "NAV-PVT":
                            # NAV-PVT units: lat/lon already decimal degrees in pyubx2,
                            # hAcc/gSpeed are mm and mm/s.
                            lat = msg.lat * 1e-7
                            lon = msg.lon * 1e-7
                            self.rover_fix_type = getattr(msg, 'fixType', 0)
                            self.rover_carr_soln = getattr(msg, 'carrSoln', 0)
                            self.rover_num_sv = getattr(msg, 'numSV', 0)
                            self.rover_h_acc_m = getattr(msg, 'hAcc', 0) / 1000.0
                            self.rover_speed_mps = getattr(msg, 'gSpeed', 0) / 1000.0
                            # store GNSS lat/lon
                            self.rover_gnss = (lat, lon)
                            self._try_start_base_and_rtcm(ser)
                            print(
                                f"📡 [NAV-PVT] Fix: {fix_type_to_text(self.rover_fix_type):12} | "
                                f"RTK: {carr_soln_to_text(self.rover_carr_soln):10} | "
                                f"Sats: {self.rover_num_sv:2} | "
                                f"hAcc: {self.rover_h_acc_m:.3f} m | "
                                f"Speed: {self.rover_speed_mps:.2f} m/s | "
                                f"Pos: ({lat:.7f}, {lon:.7f})"
                            )
                    except Exception as read_exc:
                        print(f"⚠️  GNSS read warning: {read_exc}")
                        continue
            except Exception as e:
                try:
                    if self.rtcm_stop_event is not None:
                        self.rtcm_stop_event.set()
                except Exception:
                    pass
                try:
                    if ser is not None:
                        ser.close()
                except Exception:
                    pass
                self.zed_connected = False
                self.rtcm_connected = False
                self.rtcm_bridge_started = False
                self.base_wifi_connected = False
                self.rover_fix_type = 0
                self.rover_carr_soln = 0
                self.rover_num_sv = 0
                self.rover_h_acc_m = None
                self.rover_speed_mps = None
                print(f"⚠️  ZED-F9P error: {e}")
                time.sleep(5)

    def _update_rover_on_map(self, dt):
        """Update rover marker on map (called by Clock scheduler)"""
        if not hasattr(self, 'map_container'):
            return
        # If we have a GNSS->local reference and GNSS fix, compute local coords
        if self.gnss_reference and self.rover_gnss:
            lat0, lon0 = self.gnss_reference['gnss']
            x0, y0 = self.gnss_reference['local']
            lat, lon = self.rover_gnss
            # degrees to meters approx
            meters_per_deg_lat = 111132.954 - 559.822 * math.cos(2 * math.radians(lat0)) + 1.175 * math.cos(4 * math.radians(lat0))
            meters_per_deg_lon = (math.pi/180) * 6378137 * math.cos(math.radians(lat0))
            dx = (lon - lon0) * meters_per_deg_lon
            dy = (lat - lat0) * meters_per_deg_lat
            x_local = x0 + dx
            y_local = y0 + dy
            self.map_container.rover_pos = (x_local, y_local)
            self.map_container._redraw()
        elif hasattr(self, 'map_container') and self.rover_position:
            self.map_container.rover_pos = self.rover_position
            self.map_container._redraw()

    def _set_gnss_reference(self):
        # Use currently selected point as reference if we have GNSS fix
        if not hasattr(self, 'map_container'):
            return
        idx = self.map_container.selected
        if idx is None:
            self.status_label.text = 'Select a point first'
            return
        if not self._has_gnss_fix() or not self.rover_gnss:
            self.status_label.text = 'ZED-F9P must be ON and in FIX state'
            return
        pt = self.map_container.points[idx]
        # pt stores local as {'name','lat'(y),'lon'(x)}
        x_local = pt['lon']
        y_local = pt['lat']
        self.gnss_reference = {'gnss': self.rover_gnss, 'local': (x_local, y_local)}
        self.status_label.text = f'Ref set: {pt["name"]}'
        print(f"🔧 GNSS reference set: GNSS={self.rover_gnss}, LOCAL=({x_local},{y_local})")

    def _clear_gnss_reference(self):
        self.gnss_reference = None
        self.status_label.text = 'Ref cleared'
        print('🔧 GNSS reference cleared')


if __name__ == '__main__':
    # ensure uploads dir exists
    UPLOAD_DIR.mkdir(exist_ok=True)
    if not display_ready():
        sys.exit(1)
    KivyRTKApp().run()

import shutil
import threading
import time
from pathlib import Path

from data import parse_uploaded_points
from network import ensure_base_wifi_connection, ensure_hotspot_mode, switch_hotspot_to_base
from uploader_server import run_server

from kivy.clock import Clock

BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_FILE = UPLOAD_DIR / "latest.csv"


class NetworkMixin:

    def _startup_hotspot_thread(self):
        ok = ensure_hotspot_mode(
            ssid=self.hotspot_ssid,
            password=self.hotspot_password,
            interface=self.hotspot_interface,
            connection_name=self.hotspot_connection_name,
        )
        Clock.schedule_once(lambda dt: self._on_hotspot_ready(ok), 0)

    def _on_hotspot_ready(self, ok):
        if ok:
            self._set_main_status(
                f"Hotspot active: {self.hotspot_ssid} | Upload: http://{self.hotspot_gateway}:{self.web_port}/"
            )
            self._start_web_uploader_server()
        else:
            self._set_main_status("Failed to activate hotspot. Check nmcli permissions.")
        self.root.current = "main_menu"

    def open_csv_upload(self):
        if self.network_transition_running:
            return
        self.network_transition_running = True
        self._set_main_status("Switching to hotspot for CSV upload...")
        self._set_csv_status("Activating rover hotspot...")
        threading.Thread(target=self._switch_to_hotspot_for_upload_thread, daemon=True).start()

    def import_selected_csv(self):
        csv_screen = self.root.get_screen("csv_upload")
        chooser = csv_screen.csv_chooser
        selected = chooser.selection[0] if chooser.selection else None

        if not selected:
            self._set_csv_status("No file selected")
            return

        try:
            source = Path(selected)
            if source.suffix.lower() != ".csv":
                self._set_csv_status("Selected file is not a CSV")
                return
            UPLOAD_DIR.mkdir(exist_ok=True)
            shutil.copy2(source, UPLOAD_FILE)
            points = self._parse_uploaded_file()
            if not points:
                self._set_csv_status("CSV imported but no valid points were found")
                return
            self._update_points(points)
            self._set_csv_status(f"Imported {len(points)} points")
            self._set_main_status(f"CSV ready ({len(points)} points)")
        except Exception as exc:
            self._set_csv_status(f"Import failed: {exc}")

    def connect_to_base_station(self):
        if self.network_transition_running:
            return
        if not self._base_wifi_is_configured():
            self._set_main_status(
                "Base WiFi not configured. Check base_wifi_ssid and base_wifi_password in app.py."
            )
            return
        self.network_transition_running = True
        self._set_main_status("Switching from hotspot to base station...")
        threading.Thread(target=self._connect_base_thread, daemon=True).start()

    def _connect_base_thread(self):
        ok, message = switch_hotspot_to_base(
            ssid=self.base_wifi_ssid,
            password=self.base_wifi_password,
            interface=self.base_wifi_interface,
        )
        Clock.schedule_once(lambda dt: self._on_base_connected(ok, message), 0)

    def _switch_to_hotspot_for_upload_thread(self):
        try:
            self._stop_rtcm_bridge_for_network_switch()
            ok = ensure_hotspot_mode(
                ssid=self.hotspot_ssid,
                password=self.hotspot_password,
                interface=self.hotspot_interface,
                connection_name=self.hotspot_connection_name,
            )
            message = (
                f"Hotspot active: {self.hotspot_ssid}"
                if ok
                else "Failed to activate hotspot. Check nmcli permissions."
            )
        except Exception as exc:
            ok = False
            message = f"Hotspot switch failed: {exc}"
        Clock.schedule_once(lambda _dt: self._on_hotspot_for_upload_ready(ok, message), 0)

    def _on_hotspot_for_upload_ready(self, ok, message):
        self.network_transition_running = False
        self.base_wifi_connected = False
        self.base_start_requested = False
        self.heading_walk_prompt_active = False

        if not ok:
            self._set_main_status(message)
            self._set_csv_status(message)
            self.root.current = "main_menu"
            return

        self._set_main_status(message)
        self.root.current = "csv_upload"
        upload_url = f"http://{self.hotspot_gateway}:{self.web_port}/upload"
        self._set_csv_status(f"Local import or external upload: {upload_url}")

    def _on_base_connected(self, ok, message):
        self.network_transition_running = False
        if not ok:
            self.base_wifi_connected = False
            self.heading_walk_prompt_active = False
        self._set_main_status(message)

        self.base_start_requested = True
        self.base_wifi_connected = True
        self.heading_walk_prompt_active = True
        self.last_wifi_attempt_ts = time.time()
        self._set_main_status(message)
        self.root.current = "existing_flow"
        self.status_label.text = (
            "Status: Base station connected. Cekam na RTCM, pak jdi rovne pro heading lock."
        )

    def _base_wifi_is_configured(self):
        return bool(self.base_wifi_ssid) and bool(self.base_wifi_password)

    def _load_points_from_latest_csv(self, initial=False):
        if not UPLOAD_FILE.exists():
            if initial:
                self._set_main_status("Import a CSV before starting base station flow")
            return
        points = self._parse_uploaded_file()
        if points:
            self._update_points(points)
            if initial:
                self._set_main_status(f"Loaded {len(points)} points from latest.csv")

    def _start_web_uploader_server(self):
        if self.web_server_started:
            return
        self.web_server_started = True
        thread = threading.Thread(
            target=run_server,
            args=(self.upload_signal_event,),
            kwargs={
                "host": "0.0.0.0",
                "port": self.web_port,
                "on_start_action": self._start_rtk_from_web,
                "base_ssid": self.base_wifi_ssid,
                "hotspot_gateway": self.hotspot_gateway,
            },
            daemon=True,
        )
        thread.start()

    def _start_rtk_from_web(self):
        if self.network_transition_running:
            return False, "RTK start is already in progress"
        if not self._base_wifi_is_configured():
            Clock.schedule_once(
                lambda _dt: self._set_main_status(
                    "Base WiFi not configured. Check base_wifi_ssid and base_wifi_password in app.py."
                ),
                0,
            )
            return False, "Base WiFi not configured"
        Clock.schedule_once(lambda _dt: self.connect_to_base_station(), 0)
        return True, "RTK transition started (hotspot will disconnect)"

    def _poll_uploaded_csv_signal(self, _dt):
        if not self.upload_signal_event.is_set():
            return
        self.upload_signal_event.clear()
        points = self._parse_uploaded_file()
        if not points:
            self._set_main_status("Uploaded CSV has no valid points")
            return
        self._update_points(points)
        self._set_main_status(f"Loaded {len(points)} uploaded points")

    def _monitor_base_wifi(self, _dt):
        if not self.base_start_requested:
            return
        if not self._base_wifi_is_configured():
            return
        if self.network_transition_running or self.wifi_check_in_progress:
            return

        now = time.time()
        if (now - self.last_wifi_attempt_ts) < self.wifi_retry_interval:
            return

        self.last_wifi_attempt_ts = now
        self.wifi_check_in_progress = True
        threading.Thread(target=self._base_wifi_watchdog_thread, daemon=True).start()

    def _base_wifi_watchdog_thread(self):
        connected = ensure_base_wifi_connection(
            ssid=self.base_wifi_ssid,
            password=self.base_wifi_password,
            interface=self.base_wifi_interface,
            max_attempts=3,
            retry_delay_s=2.0,
            verbose=False,
        )
        Clock.schedule_once(lambda _dt: self._on_base_wifi_watchdog_result(connected), 0)

    def _on_base_wifi_watchdog_result(self, connected):
        previous = self.base_wifi_connected
        self.base_wifi_connected = bool(connected)
        self.wifi_check_in_progress = False
        if connected and not previous:
            self._set_main_status(f"Reconnected to base SSID '{self.base_wifi_ssid}'")
        elif not connected and previous:
            self._set_main_status(f"Lost base SSID '{self.base_wifi_ssid}', retrying...")

    def _parse_uploaded_file(self):
        return parse_uploaded_points(UPLOAD_FILE, UPLOAD_DIR)

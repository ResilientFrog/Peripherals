import sys
import threading
import time
from pathlib import Path

from display import display_ready
from display.widgets import HeadingWidget, MapContainer  # noqa: F401 – required by map.kv
from ints.gnss_reader import GnssReaderMixin
from ints.bno_reader import BnoReaderMixin
from nav_mixin import NavMixin
from network_mixin import NetworkMixin

from kivy.app import App
from kivy.clock import Clock
from kivy.lang import Builder
from kivy.metrics import dp
from kivy.properties import ObjectProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import Screen, ScreenManager

try:
    sys.stdout = open(sys.stdout.fileno(), mode="w", buffering=1)
    sys.stderr = open(sys.stderr.fileno(), mode="w", buffering=1)
except Exception:
    pass

BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_FILE = UPLOAD_DIR / "latest.csv"



class RootWidget(BoxLayout):
    pass


class AppRoot(ScreenManager):
    pass


class StartupScreen(Screen):
    startup_status = ObjectProperty(None)


class MainMenuScreen(Screen):
    network_status = ObjectProperty(None)


class CsvUploadScreen(Screen):
    csv_chooser = ObjectProperty(None)
    csv_status = ObjectProperty(None)


class ExistingFlowScreen(Screen):
    pass


class KivyRTKApp(App, GnssReaderMixin, BnoReaderMixin, NavMixin, NetworkMixin):
    def _log_app_start_marker(self):
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        marker = (
            f"{ts} [APP] start KivyRTKApp "
            f"(rtcm={self.rtcm_host}:{self.rtcm_port}, "
            f"wifi_if={self.base_wifi_interface}, hotspot_if={self.hotspot_interface})\n"
        )
        try:
            Path(self.rtcm_log_path).parent.mkdir(parents=True, exist_ok=True)
            with open(self.rtcm_log_path, "a", encoding="utf-8") as f:
                f.write(marker)
        except Exception:
            pass

    def build(self):
        
        self.network_transition_running = False
        self.rover_position = (0, 0)
        self.rover_gnss = None
        self.rover_gnss_raw = None
        self.rover_gnss_quality_ok = False
        self.rover_gnss_hold_reason = ""
        self.nav_require_rtk_fixed = False
        self.nav_max_h_acc_m = None
        self.nav_hold_last_good_gnss = True
        self.rover_fix_type = 0
        self.rover_carr_soln = 0
        self.rover_num_sv = 0
        self.rover_h_acc_m = None
        self.rover_heading_deg = None
        self.rover_heading_cardinal = None
        self.rover_north_offset_deg = None
        self.nav_pvt_heading_deg = None
        self.nav_pvt_heading_ts = 0.0
        self.nav_pvt_heading_valid = False
        self.nav_pvt_heading_smoothed_deg = None
        self.nav_pvt_min_baseline_m = 0.30
        self.nav_pvt_fresh_timeout_s = 2.5
        self.nav_pvt_heading_alpha = 0.35
        self.nav_pvt_bno_match_threshold_deg = 35.0
        self.nav_pvt_bno_diff_deg = None
        self.nav_pvt_bno_match_ok = None
        self.nav_pvt_anchor_bno_heading_deg = None
        self.nav_pvt_anchor_ts = 0.0
        self.nav_pvt_fused_heading_deg = None
        self.gnss_heading_lock_deg = None
        self.gnss_heading_lock_cardinal = None
        self.gnss_heading_lock_ts = 0.0
        self.rover_speed_ms = None
        self._prev_gnss_for_heading = None
        self._stored_heading_offset = None  # Store GNSS heading lock here
        self._widget_heading_smoothed = None
        self._widget_heading_alpha = 0.75
        self._last_angle_log_ts = 0.0
        self._last_angle_log_value = None
        self._last_visual_heading_deg = None
        self._heading_hold_active = False
        self._heading_ever_visible = False
        self.rb_selected_idx = None
        self.rb_selected_name = None
        self.rb_distance_m = None
        self.rb_bearing_deg = None
        self.rb_confirmed = False
        self.b0_confirmed_once = False
        self.rb_confirm_threshold_m = 1.0
        # Compensate GNSS bias by translating all CSV points by B0 delta.
        # This keeps local distances/angles between points unchanged.
        self.apply_b0_shift_to_points = True
        self.rb_origin_gnss = None
        self.rb_local_origin = (0.0, 0.0)
        self.csv_b0_lat = None
        self.csv_b0_lon = None
        self.nav_target_idx = None
        self.nav_target_name = None
        self.nav_distance_m = None
        self.nav_bearing_deg = None
        self.nav_auto_confirm_enabled = False
        self.nav_auto_confirm_distance_m = 0.05
        self.nav_auto_confirm_hold_s = 1.5
        self.nav_manual_confirm_distance_m = 0.05
        self.nav_target_dwell_start_ts = 0.0
        self.nav_target_reached = False
        self.nav_target_reached_ts = 0.0
        self.nav_target_in_zone = False
        self.nav_confirmed_targets = set()
        self.nav_reference_bearings = {}
        self.nav_vector_samples = []
        self.nav_vector_samples_max = 60
        self.nav_guidance_smoothed_bearing_deg = None
        self.nav_guidance_smoothed_steer_deg = None
        self.nav_guidance_smooth_alpha = 0.55
        self.nav_guidance_max_turn_rate_deg_s = 260.0
        self.nav_guidance_close_alpha = 0.25
        self.nav_guidance_close_distance_m = 1.0
        self.nav_guidance_hold_distance_m = 0.20
        self.nav_last_update_ts = 0.0
        self.nav_blink_hz = 2.0
        self.nav_stale_after_s = 0.8
        self.nav_estimate_max_horizon_s = 3.0
        self.nav_last_live_pos = None
        self.nav_last_live_pos_ts = 0.0
        self.nav_prev_live_pos = None
        self.nav_prev_live_pos_ts = 0.0
        self.nav_estimated_pos = None
        self.nav_last_used_estimation = False
        self.nav_display_bearing_deg = None

        self.bno_connected = False
        self.bno_heading_deg = None
        self.bno_heading_cardinal = None
        self.bno_quaternion = None
        self.bno_first_heading_deg = None
        self.bno_first_is_valid = False
        self.bno_anchor_first_heading_deg = None
        self.bno_rebase_delta_deg = 0.0
        self.bno_restart_calib_pos = None
        self.bno_move_prompt_active = False
        self.bno_move_prompt_threshold_m = 0.30
        self.bno_recalc_min_baseline_m = 0.70
        self.bno_last_raw_log_ts = 0.0
        self.bno_block_until_ts = 0.0
        self.bno_error_ts_window = []
        self.bno_mode = None
        self.bno_address = None
        self.bno_last_error = ""
        self.bno_recovering = False
        self.bno_reset_status_active = False
        self.bno_reset_state = "NONE"
        self._bno_thread = None

        self.zed_connected = False
        self.gnss_popup_shown = False
        self.gnss_reference = None
        self.rtcm_connected = False
        self.rtcm_bytes_total = 0
        self.rtcm_last_bytes_ts = 0.0
        self.rtcm_last_error = ""
        self.rtcm_bridge_started = False
        self.rtcm_stop_event = None
        self.rtcm_restart_requested = False
        self.rtk_last_fixed_ts = 0.0
        self.rtk_fix_recovery_timeout_s = 20.0
        self.rtk_float_since_ts = 0.0
        self.rtk_float_reinit_timeout_s = 45.0
        self.rtcm_last_reinit_ts = 0.0
        self.rtcm_reinit_cooldown_s = 12.0
        self.base_wifi_connected = False
        self.base_start_requested = False
        self.heading_walk_prompt_active = False
        self.last_wifi_attempt_ts = 0.0
        self.wifi_retry_interval = 10.0
        self.wifi_check_in_progress = False

        self.base_wifi_ssid = "BASE_STATION_AP"
        self.base_wifi_password = "BASE_STATION_PASSWORD"
        self.base_wifi_interface = "wlan1"
        self.hotspot_interface = "wlan0"
        self.hotspot_ssid = "Rover-Upload"
        self.hotspot_password = "RoverUpload123"
        self.hotspot_connection_name = "RoverHotspot"
        self.hotspot_gateway = "10.42.0.1"
        self.web_port = 5000
        self.upload_signal_event = threading.Event()
        self.web_server_started = False
        self.rtcm_host = "192.168.4.1"
        self.rtcm_port = 2101
        self.rtcm_log_path = str(BASE_DIR / "rtcm_incoming.log")
        self.rtcm_log_event = None
        self._log_app_start_marker()

        Builder.load_file(str(BASE_DIR / "map.kv"))
        self.root = AppRoot()

        existing_screen = self.root.get_screen("existing_flow")
        existing_root = existing_screen.ids.get("root_widget")
        if existing_root is None and existing_screen.children:
            existing_root = existing_screen.children[0]
        if existing_root is None:
            raise RuntimeError("Existing flow root widget not found")

        self.map_container = existing_root.ids.map_container
        self.buttons_grid = existing_root.ids.buttons_grid
        self.status_label = existing_root.ids.status_label
        self.heading_widget = existing_root.ids.heading_widget
        self.rb_status_label = existing_root.ids.rb_status_label
        self.rb_confirm_btn = existing_root.ids.rb_confirm_btn
        self.distance_live_label = existing_root.ids.distance_live_label
        self.rb_confirm_btn.bind(on_press=lambda _: self._on_rb_confirm_button())

        self.set_ref_btn = existing_root.ids.set_ref_btn
        self.clear_ref_btn = existing_root.ids.clear_ref_btn
        self.set_ref_btn.bind(on_press=lambda _: self._set_gnss_reference())
        self.clear_ref_btn.bind(on_press=lambda _: self._clear_gnss_reference())
        self._log_point_counter = 0

        self._set_startup_status("")
        threading.Thread(target=self._startup_hotspot_thread, daemon=True).start()
        threading.Thread(target=self._read_zed_f9p_loop, daemon=True).start()

        Clock.schedule_interval(self._monitor_gnss_status, 1.0)
        Clock.schedule_interval(self._update_heading_widget, 0.05)
        Clock.schedule_interval(self._update_rover_on_map, 0.3)
        Clock.schedule_interval(self._poll_uploaded_csv_signal, 1.0)
        Clock.schedule_interval(self._monitor_base_wifi, 3.0)

        self._load_points_from_latest_csv(initial=True)
        self._update_rb_tile_text()
        self._update_distance_display()
        return self.root

    def _set_startup_status(self, text):
        startup_screen = self.root.get_screen("startup")
        if startup_screen.startup_status is not None:
            startup_screen.startup_status.text = text

    def _set_main_status(self, text):
        main_screen = self.root.get_screen("main_menu")
        if main_screen.network_status is not None:
            main_screen.network_status.text = text

    def _set_csv_status(self, text):
        csv_screen = self.root.get_screen("csv_upload")
        if csv_screen.csv_status is not None:
            csv_screen.csv_status.text = text

    def back_to_main_menu(self):
        self.root.current = "main_menu"

    def _prompt_gnss_required(self):
        content = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
        message = Label(
            text="ZED-F9P must be ON and in FIX state.\nCheck /dev/ttyACM0 and wait for 3D fix.",
            halign="center",
            valign="middle",
        )
        message.bind(size=lambda instance, value: setattr(instance, "text_size", value))
        close_btn = Button(text="OK", size_hint_y=None, height=dp(40))
        content.add_widget(message)
        content.add_widget(close_btn)

        popup = Popup(title="GNSS Required", content=content, size_hint=(0.8, 0.4), auto_dismiss=False)
        close_btn.bind(on_press=popup.dismiss)
        popup.open()

    # --- Heading visual helpers ---

    def _get_visual_heading_deg(self):
        # GNSS is bootstrap-only (initial heading lock + offset anchor).
        # Runtime heading should come from BNO only after first stable BNO sample.
        if self.gnss_heading_lock_deg is None:
            self._heading_hold_active = False
            return None

        if (
            self.bno_connected
            and self.bno_heading_deg is not None
            and getattr(self, "bno_first_heading_deg", None) is not None
            and bool(getattr(self, "bno_first_is_valid", False))
            and time.time() >= float(getattr(self, "bno_block_until_ts", 0.0))
            and self._stored_heading_offset is not None
        ):
            bno_first = getattr(self, "bno_first_heading_deg", None)
            rebase_delta = float(getattr(self, "bno_rebase_delta_deg", 0.0))
            anchor_first = getattr(self, "bno_anchor_first_heading_deg", None)
            rebased_heading = (self.bno_heading_deg + rebase_delta) % 360.0
            if anchor_first is not None:
                relative_heading = (rebased_heading - anchor_first) % 360.0
            else:
                relative_heading = (self.bno_heading_deg - bno_first) % 360.0
            heading = (relative_heading + self._stored_heading_offset) % 360.0
            self._last_visual_heading_deg = heading
            self._heading_hold_active = False
            self._heading_ever_visible = True
            return heading

        # During BNO reconnect/restart use HOLD only after first successful visible heading.
        if self._heading_ever_visible and self._last_visual_heading_deg is not None:
            self._heading_hold_active = True
            return self._last_visual_heading_deg

        self._heading_hold_active = False
        return None

    def _get_visual_heading_source(self):
        if (
            self.bno_connected
            and self.bno_heading_deg is not None
            and getattr(self, "bno_first_heading_deg", None) is not None
            and bool(getattr(self, "bno_first_is_valid", False))
            and time.time() >= float(getattr(self, "bno_block_until_ts", 0.0))
            and self._stored_heading_offset is not None
        ):
            return "BNO_RUNTIME"
        if self._heading_hold_active:
            return "HOLD"
        return "NONE"
    def store_gnss_heading_offset(self):
        """
        Call this after RTK fix and heading lock is acquired. Stores the heading lock as offset for BNO.
        """
        if self.gnss_heading_lock_deg is None or self.bno_heading_deg is None:
            return False
        bno_first = getattr(self, "bno_first_heading_deg", None)
        if bno_first is None or not bool(getattr(self, "bno_first_is_valid", False)):
            return False
        rebase_delta = float(getattr(self, "bno_rebase_delta_deg", 0.0))
        anchor_first = getattr(self, "bno_anchor_first_heading_deg", None)
        rebased_heading = (self.bno_heading_deg + rebase_delta) % 360.0
        if anchor_first is not None:
            bno_relative = (rebased_heading - anchor_first) % 360.0
        else:
            bno_relative = (self.bno_heading_deg - bno_first) % 360.0
        self._stored_heading_offset = (self.gnss_heading_lock_deg - bno_relative) % 360.0
        return True
    def clear_gnss_heading_offset(self):
        """
        Optionally call this to clear the stored heading offset.
        """
        self._stored_heading_offset = None

    def _update_heading_widget(self, _dt):
        if not hasattr(self, "heading_widget"):
            return

        heading_deg = self._get_visual_heading_deg()
        heading_source = self._get_visual_heading_source()
        if heading_deg is None and self._heading_ever_visible and self._last_visual_heading_deg is not None:
            # Keep displaying last known north-referenced heading in all states.
            heading_deg = self._last_visual_heading_deg
            heading_source = "HOLD"
        heading_display = self._smooth_heading_for_widget(heading_deg)
        target_display = self.nav_bearing_deg
        if not getattr(self.map_container, "nav_arrow_visible", True):
            target_display = None
        target_delta_deg = None
        if heading_display is not None and target_display is not None:
            target_delta_deg = ((target_display - heading_display + 180.0) % 360.0) - 180.0
        carr = int(getattr(self, "rover_carr_soln", 0) or 0)
        if carr == 2:
            rtk_badge = "RTK FIXED"
        elif carr == 1:
            rtk_badge = "RTK FLOAT"
        else:
            rtk_badge = "NO RTK"
        if self.nav_last_used_estimation:
            rtk_badge += " EST"
        self.heading_widget.set_heading(
            heading_display,
            source=heading_source,
            detail=self._heading_widget_detail_text(),
            target_deg=target_display,
            target_delta_deg=target_delta_deg,
            target_distance_m=self.nav_distance_m,
            rtk_badge_text=rtk_badge,
        )
        self._maybe_log_angle(heading_display, heading_source)

    def _smooth_heading_for_widget(self, heading_deg):
        if heading_deg is None:
            # Keep last valid heading when source momentarily drops out.
            return self._widget_heading_smoothed

        if self._widget_heading_smoothed is None:
            self._widget_heading_smoothed = heading_deg % 360.0
            return self._widget_heading_smoothed

        # Circular smoothing keeps continuity across 0/360 wrap.
        delta = ((heading_deg - self._widget_heading_smoothed + 180.0) % 360.0) - 180.0
        self._widget_heading_smoothed = (self._widget_heading_smoothed + self._widget_heading_alpha * delta) % 360.0
        return self._widget_heading_smoothed

    def _maybe_log_angle(self, angle_deg, source):
        if self.rtcm_log_event is None or angle_deg is None:
            return

        now = time.time()
        should_log = False

        if self._last_angle_log_value is None:
            should_log = True
        else:
            delta = abs(((angle_deg - self._last_angle_log_value + 180.0) % 360.0) - 180.0)
            if delta >= 1.0:
                should_log = True

        if not should_log and (now - self._last_angle_log_ts) >= 2.0:
            should_log = True

        if should_log:
            self.rtcm_log_event(f"[ANGLE] phi={angle_deg:.2f}° source={source}")
            self._last_angle_log_ts = now
            self._last_angle_log_value = angle_deg


if __name__ == "__main__":
    UPLOAD_DIR.mkdir(exist_ok=True)
    if not display_ready():
        sys.exit(1)
    KivyRTKApp().run()

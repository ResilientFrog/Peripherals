import math
import time

from .rtk import carr_soln_to_text, fix_type_to_text
from .rtcm_wifi import start_rtcm_wifi_bridge


class GnssReaderMixin:

    def _has_gnss_fix(self):
        return self.zed_connected and self.rover_fix_type >= 3

    def _gnss_heading_ready(self):
        return self._has_gnss_fix() and self.gnss_heading_lock_deg is not None

    @staticmethod
    def _haversine_distance_m(lat1, lon1, lat2, lon2):
        R = 6_371_000.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    @staticmethod
    def _bearing_from_coords(lat1, lon1, lat2, lon2):
        lat1_r = math.radians(lat1)
        lat2_r = math.radians(lat2)
        dlon_r = math.radians(lon2 - lon1)
        x = math.sin(dlon_r) * math.cos(lat2_r)
        y = math.cos(lat1_r) * math.sin(lat2_r) - math.sin(lat1_r) * math.cos(lat2_r) * math.cos(dlon_r)
        return math.degrees(math.atan2(x, y)) % 360.0

    @staticmethod
    def _heading_to_cardinal(heading_deg):
        directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        idx = int(((heading_deg % 360.0) + 22.5) // 45.0) % 8
        return directions[idx]

    @staticmethod
    def _signed_north_offset_deg(heading_deg):
        return ((heading_deg + 180.0) % 360.0) - 180.0

    def _rtcm_status_text(self):
        target = f"{self.rtcm_host}:{self.rtcm_port}"
        if not self.rtcm_bridge_started:
            return "RTCM HOLD (waiting WiFi/base)"
        if self.rtcm_connected:
            if self.rtcm_last_bytes_ts > 0:
                age_s = max(0.0, time.time() - self.rtcm_last_bytes_ts)
                return f"RTCM OK ({self.rtcm_bytes_total} B, age={age_s:.1f}s)"
            return f"RTCM OK ({self.rtcm_bytes_total} B)"
        return f"RTCM WAIT ({target})"

    def _stop_rtcm_bridge_for_network_switch(self):
        try:
            if self.rtcm_stop_event is not None:
                self.rtcm_stop_event.set()
        except Exception:
            pass
        self.rtcm_stop_event = None
        self.rtcm_connected = False
        self.rtcm_bridge_started = False
        self.rtcm_restart_requested = False
        self.rtcm_last_bytes_ts = 0.0

    def _request_rtcm_reinit(self, reason):
        now = time.time()
        if (now - self.rtcm_last_reinit_ts) < self.rtcm_reinit_cooldown_s:
            return
        self.rtcm_last_reinit_ts = now
        try:
            if self.rtcm_stop_event is not None:
                self.rtcm_stop_event.set()
        except Exception:
            pass
        self.rtcm_stop_event = None
        self.rtcm_connected = False
        self.rtcm_bridge_started = False
        self.rtcm_restart_requested = False
        self.rtcm_last_bytes_ts = 0.0
        self.rtcm_last_error = f"RTCM reinit: {reason}"
        if self.rtcm_log_event is not None:
            self.rtcm_log_event(f"[RTCM-REINIT] {reason}")

    def _try_start_base_and_rtcm(self, ser):
        if self.rtcm_bridge_started or not self.base_start_requested:
            return
        if not self.base_wifi_connected:
            return
        self.rtcm_stop_event, _rtcm_thread, self.rtcm_log_event = start_rtcm_wifi_bridge(
            ser,
            host=self.rtcm_host,
            port=self.rtcm_port,
            stream_url=None,
            http_poll_interval_s=None,
            data_log_path=self.rtcm_log_path,
            status_callback=self._on_rtcm_status_update,
            hosts=[self.rtcm_host],
            interface=self.base_wifi_interface,
            rate_hz=1.0,
        )
        self.rtcm_bridge_started = True

    def _on_rtcm_status_update(self, status):
        connected = status.get("connected")
        bytes_total = status.get("bytes_total")
        error = status.get("error")
        host = status.get("host")
        if connected is not None:
            self.rtcm_connected = bool(connected)
        if bytes_total is not None:
            prev_total = self.rtcm_bytes_total
            self.rtcm_bytes_total = int(bytes_total)
            if self.rtcm_bytes_total > prev_total:
                self.rtcm_last_bytes_ts = time.time()
        if error is not None:
            self.rtcm_last_error = str(error)
        if host:
            self.rtcm_host = str(host)

        self.heading_walk_prompt_active = (
            self.base_start_requested
            and self.rtcm_connected
            and self.rtcm_last_bytes_ts > 0
            and self.gnss_heading_lock_deg is None
        )

        if (
            self.base_start_requested
            and self.rtcm_bridge_started
            and self.rtcm_connected
            and self.rtcm_last_bytes_ts > 0
            and (time.time() - self.rtcm_last_bytes_ts) > 20.0
            and self.rover_carr_soln == 0
        ):
            self.rtcm_restart_requested = True

    def _read_zed_f9p_loop(self):
        import serial

        try:
            from pyubx2 import UBXReader
        except ImportError:
            return

        ports = ["/dev/ttyACM0", "/dev/ttyACM1"]
        baud = 115200

        while True:
            ser = None
            try:
                connected_port = None
                for port in ports:
                    try:
                        ser = serial.Serial(port, baud, timeout=1)
                        connected_port = port
                        break
                    except Exception:
                        continue

                if ser is None:
                    raise RuntimeError(f"Unable to open GNSS port(s): {', '.join(ports)}")

                self.rtcm_connected = False
                self.rtcm_bytes_total = 0
                self.rtcm_last_bytes_ts = 0.0
                self.rtcm_bridge_started = False
                self.rtcm_stop_event = None
                self.rtcm_restart_requested = False
                ubr = UBXReader(ser, protfilter=3)
                self.zed_connected = True

                while True:
                    try:
                        _raw, msg = ubr.read()
                        if msg and msg.identity == "NAV-PVT":
                            lat = msg.lat
                            lon = msg.lon
                            self.rover_gnss_raw = (lat, lon)
                            self.rover_fix_type = getattr(msg, "fixType", 0)
                            self.rover_carr_soln = getattr(msg, "carrSoln", 0)
                            self.rover_num_sv = getattr(msg, "numSV", 0)
                            h_acc_mm = getattr(msg, "hAcc", 0)
                            v_acc_mm = getattr(msg, "vAcc", 0)
                            self.rover_h_acc_m = h_acc_mm / 1000.0
                            spd = getattr(msg, "gSpeed", 0) / 1000.0
                            self.rover_speed_ms = spd
                            flags = getattr(msg, "flags", 0)
                            gnss_fix_ok = bool(flags & 0x01)
                            rtcm_flowing = self.rtcm_connected and self.rtcm_last_bytes_ts > 0

                            if self.rover_carr_soln == 2 and rtcm_flowing:
                                self.rtk_float_since_ts = 0.0
                                self.rtk_last_fixed_ts = time.time()
                                if self._prev_gnss_for_heading is not None:
                                    prev_lat, prev_lon = self._prev_gnss_for_heading
                                    dist = self._haversine_distance_m(prev_lat, prev_lon, lat, lon)
                                    if dist >= 0.3:
                                        bearing = self._bearing_from_coords(prev_lat, prev_lon, lat, lon)
                                        if self.gnss_heading_lock_deg is None:
                                            self.gnss_heading_lock_deg = bearing
                                            self.gnss_heading_lock_cardinal = self._heading_to_cardinal(bearing)
                                            self.gnss_heading_lock_ts = time.time()
                                            self.rover_heading_deg = bearing
                                            self.rover_heading_cardinal = self.gnss_heading_lock_cardinal
                                            self.rover_north_offset_deg = self._signed_north_offset_deg(bearing)
                                            self.heading_walk_prompt_active = False
                                            self.start_bno085_after_heading_lock()
                                            if self.store_gnss_heading_offset() and self.rtcm_log_event is not None:
                                                self.rtcm_log_event(
                                                    f"[HEAD-OFFSET] stored={self._stored_heading_offset:.2f}° "
                                                    f"(lock={self.gnss_heading_lock_deg:.2f}° bno={self.bno_heading_deg:.2f}°)"
                                                )
                                            if self.rtcm_log_event is not None:
                                                self.rtcm_log_event(
                                                    f"[HEAD-LOCK] GNSS lock acquired: {bearing:.2f}° "
                                                    f"({self.gnss_heading_lock_cardinal}) dist={dist:.2f}m"
                                                )
                                        self._prev_gnss_for_heading = (lat, lon)
                                else:
                                    self._prev_gnss_for_heading = (lat, lon)
                            elif (
                                self.base_start_requested
                                and self.rtcm_bridge_started
                                and rtcm_flowing
                                and self.rover_carr_soln == 1
                            ):
                                if self.rtk_float_since_ts <= 0.0:
                                    self.rtk_float_since_ts = time.time()
                                elif (time.time() - self.rtk_float_since_ts) > self.rtk_float_reinit_timeout_s:
                                    self._request_rtcm_reinit(
                                        f"RTK FLOAT for >{self.rtk_float_reinit_timeout_s:.0f}s"
                                    )
                                    self.rtk_float_since_ts = 0.0
                            elif (
                                self.base_start_requested
                                and self.rtcm_bridge_started
                                and self.rtk_last_fixed_ts > 0
                                and (time.time() - self.rtk_last_fixed_ts) > self.rtk_fix_recovery_timeout_s
                                and self.rover_carr_soln == 0
                            ):
                                self._request_rtcm_reinit(
                                    f"RTK FIX lost for >{self.rtk_fix_recovery_timeout_s:.0f}s"
                                )
                                self.rtk_float_since_ts = 0.0
                            else:
                                self.rtk_float_since_ts = 0.0

                            # Navigation quality gate:
                            # Use fresh GNSS position only when RTK Fixed is present
                            # and horizontal accuracy is within configured threshold.
                            nav_quality_ok = True
                            hold_reason = ""
                            if getattr(self, "nav_require_rtk_fixed", True):
                                nav_quality_ok = nav_quality_ok and (self.rover_carr_soln == 2)
                                if not nav_quality_ok:
                                    hold_reason = "RTK not fixed"
                            max_h_acc = getattr(self, "nav_max_h_acc_m", None)
                            if max_h_acc is not None and self.rover_h_acc_m is not None:
                                if self.rover_h_acc_m > max_h_acc:
                                    nav_quality_ok = False
                                    hold_reason = f"hAcc>{max_h_acc:.2f}m"
                            self.rover_gnss_quality_ok = nav_quality_ok
                            self.rover_gnss_hold_reason = hold_reason if not nav_quality_ok else ""
                            if nav_quality_ok:
                                self.rover_gnss = (lat, lon)
                            elif not getattr(self, "nav_hold_last_good_gnss", True):
                                self.rover_gnss = None

                            if self.rtcm_log_event is not None:
                                fix_txt = fix_type_to_text(self.rover_fix_type)
                                rtk_txt = carr_soln_to_text(self.rover_carr_soln)
                                alt_m = getattr(msg, "height", 0) / 1000.0
                                head_str = (
                                    f"{self.rover_heading_deg:6.2f}\u00b0"
                                    if self.rover_heading_deg is not None
                                    else "   N/A "
                                )
                                cardinal = self.rover_heading_cardinal if self.rover_heading_cardinal else "?"
                                spd_kmh = spd * 3.6
                                self.rtcm_log_event(
                                    f"[NAV-PVT] Fix: {fix_txt:<12} | [RTK] {rtk_txt:<10} | Sats: {self.rover_num_sv:2d} "
                                    f"| Lat: {lat:.8f} | Lon: {lon:.8f} | Alt: {alt_m:.3f} m "
                                    f"| hAcc: {h_acc_mm/1000.0:.4f} m | vAcc: {v_acc_mm/1000.0:.4f} m "
                                    f"| Head: {head_str} ({cardinal}) "
                                    f"| Speed: {spd:.3f} m/s ({spd_kmh:.2f} km/h) "
                                    f"| gnssFixOK={gnss_fix_ok} "
                                    f"| navQ={'OK' if nav_quality_ok else 'HOLD'}"
                                )

                            if self.rtcm_restart_requested and self.rtcm_stop_event is not None:
                                self._request_rtcm_reinit("stale RTCM stream")

                            self._try_start_base_and_rtcm(ser)
                    except Exception:
                        continue
            except Exception:
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
                self.rtcm_last_bytes_ts = 0.0
                self.rtcm_bridge_started = False
                self.rtcm_restart_requested = False
                self.rtk_last_fixed_ts = 0.0
                self.rtk_float_since_ts = 0.0
                self.rover_fix_type = 0
                self.rover_carr_soln = 0
                self.rover_num_sv = 0
                self.rover_h_acc_m = None
                self.rover_gnss = None
                self.rover_gnss_raw = None
                self.rover_gnss_quality_ok = False
                self.rover_gnss_hold_reason = "GNSS disconnected"
                self.rover_heading_deg = None
                self.rover_heading_cardinal = None
                self.rover_north_offset_deg = None
                self.gnss_heading_lock_deg = None
                self.gnss_heading_lock_cardinal = None
                self.gnss_heading_lock_ts = 0.0
                self.heading_walk_prompt_active = False
                self.rover_speed_ms = None
                time.sleep(1)

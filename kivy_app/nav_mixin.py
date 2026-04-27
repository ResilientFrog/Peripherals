import math
import time

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.button import Button

from ints import carr_soln_to_text, fix_type_to_text, rtk_status_text


class NavMixin:

    @staticmethod
    def _normalize_signed_angle(deg):
        return ((deg + 180.0) % 360.0) - 180.0

    def _smooth_circular_angle(self, current, target, alpha):
        if target is None:
            return None
        if current is None:
            return target % 360.0
        delta = self._normalize_signed_angle(target - current)
        return (current + alpha * delta) % 360.0

    def _rate_limit_circular_angle(self, current, target, max_delta):
        if target is None:
            return None
        if current is None:
            return target % 360.0
        delta = self._normalize_signed_angle(target - current)
        if delta > max_delta:
            delta = max_delta
        elif delta < -max_delta:
            delta = -max_delta
        return (current + delta) % 360.0

    def _append_nav_vector_sample(
        self,
        sample_ts,
        source_lon,
        source_lat,
        target_lon,
        target_lat,
        bearing_abs_deg,
        steer_rel_deg,
        heading_deg,
        distance_m,
        estimated,
    ):
        sample = {
            "ts": sample_ts,
            "source_lon": source_lon,
            "source_lat": source_lat,
            "target_lon": target_lon,
            "target_lat": target_lat,
            "bearing_abs_deg": bearing_abs_deg,
            "steer_rel_deg": steer_rel_deg,
            "heading_deg": heading_deg,
            "distance_m": distance_m,
            "rtk_carr_soln": int(getattr(self, "rover_carr_soln", 0) or 0),
            "estimated": bool(estimated),
        }
        self.nav_vector_samples.append(sample)
        max_len = int(getattr(self, "nav_vector_samples_max", 60))
        if len(self.nav_vector_samples) > max_len:
            self.nav_vector_samples = self.nav_vector_samples[-max_len:]

    def _reset_nav_guidance_state(self):
        self.nav_vector_samples = []
        self.nav_guidance_smoothed_bearing_deg = None
        self.nav_guidance_smoothed_steer_deg = None
        self.nav_last_update_ts = 0.0
        self.nav_last_live_pos = None
        self.nav_last_live_pos_ts = 0.0
        self.nav_prev_live_pos = None
        self.nav_prev_live_pos_ts = 0.0
        self.nav_estimated_pos = None
        self.nav_last_used_estimation = False
        self.nav_display_bearing_deg = None
        if hasattr(self, "map_container"):
            self.map_container.nav_heading_deg = None
            self.map_container.nav_arrow_visible = True

    def _compute_reference_bearings_from_b0(self, points, b0_lat, b0_lon):
        ref = {}
        for pt in points:
            if not self._is_b_target_point(pt):
                continue
            lat = pt.get("lat")
            lon = pt.get("lon")
            if lat is None or lon is None:
                continue
            name = str(pt.get("name", "")).strip().upper()
            az = self._bearing_from_coords(float(b0_lat), float(b0_lon), float(lat), float(lon))
            dist = self._haversine_distance_m(float(b0_lat), float(b0_lon), float(lat), float(lon))
            ref[name] = {"azimuth_deg": az, "distance_m": dist}
        return ref

    # --- Status text helpers ---

    def _position_status_text(self):
        heading = self._heading_text()
        if not self.rover_gnss:
            if heading:
                return f"Pos: waiting GNSS | {heading}"
            return "Pos: waiting GNSS"

        lat, lon = self.rover_gnss
        metrics = []
        if self.rover_num_sv:
            metrics.append(f"SVs={self.rover_num_sv}")
        if self.rover_h_acc_m is not None:
            metrics.append(f"hAcc={self.rover_h_acc_m:.3f}m")
        if self.rover_speed_ms is not None:
            metrics.append(f"Spd={self.rover_speed_ms:.2f}m/s")
        if heading:
            metrics.append(heading)
        metrics.append(self._bno_text())
        if metrics:
            return f"Pos: {lat:.8f}, {lon:.8f} | " + " ".join(metrics)
        return f"Pos: {lat:.8f}, {lon:.8f}"

    def _current_lon_lat_text(self):
        if not self.rover_gnss:
            return "Current lon/lat: waiting GNSS"
        lat, lon = self.rover_gnss
        return f"Current lon/lat: {lon:.8f}, {lat:.8f}"

    def _heading_text(self):
        heading = self._get_visual_heading_deg()
        if heading is None:
            heading = getattr(self, "_last_visual_heading_deg", None)
        if heading is not None:
            source = self._get_visual_heading_source()
            parts = [f"Angle φ[{source}]: {heading:.1f}°"]
            if source == "BNO_FIRST+OFFSET" and self._stored_heading_offset is not None:
                parts.append(f"off={self._stored_heading_offset:+.1f}°")
            if source == "BNO_FIRST":
                bno_first = getattr(self, "bno_first_heading_deg", None)
                if bno_first is not None:
                    parts.append(f"b0={bno_first:.1f}°")
            return " ".join(parts)
        return None

    def _bno_text(self):
        if self.gnss_heading_lock_deg is None:
            if self.heading_walk_prompt_active:
                return "BNO: waiting heading lock (RTK FIX + walk straight)"
            return "BNO: waiting GNSS heading lock from movement"
        if self.bno_connected and self.bno_heading_deg is not None:
            mode = self.bno_mode or "?"
            addr = f"0x{self.bno_address:02X}" if self.bno_address is not None else "?"
            return f"BNO[{mode}@{addr}]={self.bno_heading_deg:.1f}°"
        if self.bno_last_error:
            return f"BNO: waiting ({self.bno_last_error})"
        return "BNO: waiting"

    def _heading_widget_detail_text(self):
        parts = []
        if self.rover_gnss:
            lat, lon = self.rover_gnss
            parts.append(f"Lat {lat:.6f}, Lon {lon:.6f}")
        else:
            parts.append("Lat/Lon waiting")

        if self.bno_recovering:
            parts.append("BNO RECOVERING...")

        if self.bno_connected and self.bno_mode:
            addr = f"0x{self.bno_address:02X}" if self.bno_address is not None else "?"
            parts.append(f"BNO {self.bno_mode}@{addr}")

        if self.gnss_heading_lock_deg is not None:
            parts.append(f"GNSS_LOCK {self.gnss_heading_lock_deg:.1f}°")
        nav_pvt_hdg = getattr(self, "nav_pvt_heading_deg", None)
        nav_pvt_ts = float(getattr(self, "nav_pvt_heading_ts", 0.0) or 0.0)
        if (
            bool(getattr(self, "nav_pvt_heading_valid", False))
            and nav_pvt_hdg is not None
            and (time.time() - nav_pvt_ts) <= float(getattr(self, "nav_pvt_fresh_timeout_s", 2.5))
        ):
            parts.append(f"NAV_PVT {nav_pvt_hdg:.1f}°")
            diff = getattr(self, "nav_pvt_bno_diff_deg", None)
            if diff is not None:
                parts.append(f"ΔBNO={diff:.1f}°")
        if self.bno_heading_deg is not None:
            parts.append(f"BNO_RAW {self.bno_heading_deg:.1f}°")
        if self.bno_heading_deg is not None and getattr(self, "bno_first_heading_deg", None) is not None:
            bno_rel = (self.bno_heading_deg - self.bno_first_heading_deg) % 360.0
            parts.append(f"BNO_REL0 {bno_rel:.1f}°")
        visual_heading = self._get_visual_heading_deg()
        if visual_heading is not None:
            parts.append(f"WIDGET={visual_heading:.1f}°")
        target_display = self.nav_display_bearing_deg
        if target_display is None:
            target_display = self.nav_bearing_deg
        if target_display is not None:
            parts.append(f"TARGET={target_display:.1f}°")
            if visual_heading is not None:
                turn_delta = self._normalize_signed_angle(target_display - visual_heading)
                parts.append(f"TURN={turn_delta:+.1f}°")
        if self.nav_last_used_estimation:
            parts.append("NAV_EST")

        return " | ".join(parts)

    # --- GNSS/status monitor ---

    def _monitor_gnss_status(self, dt):
        if not hasattr(self, "status_label"):
            return

        if self._has_gnss_fix():
            self.gnss_popup_shown = False
            position_text = self._position_status_text()
            walk_prompt = ""
            if self.heading_walk_prompt_active:
                walk_prompt = "\nHeading init: jdi rovne pro ziskani heading locku"
            bno_move_prompt = ""
            if getattr(self, "bno_move_prompt_active", False):
                bno_move_prompt = "\nBNO restart: posun se znovu (>= 0.30 m) pro novou kalibraci offsetu"
            bno_reset_prompt = ""
            if getattr(self, "bno_reset_status_active", False):
                bno_reset_prompt = "\nBNO085 se resetuje"
            self.status_label.text = (
                f"Status: {rtk_status_text(self.rover_fix_type, self.rover_carr_soln)} "
                f"({fix_type_to_text(self.rover_fix_type)}, {carr_soln_to_text(self.rover_carr_soln)}) | "
                f"{self._rtcm_status_text()}\n{position_text}{walk_prompt}{bno_move_prompt}{bno_reset_prompt}"
            )
            return

        if self.zed_connected:
            position_text = self._position_status_text()
            lon_lat_text = self._current_lon_lat_text()
            recovery_txt = ""
            if (
                self.base_start_requested
                and self.rtcm_last_reinit_ts > 0
                and (time.time() - self.rtcm_last_reinit_ts) < 6.0
            ):
                recovery_txt = " | RTCM recovery..."
            self.status_label.text = (
                f"Status: ZED-F9P connected, searching for fix "
                f"({rtk_status_text(self.rover_fix_type, self.rover_carr_soln)}, "
                f"{fix_type_to_text(self.rover_fix_type)}, {carr_soln_to_text(self.rover_carr_soln)}) | "
                f"{self._rtcm_status_text()}{recovery_txt}\n{lon_lat_text} | {position_text}"
            )
        else:
            self.status_label.text = "Status: ZED-F9P not connected. Turn it on and check /dev/ttyACM0"

        if not self.gnss_popup_shown and self.root.current == "existing_flow":
            self.gnss_popup_shown = True
            Clock.schedule_once(lambda _dt: self._prompt_gnss_required(), 0.1)

    # --- Map update ---

    def _update_rover_on_map(self, _dt):
        if not hasattr(self, "map_container"):
            return

        heading_deg = self._get_visual_heading_deg()
        self.map_container.rover_heading_deg = heading_deg

        map_gnss = self.rover_gnss_raw if self.rover_gnss_raw is not None else self.rover_gnss
        if map_gnss:
            lat, lon = map_gnss
            self.map_container.rover_pos = (lon, lat)
        else:
            self.map_container.rover_pos = None

        self._update_rb_guidance()
        self.map_container.nav_target_in_zone = bool(getattr(self, "nav_target_in_zone", False))
        self.map_container._redraw()

    # --- Points / B0 ---

    def _update_points(self, points):
        b0_point = self._find_b0_point(points)
        if b0_point is not None:
            b0_global_lat = b0_point.get("global_lat")
            b0_global_lon = b0_point.get("global_lon")
            # B0 must use global coordinates for GNSS shift computation.
            self.csv_b0_lat = float(b0_global_lat) if b0_global_lat is not None else float(b0_point["lat"])
            self.csv_b0_lon = float(b0_global_lon) if b0_global_lon is not None else float(b0_point["lon"])
        else:
            self.csv_b0_lat = None
            self.csv_b0_lon = None

        self.map_container.set_points(points)
        self.rb_selected_idx = None
        self.rb_selected_name = None
        self.rb_distance_m = None
        self.rb_bearing_deg = None
        self.rb_confirmed = False
        self.rb_origin_gnss = None
        self.nav_target_idx = None
        self.nav_target_name = None
        self.nav_distance_m = None
        self.nav_bearing_deg = None
        self.nav_target_dwell_start_ts = 0.0
        self.nav_target_reached = False
        self.nav_target_reached_ts = 0.0
        self.nav_target_in_zone = False
        self.nav_confirmed_targets = set()
        self._reset_nav_guidance_state()
        self.map_container.nav_line_start_pos = None
        self.map_container.nav_line_end_pos = None
        self._render_point_buttons()

    def _render_point_buttons(self):
        self.buttons_grid.clear_widgets()
        for i, pt in enumerate(self.map_container.points):
            name = pt["name"]
            rel_y = pt["lat"]
            rel_x = pt["lon"]
            is_b_target = self._is_b_target_point(pt)
            is_confirmed_target = i in self.nav_confirmed_targets
            btn_text = f"{name} (Rel_X={rel_x:.2f}, Rel_Y={rel_y:.2f})"
            if is_confirmed_target:
                btn_text += " [OK]"
            disabled = False

            if self.rb_confirmed:
                if not is_b_target or i == self.rb_selected_idx or is_confirmed_target:
                    disabled = True
            else:
                disabled = True

            b = Button(
                text=btn_text,
                size_hint_x=1,
                size_hint_y=None,
                height=dp(40),
                disabled=disabled,
            )
            b.bind(on_press=lambda inst, idx=i: self._on_point_button(idx))
            self.buttons_grid.add_widget(b)

        Clock.schedule_once(
            lambda dt: setattr(self.buttons_grid, "height", self.buttons_grid.minimum_height), 0.01
        )

    def _on_point_button(self, idx):
        self.map_container.select_index(idx)
        if 0 <= idx < len(self.map_container.points):
            point = self.map_container.points[idx]
            name = point.get("name", f"Point {idx + 1}")
            if not self.rb_confirmed:
                self.status_label.text = "Nejdřív potvrď bod B0 (aktuální pozice roveru)"
                return
            else:
                if not self._is_b_target_point(point) or idx == self.rb_selected_idx:
                    self.status_label.text = "Po potvrzení B0 lze vybrat jen body B1..Bn"
                    return
                self.nav_target_idx = idx
                self.nav_target_name = name
                self.nav_target_dwell_start_ts = 0.0
                self.nav_target_reached = False
                self.nav_target_reached_ts = 0.0
                self.nav_target_in_zone = False
                self._reset_nav_guidance_state()
                heading_now = self._get_visual_heading_deg()
                if heading_now is None:
                    heading_now = getattr(self, "_last_visual_heading_deg", None)
                heading_txt = f" | φ={heading_now:.2f}°" if heading_now is not None else " | φ=---"
                self.status_label.text = f"Selected target: {name}{heading_txt}"
                if self.rtcm_log_event is not None:
                    self.rtcm_log_event(f"[NAV] target selected name={name}")
            self._update_rb_guidance()

    def _set_gnss_reference(self):
        idx = self.map_container.selected
        if idx is None:
            self.status_label.text = "Select a point first"
            return
        if not self._has_gnss_fix() or not self.rover_gnss:
            self.status_label.text = "ZED-F9P must be ON and in FIX state"
            return
        pt = self.map_container.points[idx]
        self.gnss_reference = {"gnss": self.rover_gnss, "local": (pt["lon"], pt["lat"])}
        self.status_label.text = f"Ref set: {pt['name']}"
        self._update_rb_guidance()

    def _clear_gnss_reference(self):
        self.gnss_reference = None
        self.status_label.text = "Ref cleared"
        self._update_rb_guidance()

    # --- Navigation guidance ---

    def _on_rb_confirm_button(self):
        if not self.rb_confirmed:
            self._confirm_rb_point()
            return
        self._confirm_active_nav_target()

    def _confirm_active_nav_target(self):
        if self.nav_target_idx is None:
            self.status_label.text = "Vyber cíl B1..Bn."
            return
        if self.nav_distance_m is None:
            self.status_label.text = "Cekam na vzdalenost k cili."
            return
        threshold_m = float(getattr(self, "nav_manual_confirm_distance_m", 0.05))
        if self.nav_distance_m > threshold_m:
            self.status_label.text = (
                f"Mimo zónu potvrzení: {self.nav_distance_m:.2f} m (potřeba <= {threshold_m:.2f} m)"
            )
            return

        reached_idx = self.nav_target_idx
        reached_name = self.nav_target_name or f"B{reached_idx + 1}"
        reached_dist = self.nav_distance_m
        self.nav_confirmed_targets.add(reached_idx)
        self.nav_target_reached = True
        self.nav_target_reached_ts = time.time()
        self.nav_target_idx = None
        self.nav_target_name = None
        self.nav_distance_m = None
        self.nav_bearing_deg = None
        self.nav_display_bearing_deg = None
        self.nav_target_dwell_start_ts = 0.0
        self.nav_target_in_zone = False
        self._reset_nav_guidance_state()
        self.map_container.nav_line_start_pos = None
        self.map_container.nav_line_end_pos = None
        self._render_point_buttons()
        self.status_label.text = f"{reached_name} potvrzen ručně ({reached_dist:.2f} m). Vyber další cíl."
        if self.rtcm_log_event is not None:
            self.rtcm_log_event(
                f"[NAV-MANUAL] confirmed target name={reached_name} idx={reached_idx} dist={reached_dist:.3f}m"
            )

    def _update_rb_guidance(self):
        now = time.time()
        live_gnss = self.rover_gnss_raw if self.rover_gnss_raw is not None else self.rover_gnss

        if not self.rb_confirmed or live_gnss is None:
            self.rb_distance_m = None
            self.rb_bearing_deg = None
            self.nav_distance_m = None
            self.nav_bearing_deg = None
            self.nav_display_bearing_deg = None
            self.nav_target_in_zone = False
            self.map_container.nav_heading_deg = None
            self.map_container.nav_arrow_visible = True
            self.map_container.nav_line_start_pos = None
            self.map_container.nav_line_end_pos = None
            self._update_rb_tile_text()
            self._update_distance_display()
            return

        target_point = self._get_selected_nav_point()

        if target_point is None:
            self.nav_distance_m = None
            self.nav_bearing_deg = None
            self.nav_display_bearing_deg = None
            self.nav_target_in_zone = False
            self.map_container.nav_heading_deg = None
            self.map_container.nav_arrow_visible = True
            self.map_container.nav_line_start_pos = None
            self.map_container.nav_line_end_pos = None
            self._update_rb_tile_text()
            self._update_distance_display()
            return

        rover_lat, rover_lon = live_gnss
        target_lon = target_point.get("lon")
        target_lat = target_point.get("lat")
        if target_lon is None or target_lat is None:
            self.nav_distance_m = None
            self.nav_bearing_deg = None
            self.nav_display_bearing_deg = None
            self.nav_target_in_zone = False
            self.map_container.nav_heading_deg = None
            self.map_container.nav_arrow_visible = True
            self.map_container.nav_line_start_pos = None
            self.map_container.nav_line_end_pos = None
            self._update_rb_tile_text()
            self._update_distance_display()
            return

        if self.nav_last_live_pos is None or abs(rover_lat - self.nav_last_live_pos[0]) > 1e-12 or abs(rover_lon - self.nav_last_live_pos[1]) > 1e-12:
            if self.nav_last_live_pos is not None:
                self.nav_prev_live_pos = self.nav_last_live_pos
                self.nav_prev_live_pos_ts = self.nav_last_live_pos_ts
            self.nav_last_live_pos = (rover_lat, rover_lon)
            self.nav_last_live_pos_ts = now
            self.nav_estimated_pos = (rover_lat, rover_lon)

        used_estimation = False
        nav_lat = rover_lat
        nav_lon = rover_lon
        stale_after_s = float(getattr(self, "nav_stale_after_s", 0.8))
        estimate_horizon_s = float(getattr(self, "nav_estimate_max_horizon_s", 3.0))
        rtk_fixed = int(getattr(self, "rover_carr_soln", 0) or 0) == 2
        if self.nav_last_live_pos_ts > 0:
            age_s = max(0.0, now - self.nav_last_live_pos_ts)
            if age_s > stale_after_s and not rtk_fixed:
                if (
                    self.nav_prev_live_pos is not None
                    and self.nav_prev_live_pos_ts > 0
                    and self.nav_last_live_pos is not None
                ):
                    dt_live = self.nav_last_live_pos_ts - self.nav_prev_live_pos_ts
                    if dt_live > 1e-3:
                        dt_pred = min(age_s, estimate_horizon_s)
                        prev_lat, prev_lon = self.nav_prev_live_pos
                        last_lat, last_lon = self.nav_last_live_pos
                        vel_lat = (last_lat - prev_lat) / dt_live
                        vel_lon = (last_lon - prev_lon) / dt_live
                        nav_lat = last_lat + vel_lat * dt_pred
                        nav_lon = last_lon + vel_lon * dt_pred
                        self.nav_estimated_pos = (nav_lat, nav_lon)
                        used_estimation = True
                if not used_estimation and self.nav_estimated_pos is not None:
                    nav_lat, nav_lon = self.nav_estimated_pos
                    used_estimation = True

        target_lat = float(target_lat)
        target_lon = float(target_lon)
        self.nav_distance_m = self._haversine_distance_m(nav_lat, nav_lon, target_lat, target_lon)
        self.nav_bearing_deg = self._bearing_from_coords(nav_lat, nav_lon, target_lat, target_lon)

        current_heading = self._get_visual_heading_deg()
        steer_rel_deg = None
        if current_heading is not None:
            steer_rel_deg = self._normalize_signed_angle(self.nav_bearing_deg - current_heading)

        alpha = float(getattr(self, "nav_guidance_smooth_alpha", 0.55))
        close_dist_m = float(getattr(self, "nav_guidance_close_distance_m", 1.0))
        if self.nav_distance_m is not None and self.nav_distance_m < close_dist_m:
            alpha = float(getattr(self, "nav_guidance_close_alpha", 0.25))
        self.nav_guidance_smoothed_bearing_deg = self._smooth_circular_angle(
            self.nav_guidance_smoothed_bearing_deg, self.nav_bearing_deg, alpha
        )
        if steer_rel_deg is not None:
            current_rel = self.nav_guidance_smoothed_steer_deg
            if current_rel is None:
                self.nav_guidance_smoothed_steer_deg = steer_rel_deg
            else:
                delta = self._normalize_signed_angle(steer_rel_deg - current_rel)
                self.nav_guidance_smoothed_steer_deg = current_rel + alpha * delta
        if self.nav_guidance_smoothed_steer_deg is not None:
            self.nav_guidance_smoothed_steer_deg = self._normalize_signed_angle(self.nav_guidance_smoothed_steer_deg)

        dt = 0.1
        if self.nav_last_update_ts > 0.0:
            dt = min(0.5, max(0.02, now - self.nav_last_update_ts))
        self.nav_last_update_ts = now
        max_turn_rate = float(getattr(self, "nav_guidance_max_turn_rate_deg_s", 140.0))
        max_delta = max_turn_rate * dt
        display_bearing = self._rate_limit_circular_angle(
            self.map_container.nav_heading_deg,
            self.nav_guidance_smoothed_bearing_deg,
            max_delta,
        )
        hold_dist_m = float(getattr(self, "nav_guidance_hold_distance_m", 0.20))
        if self.nav_distance_m is not None and self.nav_distance_m <= hold_dist_m and self.nav_display_bearing_deg is not None:
            # Near target, hold bearing to avoid noisy spinning from centimeter GNSS jitter.
            display_bearing = self.nav_display_bearing_deg
        # Keep map navigation cue static (B0 -> selected target line/arrow), no dynamic rover nav arrow.
        self.map_container.nav_heading_deg = None
        self.nav_display_bearing_deg = display_bearing
        self.nav_last_used_estimation = used_estimation
        self._append_nav_vector_sample(
            sample_ts=now,
            source_lon=nav_lon,
            source_lat=nav_lat,
            target_lon=target_lon,
            target_lat=target_lat,
            bearing_abs_deg=self.nav_bearing_deg,
            steer_rel_deg=self.nav_guidance_smoothed_steer_deg,
            heading_deg=current_heading,
            distance_m=self.nav_distance_m,
            estimated=used_estimation,
        )

        # Keep map guidance static: draw fixed B0 -> selected Bn line.
        if self.rb_origin_gnss is not None:
            b0_lat, b0_lon = self.rb_origin_gnss
            self.map_container.nav_line_start_pos = (b0_lon, b0_lat)
            self.map_container.nav_line_end_pos = (target_lon, target_lat)
        else:
            self.map_container.nav_line_start_pos = None
            self.map_container.nav_line_end_pos = None

        threshold_m = float(getattr(self, "nav_manual_confirm_distance_m", 0.05))
        self.nav_target_in_zone = self.nav_distance_m <= threshold_m

        if int(getattr(self, "rover_carr_soln", 0) or 0) == 2:
            self.map_container.nav_arrow_visible = True
        else:
            blink_hz = max(0.1, float(getattr(self, "nav_blink_hz", 2.0)))
            phase = (now * blink_hz) % 1.0
            self.map_container.nav_arrow_visible = phase < 0.5

        self._update_rb_tile_text()
        self._update_distance_display()

    def _update_rb_tile_text(self):
        if not hasattr(self, "rb_status_label"):
            return

        current_heading = self._get_visual_heading_deg()
        if current_heading is None:
            current_heading = getattr(self, "_last_visual_heading_deg", None)
        heading_txt = f"φ={current_heading:.2f}°" if current_heading is not None else "φ=---"

        if not self.rb_confirmed:
            fix_ok = self._has_gnss_fix() and self.rover_gnss is not None and self.csv_b0_lat is not None
            if self.csv_b0_lat is None:
                self.rb_status_label.text = f"Bod B0 nebyl nalezen v CSV.\n{heading_txt}"
            else:
                self.rb_status_label.text = (
                    f"Dojdi fyzicky do bodu B0 a potvrď svoji polohu.\n{heading_txt}"
                )
            self.rb_confirm_btn.text = "Potvrdit bod B0"
            self.rb_confirm_btn.disabled = not fix_ok
            return

        nav_point = self._get_selected_nav_point()
        if nav_point is None or self.nav_distance_m is None or self.nav_bearing_deg is None:
            self.rb_status_label.text = f"B0 potvrzen\nVyber cíl B1/B2/... v seznamu | {heading_txt}"
            self.rb_confirm_btn.text = "Bod B0 potvrzen"
            self.rb_confirm_btn.disabled = True
            return

        nav_name = nav_point.get("name", "B")
        zone_txt = ""
        threshold_m = float(getattr(self, "nav_manual_confirm_distance_m", 0.05))
        if self.nav_target_in_zone:
            zone_txt = f" | IN ZONE <= {threshold_m:.2f}m"
        bearing_txt = self.nav_display_bearing_deg if self.nav_display_bearing_deg is not None else self.nav_bearing_deg
        steer_txt = ""
        if self.nav_guidance_smoothed_steer_deg is not None:
            steer_txt = f" steer={self.nav_guidance_smoothed_steer_deg:+.1f}°"
        est_txt = " EST" if self.nav_last_used_estimation else ""
        self.rb_status_label.text = (
            f"B0 potvrzen | Cíl {nav_name}\n"
            f"go: dist={self.nav_distance_m:.2f}m azN={bearing_txt:.2f}°{steer_txt}{est_txt}{zone_txt} | {heading_txt}"
        )
        self.rb_confirm_btn.text = f"Potvrdit cíl {nav_name}"
        self.rb_confirm_btn.disabled = not self.nav_target_in_zone

    def _update_distance_display(self):
        if not hasattr(self, "distance_live_label"):
            return

        if not self.rb_confirmed:
            self.distance_live_label.text = "Vzdálenost k B0: potvrď aktuální pozici"
            return

        nav_point = self._get_selected_nav_point()
        if nav_point is None:
            self.distance_live_label.text = "Vzdálenost k Bx: vyber cíl B1..Bn"
            return

        nav_name = nav_point.get("name", "B")
        if self.nav_distance_m is None:
            self.distance_live_label.text = f"Aktuální vzdálenost k {nav_name}: ---"
        else:
            self.distance_live_label.text = f"Aktuální vzdálenost k {nav_name}: {self.nav_distance_m:.2f} m"

    def _confirm_rb_point(self):
        if getattr(self, "b0_confirmed_once", False):
            self.status_label.text = "B0 uz bylo potvrzeno. Pro nove potvrzeni restartuj aplikaci."
            return
        if self.rb_confirmed:
            self.status_label.text = "B0 je uz potvrzen. Pro nove potvrzeni nejdriv restartuj/obnov flow."
            return
        if not self._has_gnss_fix() or self.rover_gnss is None:
            self.status_label.text = "Pro potvrzeni B0 je potreba GNSS FIX."
            return
        if self.csv_b0_lat is None or self.csv_b0_lon is None:
            self.status_label.text = "B0 neni v CSV nebo nema globalni souradnice."
            return
        if abs(self.csv_b0_lat) < 1e-9 and abs(self.csv_b0_lon) < 1e-9:
            self.status_label.text = (
                "B0 ma globalni souradnice 0,0. Nahraj CSV znovu (Latitude/Longitude) a potvrd B0 znovu."
            )
            return

        self.rb_confirmed = True
        self.b0_confirmed_once = True
        self.rb_origin_gnss = self.rover_gnss
        gnss_lat, gnss_lon = self.rover_gnss

        shift_lat = gnss_lat - self.csv_b0_lat
        shift_lon = gnss_lon - self.csv_b0_lon

        apply_shift = bool(getattr(self, "apply_b0_shift_to_points", False))
        corrected_points = []
        for pt in self.map_container.points:
            new_pt = dict(pt)
            # Prefer global coordinates from CSV (if present). The parser stores
            # rel_x/rel_y into lat/lon fields for local map drawing, which are in
            # meters and must not be shifted as geographic degrees.
            base_lat = pt.get("global_lat")
            base_lon = pt.get("global_lon")
            if base_lat is None or base_lon is None:
                base_lat = pt.get("lat")
                base_lon = pt.get("lon")
            if base_lat is not None and base_lon is not None:
                lat = float(base_lat)
                lon = float(base_lon)
                if apply_shift:
                    lat += shift_lat
                    lon += shift_lon
                new_pt["lat"] = lat
                new_pt["lon"] = lon
            corrected_points.append(new_pt)
        self.map_container.points = corrected_points
        self.nav_reference_bearings = self._compute_reference_bearings_from_b0(
            corrected_points, gnss_lat, gnss_lon
        )

        self.gnss_reference = {"gnss": self.rb_origin_gnss, "local": (gnss_lon, gnss_lat)}
        self.rb_selected_idx = None
        self.rb_selected_name = "B0"
        self.rb_distance_m = 0.0
        self.rb_bearing_deg = 0.0
        self.nav_target_idx = None
        self.nav_target_name = None
        self.nav_distance_m = None
        self.nav_bearing_deg = None
        self.nav_display_bearing_deg = None
        self._reset_nav_guidance_state()
        self.map_container.nav_line_start_pos = None
        self.map_container.nav_line_end_pos = None
        self._render_point_buttons()
        self._update_rb_guidance()
        self._update_rb_tile_text()
        mode_txt = "points aligned to B0 shift" if apply_shift else "points kept at original CSV coordinates"
        heading_now = self._get_visual_heading_deg()
        if heading_now is None:
            heading_now = getattr(self, "_last_visual_heading_deg", None)
        heading_txt = f" φ={heading_now:.2f}°" if heading_now is not None else " φ=---"
        self.status_label.text = (
            f"B0 potvrzen. Globalni posun: dLat={shift_lat:+.8f}, dLon={shift_lon:+.8f} ({mode_txt}). "
            f"Vyber B1/B2/...{heading_txt}"
        )
        if self.rtcm_log_event is not None:
            self.rtcm_log_event(
                f"[B0] confirmed lat={gnss_lat:.8f} lon={gnss_lon:.8f} "
                f"csv_b0=({self.csv_b0_lat:.8f},{self.csv_b0_lon:.8f}) "
                f"shift=({shift_lat:+.8f},{shift_lon:+.8f}) apply_shift={apply_shift}"
            )
            for name in sorted(self.nav_reference_bearings):
                item = self.nav_reference_bearings[name]
                self.rtcm_log_event(
                    f"[NAV-REF] from confirmed B0 to {name}: "
                    f"az={item['azimuth_deg']:.2f}° dist={item['distance_m']:.2f}m"
                )

    # --- Point helpers ---

    @staticmethod
    def _is_b0_point(point):
        name = str(point.get("name", "")).strip().upper()
        return name in {"B0", "B00"}

    def _find_b0_point(self, points):
        for point in points:
            if self._is_b0_point(point):
                return point
        return None

    def _get_selected_nav_point(self):
        if self.nav_target_idx is None:
            return None
        if self.nav_target_idx < 0 or self.nav_target_idx >= len(self.map_container.points):
            return None
        return self.map_container.points[self.nav_target_idx]

    @staticmethod
    def _is_b_target_point(point):
        name = str(point.get("name", "")).strip().upper()
        return name.startswith("B") and name not in {"B0", "B00"}

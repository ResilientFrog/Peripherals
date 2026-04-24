import math
import time

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.button import Button

from ints import carr_soln_to_text, fix_type_to_text, rtk_status_text


class NavMixin:

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
        if heading is not None:
            source = self._get_visual_heading_source()
            parts = [f"Angle φ[{source}]: {heading:.1f}°"]
            if source == "BNO+OFFSET" and self._stored_heading_offset is not None:
                parts.append(f"off={self._stored_heading_offset:+.1f}°")
            if source == "BNO" and self.rover_north_offset_deg is not None:
                parts.append(f"dN={self.rover_north_offset_deg:+.1f}°")
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
        if self.bno_heading_deg is not None:
            parts.append(f"BNO_REL {self.bno_heading_deg:.1f}°")
        visual_heading = self._get_visual_heading_deg()
        if visual_heading is not None:
            parts.append(f"WIDGET={visual_heading:.1f}°")

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
            self.status_label.text = (
                f"Status: {rtk_status_text(self.rover_fix_type, self.rover_carr_soln)} "
                f"({fix_type_to_text(self.rover_fix_type)}, {carr_soln_to_text(self.rover_carr_soln)}) | "
                f"{self._rtcm_status_text()}\n{position_text}{walk_prompt}"
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

        if self.rover_gnss:
            lat, lon = self.rover_gnss
            self.map_container.rover_pos = (lon, lat)
        else:
            self.map_container.rover_pos = None

        self._update_rb_guidance()
        self.map_container._redraw()

    # --- Points / B0 ---

    def _update_points(self, points):
        b0_point = self._find_b0_point(points)
        if b0_point is not None:
            self.csv_b0_lat = float(b0_point["lat"])
            self.csv_b0_lon = float(b0_point["lon"])
            points = [pt for pt in points if not self._is_b0_point(pt)]
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
        self.map_container.nav_heading_deg = None
        self._render_point_buttons()

    def _render_point_buttons(self):
        self.buttons_grid.clear_widgets()
        for i, pt in enumerate(self.map_container.points):
            name = pt["name"]
            rel_y = pt["lat"]
            rel_x = pt["lon"]
            is_b_target = self._is_b_target_point(pt)
            btn_text = f"{name} (Rel_X={rel_x:.2f}, Rel_Y={rel_y:.2f})"
            disabled = False

            if self.rb_confirmed:
                if not is_b_target or i == self.rb_selected_idx:
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
                self.status_label.text = f"Selected target: {name}"
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

    def _update_rb_guidance(self):
        rover_pos = self.map_container.rover_pos

        if not self.rb_confirmed or rover_pos is None:
            self.rb_distance_m = None
            self.rb_bearing_deg = None
            self.nav_distance_m = None
            self.nav_bearing_deg = None
            self.map_container.nav_heading_deg = None
            self._update_rb_tile_text()
            self._update_distance_display()
            return

        target_point = self._get_selected_nav_point()

        if target_point is None:
            self.nav_distance_m = None
            self.nav_bearing_deg = None
            self.map_container.nav_heading_deg = None
            self._update_rb_tile_text()
            self._update_distance_display()
            return

        rover_lon, rover_lat = rover_pos
        target_lon = target_point.get("lon")
        target_lat = target_point.get("lat")
        if target_lon is None or target_lat is None:
            self.nav_distance_m = None
            self.nav_bearing_deg = None
            self.map_container.nav_heading_deg = None
            self._update_rb_tile_text()
            self._update_distance_display()
            return

        target_lat = float(target_lat)
        target_lon = float(target_lon)
        self.nav_distance_m = self._haversine_distance_m(rover_lat, rover_lon, target_lat, target_lon)
        self.nav_bearing_deg = self._bearing_from_coords(rover_lat, rover_lon, target_lat, target_lon)
        dlat = target_lat - rover_lat
        dlon = target_lon - rover_lon
        self.map_container.nav_heading_deg = (math.degrees(math.atan2(dlon, dlat)) % 360.0)

        self._update_rb_tile_text()
        self._update_distance_display()

    def _update_rb_tile_text(self):
        if not hasattr(self, "rb_status_label"):
            return

        current_heading = self._get_visual_heading_deg()
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
        self.rb_status_label.text = (
            f"B0 potvrzen | Cíl {nav_name}\n"
            f"go: dist={self.nav_distance_m:.2f}m azN={self.nav_bearing_deg:.2f}° | {heading_txt}"
        )
        self.rb_confirm_btn.text = "Bod B0 potvrzen"
        self.rb_confirm_btn.disabled = True

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
        if not self._has_gnss_fix() or self.rover_gnss is None:
            self.status_label.text = "Pro potvrzeni B0 je potreba GNSS FIX."
            return
        if self.csv_b0_lat is None or self.csv_b0_lon is None:
            self.status_label.text = "B0 neni v CSV nebo nema globalni souradnice."
            return

        self.rb_confirmed = True
        self.rb_origin_gnss = self.rover_gnss
        gnss_lat, gnss_lon = self.rover_gnss

        shift_lat = gnss_lat - self.csv_b0_lat
        shift_lon = gnss_lon - self.csv_b0_lon

        corrected_points = []
        for pt in self.map_container.points:
            new_pt = dict(pt)
            if pt.get("lat") is not None and pt.get("lon") is not None:
                new_pt["lat"] = float(pt["lat"]) + shift_lat
                new_pt["lon"] = float(pt["lon"]) + shift_lon
            corrected_points.append(new_pt)
        self.map_container.points = corrected_points

        self.gnss_reference = {"gnss": self.rb_origin_gnss, "local": (gnss_lon, gnss_lat)}
        self.rb_selected_idx = None
        self.rb_selected_name = "B0"
        self.rb_distance_m = 0.0
        self.rb_bearing_deg = 0.0
        self.nav_target_idx = None
        self.nav_target_name = None
        self.nav_distance_m = None
        self.nav_bearing_deg = None
        self._render_point_buttons()
        self._update_rb_guidance()
        self._update_rb_tile_text()
        self.status_label.text = (
            f"B0 potvrzen. Globalni posun: dLat={shift_lat:+.8f}, dLon={shift_lon:+.8f}. Vyber B1/B2/..."
        )
        if self.rtcm_log_event is not None:
            self.rtcm_log_event(
                f"[B0] confirmed lat={gnss_lat:.8f} lon={gnss_lon:.8f} "
                f"csv_b0=({self.csv_b0_lat:.8f},{self.csv_b0_lon:.8f}) "
                f"shift=({shift_lat:+.8f},{shift_lon:+.8f})"
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

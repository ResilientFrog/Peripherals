import math
import time

import board
import busio
import adafruit_bno08x
from adafruit_bno08x import (
    BNO_REPORT_ROTATION_VECTOR,
)
from adafruit_bno08x.i2c import BNO08X_I2C

adafruit_bno08x._DEBUG = False
# Reduce blocking time during feature enable so failed init recovers quickly.
adafruit_bno08x._FEATURE_ENABLE_TIMEOUT = 2.5
# Use a conservative I2C clock for better stability on longer/noisy wiring.
# Standalone scripts in this project are stable in the 25-50 kHz range.
BNO_I2C_FREQUENCY_HZ = 25000
BNO_RECONNECT_ERROR_STREAK = 120
BNO_MAX_STEP_DEG = 20.0
BNO_HEADING_ALPHA = 0.65
BNO_FIRST_WARMUP_S = 0.8
BNO_FIRST_STABLE_WINDOW = 12
BNO_FIRST_MAX_SPREAD_DEG = 6.0
BNO_FIRST_REJECT_NEAR_ZERO_DEG = 1.0
BNO_POST_RECONNECT_COOLDOWN_S = 2.0
BNO_ERROR_BURST_WINDOW_S = 10.0
BNO_ERROR_BURST_THRESHOLD = 6
BNO_BLOCK_DURATION_S = 8.0


class BnoReaderMixin:

    def start_bno085_after_heading_lock(self):
        """
        Call this after heading lock is acquired (RTK Fixed). Starts BNO085 loop in a thread if not already running.
        """
        import threading
        if (
            not hasattr(self, '_bno_thread')
            or not self._bno_thread
            or not self._bno_thread.is_alive()
        ):
            self._bno_thread = threading.Thread(target=self._read_bno085_loop, daemon=True)
            self._bno_thread.start()

    @staticmethod
    def _quat_to_yaw_deg(quat):
        i, j, k, r = quat
        siny_cosp = 2.0 * (r * k + i * j)
        cosy_cosp = 1.0 - 2.0 * (j * j + k * k)
        yaw = math.degrees(math.atan2(siny_cosp, cosy_cosp))
        # Match app/map convention: clockwise turn (N->E) must increase heading.
        return (-yaw) % 360.0

    @staticmethod
    def _angle_delta_deg(current, previous):
        return ((current - previous + 180.0) % 360.0) - 180.0

    def _connect_bno_sensor(self, i2c):
        for address in (0x4A, 0x4B):
            try:
                sensor = BNO08X_I2C(i2c, address=address)
                return sensor, address
            except Exception:
                continue
        raise RuntimeError("No BNO085 found at 0x4A/0x4B")

    def _enable_bno_quaternion_mode(self, sensor):
        mode_name = "rotation"
        quat_attr = "quaternion"

        # Mirror the standalone working script behavior.
        try:
            sensor.soft_reset()
            time.sleep(1.2)
        except Exception:
            pass

        start_ts = time.monotonic()
        for _attempt in range(1, 6):
            try:
                self._log_bno(f"[BNO] enable mode={mode_name} attempt={_attempt}")
                sensor.enable_feature(BNO_REPORT_ROTATION_VECTOR, 100000)
                self._log_bno(f"[BNO] mode active: {mode_name}")
                return mode_name, quat_attr
            except Exception as exc:
                self._log_bno(f"[BNO] enable failed mode={mode_name} attempt={_attempt}: {exc}")
                time.sleep(0.4)
            if (time.monotonic() - start_ts) > 20.0:
                raise RuntimeError("BNO quaternion enable timed out")

        raise RuntimeError("BNO085 detected, but rotation quaternion report could not be enabled")

    def _log_bno(self, message):
        if self.rtcm_log_event is not None:
            self.rtcm_log_event(message)

    @staticmethod
    def _close_i2c_bus(i2c):
        try:
            deinit = getattr(i2c, "deinit", None)
            if callable(deinit):
                deinit()
        except Exception:
            pass

    def _recompute_heading_from_coordinates(self, reason="bno-restart"):
        """
        Recompute GNSS heading lock from the latest coordinate pair.
        Called when BNO reconnects/restarts so visual heading can be re-aligned.
        """
        if int(getattr(self, "rover_fix_type", 0) or 0) < 3 or int(getattr(self, "rover_carr_soln", 0) or 0) != 2:
            self._log_bno(
                f"[HEAD-RECALC] skipped ({reason}): need FIX>=3 and RTK FIX "
                f"(fix={getattr(self, 'rover_fix_type', 0)} carr={getattr(self, 'rover_carr_soln', 0)})"
            )
            return False
        if not hasattr(self, "rover_gnss") or self.rover_gnss is None:
            self._log_bno(f"[BNO] heading recalc skipped ({reason}): GNSS position unavailable")
            return False
        if not hasattr(self, "_prev_gnss_for_heading") or self._prev_gnss_for_heading is None:
            self._log_bno(f"[BNO] heading recalc skipped ({reason}): previous GNSS sample unavailable")
            return False

        prev_lat, prev_lon = self._prev_gnss_for_heading
        lat, lon = self.rover_gnss
        try:
            dist_m = self._haversine_distance_m(prev_lat, prev_lon, lat, lon)
        except Exception as exc:
            self._log_bno(f"[BNO] heading recalc failed ({reason}): {exc}")
            return False

        # Need a small movement baseline to produce a stable bearing.
        threshold = float(getattr(self, "bno_recalc_min_baseline_m", 0.70))
        if dist_m < threshold:
            self.bno_move_prompt_active = True
            self._log_bno(
                f"[HEAD-RECALC] skipped ({reason}): movement too small ({dist_m:.2f}m < {threshold:.2f}m)"
            )
            return False

        bearing = self._bearing_from_coords(prev_lat, prev_lon, lat, lon)
        self.gnss_heading_lock_deg = bearing
        self.gnss_heading_lock_cardinal = self._heading_to_cardinal(bearing)
        self.gnss_heading_lock_ts = time.time()
        self.rover_heading_deg = bearing
        self.rover_heading_cardinal = self.gnss_heading_lock_cardinal
        self.rover_north_offset_deg = self._signed_north_offset_deg(bearing)
        self.bno_move_prompt_active = False
        self._log_bno(
            f"[HEAD-RECALC] heading recalculated ({reason}): {bearing:.2f}° "
            f"({self.gnss_heading_lock_cardinal}) from {dist_m:.2f}m GNSS baseline"
        )
        return True

    def _read_bno085_loop(self):
        last_heading = None
        pending_offset_refresh = False
        while True:
            i2c = None
            try:
                i2c = busio.I2C(board.SCL, board.SDA, frequency=BNO_I2C_FREQUENCY_HZ)
                time.sleep(0.2)
                sensor, address = self._connect_bno_sensor(i2c)
                self._log_bno(f"[BNO] sensor detected at 0x{address:02X}, enabling quaternion...")
                mode, quat_attr = self._enable_bno_quaternion_mode(sensor)

                self.bno_connected = True
                self.bno_address = address
                self.bno_mode = mode
                self.bno_last_error = ""
                self.bno_recovering = False
                self.bno_reset_status_active = False
                self.bno_reset_state = "RUNNING_REBASED"
                if self.rtcm_log_event is not None and hasattr(self, 'gnss_heading_lock_deg') and self.gnss_heading_lock_deg is not None:
                    self.rtcm_log_event(
                        f"[BNO] started mode={mode} addr=0x{address:02X} offset={self.gnss_heading_lock_deg:.2f}°"
                    )

                read_error_streak = 0
                last_read_error_log_ts = 0.0
                last_quat = None
                stale_quat_streak = 0
                filtered_heading = None
                session_first_sample_latched = False
                session_started_ts = time.monotonic()
                first_heading_window = []
                pending_offset_cooldown_logged = False
                self.bno_first_heading_deg = None
                self.bno_first_is_valid = False
                while True:
                    try:
                        quat = getattr(sensor, quat_attr)

                        if quat is None:
                            raise RuntimeError("Quaternion not ready")

                        if quat == last_quat:
                            stale_quat_streak += 1
                        else:
                            stale_quat_streak = 0
                            last_quat = quat

                        # If the sensor keeps returning exactly the same quaternion for
                        # several seconds, treat it as a stalled I2C/sensor read path.
                        if stale_quat_streak >= 100:  # ~4 seconds at 25 Hz loop
                            raise RuntimeError(
                                "Quaternion stream stalled (repeated identical samples)"
                            )

                        heading_raw = self._quat_to_yaw_deg(quat)
                        if filtered_heading is None:
                            filtered_heading = heading_raw
                        else:
                            delta = self._angle_delta_deg(heading_raw, filtered_heading)
                            if abs(delta) > BNO_MAX_STEP_DEG:
                                self._log_bno(
                                    f"[BNO] jump filtered raw={heading_raw:.2f}° "
                                    f"base={filtered_heading:.2f}° delta={delta:.2f}°"
                                )
                                heading_raw = (
                                    filtered_heading + (BNO_MAX_STEP_DEG if delta > 0 else -BNO_MAX_STEP_DEG)
                                ) % 360.0
                            filtered_heading = (
                                filtered_heading
                                + BNO_HEADING_ALPHA * self._angle_delta_deg(heading_raw, filtered_heading)
                            ) % 360.0

                        heading = filtered_heading
                        self.bno_quaternion = quat
                        self.bno_heading_deg = heading
                        self.bno_heading_cardinal = self._heading_to_cardinal(heading)
                        last_heading = heading

                        if not session_first_sample_latched:
                            first_heading_window.append(heading)
                            if len(first_heading_window) > BNO_FIRST_STABLE_WINDOW:
                                first_heading_window = first_heading_window[-BNO_FIRST_STABLE_WINDOW:]

                            elapsed = time.monotonic() - session_started_ts
                            if elapsed >= BNO_FIRST_WARMUP_S and len(first_heading_window) >= BNO_FIRST_STABLE_WINDOW:
                                ref = first_heading_window[-1]
                                spread = max(
                                    abs(self._angle_delta_deg(sample, ref))
                                    for sample in first_heading_window
                                )
                                near_zero = all(
                                    abs(self._angle_delta_deg(sample, 0.0)) <= BNO_FIRST_REJECT_NEAR_ZERO_DEG
                                    for sample in first_heading_window
                                )
                                if spread <= BNO_FIRST_MAX_SPREAD_DEG and not near_zero:
                                    self.bno_first_heading_deg = ref
                                    self.bno_first_is_valid = True
                                    self.bno_reset_state = "LOCAL_FIRST_LATCHED"
                                    anchor = getattr(self, "bno_anchor_first_heading_deg", None)
                                    if anchor is None:
                                        self.bno_anchor_first_heading_deg = ref
                                        self.bno_rebase_delta_deg = 0.0
                                        self._log_bno(
                                            f"[BNO-ANCHOR] init first={ref:.2f}° delta=+0.00°"
                                        )
                                    else:
                                        self.bno_reset_state = "REBASE_PENDING"
                                        delta = self._angle_delta_deg(anchor, ref)
                                        self.bno_rebase_delta_deg = delta
                                        self._log_bno(
                                            f"[BNO-REBASE] anchor={anchor:.2f}° new_first={ref:.2f}° "
                                            f"delta={delta:+.2f}°"
                                        )
                                    self.bno_restart_calib_pos = self.rover_gnss
                                    session_first_sample_latched = True
                                    self.bno_reset_state = "RUNNING_REBASED"
                                    self.bno_reset_status_active = False
                                    if self._stored_heading_offset is None and self.gnss_heading_lock_deg is not None:
                                        # Bootstrap-only GNSS use: capture one initial north offset.
                                        self._stored_heading_offset = float(self.gnss_heading_lock_deg) % 360.0
                                        self._log_bno(
                                            f"[HEAD-OFFSET] bootstrap stored={self._stored_heading_offset:.2f}° "
                                            "(GNSS bootstrap only)"
                                        )
                                    if self.bno_restart_calib_pos is not None:
                                        lat, lon = self.bno_restart_calib_pos
                                        self._log_bno(
                                            f"[BNO-FIRST] raw={ref:.2f}° lat={lat:.8f} lon={lon:.8f} "
                                            f"window={BNO_FIRST_STABLE_WINDOW} spread={spread:.2f}°"
                                        )
                                    else:
                                        self._log_bno(
                                            f"[BNO-FIRST] raw={ref:.2f}° lat/lon unavailable "
                                            f"window={BNO_FIRST_STABLE_WINDOW} spread={spread:.2f}°"
                                        )
                                elif near_zero:
                                    # Keep waiting for a non-zero stable reference after reconnect.
                                    self._log_bno(
                                        f"[BNO-FIRST] rejected near-zero cluster "
                                        f"(window={BNO_FIRST_STABLE_WINDOW} spread={spread:.2f}°)"
                                    )

                        now_ts = time.time()
                        if (now_ts - float(getattr(self, "bno_last_raw_log_ts", 0.0))) >= 2.0:
                            self._log_bno(f"[BNO-RAW] yaw={heading:.2f}°")
                            self.bno_last_raw_log_ts = now_ts

                        if pending_offset_refresh:
                            if (time.monotonic() - session_started_ts) < BNO_POST_RECONNECT_COOLDOWN_S:
                                if not pending_offset_cooldown_logged:
                                    self.bno_reset_state = "WARMUP_WINDOW"
                                    self._log_bno(
                                        f"[BNO-RESTART] cooldown active {BNO_POST_RECONNECT_COOLDOWN_S:.1f}s, "
                                        "offset refresh delayed"
                                    )
                                    pending_offset_cooldown_logged = True
                                time.sleep(0.04)
                                continue
                            self._log_bno("[BNO-RESTART] reconnect recovered, applying BNO-only rebase")
                            self.bno_reset_state = "OFFSET_REFRESH_CHECK"
                            if not bool(getattr(self, "bno_first_is_valid", False)):
                                self._log_bno("[HEAD-OFFSET] skipped: bno_first not stable yet")
                            else:
                                self._log_bno(
                                    f"[HEAD-OFFSET] keep bootstrap={float(getattr(self, '_stored_heading_offset', 0.0)):+.2f}° "
                                    f"(rebase={float(getattr(self, 'bno_rebase_delta_deg', 0.0)):+.2f}°)"
                                )
                            if self.rover_gnss is not None and self.bno_restart_calib_pos is not None:
                                dist_m = self._haversine_distance_m(
                                    self.bno_restart_calib_pos[0],
                                    self.bno_restart_calib_pos[1],
                                    self.rover_gnss[0],
                                    self.rover_gnss[1],
                                )
                                threshold = float(getattr(self, "bno_move_prompt_threshold_m", 0.30))
                                self.bno_move_prompt_active = dist_m < threshold
                                self._log_bno(
                                    f"[BNO-RESTART] move-check dist={dist_m:.2f}m threshold={threshold:.2f}m "
                                    f"prompt={'ON' if self.bno_move_prompt_active else 'OFF'}"
                                )
                            pending_offset_refresh = False
                            pending_offset_cooldown_logged = False
                            self.bno_reset_status_active = False
                            self.bno_reset_state = "RUNNING_REBASED"
                        if read_error_streak:
                            self._log_bno(f"[BNO] read recovered after {read_error_streak} errors")
                            read_error_streak = 0
                    except Exception as exc:
                        read_error_streak += 1
                        now = time.monotonic()
                        # Always log first read failure, then rate-limit spam.
                        if read_error_streak == 1 or (now - last_read_error_log_ts) >= 2.0:
                            self._log_bno(
                                f"[BNO] read error streak={read_error_streak}: {exc}"
                            )
                            last_read_error_log_ts = now
                            err_list = list(getattr(self, "bno_error_ts_window", []))
                            now_ts = time.time()
                            err_list.append(now_ts)
                            err_list = [ts for ts in err_list if (now_ts - ts) <= BNO_ERROR_BURST_WINDOW_S]
                            self.bno_error_ts_window = err_list
                            if len(err_list) >= BNO_ERROR_BURST_THRESHOLD:
                                self.bno_block_until_ts = now_ts + BNO_BLOCK_DURATION_S
                                self._log_bno(
                                    f"[BNO-GUARD] burst errors={len(err_list)} in {BNO_ERROR_BURST_WINDOW_S:.0f}s "
                                    f"-> block BNO heading for {BNO_BLOCK_DURATION_S:.0f}s"
                                )
                        # Keep last valid heading exactly like the standalone script.
                        if last_heading is not None:
                            self.bno_heading_deg = last_heading
                            self.bno_heading_cardinal = self._heading_to_cardinal(last_heading)
                        if read_error_streak >= BNO_RECONNECT_ERROR_STREAK:
                            raise RuntimeError(
                                f"BNO read stalled: reconnect after {read_error_streak} read errors"
                            )

                    time.sleep(0.04)
            except Exception as exc:
                self.bno_connected = False
                self.bno_heading_deg = None
                self.bno_heading_cardinal = None
                self.bno_first_is_valid = False
                self.bno_mode = None
                self.bno_address = None
                self.bno_last_error = str(exc)
                self.bno_recovering = True
                self.bno_reset_status_active = True
                self.bno_reset_state = "RESET_DETECTED"
                pending_offset_refresh = True
                self.bno_move_prompt_active = False
                if self.rtcm_log_event is not None:
                    self.rtcm_log_event(f"[BNO] error: {exc}")
                self._close_i2c_bus(i2c)
                # Avoid hammering feature-enable calls when sensor is in a bad state.
                time.sleep(5.0)

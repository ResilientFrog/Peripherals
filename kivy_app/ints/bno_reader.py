import math
import time

import board
import busio
import adafruit_bno08x
from adafruit_bno08x import (
    BNO_REPORT_GAME_ROTATION_VECTOR,
)
from adafruit_bno08x.i2c import BNO08X_I2C

adafruit_bno08x._DEBUG = False
# Reduce blocking time during feature enable so failed init recovers quickly.
adafruit_bno08x._FEATURE_ENABLE_TIMEOUT = 2.5
# Use a conservative I2C clock for better stability on longer/noisy wiring.
# Standalone scripts in this project are stable in the 25-50 kHz range.
BNO_I2C_FREQUENCY_HZ = 50000
BNO_RECONNECT_ERROR_STREAK = 120
BNO_MAX_STEP_DEG = 12.0
BNO_HEADING_ALPHA = 0.35


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
        return yaw % 360.0

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
        mode_name = "game"
        quat_attr = "game_quaternion"

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
                sensor.enable_feature(BNO_REPORT_GAME_ROTATION_VECTOR, 100000)
                self._log_bno(f"[BNO] mode active: {mode_name}")
                return mode_name, quat_attr
            except Exception as exc:
                self._log_bno(f"[BNO] enable failed mode={mode_name} attempt={_attempt}: {exc}")
                time.sleep(0.4)
            if (time.monotonic() - start_ts) > 20.0:
                raise RuntimeError("BNO quaternion enable timed out")

        raise RuntimeError("BNO085 detected, but game quaternion report could not be enabled")

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
        if dist_m < 0.3:
            self._log_bno(
                f"[BNO] heading recalc skipped ({reason}): movement too small ({dist_m:.2f}m)"
            )
            return False

        bearing = self._bearing_from_coords(prev_lat, prev_lon, lat, lon)
        self.gnss_heading_lock_deg = bearing
        self.gnss_heading_lock_cardinal = self._heading_to_cardinal(bearing)
        self.gnss_heading_lock_ts = time.time()
        self.rover_heading_deg = bearing
        self.rover_heading_cardinal = self.gnss_heading_lock_cardinal
        self.rover_north_offset_deg = self._signed_north_offset_deg(bearing)
        self._log_bno(
            f"[BNO] heading recalculated ({reason}): {bearing:.2f}° "
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
                if self.rtcm_log_event is not None and hasattr(self, 'gnss_heading_lock_deg') and self.gnss_heading_lock_deg is not None:
                    self.rtcm_log_event(
                        f"[BNO] started mode={mode} addr=0x{address:02X} offset={self.gnss_heading_lock_deg:.2f}°"
                    )

                read_error_streak = 0
                last_read_error_log_ts = 0.0
                last_quat = None
                stale_quat_streak = 0
                filtered_heading = None
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
                        if pending_offset_refresh:
                            self._recompute_heading_from_coordinates(reason="post-bno-reconnect")
                            if self.store_gnss_heading_offset():
                                self._log_bno(
                                    f"[BNO] offset refreshed after reconnect: {self._stored_heading_offset:+.2f}°"
                                )
                            pending_offset_refresh = False
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
                self.bno_mode = None
                self.bno_address = None
                self.bno_last_error = str(exc)
                self.bno_recovering = True
                pending_offset_refresh = True
                if self.rtcm_log_event is not None:
                    self.rtcm_log_event(f"[BNO] error: {exc}")
                self._close_i2c_bus(i2c)
                # Avoid hammering feature-enable calls when sensor is in a bad state.
                time.sleep(5.0)

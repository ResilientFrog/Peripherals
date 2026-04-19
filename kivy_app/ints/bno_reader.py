import math
import time

import board
import busio
import adafruit_bno08x
from adafruit_bno08x import (
    BNO_REPORT_ROTATION_VECTOR,
    BNO_REPORT_GAME_ROTATION_VECTOR,
    BNO_REPORT_GEOMAGNETIC_ROTATION_VECTOR,
)
from adafruit_bno08x.i2c import BNO08X_I2C

adafruit_bno08x._DEBUG = False
adafruit_bno08x._FEATURE_ENABLE_TIMEOUT = 8.0


class BnoReaderMixin:

    @staticmethod
    def _quat_to_yaw_deg(quat):
        i, j, k, r = quat
        siny_cosp = 2.0 * (r * k + i * j)
        cosy_cosp = 1.0 - 2.0 * (j * j + k * k)
        yaw = math.degrees(math.atan2(siny_cosp, cosy_cosp))
        return yaw % 360.0

    def _connect_bno_sensor(self, i2c):
        for address in (0x4A, 0x4B):
            try:
                sensor = BNO08X_I2C(i2c, address=address)
                return sensor, address
            except Exception:
                continue
        raise RuntimeError("No BNO085 found at 0x4A/0x4B")

    def _enable_bno_quaternion_mode(self, sensor):
        modes = [
            ("rotation", BNO_REPORT_ROTATION_VECTOR),
            ("game", BNO_REPORT_GAME_ROTATION_VECTOR),
            ("geomagnetic", BNO_REPORT_GEOMAGNETIC_ROTATION_VECTOR),
        ]
        try:
            sensor.soft_reset()
            time.sleep(1.2)
        except Exception:
            pass

        for mode_name, report_id in modes:
            for attempt in range(1, 6):
                try:
                    sensor.enable_feature(report_id, 100000)
                    return mode_name
                except Exception:
                    time.sleep(0.4)

        raise RuntimeError("BNO085 detected, but no quaternion report could be enabled")

    def _read_bno085_loop(self):
        while True:
            try:
                if not self._gnss_heading_ready():
                    self.bno_connected = False
                    self.bno_heading_deg = None
                    self.bno_heading_cardinal = None
                    self.bno_mode = None
                    self.bno_address = None
                    self.bno_last_error = "waiting GNSS heading from movement"
                    time.sleep(0.5)
                    continue

                i2c = busio.I2C(board.SCL, board.SDA, frequency=100000)
                time.sleep(0.2)
                sensor, address = self._connect_bno_sensor(i2c)
                mode = self._enable_bno_quaternion_mode(sensor)

                self.bno_connected = True
                self.bno_address = address
                self.bno_mode = mode
                self.bno_last_error = ""
                self.bno_recovering = False
                if self.rtcm_log_event is not None and self.gnss_heading_lock_deg is not None:
                    self.rtcm_log_event(
                        f"[BNO] started mode={mode} addr=0x{address:02X} offset={self.gnss_heading_lock_deg:.2f}°"
                    )

                last_good_sample_ts = time.time()
                consecutive_read_errors = 0

                while True:
                    if not self._gnss_heading_ready():
                        raise RuntimeError("GNSS heading no longer ready")

                    try:
                        if mode == "rotation":
                            quat = sensor.quaternion
                        elif mode == "game":
                            quat = sensor.game_quaternion
                        else:
                            quat = sensor.geomagnetic_quaternion

                        if quat is None:
                            raise RuntimeError("Quaternion not ready")

                        heading = self._quat_to_yaw_deg(quat)
                        self.bno_quaternion = quat
                        self.bno_heading_deg = heading
                        self.bno_heading_cardinal = self._heading_to_cardinal(heading)
                        last_good_sample_ts = time.time()
                        consecutive_read_errors = 0
                    except Exception as exc:
                        consecutive_read_errors += 1
                        if consecutive_read_errors >= 25 or (time.time() - last_good_sample_ts) > 1.5:
                            raise RuntimeError(f"BNO sample stalled: {exc}")

                    time.sleep(0.04)
            except Exception as exc:
                self.bno_connected = False
                self.bno_heading_deg = None
                self.bno_heading_cardinal = None
                self.bno_mode = None
                self.bno_address = None
                self.bno_last_error = str(exc)
                self.bno_recovering = True
                if self.rtcm_log_event is not None:
                    self.rtcm_log_event(f"[BNO] error: {exc}")
                time.sleep(1)

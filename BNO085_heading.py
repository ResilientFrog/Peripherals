#!/usr/bin/env python3
"""
BNO085 Compass - Shows heading relative to North
"""

import time
import math
import board
import busio
from adafruit_bno08x import (
    BNO_REPORT_GAME_ROTATION_VECTOR,
)
from adafruit_bno08x.i2c import BNO08X_I2C

def quaternion_to_heading(quat):
    """Convert quaternion to compass heading in degrees"""
    i, j, k, real = quat
    
    heading = math.atan2(
        2.0 * (real * k + i * j),
        1.0 - 2.0 * (j * j + k * k)
    )
    
    heading = math.degrees(heading)
    if heading < 0:
        heading += 360
    
    return heading

def main():
    print("Initializing BNO085...")
    # Initialize I2C with slower speed for stability
    i2c = busio.I2C(board.SCL, board.SDA, frequency=100000)
    time.sleep(0.2)

    # Try both possible addresses
    try:
        bno = BNO08X_I2C(i2c, address=0x4A)
        print("Found BNO085 at 0x4A")
    except:
        bno = BNO08X_I2C(i2c, address=0x4B)
        print("Found BNO085 at 0x4B")
    '''
    # Reset the sensor
    print("Resetting sensor...")
    bno.soft_reset()
    time.sleep(1)
    '''
    mode_candidates = [
        ("game", BNO_REPORT_GAME_ROTATION_VECTOR, "game_quaternion"),
    ]

    active_quat_property = None
    print("Enabling compass mode...")
    for mode_name, report_id, quat_property in mode_candidates:
        for _ in range(3):
            try:
                bno.enable_feature(report_id, 100000)
                active_quat_property = quat_property
                print(f"Using {mode_name} mode")
                break
            except Exception:
                time.sleep(0.4)
        if active_quat_property is not None:
            break

    if active_quat_property is None:
        print("Could not enable any quaternion mode.")
        print("1. Check wiring (SDA, SCL, VCC, GND)")
        print("2. Verify I2C is enabled: sudo raspi-config")
        print("3. Check I2C devices: i2cdetect -y 1")
        return

    time.sleep(1.0)
    
    print("\nCompass Ready!")
    print("=" * 50)
    print("North=0° | East=90° | South=180° | West=270°\n")
    last_error_print = 0.0
    while True:
        try:
            quat = getattr(bno, active_quat_property)
            
            heading = quaternion_to_heading(quat)
            
            # Cardinal direction
            dirs = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
            direction = dirs[round(heading / 45) % 8]
            
            print(f"\r{heading:6.1f}° {direction:3s}", 
                    end='', flush=True)
            
            time.sleep(0.4)
        except KeyboardInterrupt:
            print("\n\nStopped")
            break
        except Exception as e:
            now = time.monotonic()
            if now - last_error_print > 3:
                print(f"\nError: {e}")
                last_error_print = now
            continue

if __name__ == "__main__":
    main()
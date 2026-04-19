import math
import time
import pygame
import board
import busio
import adafruit_bno08x

from adafruit_bno08x import (
    BNO_REPORT_ROTATION_VECTOR,
    BNO_REPORT_GAME_ROTATION_VECTOR,
    BNO_REPORT_GEOMAGNETIC_ROTATION_VECTOR,
)
from adafruit_bno08x.i2c import BNO08X_I2C

# Reduce driver noise
adafruit_bno08x._DEBUG = False
# Keep local workaround in app code only: some boards need longer first-report wait.
adafruit_bno08x._FEATURE_ENABLE_TIMEOUT = 8.0

# -------------------------
# Sensor setup
# -------------------------
i2c = busio.I2C(board.SCL, board.SDA, frequency=100000)
time.sleep(0.2)


def connect_bno_sensor():
    for address in (0x4A, 0x4B):
        try:
            sensor = BNO08X_I2C(i2c, address=address)
            print(f"Connected to BNO085 at 0x{address:02X}")
            return sensor
        except Exception:
            continue
    raise RuntimeError("No BNO085 found at 0x4A/0x4B")


def enable_quaternion_mode(sensor):
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
                print(f"Using mode: {mode_name}")
                return mode_name
            except Exception as exc:
                if attempt == 5:
                    print(f"Mode {mode_name} failed: {exc}")
                time.sleep(0.4)

    raise RuntimeError("BNO085 detected, but no quaternion report could be enabled")


bno = connect_bno_sensor()
active_mode = enable_quaternion_mode(bno)

def quaternion_to_yaw(q):
    i, j, k, r = q
    siny_cosp = 2.0 * (r * k + i * j)
    cosy_cosp = 1.0 - 2.0 * (j * j + k * k)
    return math.degrees(math.atan2(siny_cosp, cosy_cosp))

# -------------------------
# Pygame setup
# -------------------------
pygame.init()
size = 400
screen = pygame.display.set_mode((size, size))
pygame.display.set_caption("Compass")

clock = pygame.time.Clock()
center = size // 2
radius = 150

last_heading = 0.0

# -------------------------
# Main loop
# -------------------------
running = True
while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False

    # Read sensor (robust)
    try:
        if active_mode == "rotation":
            quat = bno.quaternion
        elif active_mode == "game":
            quat = bno.game_quaternion
        else:
            quat = bno.geomagnetic_quaternion

        if quat is None:
            raise RuntimeError("Quaternion not ready")

        heading = quaternion_to_yaw(quat)
        if heading < 0:
            heading += 360
        last_heading = heading
    except Exception:
        heading = last_heading

    # Draw compass
    screen.fill((0, 0, 0))
    pygame.draw.circle(screen, (255, 255, 255), (center, center), radius, 2)

    angle = math.radians(90 - heading)
    x = center + radius * math.cos(angle)
    y = center - radius * math.sin(angle)

    pygame.draw.line(
        screen,
        (255, 0, 0),
        (center, center),
        (x, y),
        4,
    )

    pygame.display.flip()
    clock.tick(30)  # stable frame rate

pygame.quit()
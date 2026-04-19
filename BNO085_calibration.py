import time
import board
import busio
from adafruit_bno08x.i2c import BNO08X_I2C
from adafruit_bno08x import (
    BNO_REPORT_ROTATION_VECTOR

)

i2c = busio.I2C(board.SCL, board.SDA, frequency=400000)
bno = BNO08X_I2C(i2c)

print("Soft reset...")
bno.soft_reset()

# Enable BOTH reports (this matters)
bno.enable_feature(BNO_REPORT_ROTATION_VECTOR, 40000)

print("Move the sensor slowly in all directions")
print("Accuracy: 0=Unreliable, 1=Low, 2=Medium, 3=High\n")

start = time.monotonic()
acc = bno.calibration_status
print( "Accuracy:", acc)
'''while True:
    try:
        quat = bno.quaternion
        game_quat = bno.game_quaternion
        
        last = time.monotonic()
        if time.monotonic() - last > 0.2:
            print(
                f"Acc: {acc} | "
                f"Quat: {quat} | "
                f"GameQuat: {game_quat}"
            )
       

        if acc == 3:
            print("✅ FULLY CALIBRATED")
            break

        if time.monotonic() - start > 180:
            print("⏱️ Timeout (3 minutes)")
            break

    except RuntimeError:
        # Happens occasionally if data not ready
        continue
    except KeyError as ke:
        # 3. HANDLE GLITCHES
        # If we get "Unprocessable Batch bytes", just skip this loop iteration
        # print(f"Glitch ignored: {e}") # Uncomment to see how often it happens
        continue
    except OSError as oe:
        # 3. HANDLE GLITCHES
        # If we get "Unprocessable Batch bytes", just skip this loop iteration
        # print(f"Glitch ignored: {e}") # Uncomment to see how often it happens
        continue
    except IndexError as ie:
        # 3. HANDLE GLITCHES
        # If we get "Unprocessable Batch bytes", just skip this loop iteration
        # print(f"Glitch ignored: {e}") # Uncomment to see how often it happens
        continue 
    except KeyboardInterrupt:
        print("\n\n✅ Stopped reading.")
'''
time.sleep(0.001)
#!/usr/bin/env python3
import argparse
import os
import time

import serial
from pyubx2 import SET, UBXMessage


def configure_rover(ser):
    def send(msg):
        ser.write(msg.serialize())
        ser.flush()
        time.sleep(0.1)

    print("⚙️  Configuring ZED-F9P as rover (runtime)...")

    send(
        UBXMessage(
            "CFG",
            "CFG-TMODE3",
            SET,
            version=0,
            flags=0,
        )
    )

    send(
        UBXMessage(
            "CFG",
            "CFG-PRT",
            SET,
            portID=3,
            inProtoMask=0x01 | 0x02 | 0x20,
            outProtoMask=0x01 | 0x02,
            flags=0,
        )
    )

    send(
        UBXMessage(
            "CFG",
            "CFG-MSG",
            SET,
            msgClass=0x01,
            msgID=0x07,
            rateUSB=1,
            rateUART1=0,
            rateUART2=0,
        )
    )

    print("✅ Rover configuration sent")
    print("ℹ️  Mode target: 3D fix without RTCM, RTK float/fixed when RTCM arrives")


def main():
    parser = argparse.ArgumentParser(description="Configure ZED-F9P to rover mode")
    parser.add_argument("--port", default="/dev/ttyACM0", help="Serial port")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate")
    parser.add_argument("--timeout", type=float, default=2.0, help="Serial timeout (s)")
    args = parser.parse_args()

    if not os.path.exists(args.port):
        print(f"❌ Device not found: {args.port}")
        print("ℹ️  Check cable and power, then unplug/replug the receiver")
        return 1

    try:
        with serial.Serial(args.port, args.baud, timeout=args.timeout) as ser:
            configure_rover(ser)
            return 0
    except Exception as exc:
        print(f"❌ Failed to configure ZED-F9P: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

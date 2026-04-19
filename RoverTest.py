import argparse
import time

import serial
from pyubx2 import UBXReader


def main():
    parser = argparse.ArgumentParser(description="Check ZED-F9P for 3D fix")
    parser.add_argument("--port", default="/dev/ttyACM0", help="Serial port")
    parser.add_argument("--baud", type=int, default=115200, help="Baudrate")
    parser.add_argument(
        "--max-wait",
        type=int,
        default=180,
        help="Maximum seconds to wait for 3D fix (default: 180)",
    )
    args = parser.parse_args()

    print("STATUS: WAITING_FOR_3D")
    started = time.time()
    last_status = None

    try:
        with serial.Serial(args.port, args.baud, timeout=1) as ser:
            ubr = UBXReader(ser, protfilter=3)

            while True:
                if time.time() - started >= args.max_wait:
                    print("STATUS: TIMEOUT_NO_3D")
                    return 1

                try:
                    _raw, msg = ubr.read()
                except Exception:
                    continue

                if not msg or msg.identity != "NAV-PVT":
                    continue

                fix_type = int(getattr(msg, "fixType", 0))
                if fix_type >= 3:
                    print("STATUS: 3D_FIX")
                    return 0

                status = f"STATUS: NOT_3D fixType={fix_type} sats={int(getattr(msg, 'numSV', 0))}"
                if status != last_status:
                    print(status)
                    last_status = status

    except Exception as exc:
        print(f"STATUS: ERROR {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
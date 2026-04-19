# Rover Peripheral Control - AI Coding Agent Guide

## Project Overview
This is a **Raspberry Pi robotics rover** project focused on GNSS/RTK positioning and sensor integration. The codebase controls:
- **ZED-F9P**: Dual-frequency GNSS receiver (u-blox) with RTK support for meter-level accuracy
- **BNO085**: 9-axis IMU (Adafruit) for heading/rotation via I2C
- **WiFi**: NetworkManager-based connectivity on RPi

The architecture follows a **modular peripheral pattern**: each sensor has dedicated driver/configuration files that can be tested independently.

## Critical Architecture Patterns

### GNSS/RTK Data Flow (ZED-F9P)
- **Port**: `/dev/ttyACM0` at 115200 baud (USB serial)
- **Protocol**: UBX binary (primary) + NMEA (fallback) via `pyubx2` library
- **Core message**: `NAV-PVT` (Position, Velocity, Time) - contains:
  - `lat/lon` (in 1e-7 degrees - **must divide by 1e-7**)
  - `gSpeed` (mm/s - **must divide by 1000** for m/s)
  - `headMot` (heading in 1e5 degrees - **must divide by 1e5**)
  - `fixType` (0=no fix, 3=3D) and `carrSoln` (RTK solution type)
  - `hAcc` (horizontal accuracy in mm - **must divide by 1000**)
- **Key flow**: 
  - Setup mode: `Rover_setup.py` configures device at runtime (no flash needed)
  - Read mode: `UBXReader(ser, protfilter=3)` accepts UBX (bit 1) + NMEA (bit 2)
  - **USB stability critical**: `time.sleep(0.1)` required between writes on RPi

### BNO085 IMU (I2C Interface)
- **Library**: Adafruit's `adafruit_bno08x` over I2C (400kHz typical)
- **Calibration**: `BNO085_calibration.py` uses `ROTATION_VECTOR` report with status tracking
- **Quaternion modes**:
  - `quaternion`: Absolute orientation (9-axis)
  - `game_quaternion`: Relative (no magnetometer drift)
- **Common pattern**: Enable features first, then poll in loop with ~40ms intervals

### WiFi Management (NetworkManager)
- **Tool**: `nmcli` (NetworkManager CLI)
- **Pattern**: Direct subprocess calls to enable/list networks
- **Device**: Typical RPi WiFi over wlan0

## Developer Workflows

### Testing Sensors
```bash
# Setup virtual environment (required)
source .venv/bin/activate

# Individual sensor tests:
python3 Rover_ReadGNSS.py        # Check GNSS connectivity
python3 BNO085_calibration.py    # Calibrate IMU
python3 WiFiConnection.py        # Test/enable WiFi

# Full integration tests:
python3 RoverTest.py             # Combined sensor read
python3 Rover_setup.py           # Configure ZED-F9P
```

### Jupyter Notebooks (Experimental/Development)
- `test_F9P.ipynb`: ZED-F9P experimentation
- `test_Wi-Fi.ipynb`: WiFi troubleshooting
- Launch via `setup.sh`: `jupyter lab --no-browser` with empty token (development only)

## Project-Specific Conventions

### Error Handling
- **Device discovery**: Always check `/dev/ttyACM0` exists before opening. Print helpful hints ("Did you remember to unplug/replug?")
- **Serial communication**: Use try/except around `ubr.read()` loops - incomplete messages are common
- **Subprocess calls**: Wrap `nmcli` commands with `check=True` and catch `FileNotFoundError` (nmcli may not be installed)

### Unit Conversion (Critical)
These are **non-standard** and frequently misapplied:
| Value | Raw → SI | Example |
|-------|----------|---------|
| Latitude/Longitude | `÷ 1e-7` | `msg.lat * 1e-7` |
| Heading (headMot) | `÷ 1e5` | `msg.headMot / 1e5` |
| Speed (gSpeed) | `mm/s → m/s` | `msg.gSpeed / 1000` |
| Height (hMSL) | `mm → m` | `msg.hMSL / 1000` |
| Accuracy (hAcc) | `mm → m` | `msg.hAcc / 1000` |

### Status Reporting
- Use emoji prefixes for clarity: `✅ Success`, `❌ Error`, `📡 Reading`, `⚙️  Config`, `ℹ️  Info`
- Always distinguish message types in output: `[NAV-PVT]`, `[NMEA]`, `[RTK]`
- Print `fixType` and `carrSoln` (RTK solution: 0=none, 1=float, 2=fixed)

## Integration Points & Dependencies

### External Libraries
- `pyubx2`: u-blox protocol parsing (GNSS)
- `adafruit_bno08x`: IMU sensor driver (I2C)
- `serial`: PySerial for device communication
- `busio`, `board`: Blinka I2C/GPIO abstractions (RPi)

### Hardware Requirements
- Raspberry Pi 4+ with:
  - USB-A to USB-C cable (ZED-F9P)
  - I2C bus (SDA/SCL) for BNO085
  - NetworkManager installed (`nmcli` available)

### Device Files (Stable)
- `/dev/ttyACM0`: ZED-F9P (USB serial) - consistent across RPi boots
- I2C address `0x4A` or `0x4B`: BNO085 (configurable on board)

## Debugging Tips
1. **GNSS locked?** Check `fixType == 3` and `numSV > 4`
2. **No RTK fix?** Verify RTCM3 input enabled in `CFG-PRT` and base station nearby
3. **IMU not calibrating?** Ensure slow circular motions; check `calibration_status` (0-3)
4. **Serial timeout?** Add `print(raw)` in loop to confirm bytes arriving
5. **RPi USB instability?** Increase `time.sleep()` to 0.2s; use powered USB hub

## Files by Role
- **Drivers**: `ZEDF9P.py` (minimal read), `BNO085_*.py` (feature-specific)
- **Configuration**: `Rover_setup.py` (ZED-F9P runtime config)
- **Integration**: `RoverTest.py`, `kickstart_device.py`
- **Utilities**: `WiFiConnection.py`
- **Bootstrap**: `setup.sh` (Jupyter), `.venv/` (Python environment)

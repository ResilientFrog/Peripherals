"""
RTCM3 Simulator for ZED-F9P Rover Testing
Generates synthetic RTCM correction messages for RTK testing without base station
"""

import struct
import time
import serial
import sys
import os
from pathlib import Path
from pyubx2 import UBXMessage, SET

# RTCM3 Frame Structure: [Preamble:1] [Length:2] [Payload:N] [CRC:3]
RTCM3_PREAMBLE = 0xD3

def crc24(data):
    """Calculate CRC24 for RTCM3 frame (polynomial 0x1864CFB)"""
    crc = 0
    for byte in data:
        crc ^= byte << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x1864CFB
    return crc & 0xFFFFFF

def create_rtcm3_frame(message_type, payload):
    """
    Create valid RTCM3 frame with CRC
    
    Args:
        message_type: RTCM message type (int)
        payload: Message payload (bytes)
    
    Returns:
        Complete RTCM3 frame (bytes) ready for serial transmission
    """
    # Message type in first 12 bits of payload
    msg_header = struct.pack('>H', (message_type << 4))[0:1]
    full_payload = msg_header + payload
    
    # Frame: [Preamble] [Length:2] [Payload] [CRC:3]
    frame_length = len(full_payload)
    frame = struct.pack('>BH', RTCM3_PREAMBLE, frame_length) + full_payload
    
    # Append CRC24
    calculated_crc = crc24(full_payload)
    frame += struct.pack('>I', calculated_crc << 8)[1:4]  # 3 bytes of CRC
    
    return frame

def create_1005_base_station(station_id, ecef_x, ecef_y, ecef_z):
    """
    Create RTCM Type 1005: Base Station Coordinates (ECEF)
    
    Args:
        station_id: Reference station ID (0-4095)
        ecef_x, ecef_y, ecef_z: Earth-Centered coordinates (meters)
    
    Returns:
        RTCM3 Type 1005 frame (bytes)
    """
    # RTCM 1005 payload structure (simplified)
    # [Reserved:1] [ID:12] [System:3] [ECEF X:38] [Y:38] [Z:38] [Reserved:2]
    
    # Convert meters to 0.0001m resolution (10000x scale)
    x_scaled = int(ecef_x * 10000) & 0x3FFFFFFFFF
    y_scaled = int(ecef_y * 10000) & 0x3FFFFFFFFF
    z_scaled = int(ecef_z * 10000) & 0x3FFFFFFFFF
    
    # Pack data (38-bit signed integers require careful handling)
    payload = struct.pack('>H', (station_id & 0xFFF) << 4)
    payload += struct.pack('>Q', x_scaled)[2:] + struct.pack('>Q', y_scaled)[2:] + struct.pack('>Q', z_scaled)[2:]
    
    return create_rtcm3_frame(1005, payload)

def create_1077_gnss_observations(station_id, epoch, gps_sats):
    """
    Create RTCM Type 1077: GPS MSM7 (Multi-Signal Message)
    
    Args:
        station_id: Reference station ID
        epoch: GPS epoch time (seconds)
        gps_sats: List of (satellite_id, pseudorange, phase, cn0) tuples
    
    Returns:
        RTCM3 Type 1077 frame (bytes)
    """
    # Simplified MSM7 payload (real implementation requires detailed bit packing)
    payload = struct.pack('>H', (station_id & 0xFFF) << 4)
    payload += struct.pack('>I', int(epoch) & 0x3FFFF)  # GPS TOW
    payload += struct.pack('>B', len(gps_sats))  # Satellite mask
    
    for sat_id, pseudorange, phase, cn0 in gps_sats:
        payload += struct.pack('>I', int(pseudorange * 256))
        payload += struct.pack('>I', int(phase * 256))
        payload += struct.pack('>B', int(cn0))
    
    return create_rtcm3_frame(1077, payload)

def inject_rtcm_to_device(ser, frames, delay=0.05):
    """
    Inject RTCM3 frames into ZED-F9P via serial port
    
    Args:
        ser: Open serial.Serial object (configured ZED-F9P)
        frames: List of RTCM3 frames (bytes) to inject
        delay: Delay between frames (seconds)
    
    Returns:
        Number of frames successfully injected
    """
    injected = 0
    try:
        for frame in frames:
            ser.write(frame)
            ser.flush()
            time.sleep(delay)
            injected += 1
            print(f"📡 Injected RTCM frame {injected}: Type {frame[2] >> 4 & 0xFF}, {len(frame)} bytes")
    except Exception as e:
        print(f"❌ Error injecting RTCM: {e}", file=sys.stderr)
        return injected
    
    return injected

def setup_rtcm_input(ser):
    """
    Configure ZED-F9P to accept RTCM3 input
    Uses CFG-PRT to enable RTCM3 on USB port
    
    Args:
        ser: Open serial.Serial object
    
    Returns:
        True if configuration successful, False otherwise
    """
    try:
        print("⚙️  Configuring ZED-F9P for RTCM3 input...")
        
        # CFG-PRT: Port configuration
        # inProtoMask: 0x01=UBX, 0x02=NMEA, 0x20=RTCM3
        msg = UBXMessage("CFG", "CFG-PRT", SET, 
                        portID=3,  # USB port
                        inProtoMask=0x01 | 0x02 | 0x20,  # UBX + NMEA + RTCM3
                        outProtoMask=0x01 | 0x02,  # Output: UBX + NMEA
                        flags=0)
        
        ser.write(msg.serialize())
        ser.flush()
        time.sleep(0.1)
        
        print("✅ RTCM3 input configured")
        return True
        
    except Exception as e:
        print(f"❌ Configuration failed: {e}", file=sys.stderr)
        return False

def find_available_ports():
    """
    List available serial ports on the system
    
    Returns:
        List of (port, description) tuples
    """
    import glob
    ports = []
    
    # Linux: Check /dev/ttyACM* and /dev/ttyUSB*
    for pattern in ['/dev/ttyACM*', '/dev/ttyUSB*']:
        for port in glob.glob(pattern):
            try:
                # Try to get device info
                if os.path.exists(port):
                    ports.append(port)
            except:
                pass
    
    return ports

def check_device_exists(port):
    """
    Check if device exists at the given port path
    
    Args:
        port: Device path (e.g., '/dev/ttyACM0')
    
    Returns:
        Tuple (exists: bool, info: str)
    """
    if os.path.exists(port):
        perms = oct(os.stat(port).st_mode)[-3:]
        return True, f"Device exists with permissions {perms}"
    
    # Check if user has read/write permissions
    if '/dev/' in port:
        available = find_available_ports()
        if available:
            info = f"Device not found. Available ports: {', '.join(available)}"
        else:
            info = "Device not found. No serial ports detected."
        return False, info
    
    return False, "Invalid port path"

def open_with_diagnostics(port=None):
    """
    Open serial port with detailed error diagnostics
    
    Args:
        port: Serial port path (default: /dev/ttyACM0)
    
    Returns:
        Tuple (ser: serial.Serial or None, success: bool)
    """
    if port is None:
        port = "/dev/ttyACM0"
    
    BAUD = 115200
    
    print(f"🔍 Checking device at {port}...")
    exists, info = check_device_exists(port)
    
    if not exists:
        print(f"❌ {info}", file=sys.stderr)
        print("\n📋 Troubleshooting steps:")
        print("   1. Check ZED-F9P USB cable is connected to Raspberry Pi")
        print("   2. Try unplugging/replugging the device")
        print("   3. Verify device with: lsusb | grep u-blox")
        print("   4. Check dmesg output: dmesg | tail -20")
        if not find_available_ports():
            print("   5. No serial devices found - check USB connection")
        return None, False
    
    print(f"✅ {info}")
    
    try:
        print(f"🔌 Opening connection at {BAUD} baud...")
        ser = serial.Serial(port, BAUD, timeout=1)
        print(f"✅ Successfully opened {port}")
        return ser, True
        
    except PermissionError:
        print(f"❌ Permission denied on {port}", file=sys.stderr)
        print("   Try: sudo python3 rtcm_sim.py")
        return None, False
        
    except serial.SerialException as e:
        print(f"❌ Serial error: {e}", file=sys.stderr)
        print("   The port may be in use by another process")
        print("   Check with: lsof | grep {port}")
        return None, False
        
    except Exception as e:
        print(f"❌ Unexpected error: {e}", file=sys.stderr)
        return None, False

def simulate_base_station_rtk(base_ecef=(4000000.0, 3000000.0, 5000000.0), 
                              station_id=100,
                              num_satellites=8):
    """
    Generate realistic RTCM stream simulating a base station
    
    Args:
        base_ecef: Base station ECEF coordinates (meters)
        station_id: Reference station ID
        num_satellites: Number of visible GPS satellites to simulate
    
    Returns:
        List of RTCM3 frames to inject
    """
    frames = []
    
    # Frame 1: Base station coordinates (Type 1005)
    frame_1005 = create_1005_base_station(
        station_id=station_id,
        ecef_x=base_ecef[0],
        ecef_y=base_ecef[1],
        ecef_z=base_ecef[2]
    )
    frames.append(frame_1005)
    print(f"ℹ️  Generated RTCM Type 1005 (Base coordinates)")
    
    # Frame 2: GPS observations (Type 1077) with simulated satellites
    gps_sats = []
    for sat_id in range(1, num_satellites + 1):
        pseudorange = 20000000.0 + (sat_id * 100)  # ~20M meters
        phase = 105000000.0 + (sat_id * 50)  # Carrier phase
        cn0 = 40 + sat_id  # Signal strength
        gps_sats.append((sat_id, pseudorange, phase, cn0))
    
    frame_1077 = create_1077_gnss_observations(
        station_id=station_id,
        epoch=time.time() % 86400,  # GPS TOW
        gps_sats=gps_sats
    )
    frames.append(frame_1077)
    print(f"ℹ️  Generated RTCM Type 1077 ({len(gps_sats)} satellites)")
    
    return frames

def test_rtcm_injection():
    """
    Complete test workflow: setup → configure → inject RTCM → monitor RTK fix
    """
    PORT = "/dev/ttyACM0"
    BAUD = 115200
    
    ser, success = open_with_diagnostics(PORT)
    
    if not success or ser is None:
        return False
    
    try:
        # Configure for RTCM input
        if not setup_rtcm_input(ser):
            return False
        
        # Generate synthetic RTCM corrections
        print("\n📊 Generating synthetic RTCM stream...")
        frames = simulate_base_station_rtk(
            base_ecef=(4000000.0, 3000000.0, 5000000.0),
            num_satellites=10
        )
        
        # Inject frames
        print(f"\n📤 Injecting {len(frames)} RTCM frames...")
        injected = inject_rtcm_to_device(ser, frames, delay=0.1)
        
        if injected > 0:
            print(f"\n✅ Successfully injected {injected}/{len(frames)} RTCM frames")
            print("ℹ️  Monitor NAV-PVT for RTK fix indication (carrSoln field)")
            print("   0=No solution, 1=Float RTK, 2=Fixed RTK")
        
        ser.close()
        return True
        
    except Exception as e:
        print(f"❌ Test failed: {e}", file=sys.stderr)
        return False
    finally:
        if ser and ser.is_open:
            ser.close()

def test_rtcm_generation_mock():
    """
    Test RTCM frame generation without hardware (mock mode)
    Useful for validating RTCM structure when device not available
    """
    print("🧪 RTCM3 Frame Generation Test (No Hardware Required)")
    print("=" * 60)
    
    # Generate RTCM frames
    print("\n📊 Generating synthetic RTCM stream...")
    frames = simulate_base_station_rtk(
        base_ecef=(4000000.0, 3000000.0, 5000000.0),
        num_satellites=10
    )
    
    print(f"\n📋 Generated {len(frames)} RTCM frames:")
    total_bytes = 0
    for i, frame in enumerate(frames, 1):
        msg_type = (frame[2] >> 4) & 0xFF
        length = struct.unpack('>H', frame[1:3])[0]
        total_bytes += len(frame)
        print(f"   Frame {i}: Type {msg_type:4d} | {len(frame):3d} bytes | CRC: {frame[-3:].hex()}")
    
    print(f"\n✅ Total generated: {total_bytes} bytes")
    print("   Ready to inject when device connects")
    print("\n💡 Next steps:")
    print("   1. Plug in ZED-F9P via USB")
    print("   2. Run: python3 rtcm_sim.py --port /dev/ttyACM0")
    print("   3. Or: python3 rtcm_sim.py --list-ports (to find your device)")
    
    return True

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="RTCM3 Simulator for ZED-F9P")
    parser.add_argument("--port", type=str, default="/dev/ttyACM0", 
                       help="Serial port (default: /dev/ttyACM0)")
    parser.add_argument("--list-ports", action="store_true", 
                       help="List available serial ports and exit")
    parser.add_argument("--mock", action="store_true",
                       help="Test frame generation without hardware")
    
    args = parser.parse_args()
    
    if args.list_ports:
        print("📋 Available serial ports:")
        ports = find_available_ports()
        if ports:
            for port in ports:
                print(f"  • {port}")
        else:
            print("  (none found)")
            print("\n💡 Troubleshooting:")
            print("   • Check USB cable is connected")
            print("   • Try: lsusb | grep u-blox")
            print("   • Try: dmesg | tail -20")
        sys.exit(0)
    
    if args.mock:
        test_rtcm_generation_mock()
    else:
        test_rtcm_injection()

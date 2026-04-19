import subprocess
import sys
import os
import getpass

# Enforce sudo/elevated privileges requirement
if os.geteuid() != 0:
    print("❌ This script must be run with sudo privileges")
    print("Usage: sudo python3 WiFiConnection.py")
    sys.exit(1)

# ESP32 Network Configuration
ESP32_SSID = "ESP32_Config_Node"  # Default ESP32 SSID - update as needed
ESP32_SECURITY = "open"      # "open", "wpa", or "wep"
ESP32_PASSWORD = None        # Set to None for open network, or provide password

def enable_wifi():
    """Enable WiFi on Raspberry Pi"""
    try:
        # Enable WiFi radio
        subprocess.run(['nmcli', 'radio', 'wifi', 'on'], check=True)
        print("✅ WiFi enabled successfully")
        
        # List available networks
        result = subprocess.run(['nmcli', 'dev', 'wifi', 'list'], 
                              capture_output=True, text=True)
        print("\n📡 Available networks:")
        print(result.stdout)
        
    except subprocess.CalledProcessError as e:
        print(f"❌ Error enabling WiFi: {e}", file=sys.stderr) 
        sys.exit(1)
    except FileNotFoundError:
        print("❌ nmcli not found. Install NetworkManager.", file=sys.stderr)
        sys.exit(1)

def connect_to_esp32(ssid=ESP32_SSID, password=ESP32_PASSWORD, security=ESP32_SECURITY):
    """Connect to ESP32 WiFi network"""
    try:
        print(f"\n⚙️  Connecting to ESP32 network: {ssid}...")
        
        if security.lower() == "open":
            # Open network (no password required)
            subprocess.run([
                'nmcli', 'device', 'wifi', 'connect', ssid
            ], check=True)
            print(f"✅ Connected to {ssid}")
            
        elif security.lower() in ["wpa", "wep"]:
            # Secured network (WPA/WEP)
            if password is None:
                password = getpass.getpass(f"Enter password for {ssid}: ")
            
            subprocess.run([
                'nmcli', 'device', 'wifi', 'connect', ssid,
                'password', password
            ], check=True)
            print(f"✅ Connected to {ssid}")
        else:
            print(f"❌ Unknown security type: {security}", file=sys.stderr)
            return False
            
        # Verify connection
        result = subprocess.run(['nmcli', 'connection', 'show', '--active'],
                              capture_output=True, text=True)
        if ssid in result.stdout:
            print(f"ℹ️  Active connection verified")
            return True
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to connect: {e}", file=sys.stderr)
        return False
    except FileNotFoundError:
        print("❌ nmcli not found. Install NetworkManager.", file=sys.stderr)
        return False

def disconnect_wifi(ssid=ESP32_SSID):
    """Disconnect from WiFi network"""
    try:
        print(f"\n⚙️  Disconnecting from {ssid}...")
        subprocess.run(['nmcli', 'connection', 'delete', ssid], check=True)
        print(f"✅ Disconnected from {ssid}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to disconnect: {e}", file=sys.stderr)
        return False

def get_connection_status():
    """Get current WiFi connection status"""
    try:
        result = subprocess.run(['nmcli', 'connection', 'show', '--active'],
                              capture_output=True, text=True)
        print("\n📊 Active Connections:")
        print(result.stdout if result.stdout else "No active connections")
        return result.stdout
    except FileNotFoundError:
        print("❌ nmcli not found.", file=sys.stderr)
        return None

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="RPi WiFi Management for Rover")
    parser.add_argument("--enable", action="store_true", help="Enable WiFi and list networks")
    parser.add_argument("--connect", action="store_true", help="Connect to ESP32 network")
    parser.add_argument("--disconnect", action="store_true", help="Disconnect from network")
    parser.add_argument("--status", action="store_true", help="Show connection status")
    parser.add_argument("--ssid", type=str, help="Custom SSID (default: ESP32-Rover)")
    parser.add_argument("--password", type=str, help="WiFi password (prompted if not provided)")
    
    args = parser.parse_args()
    
    ssid = args.ssid or ESP32_SSID
    password = args.password or ESP32_PASSWORD
    
    if args.enable:
        enable_wifi()
    elif args.connect:
        connect_to_esp32(ssid=ssid, password=password)
    elif args.disconnect:
        disconnect_wifi(ssid=ssid)
    elif args.status:
        get_connection_status()
    else:
        # Default: enable WiFi and connect to ESP32
        enable_wifi()
        connect_to_esp32(ssid=ssid, password=password)
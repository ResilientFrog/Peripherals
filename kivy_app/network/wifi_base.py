import subprocess


def _nmcli_run(args, timeout=12):
    return subprocess.run(
        ["nmcli", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _active_hotspot_connections(interface: str = "wlan0"):
    active = _nmcli_run(["-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"], timeout=8)
    if active.returncode != 0:
        return []

    hotspot_names = []
    for line in active.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.strip().split(":")
        if len(parts) < 3:
            continue
        name, conn_type, device = parts[0], parts[1], parts[2]
        if conn_type != "wifi" or device != interface:
            continue

        mode = _nmcli_run(["-g", "802-11-wireless.mode", "connection", "show", name], timeout=8)
        mode_text = (mode.stdout or "").strip().lower()
        if mode.returncode == 0 and mode_text == "ap":
            hotspot_names.append(name)

    return hotspot_names


def ensure_hotspot_mode(
    ssid: str,
    password: str,
    interface: str = "wlan0",
    connection_name: str = "RoverHotspot",
) -> bool:
    """Ensure Raspberry Pi runs as a WiFi hotspot via NetworkManager."""
    if not ssid:
        print("⚠️  Hotspot skipped: empty SSID")
        return False

    try:
        hotspots = _active_hotspot_connections(interface=interface)
        if connection_name in hotspots:
            print(f"✅ Hotspot already active ('{connection_name}')")
            return True

        if hotspots:
            for conn_name in hotspots:
                _nmcli_run(["connection", "down", conn_name], timeout=12)

        existing = _nmcli_run(["-t", "-f", "NAME", "connection", "show"], timeout=8)
        names = {line.strip() for line in existing.stdout.splitlines() if line.strip()} if existing.returncode == 0 else set()

        if connection_name in names:
            up = _nmcli_run(["connection", "up", connection_name], timeout=20)
            if up.returncode == 0:
                print(f"✅ Hotspot active ('{connection_name}')")
                return True

        create = _nmcli_run(
            [
                "device",
                "wifi",
                "hotspot",
                "ifname",
                interface,
                "con-name",
                connection_name,
                "ssid",
                ssid,
                "password",
                password,
            ],
            timeout=25,
        )
        if create.returncode == 0:
            print(f"✅ Hotspot started on {interface} (SSID '{ssid}')")
            return True

        detail = (create.stderr or create.stdout or f"exit={create.returncode}").strip()
        print(f"⚠️  Failed to start hotspot: {detail}")
        return False
    except FileNotFoundError:
        print("⚠️  nmcli not found; cannot manage hotspot")
        return False
    except Exception as exc:
        print(f"⚠️  Hotspot error: {exc}")
        return False


def switch_hotspot_to_base(
    ssid: str,
    password: str,
    interface: str = "wlan0",
) -> tuple[bool, str]:
    """Disable active hotspot on interface, then connect to base SSID."""
    try:
        hotspots = _active_hotspot_connections(interface=interface)
        for conn_name in hotspots:
            down = _nmcli_run(["connection", "down", conn_name], timeout=15)
            if down.returncode == 0:
                print(f"✅ Hotspot disabled ('{conn_name}')")
            else:
                detail = (down.stderr or down.stdout or f"exit={down.returncode}").strip()
                print(f"⚠️  Could not disable hotspot '{conn_name}': {detail}")

        connected = ensure_base_wifi_connection(ssid=ssid, password=password, interface=interface)
        if connected:
            return True, f"Connected to base SSID '{ssid}'"
        return False, f"Failed to connect to base SSID '{ssid}'"
    except FileNotFoundError:
        return False, "nmcli not found"
    except Exception as exc:
        return False, str(exc)


def ensure_base_wifi_connection(ssid: str, password: str, interface: str = "wlan0") -> bool:
    """Ensure rover is connected to the base AP using NetworkManager (nmcli)."""
    if not ssid:
        print("⚠️  WiFi auto-connect skipped: empty SSID")
        return False

    try:
        current = subprocess.run(
            ["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"],
            capture_output=True,
            text=True,
            check=False,
            timeout=6,
        )
        active_lines = [line.strip() for line in current.stdout.splitlines() if line.strip()]
        if any(line == f"yes:{ssid}" for line in active_lines):
            print(f"✅ WiFi already connected to base SSID '{ssid}'")
            return True

        print(f"📶 Connecting WiFi to base SSID '{ssid}' on {interface}...")

        def try_connect(timeout_seconds: int):
            return subprocess.run(
                [
                    "nmcli",
                    "device",
                    "wifi",
                    "connect",
                    ssid,
                    "password",
                    password,
                    "ifname",
                    interface,
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_seconds,
            )

        connect = try_connect(timeout_seconds=20)
        if connect.returncode == 0:
            print(f"✅ WiFi connected to base SSID '{ssid}'")
            return True

        subprocess.run(
            ["nmcli", "device", "wifi", "rescan", "ifname", interface],
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
        )
        connect_retry = try_connect(timeout_seconds=20)
        if connect_retry.returncode == 0:
            print(f"✅ WiFi connected to base SSID '{ssid}' (after rescan)")
            return True

        stderr = (connect_retry.stderr or connect.stderr or "").strip()
        stdout = (connect_retry.stdout or connect.stdout or "").strip()
        detail = stderr or stdout or f"exit={connect_retry.returncode}"
        print(f"⚠️  WiFi connect failed for SSID '{ssid}': {detail}")
        return False
    except FileNotFoundError:
        print("⚠️  nmcli not found; cannot auto-connect WiFi")
        return False
    except Exception as exc:
        print(f"⚠️  WiFi auto-connect error: {exc}")
        return False

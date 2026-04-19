import subprocess
import time


def _nmcli_run(args, timeout=12):
    result = subprocess.run(
        ["nmcli", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )

    err_text = f"{result.stderr or ''} {result.stdout or ''}".lower()
    auth_errors = (
        "insufficient privileges",
        "not authorized to control networking",
        "not authorized",
        "permission denied",
    )
    if result.returncode != 0 and any(token in err_text for token in auth_errors):
        try:
            return subprocess.run(
                ["sudo", "-n", "nmcli", *args],
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except FileNotFoundError:
            return result

    return result


def _active_hotspot_connections(interface: str = "wlan1"):
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


def _interface_supports_ap(interface: str) -> bool:
    result = _nmcli_run(["-g", "WIFI-PROPERTIES.AP", "device", "show", interface], timeout=8)
    if result.returncode != 0:
        return False
    return (result.stdout or "").strip().lower() == "yes"


def _pick_hotspot_interface(preferred_interface: str) -> str | None:
    candidates = []
    if preferred_interface:
        candidates.append(preferred_interface)
    for iface in ("wlan0", "wlan1"):
        if iface not in candidates:
            candidates.append(iface)

    for iface in candidates:
        if _interface_supports_ap(iface):
            return iface
    return None


def _wifi_profiles_for_ssid(ssid: str):
    result = _nmcli_run(["-t", "-f", "NAME,802-11-wireless.ssid", "connection", "show"], timeout=8)
    if result.returncode != 0:
        return []

    profiles = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split(":", 1)
        if len(parts) != 2:
            continue
        name, conn_ssid = parts[0].strip(), parts[1].strip()
        if conn_ssid == ssid:
            profiles.append(name)
    return profiles


def _set_profile_autoconnect(connection_name: str):
    if not connection_name:
        return
    _nmcli_run(["connection", "modify", connection_name, "connection.autoconnect", "yes"], timeout=8)


def _is_connected_to_ssid(ssid: str, interface: str = ""):
    fields = "ACTIVE,SSID,DEVICE" if interface else "ACTIVE,SSID"
    current = _nmcli_run(["-t", "-f", fields, "dev", "wifi"], timeout=6)
    if current.returncode != 0:
        return False

    for line in current.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if interface:
            parts = line.split(":")
            # parts: ACTIVE, SSID, DEVICE  (SSID may contain colons but DEVICE won't)
            if len(parts) < 3:
                continue
            active, device = parts[0], parts[-1]
            ssid_part = ":".join(parts[1:-1])
            if active == "yes" and ssid_part == ssid and device == interface:
                return True
        else:
            if line == f"yes:{ssid}":
                return True
    return False


def _kill_rogue_hotspots(allowed_interface: str):
    """Tear down any hotspot connection running on an interface OTHER than allowed_interface."""
    active = _nmcli_run(["-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"], timeout=8)
    if active.returncode != 0:
        return
    for line in active.stdout.splitlines():
        parts = line.strip().split(":")
        if len(parts) < 3:
            continue
        name, conn_type, device = parts[0], parts[1], parts[2]
        if conn_type != "wifi" or device == allowed_interface:
            continue
        mode = _nmcli_run(["-g", "802-11-wireless.mode", "connection", "show", name], timeout=8)
        if mode.returncode == 0 and (mode.stdout or "").strip().lower() == "ap":
            _nmcli_run(["connection", "down", name], timeout=12)


def ensure_hotspot_mode(
    ssid: str,
    password: str,
    interface: str = "wlan1",
    connection_name: str = "RoverHotspot",
) -> bool:
    """Ensure Raspberry Pi runs as a WiFi hotspot via NetworkManager."""
    if not ssid:
        return False

    try:
        selected_interface = _pick_hotspot_interface(interface)
        if selected_interface is None:
            return False
        interface = selected_interface

        _kill_rogue_hotspots(allowed_interface=interface)
        hotspots = _active_hotspot_connections(interface=interface)
        if connection_name in hotspots:
            return True

        if hotspots:
            for conn_name in hotspots:
                _nmcli_run(["connection", "down", conn_name], timeout=12)

        existing = _nmcli_run(["-t", "-f", "NAME", "connection", "show"], timeout=8)
        names = {line.strip() for line in existing.stdout.splitlines() if line.strip()} if existing.returncode == 0 else set()

        if connection_name in names:
            # Re-pin to correct interface and disable autoconnect before bringing up
            _nmcli_run(["connection", "modify", connection_name,
                        "connection.interface-name", interface,
                        "connection.autoconnect", "no"], timeout=8)
            up = _nmcli_run(["connection", "up", connection_name, "ifname", interface], timeout=20)
            if up.returncode == 0:
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
            # Pin profile to this interface and disable autoconnect
            _nmcli_run(["connection", "modify", connection_name,
                        "connection.interface-name", interface,
                        "connection.autoconnect", "no"], timeout=8)
            return True

        detail = (create.stderr or create.stdout or f"exit={create.returncode}").strip()
        return False
    except FileNotFoundError:
        return False
    except Exception as exc:
        return False


def switch_hotspot_to_base(
    ssid: str,
    password: str,
    interface: str = "wlan1",
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


def ensure_base_wifi_connection(
    ssid: str,
    password: str,
    interface: str = "wlan1",
    max_attempts: int = 4,
    retry_delay_s: float = 2.0,
    verbose: bool = True,
) -> bool:
    """Ensure rover is connected to the base AP using NetworkManager (nmcli)."""
   
    if not ssid:
        return False

    try:
        if _is_connected_to_ssid(ssid, interface=interface):
            return True

        attempts = max(1, int(max_attempts))

        def try_connect(timeout_seconds: int, allow_rescan: bool):
            profiles = _wifi_profiles_for_ssid(ssid)
            for profile in profiles:
                up = _nmcli_run(["connection", "up", profile], timeout=timeout_seconds)
                if up.returncode == 0:
                    _set_profile_autoconnect(profile)
                    return up

            if allow_rescan:
                _nmcli_run(["device", "wifi", "rescan", "ifname", interface], timeout=8)

            return _nmcli_run(
                ["device", "wifi", "connect", ssid, "password", password, "ifname", interface],
                timeout=timeout_seconds,
            )

        last_result = None
        for attempt in range(1, attempts + 1):
            connect = try_connect(timeout_seconds=20, allow_rescan=(attempt > 1))
            last_result = connect

            if connect.returncode == 0 or _is_connected_to_ssid(ssid, interface=interface):
                active_profiles = _wifi_profiles_for_ssid(ssid)
                if active_profiles:
                    _set_profile_autoconnect(active_profiles[0])

                return True

            if attempt < attempts:
                time.sleep(max(0.0, float(retry_delay_s)))

        stderr = (last_result.stderr or "").strip() if last_result else ""
        stdout = (last_result.stdout or "").strip() if last_result else ""
        exit_code = last_result.returncode if last_result is not None else "unknown"
        detail = stderr or stdout or f"exit={exit_code}"
        return False
    except FileNotFoundError:
        return False
    except Exception as exc:
        return False

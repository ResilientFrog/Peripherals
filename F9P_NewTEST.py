import serial
from pyubx2 import UBXReader
import argparse
import glob
import http.client
import os
import socket
import subprocess
import threading
import time
from urllib.parse import urlparse
from urllib.parse import urljoin
from serial.serialutil import SerialException

BAUD = 115200
PLACEHOLDER_WIFI_SSIDS = {"", "CHANGE_ME_SSID", "YOUR_SSID", "YOUR_WIFI_SSID"}


class RTCMRateLimitError(ConnectionError):
    def __init__(self, message, retry_after_s=None):
        super().__init__(message)
        self.retry_after_s = retry_after_s


def append_text_log(log_file_path, text):
    if not log_file_path:
        return

    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    try:
        log_dir = os.path.dirname(log_file_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        with open(log_file_path, "a", encoding="utf-8") as log_file:
            log_file.write(f"{timestamp} {text}\n")
    except Exception:
        pass


def log_step(text, log_file_path=None):
    print(text)
    append_text_log(log_file_path, text)


def ensure_wifi_connection(ssid, password, interface="wlan0", log_file_path=None):
    normalized_ssid = str(ssid or "").strip()
    if normalized_ssid in PLACEHOLDER_WIFI_SSIDS:
        log_step("ℹ️  [RTCM/WiFi] auto-connect skipped (SSID not configured)", log_file_path)
        return True

    try:
        current = subprocess.run(
            ["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"],
            capture_output=True,
            text=True,
            check=False,
            timeout=6,
        )
        active_lines = [line.strip() for line in current.stdout.splitlines() if line.strip()]
        if any(line == f"yes:{normalized_ssid}" for line in active_lines):
            log_step(f"✅ [RTCM/WiFi] already connected to '{normalized_ssid}'", log_file_path)
            return True

        log_step(f"📶 [RTCM/WiFi] connecting to '{normalized_ssid}' on {interface}...", log_file_path)

        # 1) Try existing NetworkManager profile first (works for hidden/known APs)
        profile_up = subprocess.run(
            ["nmcli", "connection", "up", normalized_ssid, "ifname", interface],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        if profile_up.returncode == 0:
            log_step(f"✅ [RTCM/WiFi] connected to '{normalized_ssid}' via saved profile", log_file_path)
            return True

        # 2) Try scan/connect loop
        connect_cmd = ["nmcli", "device", "wifi", "connect", normalized_ssid, "ifname", interface]
        if str(password or ""):
            connect_cmd.extend(["password", password])

        attempts = 4
        last_result = profile_up
        for attempt in range(1, attempts + 1):
            subprocess.run(
                ["nmcli", "device", "wifi", "rescan", "ifname", interface],
                capture_output=True,
                text=True,
                check=False,
                timeout=8,
            )
            connect = subprocess.run(
                connect_cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
            last_result = connect
            if connect.returncode == 0:
                suffix = "" if attempt == 1 else f" (attempt {attempt}/{attempts})"
                log_step(f"✅ [RTCM/WiFi] connected to '{normalized_ssid}'{suffix}", log_file_path)
                return True
            time.sleep(1)

        stderr = (last_result.stderr or profile_up.stderr or "").strip()
        stdout = (last_result.stdout or profile_up.stdout or "").strip()
        detail = stderr or stdout or f"exit={last_result.returncode}"
        log_step(f"❌ [RTCM/WiFi] failed to connect to '{normalized_ssid}': {detail}", log_file_path)
        return False
    except FileNotFoundError:
        log_step("❌ [RTCM/WiFi] nmcli not found. Install NetworkManager.", log_file_path)
        return False
    except Exception as exc:
        log_step(f"❌ [RTCM/WiFi] connect error: {exc}", log_file_path)
        return False


def gnss_id_to_str(gnss_id):
    mapping = {
        0: "GPS",
        1: "SBAS",
        2: "GAL",
        3: "BDS",
        4: "IMES",
        5: "QZSS",
        6: "GLO",
        7: "NAVIC",
    }
    return mapping.get(gnss_id, f"GNSS{gnss_id}")


def _msg_attr(msg, base_name, idx):
    names = (f"{base_name}_{idx:02d}", f"{base_name}_{idx}")
    for name in names:
        if hasattr(msg, name):
            return getattr(msg, name)
    return None


def summarize_nav_sat(msg):
    total = int(getattr(msg, "numSvs", 0) or 0)
    if total <= 0:
        return "[NAV-SAT] vis: 0 | used: 0"

    used = 0
    max_cno = 0
    constellation_counts = {}

    for idx in range(1, total + 1):
        gnss_id = _msg_attr(msg, "gnssId", idx)
        flags = _msg_attr(msg, "flags", idx) or 0
        cno = _msg_attr(msg, "cno", idx) or 0

        constellation = gnss_id_to_str(gnss_id) if gnss_id is not None else "UNK"
        constellation_counts[constellation] = constellation_counts.get(constellation, 0) + 1

        if flags & 0x08:
            used += 1
        if cno > max_cno:
            max_cno = cno

    ordered = ["GPS", "GAL", "GLO", "BDS", "QZSS", "SBAS", "NAVIC", "IMES", "UNK"]
    by_constellation = ", ".join(
        f"{key}:{constellation_counts[key]}" for key in ordered if key in constellation_counts
    )

    return (
        f"[NAV-SAT] vis: {total:2} | used: {used:2} | "
        f"maxCNO: {max_cno:2} dBHz | {by_constellation}"
    )


def _extract_rtcm_frames(buffer):
    frames = []
    index = 0
    size = len(buffer)

    while index + 6 <= size:
        if buffer[index] != 0xD3:
            index += 1
            continue

        length = ((buffer[index + 1] & 0x03) << 8) | buffer[index + 2]
        frame_len = 3 + length + 3
        if index + frame_len > size:
            break

        payload_start = index + 3
        payload = buffer[payload_start : payload_start + length]
        msg_type = None
        if len(payload) >= 2:
            msg_type = (payload[0] << 4) | (payload[1] >> 4)

        frames.append(
            {
                "type": msg_type,
                "payload_length": length,
                "frame_length": frame_len,
            }
        )
        index += frame_len

    if index > 0:
        del buffer[:index]

    if len(buffer) > 4096:
        del buffer[:-4096]

    return frames


def _log_rtcm_payload(payload, parse_buffer, log_writer=None):
    parse_buffer.extend(payload)
    frames = _extract_rtcm_frames(parse_buffer)
    for frame in frames:
        msg_type = frame["type"]
        msg_label = f"{msg_type}" if msg_type is not None else "unknown"
        text = f"📡 [RTCM] Parsed type={msg_label} payload={frame['payload_length']}B frame={frame['frame_length']}B"
        print(text)
        if log_writer is not None:
            log_writer(text)


def _normalize_stream_url(stream_url):
    if not stream_url:
        return None

    cleaned = str(stream_url).strip()
    if not cleaned:
        return None

    if "://" not in cleaned:
        cleaned = f"http://{cleaned}"

    parsed = urlparse(cleaned)
    if not parsed.hostname:
        raise ValueError(f"invalid RTCM stream URL: {stream_url}")

    scheme = (parsed.scheme or "http").lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"unsupported RTCM stream scheme: {scheme}")

    port = parsed.port or (443 if scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    return {
        "url": cleaned,
        "scheme": scheme,
        "host": parsed.hostname,
        "port": port,
        "path": path,
    }


def _resolve_redirect_stream_url(current_url, location):
    if not location:
        return None
    next_url = urljoin(current_url, str(location).strip())
    return _normalize_stream_url(next_url)


def _parse_retry_after_seconds(value):
    if value is None:
        return None
    try:
        parsed = float(str(value).strip())
        if parsed > 0:
            return parsed
    except Exception:
        return None
    return None


def start_rtcm_bridge(
    ser,
    stop_event,
    stream_url=None,
    host="192.168.4.1",
    port=2101,
    http_poll_interval_s=None,
    stream_failure_fallback_threshold=3,
    log_file_path=None,
):
    stream_config = _normalize_stream_url(stream_url)

    def loop():
        bytes_total = 0
        parse_buffer = bytearray()
        log_file = None
        consecutive_errors = 0
        quick_close_errors = 0
        timeout_errors = 0
        stream_read_timeout_streak = 0
        active_http_poll_interval_s = http_poll_interval_s
        active_stream_config = dict(stream_config) if stream_config else None
        stream_fallback_to_tcp = False

        def write_log(text):
            if log_file is None:
                return
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            try:
                log_file.write(f"{timestamp} {text}\n")
            except Exception:
                pass

        if log_file_path:
            try:
                log_dir = os.path.dirname(log_file_path)
                if log_dir:
                    os.makedirs(log_dir, exist_ok=True)
                log_file = open(log_file_path, "a", buffering=1)
                print(f"📝 [RTCM] logging to {log_file_path}")
                write_log(f"ℹ️  [RTCM] log started target={stream_config['url'] if stream_config else f'{host}:{port}'}")
            except Exception as exc:
                print(f"⚠️  [RTCM] cannot open log file '{log_file_path}': {exc}")
                log_file = None

        while not stop_event.is_set():
            try:
                if active_stream_config and not stream_fallback_to_tcp:
                    endpoint = active_stream_config["url"]

                    if active_http_poll_interval_s is not None and active_http_poll_interval_s > 0:
                        started = time.time()
                        redirect_hops = 0
                        poll_target = active_stream_config
                        payload = b""
                        while True:
                            conn_cls = (
                                http.client.HTTPSConnection
                                if poll_target["scheme"] == "https"
                                else http.client.HTTPConnection
                            )
                            conn = conn_cls(poll_target["host"], poll_target["port"], timeout=15)
                            try:
                                conn.request(
                                    "GET",
                                    poll_target["path"],
                                    headers={"Accept": "*/*", "Connection": "close"},
                                )
                                response = conn.getresponse()

                                if response.status in (301, 302, 303, 307, 308):
                                    location = response.getheader("Location")
                                    redirected = _resolve_redirect_stream_url(poll_target["url"], location)
                                    response.read()
                                    if redirected is None:
                                        raise ConnectionError(f"HTTP {response.status} redirect without Location")
                                    if redirect_hops >= 5:
                                        raise ConnectionError("too many HTTP redirects")
                                    redirect_hops += 1
                                    redirect_text = f"ℹ️  [RTCM] redirect {response.status}: {poll_target['url']} -> {redirected['url']}"
                                    print(redirect_text)
                                    write_log(redirect_text)
                                    poll_target = redirected
                                    continue

                                if response.status >= 400:
                                    if response.status == 429:
                                        retry_after_s = _parse_retry_after_seconds(response.getheader("Retry-After"))
                                        raise RTCMRateLimitError(
                                            f"HTTP 429 {response.reason}",
                                            retry_after_s=retry_after_s,
                                        )
                                    raise ConnectionError(f"HTTP {response.status} {response.reason}")

                                payload = response.read(4096)
                                break
                            finally:
                                conn.close()

                        if poll_target["url"] != active_stream_config["url"]:
                            active_stream_config = poll_target
                            endpoint = active_stream_config["url"]

                        if payload:
                            _log_rtcm_payload(payload, parse_buffer, write_log)
                            ser.write(payload)
                            bytes_total += len(payload)
                            consecutive_errors = 0
                            quick_close_errors = 0
                            timeout_errors = 0
                            status_text = f"✅ [RTCM] polled {endpoint} bytes={len(payload)} total={bytes_total}"
                            print(status_text)
                            write_log(status_text)

                        elapsed = time.time() - started
                        sleep_for = max(0.0, active_http_poll_interval_s - elapsed)
                        if sleep_for > 0:
                            time.sleep(sleep_for)
                    else:
                        print(f"📶 [RTCM] connecting stream {endpoint} ...")
                        redirect_hops = 0
                        stream_target = active_stream_config
                        conn = None
                        try:
                            while True:
                                conn_cls = (
                                    http.client.HTTPSConnection
                                    if stream_target["scheme"] == "https"
                                    else http.client.HTTPConnection
                                )
                                conn = conn_cls(stream_target["host"], stream_target["port"], timeout=15)
                                conn.request(
                                    "GET",
                                    stream_target["path"],
                                    headers={"Accept": "*/*", "Connection": "keep-alive"},
                                )
                                response = conn.getresponse()

                                if response.status in (301, 302, 303, 307, 308):
                                    location = response.getheader("Location")
                                    redirected = _resolve_redirect_stream_url(stream_target["url"], location)
                                    response.read()
                                    conn.close()
                                    conn = None
                                    if redirected is None:
                                        raise ConnectionError(f"HTTP {response.status} redirect without Location")
                                    if redirect_hops >= 5:
                                        raise ConnectionError("too many HTTP redirects")
                                    redirect_hops += 1
                                    redirect_text = f"ℹ️  [RTCM] redirect {response.status}: {stream_target['url']} -> {redirected['url']}"
                                    print(redirect_text)
                                    write_log(redirect_text)
                                    stream_target = redirected
                                    continue

                                if response.status >= 400:
                                    if response.status == 429:
                                        retry_after_s = _parse_retry_after_seconds(response.getheader("Retry-After"))
                                        raise RTCMRateLimitError(
                                            f"HTTP 429 {response.reason}",
                                            retry_after_s=retry_after_s,
                                        )
                                    raise ConnectionError(f"HTTP {response.status} {response.reason}")
                                break

                            if stream_target["url"] != active_stream_config["url"]:
                                active_stream_config = stream_target
                                endpoint = active_stream_config["url"]

                            print(f"✅ [RTCM] stream connected: {endpoint}")
                            write_log(f"✅ [RTCM] stream connected: {endpoint}")
                            connected_at = time.time()
                            while not stop_event.is_set():
                                try:
                                    payload = response.read(4096)
                                except (TimeoutError, socket.timeout):
                                    stream_read_timeout_streak += 1
                                    if stream_read_timeout_streak >= 3:
                                        raise TimeoutError("stream read timed out repeatedly (no RTCM data)")
                                    continue
                                except OSError as read_exc:
                                    if "timed out" in str(read_exc).lower():
                                        stream_read_timeout_streak += 1
                                        if stream_read_timeout_streak >= 3:
                                            raise TimeoutError("stream read timed out repeatedly (no RTCM data)")
                                        continue
                                    raise
                                if not payload:
                                    alive_s = time.time() - connected_at
                                    if alive_s < 3.0:
                                        raise ConnectionError(f"base closed RTCM stream quickly ({alive_s:.1f}s)")
                                    raise ConnectionError("base closed RTCM stream")
                                stream_read_timeout_streak = 0
                                _log_rtcm_payload(payload, parse_buffer, write_log)
                                ser.write(payload)
                                bytes_total += len(payload)
                                consecutive_errors = 0
                                quick_close_errors = 0
                                timeout_errors = 0
                        finally:
                            if conn is not None:
                                conn.close()
                else:
                    print(f"📶 [RTCM] connecting TCP {host}:{port} ...")
                    with socket.create_connection((host, port), timeout=5) as sock:
                        sock.settimeout(2)
                        print(f"✅ [RTCM] TCP connected: {host}:{port}")
                        write_log(f"✅ [RTCM] TCP connected: {host}:{port}")
                        while not stop_event.is_set():
                            try:
                                payload = sock.recv(4096)
                            except socket.timeout:
                                continue
                            if not payload:
                                raise ConnectionError("base closed TCP RTCM stream")
                            _log_rtcm_payload(payload, parse_buffer, write_log)
                            ser.write(payload)
                            bytes_total += len(payload)
                            consecutive_errors = 0
                            timeout_errors = 0
                            stream_read_timeout_streak = 0
            except Exception as exc:
                if stop_event.is_set():
                    break
                target = active_stream_config["url"] if active_stream_config else f"{host}:{port}"
                consecutive_errors += 1
                stream_read_timeout_streak = 0
                if "base closed RTCM stream quickly" in str(exc):
                    quick_close_errors += 1
                elif quick_close_errors > 0:
                    quick_close_errors -= 1
                if isinstance(exc, TimeoutError) or "timed out" in str(exc).lower():
                    timeout_errors += 1
                elif timeout_errors > 0:
                    timeout_errors -= 1

                print(f"⚠️  [RTCM] link error on {target}: {exc}")
                write_log(f"⚠️  [RTCM] link error on {target}: {exc}")

                if (
                    active_stream_config
                    and active_http_poll_interval_s is None
                    and quick_close_errors >= 5
                ):
                    active_http_poll_interval_s = 1.0
                    fallback_text = "⚠️  [RTCM] stream closes quickly; switching to HTTP poll mode every 1.0s"
                    print(fallback_text)
                    write_log(fallback_text)

                if (
                    active_stream_config
                    and active_http_poll_interval_s is None
                    and timeout_errors >= 3
                ):
                    active_http_poll_interval_s = 2.0
                    timeout_text = "⚠️  [RTCM] stream read timed out repeatedly; switching to HTTP poll mode every 2.0s"
                    print(timeout_text)
                    write_log(timeout_text)

                if (
                    active_stream_config
                    and not stream_fallback_to_tcp
                    and consecutive_errors >= max(1, int(stream_failure_fallback_threshold))
                ):
                    stream_fallback_to_tcp = True
                    fallback_text = (
                        f"⚠️  [RTCM] stream endpoint unstable; falling back to TCP {host}:{port}"
                    )
                    print(fallback_text)
                    write_log(fallback_text)

                retry_delay = min(10.0, 2 ** min(consecutive_errors - 1, 4))
                if isinstance(exc, RTCMRateLimitError) and exc.retry_after_s is not None:
                    retry_delay = max(retry_delay, min(60.0, exc.retry_after_s))
                retry_text = f"ℹ️  [RTCM] retrying in {retry_delay:.1f}s (consecutive errors: {consecutive_errors})"
                print(retry_text)
                write_log(retry_text)
                time.sleep(retry_delay)

        if log_file is not None:
            try:
                write_log("ℹ️  [RTCM] log stopped")
                log_file.close()
            except Exception:
                pass

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return thread


def fix_type_to_str(fix):
    mapping = {
        0: "No Fix",
        1: "Dead Reckoning",
        2: "2D Fix",
        3: "3D Fix",
        4: "GNSS + DR",
        5: "Time Only",
    }
    return mapping.get(fix, "Unknown")


def rtk_status_to_str(carr):
    mapping = {
        0: "No RTK",
        1: "RTK Float",
        2: "RTK Fixed",
    }
    return mapping.get(carr, "Unknown")


def heading_to_cardinal(heading_deg):
    directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = int(((heading_deg % 360.0) + 22.5) // 45.0) % 8
    return directions[idx]


def signed_north_offset_deg(heading_deg):
    # Signed shortest angular offset from North in degrees (-180, 180].
    return ((heading_deg + 180.0) % 360.0) - 180.0


def pick_port(user_port=None):
    if user_port:
        return user_port

    candidates = ["/dev/ttyACM0", "/dev/ttyACM1"]
    candidates.extend(sorted(glob.glob("/dev/ttyACM*")))

    seen = set()
    for port in candidates:
        if port in seen:
            continue
        seen.add(port)
        if os.path.exists(port):
            return port
    return None


def open_serial_port(port, baud, timeout):
    try:
        return serial.Serial(port, baud, timeout=timeout, exclusive=True)
    except TypeError:
        return serial.Serial(port, baud, timeout=timeout)


def main():
    parser = argparse.ArgumentParser(description="Read ZED-F9P NAV-PVT status")
    parser.add_argument("--port", default=None, help="Serial port (auto-detect if omitted)")
    parser.add_argument("--baud", type=int, default=BAUD, help="Baud rate")
    parser.add_argument("--timeout", type=float, default=1.0, help="Serial timeout seconds")
    parser.add_argument(
        "--silence-timeout",
        type=float,
        default=8.0,
        help="Warn if no NAV-PVT is seen for this many seconds",
    )
    parser.add_argument(
        "--rtcm-enable",
        dest="rtcm_enable",
        action="store_true",
        default=True,
        help="Enable RTCM forwarding from base endpoint to ZED-F9P",
    )
    parser.add_argument(
        "--no-rtcm-enable",
        dest="rtcm_enable",
        action="store_false",
        help="Disable RTCM forwarding from base endpoint",
    )
    parser.add_argument(
        "--rtcm-transport",
        choices=["tcp", "http"],
        default=os.environ.get("RTCM_TRANSPORT", "tcp").strip().lower() or "tcp",
        help="RTCM transport mode: tcp (default) or http",
    )
    parser.add_argument(
        "--rtcm-stream-url",
        default=os.environ.get("RTCM_STREAM_URL", "").strip(),
        help="HTTP/HTTPS RTCM endpoint (used when --rtcm-transport=http)",
    )
    parser.add_argument(
        "--rtcm-host",
        default=os.environ.get("RTCM_BASE_HOST", "192.168.4.1"),
        help="RTCM TCP host (used when --rtcm-stream-url is not set)",
    )
    parser.add_argument(
        "--rtcm-port",
        type=int,
        default=int(os.environ.get("RTCM_BASE_PORT", "2101")),
        help="RTCM TCP port",
    )
    parser.add_argument(
        "--rtcm-http-poll-interval",
        type=float,
        default=None,
        help="For HTTP endpoint, poll interval seconds (omit for streaming mode)",
    )
    parser.add_argument(
        "--rtcm-stream-fallback-errors",
        type=int,
        default=3,
        help="Consecutive stream errors before fallback to TCP host:port",
    )
    parser.add_argument(
        "--show-nmea",
        action="store_true",
        help="Parse and print NMEA messages (may show checksum noise on mixed/binary streams)",
    )
    parser.add_argument(
        "--rtcm-wifi-ssid",
        default=os.environ.get("RTCM_WIFI_SSID", "").strip()
        or os.environ.get("BASE_WIFI_SSID", "").strip()
        or "CHANGE_ME_SSID",
        help="WiFi SSID to use for RTCM correction link",
    )
    parser.add_argument(
        "--rtcm-wifi-password",
        default=os.environ.get("RTCM_WIFI_PASSWORD", "").strip() or os.environ.get("BASE_WIFI_PASSWORD", "CHANGE_ME_PASSWORD").strip(),
        help="WiFi password for --rtcm-wifi-ssid",
    )
    parser.add_argument(
        "--rtcm-wifi-iface",
        default=os.environ.get("RTCM_WIFI_IFACE", "").strip() or os.environ.get("BASE_WIFI_IFACE", "wlan0").strip(),
        help="WiFi interface for RTCM network (default: wlan0)",
    )
    parser.add_argument(
        "--rtcm-log-file",
        default=os.environ.get("RTCM_LOG_FILE", "rtcm_log.txt").strip(),
        help="Text file path for RTCM download/parse logs",
    )
    args = parser.parse_args()
    log_step(f"ℹ️  [START] GNSS reader started | RTCM enabled={args.rtcm_enable}", args.rtcm_log_file)

    port = pick_port(args.port)
    if not port:
        log_step("❌ Error: No /dev/ttyACM* GNSS device found", args.rtcm_log_file)
        log_step("ℹ️  Hint: Check cable/power and unplug/replug the ZED-F9P", args.rtcm_log_file)
        return

    first_connect = True
    while True:
        port = pick_port(args.port)
        if not port:
            log_step("❌ Error: No /dev/ttyACM* GNSS device found", args.rtcm_log_file)
            log_step("ℹ️  Hint: Check cable/power and unplug/replug the ZED-F9P", args.rtcm_log_file)
            time.sleep(2)
            continue

        if first_connect:
            log_step(f"📡 Connecting to ZED-F9P on {port} at {args.baud}...", args.rtcm_log_file)
        else:
            log_step(f"🔁 Reconnecting to ZED-F9P on {port} at {args.baud}...", args.rtcm_log_file)

        try:
            ser = open_serial_port(port, args.baud, args.timeout)
        except Exception as exc:
            log_step(f"❌ Error opening serial port {port}: {exc}", args.rtcm_log_file)
            log_step("ℹ️  Hint: another process may be using this port", args.rtcm_log_file)
            time.sleep(2)
            first_connect = False
            continue

        with ser:
            protfilter = 3 if args.show_nmea else 2
            ubr = UBXReader(ser, protfilter=protfilter)
            if first_connect:
                log_step("✅ Connected. Waiting for NAV-PVT messages...", args.rtcm_log_file)
                if not args.show_nmea:
                    log_step("ℹ️  [NMEA] disabled (UBX-only parse to avoid checksum spam). Use --show-nmea to enable.", args.rtcm_log_file)

            rtcm_stop_event = threading.Event()
            rtcm_thread = None
            if args.rtcm_enable:
                stream_url = (args.rtcm_stream_url or "").strip()
                use_http_transport = args.rtcm_transport == "http"
                rtcm_target = stream_url if use_http_transport else f"{args.rtcm_host}:{args.rtcm_port}"
                rtcm_wifi_ready = True

                if use_http_transport and not stream_url:
                    log_step(
                        "❌ [RTCM] --rtcm-transport=http requires --rtcm-stream-url",
                        args.rtcm_log_file,
                    )
                    log_step(
                        "ℹ️  [RTCM] switch to --rtcm-transport=tcp or provide a valid HTTP URL",
                        args.rtcm_log_file,
                    )
                    rtcm_wifi_ready = False
                elif (not use_http_transport) and stream_url:
                    log_step(
                        "ℹ️  [RTCM] --rtcm-stream-url is set but ignored in TCP mode",
                        args.rtcm_log_file,
                    )

                if args.rtcm_wifi_ssid:
                    rtcm_wifi_ready = ensure_wifi_connection(
                        ssid=args.rtcm_wifi_ssid,
                        password=args.rtcm_wifi_password,
                        interface=args.rtcm_wifi_iface,
                        log_file_path=args.rtcm_log_file,
                    )
                    if not rtcm_wifi_ready:
                        log_step("❌ [RTCM] WiFi connect failed; continuing GNSS without RTCM bridge", args.rtcm_log_file)
                        log_step("ℹ️  [RTCM] verify SSID/password or disable auto-connect with --rtcm-wifi-ssid ''", args.rtcm_log_file)

                if rtcm_wifi_ready:
                    log_step(f"⚙️  [RTCM] enabling correction link -> {rtcm_target}", args.rtcm_log_file)
                    log_step(f"📝 [RTCM] log file -> {args.rtcm_log_file}", args.rtcm_log_file)
                    try:
                        rtcm_thread = start_rtcm_bridge(
                            ser=ser,
                            stop_event=rtcm_stop_event,
                            stream_url=stream_url if use_http_transport else None,
                            host=args.rtcm_host,
                            port=args.rtcm_port,
                            http_poll_interval_s=args.rtcm_http_poll_interval,
                            stream_failure_fallback_threshold=args.rtcm_stream_fallback_errors,
                            log_file_path=args.rtcm_log_file,
                        )
                    except Exception as exc:
                        log_step(f"❌ [RTCM] failed to start bridge: {exc}", args.rtcm_log_file)
            elif first_connect:
                log_step("ℹ️  [RTCM] bridge disabled (use --rtcm-enable to connect to base RTCM)", args.rtcm_log_file)

            last_nav_pvt_time = time.time()
            last_any_time = time.time()
            last_fix_type = None
            last_carr_soln = None
            last_diff_soln = None
            try:
                for raw, msg in ubr:
                    if msg is None:
                        continue

                    last_any_time = time.time()

                    if msg.identity == "NAV-PVT":
                        last_nav_pvt_time = time.time()

                        fix_type = int(getattr(msg, "fixType", 0) or 0)
                        carr_soln = int(getattr(msg, "carrSoln", 0) or 0)
                        diff_soln = int(getattr(msg, "diffSoln", 0) or 0)

                        if (
                            fix_type != last_fix_type
                            or carr_soln != last_carr_soln
                            or diff_soln != last_diff_soln
                        ):
                            transition_text = (
                                f"🔄 [RTK-STATE] fixType {last_fix_type}->{fix_type} "
                                f"| diffSoln {last_diff_soln}->{diff_soln} "
                                f"| carrSoln {last_carr_soln}->{carr_soln} "
                                f"({rtk_status_to_str(carr_soln)})"
                            )
                            log_step(transition_text, args.rtcm_log_file)
                            last_fix_type = fix_type
                            last_carr_soln = carr_soln
                            last_diff_soln = diff_soln

                        fix_str = fix_type_to_str(fix_type)
                        rtk_str = rtk_status_to_str(carr_soln)
                        diff_str = "Yes" if diff_soln else "No"

                        num_sv = msg.numSV
                        lat = msg.lat 
                        lon = msg.lon
                        height_m = msg.hMSL / 1000.0
                        h_acc = msg.hAcc / 1000.0  # mm → meters
                        v_acc = msg.vAcc / 1000.0
                        speed = msg.gSpeed / 1000.0  # mm/s → m/s
                        heading_deg = msg.headMot / 1e5
                        bearing_deg = heading_deg % 360.0
                        north_offset_deg = signed_north_offset_deg(bearing_deg)
                        heading_cardinal = heading_to_cardinal(bearing_deg)

                        nav_text = (
                            f"[NAV-PVT] Fix: {fix_str:12} | "
                            f"[RTK] {rtk_str:10} | "
                            f"Diff: {diff_str:3} | "
                            f"Sats: {num_sv:2} | "
                            f"Lat/Lon: {lat:.7f},{lon:.7f} | "
                            f"Alt: {height_m:.2f} m | "
                            f"hAcc: {h_acc:.3f} m | "
                            f"vAcc: {v_acc:.3f} m | "
                            f"Bearing(N): {bearing_deg:6.2f}° {heading_cardinal:2} | "
                            f"dNorth: {north_offset_deg:+6.2f}° | "
                            f"Speed: {speed:.2f} m/s"
                        )
                        log_step(nav_text, args.rtcm_log_file)
                    elif msg.identity == "NAV-SAT":
                        log_step(summarize_nav_sat(msg), args.rtcm_log_file)
                    elif msg.identity.startswith("NMEA"):
                        log_step(f"[NMEA] {msg.identity}", args.rtcm_log_file)

                    now = time.time()
                    if now - last_nav_pvt_time > args.silence_timeout:
                        log_step("⚠️  No NAV-PVT recently. Possible causes:", args.rtcm_log_file)
                        log_step("   - Receiver not configured to output NAV-PVT", args.rtcm_log_file)
                        log_step("   - Wrong serial port selected", args.rtcm_log_file)
                        log_step("   - Weak GNSS signal (indoors/antenna view blocked)", args.rtcm_log_file)
                        log_step("ℹ️  Try: python3 configure_zedf9p_rover.py --port " + port, args.rtcm_log_file)
                        last_nav_pvt_time = now

                    if now - last_any_time > args.silence_timeout:
                        log_step("⚠️  No data bytes received; check USB cable/power", args.rtcm_log_file)
                        last_any_time = now
            except SerialException as exc:
                log_step(f"❌ Serial link lost: {exc}", args.rtcm_log_file)
                log_step("ℹ️  Hint: check USB cable/power and ensure only one app is using /dev/ttyACM0", args.rtcm_log_file)
                log_step("ℹ️  Attempting auto-reconnect in 2s...", args.rtcm_log_file)
            except KeyboardInterrupt:
                log_step("ℹ️  Stopping...", args.rtcm_log_file)
                return
            finally:
                rtcm_stop_event.set()
                if rtcm_thread is not None:
                    rtcm_thread.join(timeout=2)

        first_connect = False
        time.sleep(2)


if __name__ == "__main__":
    main()
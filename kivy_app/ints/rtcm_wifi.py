import socket
import http.client
import threading
import time
from typing import Optional, Sequence
from urllib.parse import urlparse
from pathlib import Path


def _get_local_ipv4():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("1.1.1.1", 80))
            return sock.getsockname()[0]
    except Exception:
        return None


def _default_host_candidates(primary_host: Optional[str]):
    candidates = []

    if primary_host:
        candidates.append(primary_host)

    for host in ("192.168.4.1", "192.168.1.1", "10.0.0.1"):
        if host not in candidates:
            candidates.append(host)

    local_ip = _get_local_ipv4()
    if local_ip and "." in local_ip:
        prefix = ".".join(local_ip.split(".")[:3])
        for suffix in ("1", "10", "50", "100", "200"):
            host = f"{prefix}.{suffix}"
            if host not in candidates:
                candidates.append(host)

    return candidates


def _normalize_stream_url(stream_url: Optional[str]):
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


def start_rtcm_wifi_bridge(
    ser,
    host: Optional[str],
    port: int,
    stream_url: Optional[str] = None,
    http_poll_interval_s: Optional[float] = None,
    data_log_path: Optional[str] = None,
    status_callback=None,
    hosts: Optional[Sequence[str]] = None,
):
    """Start background RTCM bridge (TCP -> serial).

    The rover connects to a base station TCP endpoint and forwards received RTCM
    bytes directly into the ZED-F9P serial stream.
    """
    stop_event = threading.Event()
    stream_config = _normalize_stream_url(stream_url)
    current_host = host or "192.168.4.1"
    host_candidates = []
    data_log_file = None

    if data_log_path:
        try:
            log_path = Path(data_log_path).expanduser()
            log_path.parent.mkdir(parents=True, exist_ok=True)
            data_log_file = log_path.open("a", buffering=1)
            print(f"📝 RTCM: incoming payload log enabled at {log_path}")
        except Exception as exc:
            print(f"⚠️  RTCM: cannot open payload log '{data_log_path}' ({exc})")
            data_log_file = None

    if hosts:
        for candidate in hosts:
            cleaned = str(candidate).strip()
            if cleaned and cleaned not in host_candidates:
                host_candidates.append(cleaned)

    if stream_config:
        current_host = stream_config["host"]
        host_candidates = [stream_config["host"]]
    else:
        if not host_candidates:
            host_candidates = _default_host_candidates(host)

        if not host_candidates:
            host_candidates = ["192.168.4.1"]

    def report(connected=None, bytes_total=None, error=None):
        if status_callback is None:
            return
        try:
            status_callback(
                {
                    "connected": connected,
                    "bytes_total": bytes_total,
                    "error": error,
                    "host": current_host,
                    "port": port,
                    "endpoint": stream_config["url"] if stream_config else None,
                }
            )
        except Exception:
            pass

    def log_payload(source: str, payload: bytes):
        if data_log_file is None:
            return
        try:
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            preview_hex = payload[:64].hex(" ")
            truncated = "..." if len(payload) > 64 else ""
            data_log_file.write(
                f"{ts} source={source} bytes={len(payload)} hex={preview_hex}{truncated}\n"
            )
        except Exception:
            pass

    def loop():
        nonlocal current_host
        bytes_total = 0
        host_index = 0
        current_host = host_candidates[0]
        poll_interval = None

        if http_poll_interval_s is not None:
            try:
                parsed_interval = float(http_poll_interval_s)
                if parsed_interval > 0:
                    poll_interval = parsed_interval
            except Exception:
                poll_interval = None

        while not stop_event.is_set():
            try:
                if stream_config:
                    current_host = stream_config["host"]
                    endpoint = stream_config["url"]
                    print(f"📶 RTCM: connecting to stream {endpoint} ...")

                    if poll_interval is not None:
                        while not stop_event.is_set():
                            req_start = time.time()
                            conn_cls = http.client.HTTPSConnection if stream_config["scheme"] == "https" else http.client.HTTPConnection
                            conn = conn_cls(stream_config["host"], stream_config["port"], timeout=3)
                            try:
                                conn.request(
                                    "GET",
                                    stream_config["path"],
                                    headers={
                                        "Accept": "*/*",
                                        "Connection": "close",
                                    },
                                )
                                response = conn.getresponse()
                                if response.status >= 400:
                                    raise ConnectionError(f"HTTP {response.status} {response.reason}")

                                payload = response.read()
                                if payload:
                                    ser.write(payload)
                                    bytes_total += len(payload)
                                    log_payload(endpoint, payload)

                                report(connected=True, bytes_total=bytes_total, error="")
                                print(
                                    f"✅ RTCM: polled {endpoint} status={response.status} bytes={len(payload)} total={bytes_total}"
                                )
                            finally:
                                conn.close()

                            elapsed = time.time() - req_start
                            sleep_for = max(0.0, poll_interval - elapsed)
                            if sleep_for > 0:
                                time.sleep(sleep_for)
                    else:
                        conn_cls = http.client.HTTPSConnection if stream_config["scheme"] == "https" else http.client.HTTPConnection
                        conn = conn_cls(stream_config["host"], stream_config["port"], timeout=3)
                        try:
                            conn.request(
                                "GET",
                                stream_config["path"],
                                headers={
                                    "Accept": "*/*",
                                    "Connection": "keep-alive",
                                },
                            )
                            response = conn.getresponse()
                            if response.status >= 400:
                                raise ConnectionError(f"HTTP {response.status} {response.reason}")

                            report(connected=True, bytes_total=bytes_total, error="")
                            print(f"✅ RTCM: connected to stream {endpoint}")

                            while not stop_event.is_set():
                                payload = response.read(4096)
                                if not payload:
                                    raise ConnectionError("base closed RTCM stream")

                                ser.write(payload)
                                bytes_total += len(payload)
                                log_payload(endpoint, payload)
                                report(connected=True, bytes_total=bytes_total, error="")
                        finally:
                            conn.close()
                else:
                    current_host = host_candidates[host_index % len(host_candidates)]
                    host_index += 1
                    print(f"📶 RTCM: connecting to base {current_host}:{port} ...")
                    with socket.create_connection((current_host, port), timeout=3) as sock:
                        sock.settimeout(2)
                        report(connected=True, bytes_total=bytes_total, error="")
                        print(f"✅ RTCM: connected to base {current_host}:{port}")

                        while not stop_event.is_set():
                            payload = sock.recv(4096)
                            if not payload:
                                raise ConnectionError("base closed RTCM stream")

                            ser.write(payload)
                            bytes_total += len(payload)
                            log_payload(f"{current_host}:{port}", payload)
                            report(connected=True, bytes_total=bytes_total, error="")
            except Exception as exc:
                report(connected=False, bytes_total=bytes_total, error=str(exc))
                if stream_config:
                    print(f"⚠️  RTCM: stream error on {stream_config['url']} ({exc}), retrying")
                else:
                    print(f"⚠️  RTCM: link error on {current_host}:{port} ({exc}), trying next host")
                time.sleep(1)

        try:
            if data_log_file is not None:
                data_log_file.close()
        except Exception:
            pass

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return stop_event, thread

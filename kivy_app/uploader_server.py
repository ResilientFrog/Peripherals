"""
Lightweight Flask uploader for RTCM/Kivy app
Run inside a background thread; sets an Event when a new file arrives.
Accepts file field named 'file' and saves to uploads/latest.csv

Routes:
  GET  /            → Home page (two tile selection: Upload CSV / Start RTK)
  GET  /upload      → Upload form
  POST /upload      → Handle CSV upload
  GET  /start-rtk   → Start RTK confirmation page
  POST /start-rtk   → Trigger base WiFi switch + RTCM start
  Captive portal detection routes (redirect to /) for iOS, Android, Windows
"""
from flask import Flask, request, redirect, url_for, render_template_string
import os
import threading
import http.server
from pathlib import Path

UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Shared CSS (base variables + resets) injected into every page
# ---------------------------------------------------------------------------
_BASE_CSS = """
    :root {
        --bg-top: #e8f0fe;
        --bg-bottom: #f0f7ff;
        --card: #ffffff;
        --text: #111827;
        --muted: #6b7280;
        --blue: #3b82f6;
        --blue-dark: #2563eb;
        --green: #10b981;
        --green-dark: #059669;
        --border: #dbe6ff;
        --shadow: 0 20px 50px rgba(30, 64, 175, 0.13);
        --radius: 20px;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
        font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        color: var(--text);
        min-height: 100dvh;
        background: linear-gradient(145deg, var(--bg-top) 0%, var(--bg-bottom) 100%);
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        padding: 20px;
    }
    a { text-decoration: none; color: inherit; }
    .back { display: inline-flex; align-items: center; gap: 6px; margin-bottom: 18px;
            color: var(--blue-dark); font-size: 0.9rem; font-weight: 600; }
    .back:hover { opacity: 0.75; }
    .page-header { text-align: center; margin-bottom: 28px; }
    .page-header .badge {
        display: inline-block; font-size: 0.78rem; padding: 4px 12px;
        border-radius: 999px; background: #dbeafe; color: #1d4ed8;
        font-weight: 700; letter-spacing: 0.04em; margin-bottom: 10px;
    }
    .page-header h1 { font-size: 1.7rem; letter-spacing: -0.03em; }
    .page-header p { color: var(--muted); margin-top: 6px; font-size: 0.95rem; }
    .status-box {
        width: min(520px, 100%); margin-bottom: 16px;
        border-radius: 12px; padding: 12px 16px; font-size: 0.9rem;
    }
    .status-ok  { background: #ecfdf5; border: 1px solid #a7f3d0; color: #065f46; }
    .status-err { background: #fff1f2; border: 1px solid #fecdd3; color: #be123c; }
"""

# ---------------------------------------------------------------------------
# HOME PAGE – two big tiles
# ---------------------------------------------------------------------------
HOME_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Rover Setup</title>
  <style>
""" + _BASE_CSS + """
    .tiles {
      display: grid;
      grid-template-columns: 1fr;
      gap: 18px;
      width: min(520px, 100%);
    }
    @media (min-width: 540px) {
      .tiles { grid-template-columns: 1fr 1fr; }
    }
    .tile {
      display: flex; flex-direction: column; align-items: center;
      text-align: center;
      background: var(--card);
      border-radius: var(--radius);
      padding: 32px 20px 28px;
      box-shadow: var(--shadow);
      border: 1px solid var(--border);
      transition: transform 0.15s ease, box-shadow 0.15s ease;
      cursor: pointer;
    }
    .tile:active { transform: scale(0.97); }
    .tile:hover  { transform: translateY(-3px);
                   box-shadow: 0 28px 60px rgba(30,64,175,0.18); }
    .tile-icon { font-size: 3rem; margin-bottom: 14px; }
    .tile h2   { font-size: 1.25rem; letter-spacing: -0.02em; margin-bottom: 8px; }
    .tile p    { color: var(--muted); font-size: 0.88rem; line-height: 1.5; }
    .tile-blue  { border-top: 4px solid var(--blue); }
    .tile-green { border-top: 4px solid var(--green); }
  </style>
</head>
<body>
  <div class="page-header">
    <div class="badge">ROVER</div>
    <h1>Rover Setup</h1>
    <p>Select an action to continue.</p>
  </div>
  <div class="tiles">
    <a class="tile tile-blue" href="/upload">
      <div class="tile-icon">📁</div>
      <h2>Upload CSV</h2>
      <p>Upload survey points to the Raspberry Pi over the hotspot.</p>
    </a>
    <a class="tile tile-green" href="/start-rtk">
      <div class="tile-icon">📡</div>
      <h2>Start RTK</h2>
      <p>Connect to the base station and start RTK positioning.</p>
    </a>
  </div>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# UPLOAD PAGE
# ---------------------------------------------------------------------------
UPLOAD_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Upload CSV – Rover</title>
  <style>
""" + _BASE_CSS + """
    .card {
      width: min(520px, 100%);
      background: var(--card);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      border: 1px solid var(--border);
      padding: 28px 24px;
    }
    .card h2 { font-size: 1.3rem; margin-bottom: 6px; }
    .card p  { color: var(--muted); font-size: 0.9rem; margin-bottom: 20px; line-height: 1.5; }
    .file-row { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
    input[type=file] {
      flex: 1 1 250px; font-size: 0.9rem; color: #374151;
    }
    input[type=file]::file-selector-button {
      border: 1px solid #c7d7ff; border-radius: 10px; padding: 9px 14px;
      background: #f1f6ff; color: #1e3a8a; font-weight: 600; cursor: pointer;
      margin-right: 10px;
    }
    button[type=submit] {
      border: none; border-radius: 12px; padding: 11px 22px;
      background: linear-gradient(180deg, var(--blue) 0%, var(--blue-dark) 100%);
      color: #fff; font-size: 0.95rem; font-weight: 700; cursor: pointer;
      box-shadow: 0 8px 18px rgba(37,99,235,0.28);
      transition: transform 0.12s ease;
    }
    button[type=submit]:hover { transform: translateY(-1px); }
    button[type=submit]:active { transform: translateY(0); }
    .hint { margin-top: 16px; color: var(--muted); font-size: 0.82rem; }
  </style>
</head>
<body>
  <div style="width:min(520px,100%)">
    <a class="back" href="/">&#8592; Back</a>
  </div>
  <div class="card">
    <h2>📁 Upload CSV</h2>
    <p>Select a CSV file containing <strong>lat,lon</strong> values (one point per line).</p>

    {% if message %}
    <div class="status-box {{ 'status-ok' if ok else 'status-err' }}">{{ message }}</div>
    {% endif %}

    <form method="post" enctype="multipart/form-data" action="/upload" class="file-row">
      <input type="file" name="file" accept=".csv,text/csv" required>
      <button type="submit">Upload</button>
    </form>
    <p class="hint">Saved to <strong>uploads/latest.csv</strong> and auto-loaded by the Kivy app.</p>
  </div>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# START RTK PAGE
# ---------------------------------------------------------------------------
START_RTK_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Start RTK – Rover</title>
  <style>
""" + _BASE_CSS + """
    .card {
      width: min(520px, 100%);
      background: var(--card);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      border: 1px solid var(--border);
      padding: 28px 24px;
    }
    .card h2 { font-size: 1.3rem; margin-bottom: 6px; }
    .card p  { color: var(--muted); font-size: 0.9rem; margin-bottom: 20px; line-height: 1.5; }
    .step {
      display: flex; align-items: flex-start; gap: 12px;
      margin-bottom: 14px; font-size: 0.9rem;
    }
    .step-num {
      flex-shrink: 0; width: 26px; height: 26px; border-radius: 50%;
      background: #dbeafe; color: #1d4ed8; font-weight: 700;
      display: flex; align-items: center; justify-content: center; font-size: 0.82rem;
    }
    .step-text { color: var(--muted); line-height: 1.5; padding-top: 3px; }
    .divider { border: none; border-top: 1px solid var(--border); margin: 20px 0; }
    button[type=submit] {
      width: 100%; border: none; border-radius: 12px; padding: 14px;
      background: linear-gradient(180deg, var(--green) 0%, var(--green-dark) 100%);
      color: #fff; font-size: 1rem; font-weight: 700; cursor: pointer;
      box-shadow: 0 8px 18px rgba(5,150,105,0.3);
      transition: transform 0.12s ease;
    }
    button[type=submit]:hover  { transform: translateY(-1px); }
    button[type=submit]:active { transform: translateY(0); }
  </style>
</head>
<body>
  <div style="width:min(520px,100%)">
    <a class="back" href="/">&#8592; Back</a>
  </div>
  <div class="card">
    <h2>📡 Start RTK</h2>
    <p>This will disable the hotspot and connect the Raspberry Pi to the base station network.</p>

    {% if message %}
    <div class="status-box {{ 'status-ok' if ok else 'status-err' }}">{{ message }}</div>
    {% endif %}

    {% if not started %}
    <div class="step">
      <div class="step-num">1</div>
      <div class="step-text">Hotspot turns off &mdash; you will lose this connection.</div>
    </div>
    <div class="step">
      <div class="step-num">2</div>
      <div class="step-text">Raspberry Pi connects to the base station WiFi network (<strong>{{ base_ssid }}</strong>).</div>
    </div>
    <div class="step">
      <div class="step-num">3</div>
      <div class="step-text">RTCM corrections stream from base to rover via TCP.</div>
    </div>
    <hr class="divider">
    <form method="post" action="/start-rtk">
      <button type="submit">Start RTK Now</button>
    </form>
    {% endif %}
  </div>
</body>
</html>
"""

# Legacy alias kept for any external references
FORM_HTML = HOME_HTML


def create_app(signal_event=None, save_name="latest.csv", on_start_action=None, base_ssid="CHANGE_ME_SSID"):
    app = Flask(__name__)

    @app.route('/')
    def index():
        return render_template_string(HOME_HTML)

    # ---- captive portal detection endpoints --------------------------------
    # Android / Chrome OS
    @app.route('/generate_204')
    @app.route('/mobile/status.php')
    def captive_android():
        return redirect('/', code=302)

    # Apple iOS / macOS
    @app.route('/hotspot-detect.html')
    @app.route('/library/test/success.html')
    def captive_apple():
        return redirect('/', code=302)

    # Windows / Microsoft
    @app.route('/connecttest.txt')
    @app.route('/ncsi.txt')
    def captive_windows():
        return redirect('/', code=302)

    # ---- upload page -------------------------------------------------------
    @app.route('/upload', methods=['GET'])
    def upload_page():
        message = request.args.get('msg', '')
        ok = request.args.get('ok', '1') == '1'
        return render_template_string(UPLOAD_HTML, message=message, ok=ok)

    @app.route('/upload', methods=['POST'])
    def upload():
        f = request.files.get('file')
        if not f:
            return redirect(url_for('upload_page', msg='No file uploaded', ok='0'))

        save_path = UPLOAD_DIR / save_name
        f.save(save_path)

        if signal_event is not None:
            try:
                signal_event.set()
            except Exception:
                pass

        return redirect(url_for('upload_page', msg='✅ File uploaded successfully', ok='1'))

    # ---- start RTK page ----------------------------------------------------
    @app.route('/start-rtk', methods=['GET'])
    def start_rtk_page():
        message = request.args.get('msg', '')
        ok = request.args.get('ok', '1') == '1'
        started = request.args.get('started', '0') == '1'
        return render_template_string(
            START_RTK_HTML, message=message, ok=ok, started=started, base_ssid=base_ssid
        )

    @app.route('/start-rtk', methods=['POST'])
    def start_rtk():
        if on_start_action is None:
            return redirect(url_for('start_rtk_page', msg='Start action not configured', ok='0'))
        try:
            result = on_start_action()
            success = bool(result[0]) if isinstance(result, tuple) else bool(result)
            message = result[1] if isinstance(result, tuple) and len(result) > 1 else (
                '✅ RTK started – hotspot disabled, connecting to base…' if success else '❌ Start failed'
            )
            return redirect(url_for('start_rtk_page', msg=str(message),
                                    ok='1' if success else '0', started='1' if success else '0'))
        except Exception as exc:
            return redirect(url_for('start_rtk_page', msg=f'Start failed: {exc}', ok='0'))

    # keep old /start route for backwards-compat
    @app.route('/start', methods=['POST'])
    def start_legacy():
        return redirect(url_for('start_rtk'), code=307)

    return app


def run_captive_portal_redirect(flask_port: int, gateway_ip: str = '10.42.0.1', bind_host: str = '0.0.0.0'):
    """
    Bind a tiny HTTP server on port 80 that redirects every request to the
    main Flask server.  Requires root (or CAP_NET_BIND_SERVICE).
    Called from a daemon thread so failure is non-fatal.
    """
    target = f'http://{gateway_ip}:{flask_port}/'

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header('Location', target)
            self.end_headers()

        def do_POST(self):
            self.send_response(302)
            self.send_header('Location', target)
            self.end_headers()

        def log_message(self, fmt, *args):  # silence default logging
            pass

    try:
        srv = http.server.HTTPServer((bind_host, 80), _Handler)
        print(f'✅ Captive portal redirect active on port 80 → {target}')
        srv.serve_forever()
    except PermissionError:
        print('ℹ️  Port 80 redirect skipped (not root); users navigate to '
              f'http://{gateway_ip}:{flask_port}/ manually')
    except Exception as exc:
        print(f'⚠️  Captive portal redirect error: {exc}')


def run_server(event, host='0.0.0.0', port=5000, on_start_action=None,
               base_ssid='CHANGE_ME_SSID', hotspot_gateway='10.42.0.1'):
    """Run Flask server (blocking) – intended to be called from a daemon thread."""
    app = create_app(signal_event=event, on_start_action=on_start_action, base_ssid=base_ssid)

    # try to bind port-80 captive portal redirect in a separate daemon thread
    redir_thread = threading.Thread(
        target=run_captive_portal_redirect,
        args=(port, hotspot_gateway),
        daemon=True,
    )
    redir_thread.start()

    app.run(host=host, port=port, threaded=True)

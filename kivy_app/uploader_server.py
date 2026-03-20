"""
Lightweight Flask uploader for RTCM/Kivy app
Run inside a background thread; sets an Event when a new file arrives.
Accepts file field named 'file' and saves to uploads/latest.csv
"""
from flask import Flask, request, redirect, url_for, render_template_string
import os
from pathlib import Path

UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

FORM_HTML = '''
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Upload data</title>
    <style>
        :root {
            color-scheme: light;
            --bg-top: #e7efff;
            --bg-bottom: #f7f9ff;
            --card: #ffffff;
            --text: #111827;
            --muted: #6b7280;
            --accent: #3b82f6;
            --accent-strong: #2563eb;
            --accent-alt: #14b8a6;
            --accent-alt-strong: #0f766e;
            --border: #dbe6ff;
            --chip-bg: #eef4ff;
            --chip-text: #26437d;
            --shadow: 0 22px 45px rgba(30, 64, 175, 0.12);
        }

        * { box-sizing: border-box; }

        body {
            margin: 0;
            min-height: 100vh;
            font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            color: var(--text);
            background:
                radial-gradient(circle at 10% 15%, rgba(59, 130, 246, 0.18) 0%, rgba(59, 130, 246, 0) 34%),
                radial-gradient(circle at 90% 85%, rgba(37, 99, 235, 0.14) 0%, rgba(37, 99, 235, 0) 30%),
                linear-gradient(145deg, var(--bg-top) 0%, var(--bg-bottom) 100%);
            display: grid;
            place-items: center;
            padding: 22px;
        }

        .card {
            width: min(560px, 100%);
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 20px;
            box-shadow: var(--shadow);
            padding: 24px;
        }

        .top {
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 8px;
        }

        .badge {
            font-size: 0.8rem;
            padding: 4px 9px;
            border-radius: 999px;
            background: var(--chip-bg);
            color: var(--chip-text);
            font-weight: 600;
        }

        h1 {
            margin: 0;
            font-size: 1.45rem;
            letter-spacing: -0.02em;
        }

        .subtitle {
            margin: 8px 0 16px;
            color: var(--muted);
            font-size: 0.96rem;
            line-height: 1.45;
        }

        .tiles {
            display: grid;
            grid-template-columns: 1fr;
            gap: 12px;
        }

        .tile {
            border: 1px dashed #bfd2ff;
            border-radius: 14px;
            background: #fbfdff;
            padding: 14px;
        }

        .tile h2 {
            margin: 0 0 8px;
            font-size: 1.02rem;
            letter-spacing: -0.01em;
        }

        .tile p {
            margin: 0 0 12px;
            color: var(--muted);
            font-size: 0.9rem;
        }

        .row {
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 10px;
        }

        .file {
            flex: 1 1 280px;
            max-width: 100%;
            font-size: 0.94rem;
            color: #374151;
        }

        .file::file-selector-button {
            margin-right: 10px;
            border: 1px solid #c7d7ff;
            border-radius: 10px;
            padding: 8px 12px;
            background: #f1f6ff;
            color: #21427f;
            font-weight: 600;
            cursor: pointer;
        }

        .file::file-selector-button:hover {
            background: #e4eeff;
        }

        button {
            border: none;
            border-radius: 11px;
            padding: 10px 16px;
            min-width: 110px;
            background: linear-gradient(180deg, var(--accent) 0%, var(--accent-strong) 100%);
            color: #fff;
            font-size: 0.95rem;
            font-weight: 700;
            letter-spacing: 0.01em;
            cursor: pointer;
            box-shadow: 0 8px 16px rgba(37, 99, 235, 0.26);
            transition: transform 0.12s ease, box-shadow 0.12s ease;
        }

        .start-btn {
            background: linear-gradient(180deg, var(--accent-alt) 0%, var(--accent-alt-strong) 100%);
            box-shadow: 0 8px 16px rgba(15, 118, 110, 0.26);
        }

        .start-btn:hover {
            box-shadow: 0 10px 20px rgba(15, 118, 110, 0.3);
        }

        .status {
            margin: 0 0 12px;
            padding: 9px 11px;
            border-radius: 10px;
            font-size: 0.88rem;
        }

        .status-ok {
            background: #ecfeff;
            border: 1px solid #bff7f2;
            color: #0f766e;
        }

        .status-err {
            background: #fff1f2;
            border: 1px solid #fecdd3;
            color: #be123c;
        }

        button:hover {
            transform: translateY(-1px);
            box-shadow: 0 10px 20px rgba(37, 99, 235, 0.3);
        }

        button:active {
            transform: translateY(0);
        }

        .hint {
            margin-top: 14px;
            color: var(--muted);
            font-size: 0.85rem;
        }
    </style>
</head>
<body>
    <main class="card">
        <div class="top">
            <span class="badge">CSV Uploader</span>
        </div>
        <h1>Upload Survey Points</h1>
        <p class="subtitle">Select a CSV containing <strong>lat,lon</strong> values (one point per line).</p>

        {% if message %}
        <div class="status {{ 'status-ok' if ok else 'status-err' }}">{{ message }}</div>
        {% endif %}

        <section class="tiles">
            <div class="tile">
                <h2>Upload CSV</h2>
                <p>Keep hotspot active and upload your latest survey points file.</p>
                <form method="post" enctype="multipart/form-data" action="/upload" class="row">
                    <input class="file" type="file" name="file" accept=".csv,text/csv" required>
                    <button type="submit">Upload</button>
                </form>
            </div>

            <div class="tile">
                <h2>Start</h2>
                <p>Turn off hotspot and connect this Raspberry Pi to the base network.</p>
                <form method="post" action="/start">
                    <button class="start-btn" type="submit">Start</button>
                </form>
            </div>
        </section>

        <div class="hint">The file is saved to <strong>uploads/latest.csv</strong> and auto-loaded by the Kivy app.</div>
    </main>
</body>
</html>
'''


def create_app(signal_event=None, save_name="latest.csv", on_start_action=None):
    app = Flask(__name__)

    @app.route('/')
    def index():
        message = request.args.get('msg', '')
        ok = request.args.get('ok', '1') == '1'
        return render_template_string(FORM_HTML, message=message, ok=ok)

    @app.route('/upload', methods=['POST'])
    def upload():
        f = request.files.get('file')
        if not f:
            return "No file uploaded", 400

        save_path = UPLOAD_DIR / save_name
        f.save(save_path)

        # signal the Kivy app if event provided
        if signal_event is not None:
            try:
                signal_event.set()
            except Exception:
                pass

        return redirect(url_for('index'))

    @app.route('/start', methods=['POST'])
    def start():
        if on_start_action is None:
            return redirect(url_for('index', msg='Start action not configured', ok='0'))

        try:
            result = on_start_action()
            success = bool(result[0]) if isinstance(result, tuple) and len(result) > 0 else bool(result)
            message = result[1] if isinstance(result, tuple) and len(result) > 1 else ('Start complete' if success else 'Start failed')
            return redirect(url_for('index', msg=str(message), ok='1' if success else '0'))
        except Exception as exc:
            return redirect(url_for('index', msg=f'Start failed: {exc}', ok='0'))

    return app


def run_server(event, host='127.0.0.1', port=5000, on_start_action=None):
    """Run Flask server (blocking) - intended to be called from a daemon thread."""
    app = create_app(signal_event=event, on_start_action=on_start_action)
    # Use built-in server (sufficient for local testing)
    app.run(host=host, port=port, threaded=True)

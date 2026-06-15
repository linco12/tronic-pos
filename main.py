"""
Tronic POS — Desktop Launcher
Wraps the Flask backend in a native window using pywebview.
Data is stored locally in SQLite; Firebase syncs when online.
"""
import sys
import os

# ── Must happen BEFORE any other import ──────────────────────────────────────
if getattr(sys, 'frozen', False):
    # Running as a PyInstaller .exe
    _BUNDLE = sys._MEIPASS           # temp dir — reset each run, do NOT store data here
    _EXE_DIR = os.path.dirname(sys.executable)  # folder that contains TronicPOS.exe

    # Flask finds templates/ and static/ from the bundle root
    os.chdir(_BUNDLE)

    # Persistent app data lives NEXT TO the exe, survives updates and reboots
    _DATA_DIR = os.path.join(_EXE_DIR, 'TronicPOS_Data')
    os.makedirs(_DATA_DIR, exist_ok=True)
    os.environ.setdefault('DATABASE_PATH', os.path.join(_DATA_DIR, 'tronic_pos.db'))

    # Load .env from the exe folder (admins put Firebase creds there once)
    _env_file = os.path.join(_EXE_DIR, '.env')
    if os.path.exists(_env_file):
        from dotenv import load_dotenv
        load_dotenv(_env_file)
else:
    _BUNDLE  = os.path.dirname(os.path.abspath(__file__))
    _EXE_DIR = _BUNDLE
    _DATA_DIR = _BUNDLE

# Tell Flask/app.py we are running as a desktop app
os.environ['DESKTOP_MODE'] = '1'

import threading
import time
import socket
import webview

_flask_port  = 0
_flask_ready = threading.Event()


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _run_server():
    global _flask_port

    from startup import do_startup
    do_startup()

    import sync_manager
    sync_manager.start()

    _flask_port = _find_free_port()
    _flask_ready.set()

    from app import app as flask_app
    flask_app.run(
        host='127.0.0.1',
        port=_flask_port,
        use_reloader=False,
        debug=False,
        threaded=True,
    )


def main():
    server_thread = threading.Thread(target=_run_server, daemon=True)
    server_thread.start()

    # Wait up to 30 s for Flask to be ready
    if not _flask_ready.wait(timeout=30):
        print('ERROR: Server did not start in time.', file=sys.stderr)
        sys.exit(1)

    # Give Flask a moment to bind the socket
    time.sleep(0.5)

    url = f'http://127.0.0.1:{_flask_port}'

    window = webview.create_window(
        title='Tronic POS',
        url=url,
        width=1400,
        height=900,
        min_size=(1024, 700),
        resizable=True,
        background_color='#1e293b',
        text_select=True,
        confirm_close=True,
    )

    # Try Edge Chromium (Windows 10/11); fall back to default
    try:
        webview.start(debug=False, gui='edgechromium')
    except Exception:
        webview.start(debug=False)


if __name__ == '__main__':
    main()

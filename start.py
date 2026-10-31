"""Run everything with one command:  uv run start.py

Starts the engine API (port 8000) and serves the web pages (port 5500), waits until the engine
answers, then opens the product site; its Mission Control buttons use the live engine.
Press Ctrl+C to stop both.
"""

from __future__ import annotations

import functools
import http.server
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
API_PORT, WEB_PORT = 8000, 5500
API = f"http://127.0.0.1:{API_PORT}"
APP_URL = f"http://localhost:{WEB_PORT}/web/index.html?api={API}"
SITE_URL = f"http://localhost:{WEB_PORT}/web/home.html?api={API}"


def port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args: object) -> None:  # keep the terminal readable
        pass


def serve_web() -> None:
    handler = functools.partial(QuietHandler, directory=str(ROOT))
    http.server.ThreadingHTTPServer(("127.0.0.1", WEB_PORT), handler).serve_forever()


def engine_ready(timeout_s: float = 90) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{API}/api/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except OSError:
            time.sleep(1)
    return False


def main() -> int:
    for port, what in ((API_PORT, "engine"), (WEB_PORT, "web server")):
        if not port_free(port):
            print(f"Port {port} is already in use, so the {what} cannot start. "
                  f"Close the other terminal that is running Verity (Ctrl+C), then try again.")
            return 1

    print("Starting the Verity engine...")
    engine = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", str(API_PORT)],
        cwd=ROOT,
    )
    threading.Thread(target=serve_web, daemon=True).start()
    try:
        if not engine_ready() or engine.poll() is not None:
            print("The engine did not start. Scroll up for the error message.")
            return 1
        print("\n  Verity is running.")
        print(f"  Product site (opens now):      {SITE_URL}")
        print(f"  Mission Control (live engine): {APP_URL}")
        print(f"  API docs:                      {API}/docs")
        print("\n  The first load takes a few seconds while the engine plans three trips.")
        print("  Press Ctrl+C to stop.\n")
        webbrowser.open(SITE_URL)
        engine.wait()
        return engine.returncode or 0
    except KeyboardInterrupt:
        print("\nStopping Verity...")
        return 0
    finally:
        if engine.poll() is None:
            engine.terminate()
            try:
                engine.wait(timeout=10)
            except subprocess.TimeoutExpired:
                engine.kill()


if __name__ == "__main__":
    sys.exit(main())

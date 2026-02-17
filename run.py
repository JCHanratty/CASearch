"""PyInstaller entry point for CASearch — native desktop window."""

import os
import sys
import threading
import time


def main():
    # Resolve base path for PyInstaller frozen mode
    if getattr(sys, "frozen", False):
        base_path = sys._MEIPASS
        os.chdir(base_path)
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))

    # Ensure the app module is importable
    if base_path not in sys.path:
        sys.path.insert(0, base_path)

    import uvicorn
    import webview

    host = "127.0.0.1"
    port = 8000
    url = f"http://{host}:{port}"

    # Start uvicorn in a background thread
    server_thread = threading.Thread(
        target=uvicorn.run,
        args=("app.main:app",),
        kwargs={"host": host, "port": port, "log_level": "warning"},
        daemon=True,
    )
    server_thread.start()

    # Wait for server to be ready
    import urllib.request
    for _ in range(30):
        try:
            urllib.request.urlopen(f"{url}/admin/health", timeout=1)
            break
        except Exception:
            time.sleep(0.3)

    # Open native desktop window
    window = webview.create_window(
        "Contract Dashboard",
        url,
        width=1280,
        height=860,
        min_size=(900, 600),
    )
    webview.start()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)

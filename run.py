"""PyInstaller entry point for CASearch."""

import os
import sys
import threading
import webbrowser


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

    host = "127.0.0.1"
    port = 8000
    url = f"http://{host}:{port}"

    # Open browser after a short delay to let the server start
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    print(f"[CASearch] Starting server at {url}")
    print("[CASearch] Press Ctrl+C to stop")

    uvicorn.run("app.main:app", host=host, port=port)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[CASearch] Shutting down...")
        sys.exit(0)

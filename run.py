"""Run the local "Pult kachestva" (Quality Console) dashboard.

    python run.py                      # data/quality-state.yml, port 7842, opens browser
    python run.py --file ../qa/state.yml
    python run.py --port 8000 --no-browser
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser
from pathlib import Path

# Windows consoles are often cp1251/cp866 — don't crash on non-ASCII paths.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

import core
import server


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Pult kachestva - local dashboard for project test-foundation state"
    )
    ap.add_argument("--file", default=os.environ.get("PULT_FILE", "data/quality-state.yml"),
                    help="path to quality-state.yml (default: data/quality-state.yml)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PULT_PORT", "7842")))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true", help="do not open the browser")
    args = ap.parse_args()

    state_path = Path(args.file).expanduser().resolve()
    httpd = server.serve(state_path, args.host, args.port)
    url = f"http://{args.host}:{args.port}"
    note = "" if state_path.exists() else "  (will be created on first save)"

    print("Pult kachestva")
    print(f"  url:     {url}")
    print(f"  file:    {state_path}{note}")
    print(f"  format:  {core.BACKEND}")
    print("  Ctrl+C to stop")

    if not args.no_browser:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
        httpd.shutdown()


if __name__ == "__main__":
    main()

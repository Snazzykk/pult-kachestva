"""Локальный HTTP-сервер «Пульта качества». Только stdlib.

Отдаёт web/index.html и небольшой JSON API поверх quality-state.yml:
  GET  /api/state   → текущее состояние (JSON)
  PUT  /api/state   → сохранить состояние (пишет YAML + .bak)
  GET  /api/yaml    → сырой текст quality-state.yml
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import core

WEB = Path(__file__).parent / "web"


class Handler(BaseHTTPRequestHandler):
    state_path: Path = Path("data/quality-state.yml")

    # ── helpers ──────────────────────────────────────────────
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def log_message(self, fmt, *args):  # тише в консоли
        return

    # ── routes ───────────────────────────────────────────────
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            html = (WEB / "index.html").read_bytes()
            return self._send(200, html, "text/html; charset=utf-8")
        if self.path == "/api/state":
            try:
                return self._json(core.load_state(self.state_path))
            except ValueError as exc:
                return self._json({"error": str(exc)}, 500)
        if self.path == "/api/yaml":
            return self._send(200, core.raw_yaml(self.state_path).encode("utf-8"),
                              "text/plain; charset=utf-8")
        if self.path == "/api/meta":
            return self._json({"backend": core.BACKEND, "file": str(self.state_path)})
        return self._send(404, b"not found", "text/plain; charset=utf-8")

    do_HEAD = do_GET

    def do_PUT(self):
        if self.path != "/api/state":
            return self._send(404, b"not found", "text/plain; charset=utf-8")
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("ожидался объект")
            core.save_state(self.state_path, data)
        except Exception as exc:  # noqa: BLE001
            return self._json({"error": str(exc)}, 400)
        return self._json({"ok": True, "updated": data.get("company", {}).get("updated")})


def serve(state_path: Path, host: str, port: int) -> ThreadingHTTPServer:
    Handler.state_path = state_path
    httpd = ThreadingHTTPServer((host, port), Handler)
    return httpd

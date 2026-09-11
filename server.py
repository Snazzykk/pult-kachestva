"""Локальный HTTP-сервер «Пульта качества». Только stdlib.

Отдаёт web/index.html и небольшой JSON API поверх quality-state.yml:
  GET  /api/state    → текущее состояние (JSON)
  PUT  /api/state    → сохранить состояние (пишет YAML + .bak)
  GET  /api/yaml     → сырой текст quality-state.yml
  POST /api/openapi  → разобрать OpenAPI-спеку (по url или тексту) → список операций
  POST /api/tms      → разобрать CSV/XLSX-выгрузку кейсов из TMS → таблица + маппинг колонок

Сервер рассчитан на один локальный браузер на 127.0.0.1: без аутентификации,
без CORS. Единственная защита от чужой веб-страницы, дёргающей эти ручки из
браузера пользователя (CSRF) — сверка заголовка Origin с собственным Host на
запросах, которые пишут на диск или ходят в сеть; см. `_origin_ok`.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import core
import openapi

WEB = Path(__file__).parent / "web"

# грубые верхние границы на тело запроса — не пускаем сервер бесконтрольно
# раздувать память на присланных байтах, даже для локального инструмента
MAX_STATE_BYTES = 8 * 1024 * 1024
MAX_OPENAPI_BYTES = 8 * 1024 * 1024
MAX_TMS_BYTES = 32 * 1024 * 1024


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

    def _origin_ok(self) -> bool:
        """Простая CSRF-защита: если браузер прислал Origin — он должен совпадать
        с Host сервера. Origin отсутствует у большинства «своих» запросов и у
        нативных инструментов (curl, tools/*.py) — их не блокируем."""
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = self.headers.get("Host", "")
        return origin in (f"http://{host}", f"https://{host}")

    def _body(self, max_bytes: int | None = None) -> bytes | None:
        """Читает тело запроса. При превышении max_bytes отвечает 413 и
        возвращает None — вызывающий код должен сразу прекратить обработку."""
        length = int(self.headers.get("Content-Length", 0))
        if max_bytes is not None and length > max_bytes:
            self._json({"error": f"тело запроса больше {max_bytes // (1024 * 1024)} МБ"}, 413)
            return None
        return self.rfile.read(length) if length else b""

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
        if not self._origin_ok():
            return self._json({"error": "запрос не с этой страницы (Origin не совпадает)"}, 403)
        raw = self._body(MAX_STATE_BYTES)
        if raw is None:
            return
        raw = raw or b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("ожидался объект")
            core.save_state(self.state_path, data)
        except Exception as exc:  # noqa: BLE001
            return self._json({"error": str(exc)}, 400)
        return self._json({"ok": True, "updated": data.get("company", {}).get("updated")})

    def do_POST(self):
        if not self._origin_ok():
            return self._json({"error": "запрос не с этой страницы (Origin не совпадает)"}, 403)
        if self.path == "/api/openapi":
            ctype = self.headers.get("Content-Type", "").split(";")[0].strip()
            raw = self._body(MAX_OPENAPI_BYTES)
            if raw is None:
                return
            try:
                if ctype == "application/json":
                    req = json.loads(raw.decode("utf-8") or "{}")
                    url, text = str(req.get("url") or "").strip(), req.get("text")
                    if url:
                        return self._json(openapi.summarize(url, is_url=True))
                    if isinstance(text, str) and text.strip():
                        return self._json(openapi.summarize(text, is_url=False))
                    return self._json({"error": "нужен url или text"}, 400)
                # файл: сырые байты, кодировку определяет openapi.summarize_bytes сам
                if not raw:
                    return self._json({"error": "пустой файл"}, 400)
                return self._json(openapi.summarize_bytes(raw))
            except openapi.SpecError as exc:
                return self._json({"error": str(exc)}, 200)
            except Exception as exc:  # noqa: BLE001
                return self._json({"error": f"не обработать: {exc}"}, 500)
        if self.path == "/api/tms":
            try:
                import tmsio
            except Exception as exc:  # noqa: BLE001
                return self._json({"error": f"tmsio недоступен: {exc}"}, 500)
            raw = self._body(MAX_TMS_BYTES)
            if raw is None:
                return
            try:
                return self._json(tmsio.summarize_bytes(raw))
            except tmsio.TmsError as exc:
                return self._json({"error": str(exc)}, 200)
            except Exception as exc:  # noqa: BLE001
                return self._json({"error": f"не обработать: {exc}"}, 500)
        return self._send(404, b"not found", "text/plain; charset=utf-8")


def serve(state_path: Path, host: str, port: int) -> ThreadingHTTPServer:
    Handler.state_path = state_path
    httpd = ThreadingHTTPServer((host, port), Handler)
    return httpd

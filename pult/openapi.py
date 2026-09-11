"""Разбор OpenAPI / Swagger — только stdlib (PyYAML подхватывается, если есть).

Достаёт из спеки плоский список операций (метод + путь + summary + теги),
чтобы «Пульт качества» предложил завести их фичами для оценки риска.
Сам файл спеки нигде не сохраняется — берём только перечень эндпоинтов.
"""
from __future__ import annotations

import contextlib
import json
import urllib.request

from .textenc import decode_bytes  # noqa: F401 (реэкспорт — использовался как openapi.decode_bytes)

try:  # необязательно — нужен только для YAML-спек
    import yaml as _pyyaml  # type: ignore
except Exception:  # noqa: BLE001
    _pyyaml = None

try:
    from . import yamlio as _yamlio  # свой минимальный парсер
except Exception:  # noqa: BLE001
    _yamlio = None

_METHODS = ("get", "post", "put", "patch", "delete", "options", "head", "trace")
_UA = "pult-kachestva/openapi-import"


class SpecError(ValueError):
    """Спеку не удалось скачать или разобрать."""


_MAX_FETCH_BYTES = 8 * 1024 * 1024


def fetch(url: str, timeout: float = 12.0) -> bytes:
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise SpecError("ссылка должна начинаться с http:// или https://")
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json, application/yaml, text/yaml, */*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (локальный инструмент)
            # читаем чанками и обрываем сразу, как только превысили лимит —
            # не ждём, пока докачается вся (возможно, огромная) страница
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_FETCH_BYTES:
                    raise SpecError("спека больше 8 МБ — сохрани её файлом и загрузи вручную")
                chunks.append(chunk)
    except SpecError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SpecError(f"не скачалось: {exc}") from exc
    return b"".join(chunks)


def parse_spec(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise SpecError("пусто")
    # 1. JSON
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else _bad_shape()
    except json.JSONDecodeError:
        pass
    # 2. PyYAML
    if _pyyaml is not None:
        with contextlib.suppress(Exception):
            obj = _pyyaml.safe_load(text)
            return obj if isinstance(obj, dict) else _bad_shape()
    # 3. свой yamlio
    if _yamlio is not None:
        with contextlib.suppress(Exception):
            obj = _yamlio.safe_load(text)
            if isinstance(obj, dict) and obj:
                return obj
    hint = "" if _pyyaml is not None else " (для YAML поставь PyYAML: python -m pip install pyyaml)"
    raise SpecError("не разобрать как JSON или YAML — проверь, что это валидная OpenAPI-спека" + hint)


def _bad_shape() -> dict:
    raise SpecError("это не похоже на OpenAPI: на верхнем уровне ожидался объект")


def operations(spec: dict) -> list[dict]:
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        raise SpecError("в спеке нет секции paths")
    common_tags = spec.get("tags")
    out: list[dict] = []
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        shared_params = item.get("parameters")
        for method, op in item.items():
            if method.lower() not in _METHODS or not isinstance(op, dict):
                continue
            tags = op.get("tags")
            tags = [str(t) for t in tags] if isinstance(tags, list) and tags else ["без тега"]
            summary = str(op.get("summary") or op.get("description") or "").strip().splitlines()[0:1]
            out.append({
                "method": method.upper(),
                "path": str(path),
                "summary": (summary[0].strip() if summary else ""),
                "operationId": str(op.get("operationId") or ""),
                "deprecated": bool(op.get("deprecated")),
                "tags": tags,
            })
    out.sort(key=lambda o: (o["tags"][0], o["path"], o["method"]))
    _ = (common_tags, shared_params)  # не используем, но пусть будет явно
    return out


def _summary(spec: dict) -> dict:
    info = spec.get("info") if isinstance(spec.get("info"), dict) else {}
    ops = operations(spec)
    return {
        "ok": True,
        "title": str(info.get("title") or "").strip(),
        "version": str(info.get("version") or "").strip(),
        "openapi": str(spec.get("openapi") or spec.get("swagger") or "").strip(),
        "count": len(ops),
        "operations": ops,
    }


def summarize(text_or_url: str, is_url: bool) -> dict:
    text = decode_bytes(fetch(text_or_url)) if is_url else text_or_url
    return _summary(parse_spec(text))


def summarize_bytes(raw: bytes) -> dict:
    """Спека, пришедшая файлом — кодировку определяем сами (utf-8 / cp1251 / …)."""
    return _summary(parse_spec(decode_bytes(raw)))

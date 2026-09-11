"""Чтение и запись quality-state.yml.

Пишем через встроенный `yamlio` — компактный, диффабельный формат
(flow-словари, короткие списки в одну строку). Читаем через PyYAML,
если он есть (устойчивее к ручным правкам), иначе — тем же `yamlio`.
Внешние зависимости не обязательны.
"""
from __future__ import annotations

import datetime
import shutil
from pathlib import Path
from typing import Any

import yamlio
from textenc import decode_bytes

try:
    import yaml as _pyyaml

    def _load(text: str) -> Any:
        return _pyyaml.safe_load(text)

    BACKEND = "PyYAML (чтение) + yamlio (запись)"
except ImportError:
    def _load(text: str) -> Any:
        return yamlio.safe_load(text)

    BACKEND = "yamlio (встроенный)"


HEADER = (
    "# quality-state.yml — состояние тест-фундамента проекта.\n"
    "# Редактируется через «Пульт качества» (локальный UI). Можно править и руками.\n"
    "# Модель (описания уровней, критерии Quality Gates) живёт в коде инструмента,\n"
    "# здесь — только ответы «есть / нет» и цифры по конкретному проекту."
)


def load_state(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        text = decode_bytes(p.read_bytes())
        data = _load(text)
    except Exception as exc:  # noqa: BLE001 — показываем причину пользователю
        raise ValueError(f"не удалось разобрать {p.name}: {exc}") from exc
    return data or {}


def save_state(path: str | Path, data: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    data = dict(data or {})
    company = dict(data.get("company") or {})
    company["updated"] = datetime.date.today().isoformat()
    data["company"] = company

    text = yamlio.safe_dump(data, header=HEADER)

    if p.exists():
        shutil.copy2(p, p.with_name(p.name + ".bak"))
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)


def raw_yaml(path: str | Path) -> str:
    p = Path(path)
    if not p.exists():
        return "# файл ещё не создан — измени что-нибудь в интерфейсе, и он появится"
    return decode_bytes(p.read_bytes())

"""Определение кодировки текстовых файлов — только stdlib.

Общее для всех импортёров (OpenAPI, TMS-выгрузки): файл с диска может
быть не в UTF-8 (частый случай для старых корпоративных экспортов —
cp1251/cp1252). Пробуем по очереди и берём первую кодировку, что
декодируется строго, а не молча бьём байты как UTF-8.
"""
from __future__ import annotations

ENCODINGS = ("utf-8-sig", "utf-8", "cp1251", "cp1252")


def decode_bytes(raw: bytes) -> str:
    for enc in ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")

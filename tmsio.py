"""Импорт кейсов из ручного CSV/XLSX-экспорта любой TMS — только stdlib.

Не привязан к конкретной системе (Test IT, TestRail, Xray, Zephyr, ...):
разбирает файл в таблицу «заголовки + строки», угадывает, какая колонка
похожа на раздел/тег/статус автоматизации, а дальше пользователь
подтверждает маппинг в UI. Источник истины для «всего кейсов» (t) — тут
есть и ручные, и автоматизированные; если в файле есть колонка со
статусом автоматизации — заодно посчитает и «автоматизировано» (a).

XLSX читаем руками (zip + XML из sharedStrings.xml/sheetN.xml) —
openpyxl не тянем, чтобы не заводить внешнюю зависимость.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
import xml.etree.ElementTree as ET

from textenc import decode_bytes

_LAYERS = ("smoke", "sanity", "regress")
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}

# ключи — не целые слова, а основы: RU-заголовки часто в другом падеже
# («Название» / «Наименования» / «Раздела»), substring-проверка иначе не сработает
_GUESS = {
    "title": ("title", "name", "summary", "case name", "test case", "названи", "наименован", "заголов", "кейс"),
    "section": ("section", "suite", "folder", "path", "component", "раздел", "папк", "сьют", "компонент", "модул"),
    "tag": ("tag", "label", "attribute", "тег", "метк", "атрибут"),
    "automation": ("automation type", "test type", "automation", "automated", "is auto", "автоматизац"),
}
_LAYER_KW = {"smoke": ("smoke", "смок"), "sanity": ("sanity", "санит"), "regress": ("regress", "регресс")}
_AUTO_TRUE = ("automat", "авто")
_AUTO_FALSE = ("manual", "ручн")


class TmsError(ValueError):
    """Файл не удалось прочитать как таблицу кейсов."""


# ---------- CSV ----------

def _guess_delimiter(text: str) -> str:
    """csv.Sniffer капризничает на коротких/неровных выгрузках — запасной эвристик:
    берём разделитель, который встречается стабильно (минимум по всем строкам сэмпла)."""
    best, best_score = ",", 0
    for d in (",", ";", "\t"):
        counts = [ln.count(d) for ln in text.splitlines()[:20] if ln.strip()]
        score = min(counts) if counts else 0
        if score > best_score:
            best_score, best = score, d
    return best


def read_csv(raw: bytes) -> tuple[list[str], list[list[str]]]:
    text = decode_bytes(raw)
    sample = text[:4096]
    try:
        delim = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        delim = _guess_delimiter(sample)
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if any(c.strip() for c in r)]
    if not rows:
        raise TmsError("файл пуст")
    headers = [h.strip() for h in rows[0]]
    return headers, rows[1:]


# ---------- XLSX (zip + голый XML, без openpyxl) ----------

def _col_index(ref: str) -> int:
    letters = "".join(ch for ch in ref if ch.isalpha())
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch.upper()) - ord("A") + 1)
    return idx - 1


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    out = []
    for si in root.findall("m:si", _NS):
        out.append("".join((t.text or "") for t in si.iter("{%s}t" % _NS["m"])))
    return out


def _first_sheet_path(zf: zipfile.ZipFile) -> str:
    wb = ET.fromstring(zf.read("xl/workbook.xml"))
    sheets = wb.find("m:sheets", _NS)
    first = sheets.find("m:sheet", _NS) if sheets is not None else None
    if first is None:
        raise TmsError("в книге нет листов")
    rid = first.get("{%s}id" % _NS["r"])
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    target = None
    for rel in rels:
        if rel.get("Id") == rid:
            target = rel.get("Target")
            break
    if not target:
        raise TmsError("не нашёл первый лист")
    target = target.lstrip("/")
    return target if target.startswith("xl/") else "xl/" + target


def read_xlsx(raw: bytes) -> tuple[list[str], list[list[str]]]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise TmsError("файл повреждён или это не .xlsx") from exc
    try:
        shared = _shared_strings(zf)
        sheet = ET.fromstring(zf.read(_first_sheet_path(zf)))
    except KeyError as exc:
        raise TmsError(f"в архиве не нашлось {exc} — это не стандартный .xlsx") from exc
    except ET.ParseError as exc:
        raise TmsError(f"не разобрать XML внутри .xlsx: {exc}") from exc
    sheet_data = sheet.find("m:sheetData", _NS)
    if sheet_data is None:
        raise TmsError("в листе нет данных")
    grid: list[dict[int, str]] = []
    width = 0
    tpath = "{%s}t" % _NS["m"]
    for row_el in sheet_data.findall("m:row", _NS):
        cells: dict[int, str] = {}
        for i, c in enumerate(row_el.findall("m:c", _NS)):
            ref = c.get("r") or ""
            idx = _col_index(ref) if ref else i
            t = c.get("t")
            if t == "s":
                v = c.find("m:v", _NS)
                cells[idx] = shared[int(v.text)] if v is not None and v.text and v.text.isdigit() else ""
            elif t == "inlineStr":
                is_el = c.find("m:is", _NS)
                cells[idx] = "".join((el.text or "") for el in is_el.iter(tpath)) if is_el is not None else ""
            else:
                v = c.find("m:v", _NS)
                cells[idx] = v.text if v is not None and v.text is not None else ""
            width = max(width, idx + 1)
        grid.append(cells)
    rows = [[cells.get(i, "") for i in range(width)] for cells in grid]
    rows = [r for r in rows if any(str(c).strip() for c in r)]
    if not rows:
        raise TmsError("лист пуст")
    headers = [str(h).strip() for h in rows[0]]
    return headers, rows[1:]


def read_table(raw: bytes) -> tuple[list[str], list[list[str]]]:
    if not raw:
        raise TmsError("пустой файл")
    if raw[:2] == b"PK":
        return read_xlsx(raw)
    return read_csv(raw)


# ---------- маппинг колонок и классификация ----------

def guess_mapping(headers: list[str]) -> dict[str, int | None]:
    used: set[int] = set()
    out: dict[str, int | None] = {}
    for field, kws in _GUESS.items():
        found = None
        for i, h in enumerate(headers):
            if i in used:
                continue
            hl = h.lower()
            if any(kw in hl for kw in kws):
                found = i
                break
        out[field] = found
        if found is not None:
            used.add(found)
    return out


def classify_layer(text) -> str | None:
    tl = str(text or "").lower()
    for layer in _LAYERS:
        if any(kw in tl for kw in _LAYER_KW[layer]):
            return layer
    return None


def is_automated(text) -> bool | None:
    tl = str(text or "").strip().lower()
    if not tl:
        return None
    if any(kw in tl for kw in _AUTO_FALSE):
        return False
    if any(kw in tl for kw in _AUTO_TRUE):
        return True
    return None


def analyze(headers: list[str], rows: list[list[str]], mapping: dict[str, int | None]) -> dict:
    tag_i = mapping.get("tag")
    section_i = mapping.get("section")
    layer_i = tag_i if tag_i is not None else section_i
    auto_i = mapping.get("automation")

    total = {k: 0 for k in _LAYERS}
    autoc = {k: 0 for k in _LAYERS}
    unclassified = 0
    n = 0
    for row in rows:
        if not any(str(c).strip() for c in row):
            continue
        n += 1
        text = row[layer_i] if layer_i is not None and layer_i < len(row) else ""
        layer = classify_layer(text)
        if layer is None:
            unclassified += 1
            layer = "regress"
        total[layer] += 1
        if auto_i is not None and auto_i < len(row) and is_automated(row[auto_i]) is True:
            autoc[layer] += 1
    return {
        "ok": True,
        "total_rows": n,
        "buckets": total,
        "automated": autoc if auto_i is not None else None,
        "unclassified": unclassified,
    }


def summarize_bytes(raw: bytes) -> dict:
    headers, rows = read_table(raw)
    return {
        "ok": True,
        "headers": headers,
        "rows": rows,
        "guess": guess_mapping(headers),
        "total": len(rows),
    }

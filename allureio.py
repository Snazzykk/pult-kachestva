"""Разбор выгрузки Allure (allure-results) — только stdlib.

Считает по последнему прогону: сколько автотестов всего и по слоям
Smoke / Sanity / Regression, и долю нестабильных (тесты с ретраями или
пометкой flaky). Отдаёт сводку — «Пульт качества» показывает дифф со
счётчиками сервиса и по кнопке применяет.

Раскладка по слоям: ищем в метках (tag / suite / subSuite / layer /
story / feature) значения со «smoke» / «sanity» / «regress». Что без
метки — уходит в Regression (и считается отдельно, чтобы было видно).
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

_BUCKETS = ("smoke", "sanity", "regress")
_LABEL_KEYS = ("tag", "suite", "subsuite", "layer", "story", "feature", "epic", "parentsuite")


class AllureError(ValueError):
    """Выгрузку не удалось прочитать или в ней нет результатов."""


def _bucket(labels: list[dict]) -> str | None:
    blob = " ".join(str(l.get("value", "")).lower()
                     for l in labels if str(l.get("name", "")).lower() in _LABEL_KEYS)
    blob += " " + " ".join(str(l.get("value", "")).lower()
                           for l in labels if str(l.get("name", "")).lower() == "tag")
    if "smoke" in blob:
        return "smoke"
    if "sanity" in blob:
        return "sanity"
    if "regress" in blob:
        return "regress"
    return None


def _is_flaky(r: dict) -> bool:
    sd = r.get("statusDetails") or {}
    if sd.get("flaky"):
        return True
    return any(str(l.get("name", "")).lower() == "flaky" for l in r.get("labels") or [])


def analyze(results: list[dict]) -> dict:
    if not results:
        raise AllureError("в выгрузке нет *-result.json")

    groups: dict[str, list[dict]] = {}
    for r in results:
        key = str(r.get("historyId") or r.get("fullName") or r.get("name") or id(r))
        groups.setdefault(key, []).append(r)

    def final(rs: list[dict]) -> dict:
        return max(rs, key=lambda x: (x.get("stop") or x.get("start") or 0))

    total = len(groups)
    by_bucket = {b: 0 for b in _BUCKETS}
    unlabeled = 0
    by_status: dict[str, int] = {}
    flaky_keys: set[str] = set()

    for key, rs in groups.items():
        f = final(rs)
        b = _bucket(f.get("labels") or [])
        if b is None:
            unlabeled += 1
            b = "regress"
        by_bucket[b] += 1
        st = str(f.get("status") or "unknown").lower()
        by_status[st] = by_status.get(st, 0) + 1
        if len(rs) > 1 or any(_is_flaky(x) for x in rs):
            flaky_keys.add(key)

    flaky_pct = round(len(flaky_keys) / total * 100) if total else 0
    return {
        "ok": True,
        "total": total,
        "buckets": by_bucket,          # {smoke: N, sanity: N, regress: N} — все автоматизированы
        "unlabeled": unlabeled,        # из них попало в regress без явной метки
        "flaky": len(flaky_keys),
        "flakyPct": flaky_pct,
        "status": by_status,           # {passed: N, failed: N, broken: N, skipped: N}
    }


def _load_result_files(texts) -> list[dict]:
    out: list[dict] = []
    for t in texts:
        try:
            obj = json.loads(t)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and (obj.get("name") or obj.get("fullName")):
            out.append(obj)
    return out


def analyze_dir(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        raise AllureError(f"нет такого пути: {p}")
    root = p
    if p.is_file():
        raise AllureError("нужна папка allure-results, а не файл")
    files = sorted(root.rglob("*-result.json"))
    if not files:
        # вдруг указали родителя
        alt = sorted(root.rglob("*result.json"))
        files = alt
    if not files:
        raise AllureError(f"в {p} не найдено *-result.json")
    texts = []
    for fp in files:
        try:
            texts.append(fp.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    return analyze(_load_result_files(texts))


def summarize_zip(raw: bytes) -> dict:
    if not raw:
        raise AllureError("пустой файл")
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise AllureError("это не zip-архив") from exc
    names = [n for n in zf.namelist() if n.lower().endswith("-result.json")]
    if not names:
        names = [n for n in zf.namelist() if n.lower().endswith("result.json")]
    if not names:
        raise AllureError("в архиве нет *-result.json (заархивируй папку allure-results)")
    if len(names) > 20000:
        raise AllureError("слишком много файлов в архиве")
    texts = []
    for n in names:
        try:
            texts.append(zf.read(n).decode("utf-8", errors="replace"))
        except Exception:  # noqa: BLE001
            pass
    return analyze(_load_result_files(texts))

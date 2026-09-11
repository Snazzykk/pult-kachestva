"""Обновить счётчики кейсов сервиса из ручного CSV/XLSX-экспорта любой TMS.

    python tools/import_tms.py --file cases.csv --service checkout
    python tools/import_tms.py --file cases.xlsx --service checkout --layer-col Теги --auto-col "Тип автоматизации"

Не привязан к конкретной системе (Test IT, TestRail, Xray, Zephyr, ...) —
разбирает таблицу, угадывает колонку слоя (тег/раздел, по вхождению
smoke/sanity/regress) и колонку статуса автоматизации, либо бери их
по имени флагом. Источник «всего кейсов» (t) — тут и ручные, и авто;
если найдена колонка автоматизации — заодно посчитает «автоматизировано».
Слой без данных в выгрузке не трогаем (не затираем существующие числа).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core
import tmsio

_LAYERS = ("smoke", "sanity", "regress")


def _col_by_name(headers: list[str], name: str | None) -> int | None:
    if not name:
        return None
    name = name.strip().lower()
    for i, h in enumerate(headers):
        if h.strip().lower() == name:
            return i
    for i, h in enumerate(headers):  # fallback — по вхождению
        if name in h.strip().lower():
            return i
    print(f"[!] колонка '{name}' не найдена в заголовках: {headers}")
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Импорт кейсов из CSV/XLSX-выгрузки TMS")
    ap.add_argument("--file", required=True, help="файл экспорта (.csv / .xlsx)")
    ap.add_argument("--service", required=True, help="id сервиса в quality-state.yml")
    ap.add_argument("--state-file", default="data/quality-state.yml", help="путь к quality-state.yml")
    ap.add_argument("--layer-col", help="имя колонки со слоем (тег/раздел); по умолчанию — угадывается")
    ap.add_argument("--auto-col", help="имя колонки статуса автоматизации; по умолчанию — угадывается")
    ap.add_argument("--yes", action="store_true", help="применить без вопроса")
    args = ap.parse_args()

    raw = Path(args.file).read_bytes()
    try:
        headers, rows = tmsio.read_table(raw)
    except tmsio.TmsError as exc:
        print(f"[!] {exc}")
        return 1

    guess = tmsio.guess_mapping(headers)
    layer_i = _col_by_name(headers, args.layer_col) if args.layer_col else (
        guess["tag"] if guess["tag"] is not None else guess["section"])
    auto_i = _col_by_name(headers, args.auto_col) if args.auto_col else guess["automation"]

    print(f"колонки: {headers}")
    print(f"слой: {headers[layer_i] if layer_i is not None else '(не найдена)'}   "
          f"автоматизация: {headers[auto_i] if auto_i is not None else '(не указана — \"автоматизировано\" не трогаем)'}")
    print()

    rep = tmsio.analyze(headers, rows, {"tag": layer_i, "section": None, "automation": auto_i})
    if rep["unclassified"]:
        print(f"{rep['unclassified']} строк без явного слоя -> отнесены к Regression")

    state = core.load_state(args.state_file)
    svc = next((s for s in state.get("services", []) if s.get("id") == args.service), None)
    if svc is None:
        ids = ", ".join(s.get("id", "?") for s in state.get("services", [])) or "(нет сервисов)"
        print(f"[!] сервис '{args.service}' не найден. Есть: {ids}")
        return 1
    cases = svc.setdefault("cases", {})

    print(f"{'слой':<10} {'в выгрузке':>12} {'было a/t':>12} {'станет a/t':>14}")
    plan = []
    for layer in _LAYERS:
        cur = cases.get(layer) or {"t": 0, "a": 0}
        cur_t, cur_a = int(cur.get("t", 0)), int(cur.get("a", 0))
        found = rep["buckets"][layer]
        apply_ = found > 0
        new_t = found if apply_ else cur_t
        if apply_:
            new_a = min(rep["automated"][layer], new_t) if auto_i is not None else min(cur_a, new_t)
        else:
            new_a = cur_a
        mark = "  <-" if apply_ and (new_t != cur_t or new_a != cur_a) else ""
        print(f"{layer:<10} {found if apply_ else '-':>12} {f'{cur_a}/{cur_t}':>12} "
              f"{(f'{new_a}/{new_t}' if apply_ else 'без изменений'):>14}{mark}")
        if apply_:
            plan.append((layer, new_t, new_a))
    print()

    if not args.yes:
        try:
            ans = input("Применить к quality-state.yml? [y/N] ").strip().lower()
        except EOFError:
            ans = "n"
        if ans not in ("y", "yes", "д", "да"):
            print("отменено")
            return 0

    for layer, t, a in plan:
        cases[layer] = {"t": t, "a": a}
    core.save_state(args.state_file, state)
    print(f"[ok] обновлено: {args.state_file}  (прежняя версия в .bak)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

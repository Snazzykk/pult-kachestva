"""Обновить счётчики автотестов сервиса из выгрузки Allure.

    python tools/import_allure.py --dir ./allure-results --service checkout
    python tools/import_allure.py --dir ./allure-results --service checkout --file "C:/work/qa-state/quality-state.yml" --yes

Считает по последнему прогону число автотестов и раскладывает по
Smoke / Sanity / Regression (по меткам tag/suite/layer со «smoke»/
«sanity»/«regress»; что без метки — в Regression). Плюс доля flaky
(тесты с ретраями). Показывает дифф и спрашивает подтверждение.
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

import allureio
import core

_BUCKETS = ("smoke", "sanity", "regress")


def main() -> int:
    ap = argparse.ArgumentParser(description="Импорт счётчиков автотестов из Allure")
    ap.add_argument("--dir", required=True, help="папка allure-results")
    ap.add_argument("--service", required=True, help="id сервиса в quality-state.yml")
    ap.add_argument("--file", default="data/quality-state.yml", help="путь к quality-state.yml")
    ap.add_argument("--yes", action="store_true", help="применить без вопроса")
    args = ap.parse_args()

    try:
        rep = allureio.analyze_dir(args.dir)
    except allureio.AllureError as exc:
        print(f"[!] {exc}")
        return 1

    state = core.load_state(args.file)
    svc = next((s for s in state.get("services", []) if s.get("id") == args.service), None)
    if svc is None:
        ids = ", ".join(s.get("id", "?") for s in state.get("services", [])) or "(нет сервисов)"
        print(f"[!] сервис '{args.service}' не найден. Есть: {ids}")
        return 1

    cases = svc.setdefault("cases", {})
    status = ", ".join(f"{k}: {v}" for k, v in sorted(rep["status"].items()))
    print(f"Allure: {rep['total']} автотестов  ({status})")
    if rep["unlabeled"]:
        print(f"        {rep['unlabeled']} без метки smoke/sanity/regress → отнесены к Regression")
    print()
    print(f"{'слой':<10} {'было a/t':>12} {'станет a/t':>12}")
    changes = []
    for b in _BUCKETS:
        cur = cases.get(b) or {"t": 0, "a": 0}
        cur_t, cur_a = int(cur.get("t", 0)), int(cur.get("a", 0))
        new_a = rep["buckets"][b]
        new_t = max(cur_t, new_a)
        mark = "" if (new_a == cur_a and new_t == cur_t) else "  <-"
        print(f"{b:<10} {f'{cur_a}/{cur_t}':>12} {f'{new_a}/{new_t}':>12}{mark}")
        changes.append((b, new_t, new_a))
    old_flaky = int(svc.get("flaky", 0))
    print(f"{'flaky %':<10} {old_flaky:>12} {rep['flakyPct']:>12}{'  <-' if rep['flakyPct'] != old_flaky else ''}")
    print()

    if not args.yes:
        try:
            ans = input("Применить к quality-state.yml? [y/N] ").strip().lower()
        except EOFError:
            ans = "n"
        if ans not in ("y", "yes", "д", "да"):
            print("отменено")
            return 0

    for b, t, a in changes:
        cases[b] = {"t": t, "a": a}
    svc["flaky"] = rep["flakyPct"]
    core.save_state(args.file, state)
    print(f"[ok] обновлено: {args.file}  (прежняя версия в .bak)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

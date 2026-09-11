"""Собрать автономный HTML с вшитыми данными — для отправки менеджеру,
вложения в Confluence или выкладки на внутренний хостинг.

    python build.py                       # data/quality-state.yml → dist/pult.html
    python build.py --file x.yml --out out/report.html

Получившийся файл открывается двойным кликом, работает без сервера,
данные в нём read-only (правки не сохраняются).
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import sys
from pathlib import Path

# Windows-консоль часто в cp1251/cp866 — не падаем на кириллице в путях/сообщениях.
for _s in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _s.reconfigure(encoding="utf-8", errors="replace")

from pult import core  # noqa: E402 (после фикса кодировки консоли — должно быть после)

WEB = Path(__file__).parent / "web" / "index.html"


def main() -> None:
    ap = argparse.ArgumentParser(description="Сборка автономного HTML «Пульта качества»")
    ap.add_argument("--file", default="data/quality-state.yml")
    ap.add_argument("--out", default="dist/pult.html")
    args = ap.parse_args()

    html = WEB.read_text(encoding="utf-8")
    state = core.load_state(args.file)
    stamp = datetime.date.today().isoformat()

    inject = (
        "<script>window.__PULT_STATE__="
        + json.dumps(state, ensure_ascii=False)
        + f";window.__PULT_READONLY__=true;window.__PULT_BUILT__={json.dumps(stamp)};</script>"
    )
    if "</head>" not in html:
        raise SystemExit("web/index.html без <head> — сначала адаптируй его под сервер")
    html = html.replace("</head>", inject + "\n</head>", 1)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"built {out}  ({out.stat().st_size // 1024} KB, data as of {stamp})")


if __name__ == "__main__":
    main()

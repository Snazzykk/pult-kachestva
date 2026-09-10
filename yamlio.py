"""Минимальный YAML для «Пульта качества» — ноль внешних зависимостей.

Поддерживает подмножество YAML, которое пишет само приложение:
блочные словари и списки, flow-формы {..} и [..], комментарии `#`,
строки в двойных кавычках и без, числа / bool / null.

Если в окружении установлен PyYAML — `core` использует его; этот модуль
остаётся резервным вариантом, чтобы инструмент работал вообще без pip install.
"""
from __future__ import annotations

import re
from typing import Any

__all__ = ["safe_load", "safe_dump", "YamlError"]


class YamlError(ValueError):
    pass


# ─────────────────────────────── dump ────────────────────────────────

_PLAIN_RE = re.compile(r"^[^\W]|^[\w./@:+-]+$", re.UNICODE)  # первичный фильтр
_NUMISH_RE = re.compile(r"^-?\d+(\.\d+)?$")
_DATEISH_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_RESERVED = {"null", "none", "true", "false", "yes", "no", "on", "off", "~", ""}


def _needs_quote(s: str) -> bool:
    if s == "":
        return True
    if s != s.strip():
        return True
    if s.lower() in _RESERVED:
        return True
    if _NUMISH_RE.match(s) or _DATEISH_RE.match(s):
        return True
    if s[0] in "!&*?|>%@`\"'[]{},#-":
        return True
    if ": " in s or s.endswith(":") or " #" in s:
        return True
    # разрешаем буквы (в т.ч. кириллицу), цифры и немного пунктуации без пробелов
    return not re.fullmatch(r"[\w./@:+()=-]+", s, re.UNICODE)


def _scalar(v: Any) -> str:
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, float):
        # без экспоненты и без лишнего .0-хвоста через repr
        return repr(v)
    if isinstance(v, int):
        return str(v)
    s = str(v)
    if _needs_quote(s):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    return s


def _all_scalar(seq) -> bool:
    return all(not isinstance(x, (dict, list)) for x in seq)


def _flow(v: Any) -> str:
    if isinstance(v, dict):
        return "{" + ", ".join(f"{_scalar(k)}: {_flow(val)}" for k, val in v.items()) + "}"
    if isinstance(v, list):
        return "[" + ", ".join(_flow(x) for x in v) + "]"
    return _scalar(v)


def _dump(v: Any, indent: int, out: list) -> None:
    pad = "  " * indent
    if isinstance(v, dict):
        if not v:
            out[-1] += " {}"
            return
        for k, val in v.items():
            key = _scalar(k)
            if isinstance(val, dict) and val and not _all_scalar(val.values()):
                out.append(f"{pad}{key}:")
                _dump(val, indent + 1, out)
            elif isinstance(val, dict) and val:
                out.append(f"{pad}{key}: {_flow(val)}")
            elif isinstance(val, list) and val and _all_scalar(val):
                out.append(f"{pad}{key}: {_flow(val)}")
            elif isinstance(val, list) and val:
                out.append(f"{pad}{key}:")
                _dump(val, indent + 1, out)
            elif isinstance(val, (dict, list)):  # пустые
                out.append(f"{pad}{key}: {'{}' if isinstance(val, dict) else '[]'}")
            else:
                out.append(f"{pad}{key}: {_scalar(val)}")
    elif isinstance(v, list):
        for item in v:
            if isinstance(item, dict) and item and _all_scalar(item.values()):
                out.append(f"{pad}- {_flow(item)}")
            elif isinstance(item, list) and _all_scalar(item):
                out.append(f"{pad}- {_flow(item)}")
            elif isinstance(item, (dict, list)) and item:
                sub: list = []
                _dump(item, indent + 1, sub)
                out.append(f"{pad}- " + sub[0].lstrip())
                out.extend(sub[1:])
            else:
                out.append(f"{pad}- {_scalar(item)}")
    else:
        out.append(f"{pad}{_scalar(v)}")


def safe_dump(data: Any, header: str = "") -> str:
    out: list = []
    _dump(data, 0, out)
    body = "\n".join(out) + "\n"
    return (header.rstrip() + "\n\n" + body) if header else body


# ─────────────────────────────── load ────────────────────────────────

def _strip_comment(line: str) -> str:
    q = None
    for i, ch in enumerate(line):
        if q:
            if ch == q and line[i - 1] != "\\":
                q = None
        elif ch in "\"'":
            q = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line


def _parse_scalar(tok: str) -> Any:
    tok = tok.strip()
    if tok == "" or tok == "~" or tok.lower() == "null":
        return None
    if tok.lower() == "true":
        return True
    if tok.lower() == "false":
        return False
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
        inner = tok[1:-1]
        if tok[0] == '"':
            inner = inner.replace('\\n', "\n").replace('\\"', '"').replace("\\\\", "\\")
        return inner
    if _NUMISH_RE.match(tok):
        return float(tok) if "." in tok else int(tok)
    return tok


def _parse_flow(s: str) -> Any:
    val, rest = _flow_value(s.strip())
    if rest.strip():
        raise YamlError(f"лишние символы после flow-значения: {rest!r}")
    return val


def _flow_value(s: str):
    s = s.lstrip()
    if s.startswith("{"):
        d = {}
        s = s[1:].lstrip()
        if s.startswith("}"):
            return d, s[1:]
        while True:
            key, s = _flow_token(s, stop=":")
            s = s.lstrip()
            if not s.startswith(":"):
                raise YamlError("ожидалось ':' в flow-словаре")
            v, s = _flow_value(s[1:])
            d[_parse_scalar(key)] = v
            s = s.lstrip()
            if s.startswith(","):
                s = s[1:]
                continue
            if s.startswith("}"):
                return d, s[1:]
            raise YamlError(f"ожидалось ',' или '}}' в flow-словаре: {s!r}")
    if s.startswith("["):
        arr = []
        s = s[1:].lstrip()
        if s.startswith("]"):
            return arr, s[1:]
        while True:
            v, s = _flow_value(s)
            arr.append(v)
            s = s.lstrip()
            if s.startswith(","):
                s = s[1:]
                continue
            if s.startswith("]"):
                return arr, s[1:]
            raise YamlError(f"ожидалось ',' или ']' в flow-списке: {s!r}")
    return _flow_scalar(s)


def _flow_token(s: str, stop: str):
    """Читает ключ до `stop` (учитывая кавычки)."""
    s = s.lstrip()
    if s and s[0] in "\"'":
        q = s[0]
        i = 1
        while i < len(s) and not (s[i] == q and s[i - 1] != "\\"):
            i += 1
        return s[: i + 1], s[i + 1:]
    i = 0
    while i < len(s) and s[i] not in stop + ",{}[]":
        i += 1
    return s[:i], s[i:]


def _flow_scalar(s: str):
    if s and s[0] in "\"'":
        q = s[0]
        i = 1
        while i < len(s) and not (s[i] == q and s[i - 1] != "\\"):
            i += 1
        return _parse_scalar(s[: i + 1]), s[i + 1:]
    i = 0
    while i < len(s) and s[i] not in ",{}[]":
        i += 1
    return _parse_scalar(s[:i]), s[i:]


class _Line:
    __slots__ = ("indent", "text", "n")

    def __init__(self, raw: str, n: int):
        stripped = raw.lstrip(" ")
        self.indent = len(raw) - len(stripped)
        self.text = stripped.rstrip()
        self.n = n


def safe_load(text: str) -> Any:
    lines = []
    for i, raw in enumerate(text.splitlines(), 1):
        raw = _strip_comment(raw).rstrip()
        if raw.strip() == "" or raw.strip() == "---":
            continue
        lines.append(_Line(raw, i))
    if not lines:
        return None
    val, idx = _parse_block(lines, 0, lines[0].indent)
    return val


def _parse_block(lines, i: int, indent: int):
    if i >= len(lines):
        return None, i
    if lines[i].text.startswith("- "):
        return _parse_seq(lines, i, indent)
    if lines[i].text == "-":
        return _parse_seq(lines, i, indent)
    return _parse_map(lines, i, indent)


def _parse_map(lines, i: int, indent: int):
    d = {}
    while i < len(lines):
        ln = lines[i]
        if ln.indent < indent:
            break
        if ln.indent > indent:
            raise YamlError(f"строка {ln.n}: неожиданный отступ")
        if ln.text.startswith("- ") or ln.text == "-":
            break
        m = re.match(r"^([^:]+?):(?:\s+(.*))?$", ln.text)
        if not m:
            raise YamlError(f"строка {ln.n}: не разобрать '{ln.text}'")
        key = _parse_scalar(m.group(1))
        rest = m.group(2)
        if rest is not None and rest != "":
            d[key] = _parse_flow(rest) if rest[0] in "{[" or rest[0] in "\"'" else _parse_scalar(rest)
            i += 1
        else:
            if i + 1 < len(lines) and lines[i + 1].indent > indent:
                child_indent = lines[i + 1].indent
                d[key], i = _parse_block(lines, i + 1, child_indent)
            else:
                d[key] = None
                i += 1
    return d, i


def _parse_seq(lines, i: int, indent: int):
    arr = []
    while i < len(lines):
        ln = lines[i]
        if ln.indent < indent or not (ln.text.startswith("- ") or ln.text == "-"):
            if ln.indent <= indent:
                break
            raise YamlError(f"строка {ln.n}: ожидался элемент списка")
        if ln.text == "-":
            if i + 1 < len(lines) and lines[i + 1].indent > indent:
                child, i = _parse_block(lines, i + 1, lines[i + 1].indent)
                arr.append(child)
            else:
                arr.append(None)
                i += 1
            continue
        content = ln.text[2:]
        if content and content[0] in "{[":
            arr.append(_parse_flow(content))
            i += 1
        elif ":" in content and re.match(r"^[^:]+?:(\s|$)", content):
            # элемент-словарь, начинающийся на той же строке
            synthetic = _Line(" " * (ln.indent + 2) + content, ln.n)
            block = [synthetic] + lines[i + 1:]
            # найдём, докуда тянется этот под-блок
            j = 1
            while j < len(block) and (block[j].indent > ln.indent):
                j += 1
            sub = block[:j]
            val, _ = _parse_map(sub, 0, ln.indent + 2)
            arr.append(val)
            i += j
        else:
            arr.append(_parse_scalar(content))
            i += 1
    return arr, i

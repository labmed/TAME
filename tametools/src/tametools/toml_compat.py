from __future__ import annotations

import json
import math
import re
from typing import Any

import tomli


SIMPLE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def loads(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        return {}
    return tomli.loads(stripped)


def dumps(data: dict[str, Any]) -> str:
    if not data:
        return ""

    lines: list[str] = []
    _write_table(lines, [], data, array_item=False)
    return "\n".join(lines).rstrip() + "\n"


def _write_table(lines: list[str], path: list[str], table: dict[str, Any], array_item: bool) -> None:
    if path:
        if lines and lines[-1] != "":
            lines.append("")
        section_path = ".".join(_format_key_part(part) for part in path)
        lines.append(f"[[{section_path}]]" if array_item else f"[{section_path}]")

    scalars: list[tuple[str, Any]] = []
    subtables: list[tuple[str, dict[str, Any]]] = []
    array_tables: list[tuple[str, list[dict[str, Any]]]] = []

    for key, value in table.items():
        if isinstance(value, dict):
            subtables.append((key, value))
        elif isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            array_tables.append((key, value))
        else:
            scalars.append((key, value))

    for key, value in scalars:
        lines.append(f"{_format_key_part(key)} = {_format_value(value)}")

    for key, value in subtables:
        _write_table(lines, [*path, key], value, array_item=False)

    for key, values in array_tables:
        for value in values:
            _write_table(lines, [*path, key], value, array_item=True)


def _format_key_part(key: str) -> str:
    text = str(key)
    if SIMPLE_KEY_RE.match(text):
        return text
    return json.dumps(text, ensure_ascii=False)


def _format_value(value: Any) -> str:
    if value is None:
        return '""'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return '""'
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_format_value(item) for item in value) + "]"
    raise TypeError(f"Unsupported TOML value type: {type(value)!r}")

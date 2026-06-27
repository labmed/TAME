from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from typing import Any

from .models import TameDataset
from .toml_compat import dumps as dumps_toml


def append_log_entry(
    dataset: TameDataset,
    *,
    action: str,
    message: str = "",
    parent: str = "",
    output: str = "",
    parameters: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    actor: str = "tametools",
) -> TameDataset:
    meta = deepcopy(dataset.meta)
    entries = _normalized_log_entries(meta.get("LOG"))

    entry: dict[str, Any] = {
        "TIMESTAMP": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "OPERATION": str(action).upper(),
        "TOOL": str(actor),
    }
    if message:
        entry["NOTES"] = str(message)
    if parent:
        entry["PARENT"] = str(parent)
    if output:
        entry["OUTPUT"] = str(output)
    if parameters:
        entry["PARAMS"] = _toml_safe(parameters)
    if warnings:
        entry["WARNINGS"] = [str(warning) for warning in warnings]

    meta["LOG"] = [*entries, entry]

    raw_sections = dict(dataset.raw_sections)
    raw_sections["META"] = dumps_toml(meta)
    return dataset.replace(meta=meta, raw_sections=raw_sections)


def _normalized_log_entries(log: Any) -> list[dict[str, Any]]:
    if isinstance(log, list):
        return [dict(entry) for entry in log if isinstance(entry, dict)]
    if not isinstance(log, dict):
        return []
    entries = log.get("ENTRIES")
    if not isinstance(entries, list):
        return []
    normalized: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        normalized.append(_legacy_log_entry(entry))
    return normalized


def _legacy_log_entry(entry: dict[str, Any]) -> dict[str, Any]:
    params = entry.get("PARAMETERS")
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except json.JSONDecodeError:
            params = {"RAW": params}
    return {
        key: value
        for key, value in {
            "TIMESTAMP": entry.get("TIMESTAMP", entry.get("TIME", "")),
            "OPERATION": entry.get("OPERATION", entry.get("ACTION", "")),
            "TOOL": entry.get("TOOL", entry.get("ACTOR", "")),
            "PARENT": entry.get("PARENT", ""),
            "OUTPUT": entry.get("OUTPUT", ""),
            "PARAMS": params if params else None,
            "NOTES": entry.get("NOTES", entry.get("MESSAGE", "")),
            "WARNINGS": entry.get("WARNINGS", None),
        }.items()
        if value not in (None, "", [])
    }


def _toml_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _toml_safe(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_toml_safe(item) for item in value]
    if isinstance(value, list):
        return [_toml_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)

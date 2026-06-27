from __future__ import annotations

from copy import deepcopy
from typing import Any

from .config import ci_get
from .tags import merge_tags


FORMAT_SETTING_KEYS = {
    "ABSENT_TOKEN",
    "NULL_TOKEN",
    "EMPTY_TOKEN",
    "WS_TOKEN_PREFIX",
    "TOKEN_SUFFIX",
    "ESCAPE_PREFIX",
}


def format_settings(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Return serialization settings, supporting legacy META[SETTINGS]."""
    if not isinstance(meta, dict):
        return {}
    legacy = ci_get(meta, "SETTINGS", {})
    result = {
        key: value
        for key, value in (legacy.items() if isinstance(legacy, dict) else ())
        if str(key).upper() in FORMAT_SETTING_KEYS
    }
    modern = ci_get(meta, "FORMAT", {})
    if isinstance(modern, dict):
        result.update(modern)
    return result


def analysis_settings(meta: dict[str, Any] | None) -> dict[str, Any]:
    section = ci_get(meta, "SETTINGS", {}) if isinstance(meta, dict) else {}
    if not isinstance(section, dict):
        return {}
    return {key: value for key, value in section.items() if str(key).upper() not in FORMAT_SETTING_KEYS}


def column_metadata(meta: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    section = ci_get(meta, "COLUMN", {}) if isinstance(meta, dict) else {}
    if not isinstance(section, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for name, config in section.items():
        if isinstance(config, dict):
            result[str(name)] = deepcopy(config)
    return result


def column_metadata_for(meta: dict[str, Any] | None, column_name: str) -> dict[str, Any]:
    return column_metadata(meta).get(str(column_name), {})


def extract_column_tags(meta: dict[str, Any] | None) -> dict[str, tuple[str, ...]]:
    result = _legacy_tag_section(meta)
    for name, config in column_metadata(meta).items():
        tags = ci_get(config, "TAGS", [])
        if isinstance(tags, list):
            result[name] = merge_tags(result.get(name, ()), tags)
    return result


def set_column_tags(meta: dict[str, Any], column_name: str, tags: list[str] | tuple[str, ...]) -> dict[str, Any]:
    updated = deepcopy(meta)
    columns = ci_get(updated, "COLUMN", {})
    columns = dict(columns) if isinstance(columns, dict) else {}
    entry = columns.get(column_name, {})
    entry = dict(entry) if isinstance(entry, dict) else {}
    entry["TAGS"] = list(merge_tags(tags))
    columns[column_name] = entry
    updated["COLUMN"] = columns
    return updated


def remove_legacy_tag_section(meta: dict[str, Any]) -> dict[str, Any]:
    updated = deepcopy(meta)
    for key in list(updated):
        if str(key).upper() == "TAGS":
            updated.pop(key, None)
    return updated


def _legacy_tag_section(meta: dict[str, Any] | None) -> dict[str, tuple[str, ...]]:
    section = ci_get(meta, "TAGS", {}) if isinstance(meta, dict) else {}
    result: dict[str, tuple[str, ...]] = {}
    if not isinstance(section, dict):
        return result
    for name, tags in section.items():
        if isinstance(tags, list):
            result[str(name)] = merge_tags(tags)
    return result

from __future__ import annotations

from typing import Any, Mapping


def ci_get(mapping: Mapping[str, Any] | None, key: str, default: Any = None) -> Any:
    if not isinstance(mapping, Mapping):
        return default
    if key in mapping:
        return mapping[key]

    target = key.lower()
    for candidate, value in mapping.items():
        if str(candidate).lower() == target:
            return value
    return default


def ci_get_nested(mapping: Mapping[str, Any] | None, *path: str, default: Any = None) -> Any:
    current: Any = mapping
    for key in path:
        current = ci_get(current, key, default=None)
        if current is None:
            return default
    return current

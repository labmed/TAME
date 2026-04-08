from __future__ import annotations

import importlib
from typing import Iterable

from ..config import ci_get
from ..models import OperationOutput, TameDataset
from .base import PLUGIN_REGISTRY, PluginSpec, normalize_plugin_name


BUILTIN_PLUGIN_MODULES = [
    "tametools.plugins.reference_interval",
]

_BUILTINS_LOADED = False


def list_plugins(meta: dict | None = None) -> list[PluginSpec]:
    _ensure_plugins_loaded(meta)
    return [PLUGIN_REGISTRY[name] for name in sorted(PLUGIN_REGISTRY)]


def get_plugin(name: str, meta: dict | None = None) -> PluginSpec | None:
    _ensure_plugins_loaded(meta)
    return PLUGIN_REGISTRY.get(normalize_plugin_name(name))


def run_plugin(dataset: TameDataset, name: str, meta: dict | None, step_name: str, options: dict) -> OperationOutput | None:
    spec = get_plugin(name, meta)
    if spec is None:
        return None
    return spec.handler(dataset, meta or {}, step_name, options)


def _ensure_plugins_loaded(meta: dict | None = None) -> None:
    global _BUILTINS_LOADED
    if not _BUILTINS_LOADED:
        for module_name in BUILTIN_PLUGIN_MODULES:
            importlib.import_module(module_name)
        _BUILTINS_LOADED = True

    for module_name in _configured_plugin_modules(meta):
        importlib.import_module(module_name)


def _configured_plugin_modules(meta: dict | None) -> list[str]:
    if not isinstance(meta, dict):
        return []

    modules: list[str] = []
    plugins_section = ci_get(meta, "PLUGINS", {})
    if isinstance(plugins_section, dict):
        configured = ci_get(plugins_section, "MODULES", [])
        if isinstance(configured, list):
            modules.extend(str(item) for item in configured)

    use_section = ci_get(meta, "USE", {})
    if isinstance(use_section, dict):
        default_items = ci_get(use_section, "DEFAULT", [])
        if isinstance(default_items, list):
            modules.extend(str(item) for item in default_items if "." in str(item))

    seen: set[str] = set()
    result: list[str] = []
    for module_name in modules:
        if module_name not in seen:
            seen.add(module_name)
            result.append(module_name)
    return result

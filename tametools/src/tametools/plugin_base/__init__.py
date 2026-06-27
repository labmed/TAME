"""Plugin system APIs: registry, loader, roles, and execution helpers."""

from .manager import (
    configured_plugin_modules,
    get_plugin,
    list_plugins,
    plugin_directories,
    plugin_load_errors,
    run_plugin,
)

__all__ = [
    "configured_plugin_modules",
    "get_plugin",
    "list_plugins",
    "plugin_directories",
    "plugin_load_errors",
    "run_plugin",
]

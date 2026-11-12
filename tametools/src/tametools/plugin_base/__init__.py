"""Plugin system APIs: registry, loader, roles, and execution helpers.

Plugin authors import from ``tametools.plugin_api``; this package is the machinery behind it.
"""

from .base import PLUGIN_API_VERSION, PluginError, register_plugin
from .extensions import register_command, register_exporter, register_view
from .manager import (
    configured_plugin_modules,
    get_plugin,
    list_plugins,
    plugin_directories,
    plugin_load_errors,
    run_plugin,
)

__all__ = [
    "PLUGIN_API_VERSION",
    "PluginError",
    "configured_plugin_modules",
    "get_plugin",
    "list_plugins",
    "plugin_directories",
    "plugin_load_errors",
    "register_command",
    "register_exporter",
    "register_plugin",
    "register_view",
    "run_plugin",
    "selected_plugin_names",
    "with_plugin_selection",
    "plugin_selection_state",
]

from .selection import selected_plugin_names, with_plugin_selection, plugin_selection_state

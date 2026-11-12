from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable

from ..meta_sections import RESERVED_NAMES
from ..models import OperationOutput, TameDataset


PluginHandler = Callable[[TameDataset, dict, str, dict], OperationOutput]

# The plugin API is what tametools.plugin_api exports. Within one version those names keep their meaning;
# a change that would break a plugin written against them raises this number.
#   1  register_plugin/command/view/exporter, run_plugin, PluginWorkflow, forms, sync records
#   2  a plugin's own version and output columns, dependencies with a lowest version, plugin_module, require_result;
#      a plugin run by another plugin has what the person allowed for the outer one. Nothing of version 1 changed
#      its meaning, so a plugin written for 1 runs as it did.
PLUGIN_API_VERSION = 2


class PluginError(ValueError):
    """A plugin does not keep the plugin contract: what it declares, registers or returns."""


@dataclass(frozen=True)
class PluginSpec:
    name: str
    description: str
    handler: PluginHandler
    roles: tuple[Any, ...] = ()
    reporting: bool = False
    dependencies: tuple[str, ...] = ()
    form: Any = None      # questions for the person running it: a tametools.dialogs.Form, or (dataset, options) -> Form | None
    api: int = PLUGIN_API_VERSION      # the plugin API version the plugin was written for
    panel: str = ""       # a screen the host may have for this kind of work, e.g. "reference_interval" (hosts without it run the plugin plainly)
    superseded_by: str = ""             # the plugin to use instead; this one is kept so that older files still run
    version: str = ""     # the plugin's own version, e.g. "1.2.0"; recorded with every run and listed by `tametools plugins`
    output: tuple[str, ...] = ()        # columns (COLUMN.ID or name) every result of this plugin has; checked when it returns
    requires: tuple[tuple[str, str], ...] = ()   # (dependency, lowest version it works with), from dependencies written "NAME>=1.2"


PLUGIN_REGISTRY: dict[str, PluginSpec] = {}

_DEPENDENCY = re.compile(r"\s*([A-Za-z][\w-]*)\s*(?:>=\s*([\w.+-]+))?\s*")


def register_plugin(name: str, *, description: str = "", roles: tuple[Any, ...] = (), reporting: bool = False, dependencies: tuple[str, ...] = (),
                    form: Any = None, api: int = PLUGIN_API_VERSION, panel: str = "", superseded_by: str = "",
                    version: str = "", output: tuple[str, ...] | list[str] = ()):
    """Register the decorated handler as the plugin ``name``.

    ``dependencies`` names the plugins this one runs or imports (``"QC_ANALYSIS"``, or ``"QC_ANALYSIS>=1.2"`` when it
    needs at least that version). ``version`` is this plugin's own version. ``output`` lists the columns every result
    of this plugin has, so that a plugin reading the result can rely on them (``require_result``).
    """
    normalized = normalize_plugin_name(name)
    if not normalized:
        raise PluginError("register_plugin needs a plugin name")
    if normalized in RESERVED_NAMES:
        raise PluginError(f"Plugin name {normalized} is taken by tametools itself (a META section or a work step of that name). "
                          "A plugin's settings live in the META section named after it, so choose a name of your own")
    if isinstance(api, bool) or not isinstance(api, int) or api < 1:
        raise PluginError(f"Plugin {normalized}: api must be a plugin API version (a whole number from 1)")
    if api > PLUGIN_API_VERSION:
        raise PluginError(f"Plugin {normalized} needs plugin API {api}, but this tametools provides {PLUGIN_API_VERSION}. "
                          "Upgrade tametools or use the plugin release made for this version")
    if isinstance(dependencies, str) or isinstance(output, str):
        raise PluginError(f"Plugin {normalized}: dependencies and output are lists of names, e.g. (\"QC_ANALYSIS\",)")
    names: list[str] = []
    requires: list[tuple[str, str]] = []
    for item in dependencies:
        matched = _DEPENDENCY.fullmatch(str(item))
        if matched is None:
            raise PluginError(f"Plugin {normalized}: a dependency is a plugin name, or NAME>=VERSION: {item!r}")
        dependency = normalize_plugin_name(matched.group(1))
        names.append(dependency)
        if matched.group(2):
            requires.append((dependency, matched.group(2)))

    def decorator(handler: PluginHandler) -> PluginHandler:
        if not callable(handler):
            raise PluginError(f"Plugin {normalized}: @register_plugin goes on the handler function")
        PLUGIN_REGISTRY[normalized] = PluginSpec(name=normalized, description=description, handler=handler, roles=tuple(roles), reporting=reporting,
            dependencies=tuple(dict.fromkeys(names)), form=form, api=api,
            panel=str(panel or "").strip().lower(), superseded_by=normalize_plugin_name(superseded_by) if superseded_by else "",
            version=str(version or "").strip(), output=tuple(dict.fromkeys(str(column) for column in output)),
            requires=tuple(dict(requires).items()))
        return handler

    return decorator


def normalize_plugin_name(name: str) -> str:
    return str(name).strip().upper().replace("-", "_")

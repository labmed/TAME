from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..models import OperationOutput, TameDataset


PluginHandler = Callable[[TameDataset, dict, str, dict], OperationOutput]


@dataclass(frozen=True)
class PluginSpec:
    name: str
    description: str
    handler: PluginHandler


PLUGIN_REGISTRY: dict[str, PluginSpec] = {}


def register_plugin(name: str, *, description: str = ""):
    normalized = normalize_plugin_name(name)

    def decorator(handler: PluginHandler) -> PluginHandler:
        PLUGIN_REGISTRY[normalized] = PluginSpec(name=normalized, description=description, handler=handler)
        return handler

    return decorator


def normalize_plugin_name(name: str) -> str:
    return str(name).strip().upper().replace("-", "_")

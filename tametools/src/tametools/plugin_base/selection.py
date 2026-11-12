"""Per-file plugin choices. Discovery and permission to import code stay separate."""
from __future__ import annotations

from copy import deepcopy
from .base import normalize_plugin_name
from ..config import ci_get


def selected_plugin_names(meta: dict | None) -> list[str] | None:
    """None preserves legacy discovery; [] explicitly disables plugins."""
    section = ci_get(meta, "PLUGINS", {})
    if not isinstance(section, dict):
        raise ValueError("META[PLUGINS] must be a table")
    keys = [key for key in section if str(key).upper() == "ENABLED"]
    if not keys:
        return None
    if len(keys) != 1:
        raise ValueError("META[PLUGINS].ENABLED must not be repeated with different casing")
    names = section[keys[0]]
    if not isinstance(names, list) or any(not isinstance(n, str) or not n.strip() for n in names):
        raise ValueError('META[PLUGINS].ENABLED must be a list of plugin names, e.g. ["MY_PLUGIN"] (tametools plugins lists them)')
    return list(dict.fromkeys(normalize_plugin_name(name) for name in names))


def with_plugin_selection(meta: dict, names: list[str] | None) -> dict:
    """Set/remove only ENABLED, retaining module declarations and other settings."""
    updated = deepcopy(meta)
    section = deepcopy(ci_get(updated, "PLUGINS", {}))
    if not isinstance(section, dict):
        raise ValueError("META[PLUGINS] must be a table")
    section = {k: v for k, v in section.items() if str(k).upper() != "ENABLED"}
    if names is not None:
        section["ENABLED"] = selected_plugin_names({"PLUGINS": {"ENABLED": names}})
    updated = {k: v for k, v in updated.items() if str(k).upper() != "PLUGINS"}
    if section:
        updated["PLUGINS"] = section
    return updated


def inherit_plugin_selection(meta: dict, source_meta: dict) -> dict:
    selected = selected_plugin_names(source_meta)
    if selected is None:
        return meta
    updated = with_plugin_selection(meta, selected)
    modules = ci_get(ci_get(source_meta, 'PLUGINS', {}), 'MODULES')
    if modules is not None:
        section = {k: v for k, v in updated['PLUGINS'].items() if str(k).upper() != 'MODULES'}
        updated['PLUGINS'] = dict(section, MODULES=deepcopy(modules))
    return updated


def plugin_closure(names, registry):
    """Resolve dependencies and equivalent registered names without executing handlers."""
    effective, missing, errors, visited = set(), set(), [], set()
    def visit(name, trail):
        name = normalize_plugin_name(name)
        effective.add(name)
        if name in trail:
            message = "Plugin dependency cycle: " + " -> ".join([*trail, name])
            if message not in errors:
                errors.append(message)
            return
        if name in visited:
            return
        visited.add(name)
        spec = registry.get(name)
        if spec is None:
            missing.add(name)
            return
        # Aliases registered for the same handler must work through all entry points.
        effective.update(key for key, item in registry.items() if item.handler is spec.handler)
        for dependency in spec.dependencies:
            visit(dependency, [*trail, name])
    for name in names:
        visit(name, [])
    return effective, sorted(missing), errors


def plugin_selection_state(meta, plugins):
    selected = selected_plugin_names(meta)
    registry = {plugin.name: plugin for plugin in plugins}
    effective, missing, errors = plugin_closure(selected if selected is not None else registry, registry)
    warnings = [*errors]
    if missing:
        warnings.append("이 환경에서 찾을 수 없거나 아직 신뢰하지 않은 플러그인: " + ", ".join(missing))
    return {"mode": "all" if selected is None else "selected", "enabled": selected,
            "effective": sorted(effective), "missing": missing, "warnings": warnings}


def require_plugin_selected(name, meta, plugins):
    registry = {plugin.name: plugin for plugin in plugins}
    normalized = normalize_plugin_name(name)
    selected = selected_plugin_names(meta)
    if selected is not None:
        effective, _, _ = plugin_closure(selected, registry)
        if normalized not in effective:
            raise ValueError(f"이 TAME 파일에서 선택하지 않은 플러그인: {normalized}. "
                             "플러그인 설정 또는 META[PLUGINS].ENABLED에 추가해 주세요.")
    if normalized not in registry:
        return
    _, missing, errors = plugin_closure([normalized], registry)
    if missing or errors:
        detail = "; ".join([*errors, *( ["missing or untrusted: " + ", ".join(missing)] if missing else [])])
        raise ValueError(f"Plugin dependencies unavailable for {normalized}: {detail}")

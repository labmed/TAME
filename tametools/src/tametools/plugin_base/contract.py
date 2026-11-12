"""What plugins promise each other: a version, the columns of a result, and whose result a dataset is.

A plugin that reads another plugin's result used to check ``META[PLUGIN].NAME`` and then assume column names.
Here the producer declares its columns (``register_plugin(output=...)``), the consumer asks for the ones it reads
(``require_result``), and a mismatch is reported in words instead of as a pandas error deep inside a calculation.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Iterable

from ..config import ci_get
from .base import PluginError, PluginSpec, normalize_plugin_name

RESULT_SECTION = "ANALYSIS_RESULT"


def version_key(text: Any) -> tuple:
    """Order versions the way people read them: 1.10 is later than 1.9, and 1.2 equals 1.2.0."""
    parts = []
    for piece in re.split(r"[.\-+_]", str(text or "").strip().lstrip("vV")):
        if piece:
            parts.append((0, int(piece), "") if piece.isdigit() else (1, 0, piece.lower()))
    while parts and parts[-1] == (0, 0, ""):
        parts.pop()
    return tuple(parts)


def unmet_requirements(spec: PluginSpec, registry: dict[str, PluginSpec]) -> list[tuple[str, str, str]]:
    """Dependencies of ``spec`` that are there but older than it asked for: ``(name, wanted, found)``.

    A dependency that is missing altogether is reported with the dependency closure, not here.
    """
    unmet = []
    for name, wanted in spec.requires:
        found = registry.get(name)
        if found is not None and version_key(found.version) < version_key(wanted):
            unmet.append((name, wanted, found.version))
    return unmet


def column_names(dataset: Any = None, table: Any = None) -> dict[str, str]:
    """Every name a column of this result answers to (its name and its ``COLUMN.ID``) -> the column's name."""
    names: dict[str, str] = {}
    if dataset is not None:
        declared = ci_get(getattr(dataset, "meta", None) or {}, "COLUMN", {})
        for column in getattr(dataset, "columns", []) or []:
            names.setdefault(str(column.name), str(column.name))
            identifier = ci_get(ci_get(declared, column.name, {}) if isinstance(declared, dict) else {}, "ID", None)
            if isinstance(identifier, str) and identifier.strip():
                names.setdefault(identifier.strip(), str(column.name))
        if not names and getattr(dataset, "df", None) is not None:
            names.update({str(name): str(name) for name in dataset.df.columns})
    elif table is not None:
        names.update({str(name): str(name) for name in table.columns})
    return names


def check_declared_output(spec: PluginSpec, output: Any) -> None:
    """A plugin that declared its output columns must return them; said here, where the plugin is, not in whoever reads the result."""
    if not spec.output or (output.dataset is None and output.table is None):
        return
    available = column_names(output.dataset, output.table)
    missing = [name for name in spec.output if name not in available]
    if missing:
        raise PluginError(f"Plugin {spec.name} declares the output columns {', '.join(spec.output)}, but its result has no "
                          f"{', '.join(missing)} (it has: {', '.join(dict.fromkeys(available.values())) or 'no columns'})")


def result_plugins(dataset: Any) -> list[str]:
    """The plugins a dataset is the result of: the one that returned it, then the ones whose result it carried on."""
    meta = getattr(dataset, "meta", None) or {}
    declared = ci_get(meta, RESULT_SECTION, {})
    names: list[str] = []
    if isinstance(declared, dict):
        via = ci_get(declared, "VIA", [])
        for value in [ci_get(declared, "PLUGIN", None), *(via if isinstance(via, list) else [])]:
            if isinstance(value, str) and value.strip():
                names.append(normalize_plugin_name(value))
    legacy = ci_get(meta, "PLUGIN", {})       # written by plugins older than ANALYSIS_RESULT
    if isinstance(legacy, dict) and isinstance(ci_get(legacy, "NAME", None), str) and ci_get(legacy, "NAME").strip():
        names.append(normalize_plugin_name(ci_get(legacy, "NAME")))
    return list(dict.fromkeys(names))


def require_result(dataset: Any, plugin: str, *, columns: Iterable[str] = (), same: Iterable[str] = ()) -> dict[str, str]:
    """Check that ``dataset`` is a result of ``plugin`` and has the columns about to be read.

    Returns ``{name asked for: the column's name in the data}`` (a column is found by its name or its ``COLUMN.ID``).
    ``same`` are other names of that plugin (its aliases).
    """
    if isinstance(columns, str):
        columns = (columns,)
    wanted = normalize_plugin_name(plugin)
    accepted = {wanted, *(normalize_plugin_name(name) for name in same)}
    made_by = result_plugins(dataset)
    available = column_names(dataset)
    shown = ", ".join(dict.fromkeys(available.values())) or "no columns"
    if not accepted.intersection(made_by):
        source = "a result of " + ", ".join(made_by) if made_by else "not marked as any plugin's result"
        raise PluginError(f"This step needs the result of {wanted}, but the data it received is {source} (columns: {shown}). "
                          f"Run {wanted} first and pass its result on")
    missing = [name for name in columns if name not in available]
    if missing:
        raise PluginError(f"The result of {wanted} has no column {', '.join(missing)} (columns: {shown}). "
                          f"The two plugins do not fit: check their versions")
    return {name: available[name] for name in columns}


def claim_result(dataset: Any, spec: PluginSpec, same: Iterable[str], is_plugin: Callable[[str], bool],
                 has_view: Callable[[str], bool]) -> Any:
    """Make a result say which plugin returned it. The dataset comes back unchanged when it already does.

    Only results that carry ``META[ANALYSIS_RESULT]`` are touched. ``PLUGIN`` becomes the plugin that returned the
    dataset. What stood there before is kept: another plugin's name moves to ``VIA`` (this result was that plugin's,
    carried on), anything else was the name of a step (``table_output(step_name, ...)``) and moves to ``STEP``.
    """
    meta = getattr(dataset, "meta", None)
    if not isinstance(meta, dict):
        return dataset
    section_key = next((key for key in meta if str(key).upper() == RESULT_SECTION), None)
    declared = meta.get(section_key) if section_key is not None else None
    if not isinstance(declared, dict):
        return dataset
    written = ci_get(declared, "PLUGIN", None)
    current = normalize_plugin_name(written) if isinstance(written, str) and written.strip() else ""
    if current in {normalize_plugin_name(name) for name in same}:
        return dataset
    kept = {key: value for key, value in declared.items() if str(key).upper() not in {"PLUGIN", "VIA"}}
    updated = {"PLUGIN": spec.name, **kept}
    earlier = ci_get(declared, "VIA", [])
    via = [name for name in (earlier if isinstance(earlier, list) else []) if isinstance(name, str)]
    if current and is_plugin(current):
        via = [current, *via]
        if not ci_get(kept, "VIEW", None) and has_view(current) and not has_view(spec.name):
            updated["VIEW"] = current          # the screen it had as that plugin's result still opens
    elif current and ci_get(kept, "STEP", None) is None:
        updated["STEP"] = written
    if via:
        updated["VIA"] = list(dict.fromkeys(via))
    changed = {key: (updated if key == section_key else value) for key, value in meta.items()}
    raw = {key: value for key, value in (getattr(dataset, "raw_sections", None) or {}).items() if key != "META"}
    return dataset.replace(meta=changed, raw_sections=raw)

"""Everything a plugin imports from tametools.

    from tametools.plugin_api import OperationOutput, register_plugin

    @register_plugin("ROW_COUNT", description="Number of rows.")
    def row_count(dataset, meta, step_name, options):
        return OperationOutput(name=step_name, message=f"{len(dataset.df)} rows")

The names below are the plugin API. Within one ``PLUGIN_API_VERSION`` they keep their meaning, so a
plugin that imports only from here keeps working when tametools is rearranged inside. Other modules
(``tametools.sync``, ``tametools.cli``, ...) are internal and may change without notice. A plugin that
needs a newer API says so with ``register_plugin(..., api=2)`` and is refused with a clear message by
a tametools that does not have it.

Registering
    register_plugin, register_report, register_command, register_view, register_exporter,
    PLUGIN_API_VERSION, PluginError
Data
    TameDataset, ColumnSpec, merged_column_specs, read_tame, cell_state, STATE_VALUE, resolve_id
Results
    OperationOutput, table_output, chart_spec, with_visualizations, write_docx_report,
    record_effective_parameters, export_frame
Options and columns
    option, ci_get, PluginRole, RESULT_ROLE, result_binding, result_columns
Asking the person who runs it
    form, field, ask, wanted, Form
Reusing other plugins
    run_plugin, PluginWorkflow     run another plugin (it may run what the person allowed for yours; leave allow_external out)
    plugin_module                  use another plugin's functions: plugin_module("A_STATS").summarize(...)
    require_result                 check that the data you were given is another plugin's result, with the columns you read
    Declare what you use: register_plugin(dependencies=("A_STATS>=1.2",)), and what you return: version=, output=
Shared laboratory calculations
    evaluate_qc
Records kept in a sync project (what people did, kept beside results that are recomputed)
    add_record, recordable, rerender_report, sync_records, sync_state, sync_config, verify_records,
    read_result, data_fingerprint, SyncError
"""
from __future__ import annotations

from importlib import import_module
from typing import Any

from .plugin_base.base import PLUGIN_API_VERSION, PluginError, register_plugin
from .plugin_base.extensions import register_command, register_exporter, register_view
from .plugin_base.options import option

# Imported on first use, so that loading a plugin costs only what the plugin uses.
_EXPORTS = {
    "TameDataset": ".models", "ColumnSpec": ".models", "OperationOutput": ".models", "merged_column_specs": ".models",
    "read_tame": ".io",
    "cell_state": ".cellstate", "STATE_VALUE": ".cellstate",
    "resolve_id": ".analysis_contract",
    "ci_get": ".config",
    "table_output": ".plugin_workflow", "PluginWorkflow": ".plugin_workflow",
    "chart_spec": ".reporting", "with_visualizations": ".reporting", "write_docx_report": ".reporting",
    "record_effective_parameters": ".provenance",
    "export_frame": ".exporters",
    "PluginRole": ".plugin_base.roles", "RESULT_ROLE": ".plugin_base.roles", "result_binding": ".plugin_base.roles",
    "result_columns": ".plugin_base.roles",
    "form": ".dialogs", "field": ".dialogs", "ask": ".dialogs", "wanted": ".dialogs", "Form": ".dialogs",
    "run_plugin": ".plugin_base.manager", "plugin_module": ".plugin_base.manager", "require_result": ".plugin_base.manager",
    "evaluate_qc": ".quality_control",
    "register_report": ".sync", "add_record": ".sync", "recordable": ".sync", "rerender_report": ".sync",
    "sync_records": ".sync", "sync_state": ".sync", "sync_config": ".sync", "verify_records": ".sync",
    "read_result": ".sync", "SyncError": ".sync",
    "data_fingerprint": ".provenance_audit",
}

__all__ = sorted(["PLUGIN_API_VERSION", "PluginError", "register_plugin", "register_command", "register_view", "register_exporter",
                  "option", *_EXPORTS])


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"tametools.plugin_api has no {name!r}. The plugin API (version {PLUGIN_API_VERSION}) is: " + ", ".join(__all__))
    value = getattr(import_module(module_name, __package__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))

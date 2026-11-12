"""Reusable, explicitly connected analysis steps for plugin authors."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import re
from typing import Any

import pandas as pd

from .config import ci_get
from .cellstate import cell_state, STATE_EMPTY, STATE_WS
from .models import ColumnSpec, OperationOutput, TameDataset
from .plugin_base import run_plugin
from .plugin_base.base import normalize_plugin_name
from .plugin_base.selection import inherit_plugin_selection
from .provenance import append_log_entry, operation_scope
from .reporting import with_visualizations


def table_output(name, frame, *, result_columns=(), charts=(), warnings=(), message="", metadata=None):
    """Turn a plugin's small calculation into a typed, chainable result.

    ``name`` is what the output is called, normally the ``step_name`` the handler was given. The result is marked
    ``META[ANALYSIS_RESULT].PLUGIN = name``; when the handler returns, tametools puts the plugin's own name there
    and keeps a different step name as ``STEP``.
    """
    frame = frame.copy()
    if not frame.columns.is_unique or any(not isinstance(c, str) for c in frame.columns):
        raise ValueError("Output columns must have distinct string names")
    if set(result_columns) - set(frame.columns):
        raise ValueError("Unknown output result column")
    columns = []
    for key in frame.columns:
        numeric = pd.api.types.is_numeric_dtype(frame[key]) and not pd.api.types.is_bool_dtype(frame[key])
        tags = ["NUM" if numeric else "STR", "NULLABLE"]
        states = frame[key].map(cell_state)
        if states.eq(STATE_EMPTY).any():
            tags.append("EMPTY_OK")
        if states.eq(STATE_WS).any():
            tags.append("WS_OK")
        if key in result_columns:
            if not numeric:
                raise ValueError("Output RESULT columns must be numeric: " + key)
            tags.append("RESULT")
        columns.append(ColumnSpec(key, key, tags))
    meta = deepcopy(metadata or {})
    meta.setdefault("COLUMN", {}).update({key: {"ID": key, **ci_get(meta.get("COLUMN", {}), key, {})} for key in frame.columns})
    meta["ANALYSIS_RESULT"] = {"PLUGIN": name}
    dataset = with_visualizations(TameDataset(frame, columns, meta), charts)
    return OperationOutput(name=name, dataset=dataset, table=frame, charts=list(charts),
                           warnings=list(warnings), message=message)


def passed_on(output: OperationOutput, plugin: str) -> OperationOutput:
    """What the next step of a chain receives from a plugin that returned only a table: that table, as a dataset.

    A chain used to hand the next step the unchanged input in that case, so the plugin's result was dropped without
    a word. The conversion is said in the output's warnings; a table that cannot be a dataset is said there too.
    """
    if output.dataset is not None or output.table is None or output.status != "SUCCEEDED":
        return output
    try:
        converted = table_output(plugin, output.table).dataset
    except ValueError as exc:
        note = f"{plugin} returned only a table that cannot be passed on ({exc}); the next step received the data unchanged."
    else:
        if str(output.name or "") and str(output.name).strip().upper().replace("-", "_") != plugin:
            converted.meta["ANALYSIS_RESULT"]["STEP"] = str(output.name)
        output.dataset = converted
        note = f"{plugin} returned only a table; it was passed on as a dataset with inferred column tags."
    output.warnings = [*(output.warnings or []), note]
    return output


class PluginWorkflow:
    """Keep source data and named branch results separate, with shared audit records.

    Sources refer to INPUT or an earlier step name. Plugins receive copies, so
    one branch cannot mutate another. Failure stops the workflow and records
    the failed step; a report is created only after every step succeeds.
    """

    def __init__(self, dataset: TameDataset):
        self.input = deepcopy(dataset)
        self.outputs: dict[str, OperationOutput] = {}
        self.steps: list[dict[str, Any]] = []

    def _source(self, name):
        if name == "INPUT":
            return self.input
        if name not in self.outputs or self.outputs[name].dataset is None:
            raise ValueError("Unknown workflow source: " + str(name))
        return self.outputs[name].dataset

    def _run(self, name, source, kind, operation, options, execute):
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", name) or name == "INPUT" or name in self.outputs:
            raise ValueError("Workflow step names must be distinct identifiers: " + str(name))
        parent = self._source(source)
        config = dict(name=name, source=source, kind=kind, operation=operation, options=deepcopy(options))
        with operation_scope("WORKFLOW:" + name, [parent], parameters=config):
            result = execute(deepcopy(parent))
            if result is None:
                from .plugin_base.manager import unknown_plugin_hints
                hints = unknown_plugin_hints(operation, parent.meta)
                raise ValueError("Unknown workflow plugin: " + operation + "".join(f" ({hint})" for hint in hints[:2]))
            if result.status != "SUCCEEDED":
                raise ValueError("Workflow step did not succeed: " + name)
            if any(issue.severity.lower() == "error" for issue in result.issues or []):
                raise ValueError("Workflow input validation failed: " + name)
            if result.dataset is not None:
                target = result.dataset
            elif result.table is not None and kind == "PLUGIN":
                # The table is this plugin's result: it says so by the plugin's name, with the step's name beside it.
                target = table_output(normalize_plugin_name(operation), result.table).dataset
                target.meta["ANALYSIS_RESULT"]["STEP"] = name
            elif result.table is not None:
                target = table_output(result.name, result.table).dataset
            elif kind != "PLUGIN":
                target = deepcopy(parent)
            else:
                raise ValueError("Workflow plugins must return a dataset or table: " + name)
            target = target.replace(meta=inherit_plugin_selection(target.meta, parent.meta))
            result.dataset = append_log_entry(target, action="WORKFLOW:" + name, input_dataset=parent,
                parameters=config, warnings=list(result.warnings or []), message=result.message or "",
                operation_output=result)
        self.outputs[name] = result
        self.steps.append(config)
        return result

    def validate(self, name="validate", *, source="INPUT"):
        from .analysis import validate_dataset
        def execute(dataset):
            checked = validate_dataset(dataset)
            return OperationOutput(name=name, dataset=checked.cleaned_dataset, issues=checked.issues,
                tables={"validation": pd.DataFrame([asdict(issue) for issue in checked.issues])},
                message=f"Input validation: {len(checked.issues)} issue(s)")
        return self._run(name, source, "CORE", "VALIDATE", {}, execute)

    def action(self, name, action, *, source="INPUT"):
        from .actions import execute_action
        options = ci_get(ci_get(self._source(source).meta, "ACTIONS", {}), action, {})
        return self._run(name, source, "ACTION", action, options, lambda ds: execute_action(ds, action))

    def plugin(self, name, plugin, *, source="INPUT", options=None, allow_external=None):
        """Run ``plugin`` on the result of the step ``source``.

        Its settings are, later ones winning: the section named after it in the file the workflow started from,
        that section in the source step's result if it carries one, then ``options``. So ``[MY_PLUGIN]`` written
        in the file applies wherever the plugin runs in the workflow, as it does in a WORKS chain.
        ``allow_external`` is normally left out: the step may run what the person allowed for the workflow's plugin.
        """
        parent = self._source(source)
        written = ci_get(self.input.meta, plugin, {})
        declared = ci_get(parent.meta, plugin, {})
        if not isinstance(declared, dict) or not isinstance(written, dict) or not isinstance(options or {}, dict):
            raise ValueError("Plugin settings must be a table")
        effective = deepcopy(written)
        for key, value in [*(declared.items() if declared is not written else []), *(options or {}).items()]:
            effective = {k:v for k,v in effective.items() if str(k).upper() != str(key).upper()}
            effective[key] = deepcopy(value)
        return self._run(name, source, "PLUGIN", plugin, effective,
            lambda ds: run_plugin(ds, plugin, ds.meta, name, effective, allow_external=allow_external))

    def finish(self, name, *, primary, title="", summary="", output_dir=None, max_table_rows=40, report_options=None):
        if primary not in self.outputs:
            raise ValueError("Unknown primary workflow result: " + str(primary))
        from .reporting import bind_report_outputs
        tables, charts = bind_report_outputs(list(self.outputs.values()), names=list(self.outputs))
        selected = self.outputs[primary]
        from .plugin_base.manager import claim_running
        result = claim_running(deepcopy(selected.dataset))     # the workflow's plugin returns this; said before it is recorded
        result.meta["WORKFLOW_RESULT"] = {"NAME": name, "STEPS": deepcopy(self.steps), "PRIMARY": primary}
        result = with_visualizations(result, charts)
        warnings = list(dict.fromkeys(w for output in self.outputs.values() for w in output.warnings or []))
        output = OperationOutput(name=name, dataset=result, table=selected.table, tables=tables,
                                 charts=charts, warnings=warnings, message=summary)
        output.dataset = append_log_entry(result, action="WORKFLOW:COMPOSE", inputs=[self.input, *[v.dataset for v in self.outputs.values()]],
            parameters={"STEPS": self.steps, "PRIMARY": primary}, operation_output=output, message=summary)
        if output_dir:
            from .report_bundle import write_report_bundle
            output.files = [str(path) for path in write_report_bundle(output_dir, title=title or name,
                output=output, source=self.input, stages=self.outputs, max_table_rows=max_table_rows, **(report_options or {}))]
        return output

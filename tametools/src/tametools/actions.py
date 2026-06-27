from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any

import numpy as np
import pandas as pd

from .cellstate import NULL
from .config import ci_get
from .models import ColumnSpec, OperationOutput, PipelineResult, TameDataset
from .pandas_compat import concat_dataframes
from .audit import dataset_content_hash
from .provenance import append_log_entry
from .tag_catalog import any_tag_inherits
from .tags import build_header, merge_tags


class ActionError(ValueError):
    pass


@dataclass(frozen=True)
class ActionDefinition:
    name: str
    label: str
    type: str
    description: str = ""
    mutates: bool = True


SUPPORTED_ACTION_TYPES = {
    "TAG_COLUMNS",
    "ADD_COLUMN",
    "DERIVE",
    "DATE_DERIVE",
    "IMPUTE",
    "OUTLIER_FILTER",
    "RECODE",
    "JOIN",
    "ROW.INCLUDE",
    "ROW.EXCLUDE",
    "COLUMN.INCLUDE",
    "COLUMN.EXCLUDE",
    "COLUMN.SPLIT",
    "COLUMN.CONCAT",
    "DEDUP",
    "SORT",
    "ROUND",
    "BIN",
    "UNIT.CONVERT",
    "PIVOT_LONGER",
    "PIVOT_WIDER",
}


def available_actions(meta: dict | None) -> list[ActionDefinition]:
    section = ci_get(meta, "ACTIONS", {})
    if not isinstance(section, dict):
        return []

    actions: list[ActionDefinition] = []
    for name, config in section.items():
        if not isinstance(config, dict):
            continue
        action_type = _normalize_action_type(ci_get(config, "TYPE", ""))
        if not action_type:
            continue
        if action_type not in SUPPORTED_ACTION_TYPES:
            continue
        actions.append(
            ActionDefinition(
                name=str(name),
                label=str(ci_get(config, "LABEL", name)),
                type=action_type,
                description=str(ci_get(config, "DESCRIPTION", "")),
                mutates=True,
            )
        )
    return actions


def available_action_pipelines(meta: dict | None) -> list[str]:
    section = ci_get(meta, "ACTION_PIPELINES", {})
    if not isinstance(section, dict):
        return []
    names: list[str] = []
    for name, config in section.items():
        if isinstance(config, list):
            names.append(str(name))
        elif isinstance(config, dict) and isinstance(ci_get(config, "ACTIONS", []), list):
            names.append(str(name))
    return names


def action_payloads(meta: dict | None) -> list[dict[str, Any]]:
    return [
        {
            "name": action.name,
            "label": action.label,
            "type": action.type,
            "description": action.description,
            "mutates": action.mutates,
        }
        for action in available_actions(meta)
    ]


def action_pipeline_payloads(meta: dict | None) -> list[dict[str, Any]]:
    return [{"name": name, "actions": _resolve_action_pipeline_names(meta, name)} for name in available_action_pipelines(meta)]


def execute_action(dataset: TameDataset, action_name: str) -> OperationOutput:
    name, config = _resolve_action_config(dataset.meta, action_name)
    action_type = _normalize_action_type(ci_get(config, "TYPE", ""))
    if action_type == "TAG_COLUMNS":
        return _execute_tag_columns(dataset, name, config)
    if action_type == "ADD_COLUMN":
        return _execute_add_column(dataset, name, config)
    if action_type == "DERIVE":
        return _execute_derive(dataset, name, config)
    if action_type == "DATE_DERIVE":
        return _execute_date_derive(dataset, name, config)
    if action_type == "IMPUTE":
        return _execute_impute(dataset, name, config)
    if action_type == "OUTLIER_FILTER":
        return _execute_outlier_filter(dataset, name, config)
    if action_type == "RECODE":
        return _execute_recode(dataset, name, config)
    if action_type == "JOIN":
        return _execute_join(dataset, name, config)
    if action_type == "DEDUP":
        return _execute_dedup(dataset, name, config)
    if action_type == "SORT":
        return _execute_sort(dataset, name, config)
    if action_type == "ROUND":
        return _execute_round(dataset, name, config)
    if action_type == "BIN":
        return _execute_bin(dataset, name, config)
    if action_type == "UNIT.CONVERT":
        return _execute_unit_convert(dataset, name, config)
    if action_type == "ROW.INCLUDE":
        return _execute_row_filter(dataset, name, config, include=True)
    if action_type == "ROW.EXCLUDE":
        return _execute_row_filter(dataset, name, config, include=False)
    if action_type == "COLUMN.INCLUDE":
        return _execute_column_filter(dataset, name, config, include=True)
    if action_type == "COLUMN.EXCLUDE":
        return _execute_column_filter(dataset, name, config, include=False)
    if action_type == "COLUMN.SPLIT":
        return _execute_column_split(dataset, name, config)
    if action_type == "COLUMN.CONCAT":
        return _execute_column_concat(dataset, name, config)
    if action_type == "PIVOT_LONGER":
        return _execute_pivot_longer(dataset, name, config)
    if action_type == "PIVOT_WIDER":
        return _execute_pivot_wider(dataset, name, config)
    raise ActionError(f"Unsupported ACTION type for {name}: {action_type or '(empty)'}")


def execute_action_pipeline(dataset: TameDataset, pipeline_name: str = "DEFAULT", *, log_level: str = "detailed") -> PipelineResult:
    action_names = _resolve_action_pipeline_names(dataset.meta, pipeline_name)
    if not action_names:
        raise ActionError(f"Undefined or empty ACTION pipeline: {pipeline_name}")
    current = dataset
    outputs: list[OperationOutput] = []
    detailed_log = str(log_level or "detailed").strip().lower() == "detailed"
    for action_name in action_names:
        input_hash = dataset_content_hash(current)
        output = execute_action(current, action_name)
        if detailed_log and output.dataset is not None:
            name, config = _resolve_action_config(current.meta, action_name)
            output.dataset = _logged_action_dataset(
                output.dataset,
                action_name=name,
                config=config,
                input_hash=input_hash,
                output=output,
            )
        outputs.append(output)
        if output.dataset is not None:
            current = output.dataset
    return PipelineResult(work_name=str(pipeline_name), final_dataset=current, outputs=outputs)


def _logged_action_dataset(
    dataset: TameDataset,
    *,
    action_name: str,
    config: dict[str, Any],
    input_hash: str,
    output: OperationOutput,
) -> TameDataset:
    output_hash = dataset_content_hash(dataset)
    parameters = {
        "action": action_name,
        "type": _normalize_action_type(ci_get(config, "TYPE", "")),
        "config": config,
        "input_hash": input_hash,
        "output_hash": output_hash,
        "issues": len(output.issues or []),
        "warnings": len(output.warnings or []),
        "tables": sorted((output.tables or {}).keys()),
    }
    return append_log_entry(
        dataset,
        action=f"ACTION:{action_name}",
        message=output.message or "",
        parameters=parameters,
        warnings=list(output.warnings or []),
    )


def _resolve_action_config(meta: dict | None, action_name: str) -> tuple[str, dict[str, Any]]:
    section = ci_get(meta, "ACTIONS", {})
    if not isinstance(section, dict):
        raise ActionError("No META[ACTIONS] section is defined.")

    for name, config in section.items():
        if str(name).lower() == str(action_name).lower():
            if not isinstance(config, dict):
                raise ActionError(f"ACTION {name} must be a table.")
            return str(name), config
    available = ", ".join(str(name) for name in section) or "(none)"
    raise ActionError(f"Undefined ACTION: {action_name}. Available actions: {available}")


def _resolve_action_pipeline_names(meta: dict | None, pipeline_name: str) -> list[str]:
    section = ci_get(meta, "ACTION_PIPELINES", {})
    pipeline = ci_get(section, pipeline_name, None) if isinstance(section, dict) else None
    if isinstance(pipeline, dict):
        pipeline = ci_get(pipeline, "ACTIONS", [])
    if not isinstance(pipeline, list):
        return []
    return [str(name).strip() for name in pipeline if str(name).strip()]


def _normalize_action_type(value: Any) -> str:
    text = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "ROW_INCLUDE": "ROW.INCLUDE",
        "ROW.EXTRACT": "ROW.INCLUDE",
        "ROW_EXCLUDE": "ROW.EXCLUDE",
        "COLUMN_INCLUDE": "COLUMN.INCLUDE",
        "COLUMN_EXCLUDE": "COLUMN.EXCLUDE",
        "COLUMN_SPLIT": "COLUMN.SPLIT",
        "SPLIT": "COLUMN.SPLIT",
        "COLUMN_CONCAT": "COLUMN.CONCAT",
        "CONCAT": "COLUMN.CONCAT",
        "UNIT_CONVERT": "UNIT.CONVERT",
        "UNIT.CONVERSION": "UNIT.CONVERT",
        "RESULT_NORMALIZE": "UNIT.CONVERT",
    }
    return aliases.get(text, text)


def _execute_tag_columns(dataset: TameDataset, action_name: str, config: dict[str, Any]) -> OperationOutput:
    specs = ci_get(config, "COLUMNS", [])
    if not isinstance(specs, list):
        raise ActionError(f"ACTION {action_name} TAG_COLUMNS requires COLUMNS as a list.")

    columns = list(dataset.columns)
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []

    for spec in specs:
        if not isinstance(spec, dict):
            warnings.append(f"Ignored non-table column spec in ACTION {action_name}.")
            continue
        column_name = str(ci_get(spec, "COLUMN", ci_get(spec, "NAME", ""))).strip()
        tags = ci_get(spec, "TAGS", [])
        if not column_name:
            warnings.append(f"Ignored column spec without COLUMN/NAME in ACTION {action_name}.")
            continue
        if not isinstance(tags, list) or not tags:
            warnings.append(f"Ignored column spec without TAGS for column {column_name}.")
            continue

        index = _find_column_index(columns, column_name)
        if index is None:
            warnings.append(f"Column not found for ACTION {action_name}: {column_name}")
            continue

        before = columns[index]
        merged_tags = merge_tags(before.tags, tags)
        updated = ColumnSpec(
            original_header=before.original_header,
            name=before.name,
            tags=merged_tags,
        )
        columns[index] = updated
        rows.append(
            {
                "column": before.name,
                "before_tags": "::".join(before.tags),
                "after_tags": "::".join(merged_tags),
                "added_tags": "::".join(tag for tag in merged_tags if tag not in before.tags),
            }
        )

    if not rows and warnings:
        raise ActionError("; ".join(warnings))

    table = pd.DataFrame(rows, columns=["column", "before_tags", "after_tags", "added_tags"])
    return OperationOutput(
        name=action_name,
        dataset=dataset.replace(columns=columns),
        table=table,
        warnings=warnings,
        message=f"tagged_columns={len(rows)}",
    )


def _execute_add_column(dataset: TameDataset, action_name: str, config: dict[str, Any]) -> OperationOutput:
    name = str(ci_get(config, "NAME", ci_get(config, "COLUMN", ""))).strip()
    if not name:
        raise ActionError(f"ACTION {action_name} ADD_COLUMN requires NAME.")
    if name in dataset.df.columns:
        raise ActionError(f"ACTION {action_name} ADD_COLUMN target already exists: {name}")

    frame = dataset.df.copy()
    if ci_get(config, "FROM", None) is not None:
        source = _resolve_column(dataset, str(ci_get(config, "FROM", ""))).name
        frame[name] = frame[source]
    elif ci_get(config, "TEMPLATE", None) is not None:
        template = str(ci_get(config, "TEMPLATE", ""))
        frame[name] = _render_template_column(dataset, template)
    else:
        frame[name] = ci_get(config, "VALUE", "")

    tags = _new_column_tags(config, name)
    columns = [
        *dataset.columns,
        ColumnSpec(original_header=build_header(name, tags), name=name, tags=tags),
    ]
    return OperationOutput(
        name=action_name,
        dataset=dataset.replace(df=frame, columns=columns),
        table=pd.DataFrame([{"column": name, "tags": "::".join(tags), "rows": len(frame)}]),
        message=f"add_column column={name} rows={len(frame)}",
    )


def _render_template_column(dataset: TameDataset, template: str) -> pd.Series:
    refs = list(dict.fromkeys(_DERIVE_REF_RE.findall(template)))
    tag_ref_columns: dict[str, str] = {}
    safe_template = template
    for index, ref in enumerate(refs):
        text = ref.strip()
        if not text.lower().startswith("tag:"):
            continue
        column = _resolve_column(dataset, text)
        placeholder = f"__tag_ref_{index}__"
        tag_ref_columns[placeholder] = column.name
        safe_template = safe_template.replace("{" + ref + "}", "{" + placeholder + "}")

    def render(row: pd.Series) -> str:
        values = _SafeFormatDict(row.to_dict())
        for placeholder, column_name in tag_ref_columns.items():
            values[placeholder] = row[column_name]
        return safe_template.format_map(values)

    return dataset.df.apply(render, axis=1)


_DERIVE_REF_RE = re.compile(r"\{([^{}]+)\}")
_DERIVE_ALLOWED_FUNCS = {
    "abs": abs,
    "min": lambda *a: concat_dataframes([pd.Series(value) for value in a], axis=1).min(axis=1) if len(a) > 1 else a[0],
    "max": lambda *a: concat_dataframes([pd.Series(value) for value in a], axis=1).max(axis=1) if len(a) > 1 else a[0],
    "round": lambda s, n=0: pd.Series(s).round(int(n)),
    "log": np.log,
    "log10": np.log10,
    "log2": np.log2,
    "sqrt": np.sqrt,
    "exp": np.exp,
}
_DERIVE_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Num, ast.Constant, ast.Name, ast.Load,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd, ast.Call,
)


def _assert_safe_expression(node: ast.AST, action_name: str) -> None:
    for child in ast.walk(node):
        if not isinstance(child, _DERIVE_ALLOWED_NODES):
            raise ActionError(
                f"ACTION {action_name} DERIVE expression contains an unsupported element: "
                f"{type(child).__name__}. Only arithmetic and {sorted(_DERIVE_ALLOWED_FUNCS)} are allowed."
            )
        if isinstance(child, ast.Call) and not (isinstance(child.func, ast.Name) and child.func.id in _DERIVE_ALLOWED_FUNCS):
            raise ActionError(f"ACTION {action_name} DERIVE allows only functions: {sorted(_DERIVE_ALLOWED_FUNCS)}")


def _execute_derive(dataset: TameDataset, action_name: str, config: dict[str, Any]) -> OperationOutput:
    name = str(ci_get(config, "NAME", ci_get(config, "COLUMN", ""))).strip()
    expr = ci_get(config, "EXPR", ci_get(config, "EXPRESSION", None))
    if not name:
        raise ActionError(f"ACTION {action_name} DERIVE requires NAME.")
    if expr is None:
        raise ActionError(f"ACTION {action_name} DERIVE requires EXPR.")
    if name in dataset.df.columns:
        raise ActionError(f"ACTION {action_name} DERIVE target already exists: {name}")
    expr = str(expr)

    # Resolve {column} references to numeric Series under safe placeholder names.
    local_vars: dict[str, Any] = {}
    refs = list(dict.fromkeys(_DERIVE_REF_RE.findall(expr)))
    safe_expr = expr
    for index, ref in enumerate(refs):
        column = _resolve_column(dataset, ref.strip())
        placeholder = f"__c{index}__"
        local_vars[placeholder] = pd.to_numeric(dataset.df[column.name], errors="coerce")
        safe_expr = safe_expr.replace("{" + ref + "}", placeholder)

    try:
        tree = ast.parse(safe_expr, mode="eval")
    except SyntaxError as exc:
        raise ActionError(f"ACTION {action_name} DERIVE has invalid EXPR: {exc}") from exc
    _assert_safe_expression(tree, action_name)

    namespace = {"__builtins__": {}}
    namespace.update(_DERIVE_ALLOWED_FUNCS)
    namespace.update(local_vars)
    try:
        result = eval(compile(tree, "<derive>", "eval"), namespace)  # noqa: S307 - sandboxed AST
    except ZeroDivisionError:
        result = pd.Series(float("inf"), index=dataset.df.index)
    except Exception as exc:  # pragma: no cover - defensive
        raise ActionError(f"ACTION {action_name} DERIVE failed to evaluate EXPR: {exc}") from exc

    series = pd.Series(result, index=dataset.df.index)
    round_to = ci_get(config, "ROUND", None)
    if round_to is not None:
        series = series.round(int(round_to))

    frame = dataset.df.copy()
    frame[name] = series
    tags = merge_tags(_string_list(ci_get(config, "TAGS", []))) or ("NUM",)
    columns = [
        *dataset.columns,
        ColumnSpec(original_header=build_header(name, tags), name=name, tags=tags),
    ]
    return OperationOutput(
        name=action_name,
        dataset=dataset.replace(df=frame, columns=columns),
        table=pd.DataFrame([{"column": name, "expr": expr, "tags": "::".join(tags), "rows": len(frame)}]),
        message=f"derive column={name} refs={len(refs)}",
    )


def _execute_dedup(dataset: TameDataset, action_name: str, config: dict[str, Any]) -> OperationOutput:
    subset_names = _selected_column_names(dataset, config)
    subset = [column.name for column in dataset.columns if column.name in subset_names] or None
    keep_raw = str(ci_get(config, "KEEP", "first")).strip().lower() or "first"
    keep: Any = False if keep_raw in {"none", "false"} else keep_raw
    if keep not in {"first", "last", False}:
        raise ActionError(f"ACTION {action_name} DEDUP unsupported KEEP: {keep_raw}")

    before = len(dataset.df)
    frame = dataset.df.drop_duplicates(subset=subset, keep=keep).reset_index(drop=True)
    removed = before - len(frame)
    return OperationOutput(
        name=action_name,
        dataset=dataset.replace(df=frame),
        table=pd.DataFrame([{
            "subset": ", ".join(subset) if subset else "(all columns)",
            "keep": keep_raw,
            "rows_before": before,
            "rows_after": len(frame),
            "removed": removed,
        }]),
        message=f"dedup rows_before={before} removed={removed} rows_after={len(frame)}",
    )


def _execute_sort(dataset: TameDataset, action_name: str, config: dict[str, Any]) -> OperationOutput:
    by = [_resolve_column(dataset, n).name for n in _string_list(ci_get(config, "BY", ci_get(config, "COLUMNS", [])))]
    if not by:
        raise ActionError(f"ACTION {action_name} SORT requires BY column(s).")
    ascending_raw = ci_get(config, "ASCENDING", True)
    if isinstance(ascending_raw, list):
        ascending = [_bool(item, default=True) for item in ascending_raw]
    else:
        ascending = _bool(ascending_raw, default=True)
    frame = dataset.df.copy()
    if _bool(ci_get(config, "NUMERIC", False), default=False):
        helper = {col: pd.to_numeric(frame[col], errors="coerce") for col in by}
        order = pd.DataFrame(helper).sort_values(by, ascending=ascending, kind="stable").index
        frame = frame.loc[order].reset_index(drop=True)
    else:
        frame = frame.sort_values(by, ascending=ascending, kind="stable").reset_index(drop=True)
    return OperationOutput(
        name=action_name,
        dataset=dataset.replace(df=frame),
        table=pd.DataFrame([{"by": ", ".join(by), "ascending": str(ascending), "rows": len(frame)}]),
        message=f"sort by={','.join(by)} rows={len(frame)}",
    )


def _execute_round(dataset: TameDataset, action_name: str, config: dict[str, Any]) -> OperationOutput:
    targets = [column.name for column in dataset.columns if column.name in _selected_column_names(dataset, config)]
    if not targets:
        raise ActionError(f"ACTION {action_name} ROUND requires COLUMNS or TAGS.")
    decimals = int(ci_get(config, "DECIMALS", ci_get(config, "ROUND", 0)))
    frame = dataset.df.copy()
    for target in targets:
        numeric = pd.to_numeric(frame[target], errors="coerce").round(decimals)
        if decimals <= 0:
            formatted = numeric.map(lambda v: "" if pd.isna(v) else str(int(v)))
        else:
            formatted = numeric.map(lambda v: "" if pd.isna(v) else f"{v:.{decimals}f}")
        # Preserve original non-numeric cells.
        original = frame[target]
        frame[target] = [fmt if str(fmt) != "" else orig for fmt, orig in zip(formatted, original)]
    return OperationOutput(
        name=action_name,
        dataset=dataset.replace(df=frame),
        table=pd.DataFrame([{"columns": ", ".join(targets), "decimals": decimals, "rows": len(frame)}]),
        message=f"round columns={len(targets)} decimals={decimals}",
    )


def _execute_bin(dataset: TameDataset, action_name: str, config: dict[str, Any]) -> OperationOutput:
    source = _resolve_column(dataset, str(ci_get(config, "COLUMN", ci_get(config, "SOURCE", ""))))
    name = str(ci_get(config, "NAME", "")).strip() or f"{source.name}_bin"
    if name in dataset.df.columns:
        raise ActionError(f"ACTION {action_name} BIN target already exists: {name}")
    edges = ci_get(config, "BINS", ci_get(config, "EDGES", None))
    labels = ci_get(config, "LABELS", None)
    values = pd.to_numeric(dataset.df[source.name], errors="coerce")

    if isinstance(edges, list) and len(edges) >= 2:
        bins = [float(e) for e in edges]
    else:
        width = ci_get(config, "WIDTH", None)
        if width is None:
            raise ActionError(f"ACTION {action_name} BIN requires BINS (edges) or WIDTH.")
        width = float(width)
        lo = float(values.min()) if pd.notna(values.min()) else 0.0
        hi = float(values.max()) if pd.notna(values.max()) else lo + width
        edge = lo
        bins = []
        while edge <= hi + width:
            bins.append(edge)
            edge += width
    use_labels = labels if isinstance(labels, list) and len(labels) == len(bins) - 1 else False
    binned = pd.cut(values, bins=bins, labels=use_labels, include_lowest=True, right=_bool(ci_get(config, "RIGHT", True), default=True))
    frame = dataset.df.copy()
    frame[name] = binned.astype(object).map(lambda v: "" if (v is None or (isinstance(v, float) and pd.isna(v))) else str(v))
    tags = merge_tags(_string_list(ci_get(config, "TAGS", []))) or ("CATEGORY",)
    columns = [*dataset.columns, ColumnSpec(original_header=build_header(name, tags), name=name, tags=tags)]
    return OperationOutput(
        name=action_name,
        dataset=dataset.replace(df=frame, columns=columns),
        table=pd.DataFrame([{"source": source.name, "target": name, "bins": len(bins) - 1, "rows": len(frame)}]),
        message=f"bin source={source.name} target={name} bins={len(bins) - 1}",
    )


_UNIT_VALUE_RE = re.compile(
    r"^\s*(?P<op><=|>=|<|>|=)?\s*(?P<number>[+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(?P<unit>.*?)\s*$"
)

_BUILTIN_UNIT_FACTORS: dict[tuple[str, str, str], float] = {
    ("GLU", "mmol/l", "mg/dl"): 18.0182,
    ("GLUCOSE", "mmol/l", "mg/dl"): 18.0182,
    ("CREA", "umol/l", "mg/dl"): 1 / 88.4,
    ("CREATININE", "umol/l", "mg/dl"): 1 / 88.4,
}


def _execute_unit_convert(dataset: TameDataset, action_name: str, config: dict[str, Any]) -> OperationOutput:
    source = _resolve_column(dataset, str(ci_get(config, "SOURCE", ci_get(config, "COLUMN", ci_get(config, "VALUE_COLUMN", "")))))
    unit_column = _resolve_optional(dataset, str(ci_get(config, "UNIT_COLUMN", "")))
    test_column = _resolve_optional(dataset, str(ci_get(config, "TEST_COLUMN", "")))
    if test_column is None:
        test_column = dataset.first_column_with_tag("TESTNAME") or dataset.first_column_with_tag("ITEM")

    output_value = str(ci_get(config, "OUTPUT_VALUE", ci_get(config, "VALUE_OUTPUT", f"{source.name}_converted"))).strip()
    output_unit = str(ci_get(config, "OUTPUT_UNIT", ci_get(config, "UNIT_OUTPUT", ""))).strip()
    output_status = str(ci_get(config, "OUTPUT_STATUS", "conversion_status")).strip()
    if not output_value:
        raise ActionError(f"ACTION {action_name} UNIT.CONVERT requires OUTPUT_VALUE.")
    _assert_new_columns(dataset, [name for name in (output_value, output_unit, output_status) if name])

    target_by_test = ci_get(config, "TARGET_UNIT_BY_TEST", {})
    if not isinstance(target_by_test, dict):
        target_by_test = {}
    target_unit = str(ci_get(config, "TARGET_UNIT", "")).strip()
    factors = _unit_conversion_factor_map(ci_get(config, "CONVERSIONS", ci_get(config, "CONVERSION_FACTORS", {})))
    decimals_raw = ci_get(config, "DECIMALS", None)
    decimals = None if decimals_raw is None else int(decimals_raw)

    converted_values: list[Any] = []
    target_units: list[Any] = []
    statuses: list[str] = []

    for index, row in dataset.df.iterrows():
        raw_value = row[source.name]
        parsed = _parse_unit_value(raw_value)
        test_name = _text_or_empty(row[test_column.name]) if test_column is not None else ""
        parsed_unit = parsed["unit"] if parsed is not None else ""
        source_unit = _text_or_empty(row[unit_column.name]) if unit_column is not None else parsed_unit
        target = _target_unit_for_test(test_name, target_by_test, target_unit)

        if parsed is None:
            converted_values.append(NULL)
            target_units.append(target or source_unit or NULL)
            statuses.append("PARSE_ERROR")
            continue
        if not source_unit:
            converted_values.append(NULL)
            target_units.append(target or NULL)
            statuses.append("UNIT_MISSING")
            continue
        if not target:
            converted_values.append(NULL)
            target_units.append(source_unit)
            statuses.append("TARGET_UNIT_MISSING")
            continue

        factor = _unit_factor(test_name, source_unit, target, factors)
        if factor is None:
            converted_values.append(NULL)
            target_units.append(target)
            statuses.append("CONVERSION_MISSING")
            continue

        converted = float(parsed["number"]) * factor
        converted_values.append(_format_converted_value(converted, str(parsed["op"]), decimals=decimals))
        target_units.append(target)
        statuses.append("SAME_UNIT" if factor == 1.0 else "CONVERTED")

    frame = dataset.df.copy()
    frame[output_value] = converted_values
    columns = [
        *dataset.columns,
        ColumnSpec(
            original_header=build_header(output_value, _unit_value_tags(config)),
            name=output_value,
            tags=_unit_value_tags(config),
        ),
    ]
    if output_unit:
        frame[output_unit] = target_units
        columns.append(
            ColumnSpec(
                original_header=build_header(output_unit, ("UNIT", "CATEGORY", "NULLABLE")),
                name=output_unit,
                tags=("UNIT", "CATEGORY", "NULLABLE"),
            )
        )
    if output_status:
        frame[output_status] = statuses
        columns.append(
            ColumnSpec(
                original_header=build_header(output_status, ("PARSE_STATUS", "CATEGORY")),
                name=output_status,
                tags=("PARSE_STATUS", "CATEGORY"),
            )
        )

    status_counts = pd.Series(statuses, dtype="object").value_counts(dropna=False)
    table = pd.DataFrame(
        {
            "status": [str(status) for status in status_counts.index],
            "count": [int(count) for count in status_counts.values],
        }
    )
    return OperationOutput(
        name=action_name,
        dataset=dataset.replace(df=frame, columns=columns),
        table=table,
        message=f"unit_convert source={source.name} output={output_value} converted={statuses.count('CONVERTED')}",
    )


def _execute_date_derive(dataset: TameDataset, action_name: str, config: dict[str, Any]) -> OperationOutput:
    name = str(ci_get(config, "NAME", ci_get(config, "COLUMN", ""))).strip()
    kind = str(ci_get(config, "KIND", ci_get(config, "OP", ""))).strip().lower()
    if not name:
        raise ActionError(f"ACTION {action_name} DATE_DERIVE requires NAME.")
    if name in dataset.df.columns:
        raise ActionError(f"ACTION {action_name} DATE_DERIVE target already exists: {name}")
    if not kind:
        raise ActionError(f"ACTION {action_name} DATE_DERIVE requires KIND (age/diff/part).")

    frame = dataset.df.copy()
    default_tags: tuple[str, ...] = ("NUM",)

    if kind == "age":
        birth = pd.to_datetime(frame[_resolve_column(dataset, str(ci_get(config, "FROM", ci_get(config, "BIRTH", "")))).name], errors="coerce")
        as_of_raw = ci_get(config, "AS_OF", None)
        if as_of_raw is None:
            as_of = pd.Timestamp.now().normalize()
        elif _resolve_optional(dataset, str(as_of_raw)) is not None:
            as_of = pd.to_datetime(frame[_resolve_column(dataset, str(as_of_raw)).name], errors="coerce")
        else:
            as_of = pd.to_datetime(as_of_raw, errors="coerce")
        # Calendar age: full years elapsed, decremented if the birthday has not
        # yet occurred by as_of (the clinical convention, not days/365.25).
        as_of_dt = as_of if isinstance(as_of, pd.Series) else pd.Series(as_of, index=birth.index)
        had_birthday = (as_of_dt.dt.month > birth.dt.month) | (
            (as_of_dt.dt.month == birth.dt.month) & (as_of_dt.dt.day >= birth.dt.day)
        )
        raw_age = as_of_dt.dt.year - birth.dt.year - (~had_birthday).astype("Int64")
        valid = birth.notna() & as_of_dt.notna()
        series = raw_age.where(valid, other=pd.NA).astype("Int64")
        default_tags = ("AGE",)
    elif kind in {"diff", "diff_minutes", "diff_hours", "diff_days"}:
        start = pd.to_datetime(frame[_resolve_column(dataset, str(ci_get(config, "START", ci_get(config, "FROM", "")))).name], errors="coerce")
        end = pd.to_datetime(frame[_resolve_column(dataset, str(ci_get(config, "END", ci_get(config, "TO", "")))).name], errors="coerce")
        unit = kind.split("_")[1] if "_" in kind else str(ci_get(config, "UNIT", "minutes")).lower()
        delta = (end - start).dt.total_seconds()
        divisor = {"minutes": 60.0, "hours": 3600.0, "days": 86400.0, "seconds": 1.0}.get(unit, 60.0)
        series = (delta / divisor).round(int(ci_get(config, "ROUND", 2)))
    elif kind == "part":
        source = pd.to_datetime(frame[_resolve_column(dataset, str(ci_get(config, "FROM", ""))).name], errors="coerce")
        part = str(ci_get(config, "PART", "year")).lower()
        accessor = {
            "year": source.dt.year, "month": source.dt.month, "day": source.dt.day,
            "hour": source.dt.hour, "minute": source.dt.minute, "weekday": source.dt.weekday,
            "date": source.dt.date, "quarter": source.dt.quarter,
        }.get(part)
        if accessor is None:
            raise ActionError(f"ACTION {action_name} DATE_DERIVE unsupported PART: {part}")
        series = accessor
        if part in {"date"}:
            default_tags = ("DATE",)
    else:
        raise ActionError(f"ACTION {action_name} DATE_DERIVE unsupported KIND: {kind}")

    frame[name] = series
    tags = merge_tags(_string_list(ci_get(config, "TAGS", []))) or default_tags
    columns = [*dataset.columns, ColumnSpec(original_header=build_header(name, tags), name=name, tags=tags)]
    return OperationOutput(
        name=action_name,
        dataset=dataset.replace(df=frame, columns=columns),
        table=pd.DataFrame([{"column": name, "kind": kind, "tags": "::".join(tags), "rows": len(frame)}]),
        message=f"date_derive column={name} kind={kind}",
    )


def _execute_impute(dataset: TameDataset, action_name: str, config: dict[str, Any]) -> OperationOutput:
    targets = [column.name for column in dataset.columns if column.name in _selected_column_names(dataset, config)]
    if not targets:
        raise ActionError(f"ACTION {action_name} IMPUTE requires COLUMNS or TAGS.")
    method = str(ci_get(config, "METHOD", "median")).strip().lower()
    by = [_resolve_column(dataset, n).name for n in _string_list(ci_get(config, "BY", ci_get(config, "GROUP_BY", [])))]
    frame = dataset.df.copy()
    rows: list[dict[str, Any]] = []

    for target in targets:
        blank = frame[target].map(_is_blank_value)
        filled = 0
        if method in {"ffill", "bfill", "pad", "backfill"}:
            pandas_method = {"ffill": "ffill", "pad": "ffill", "bfill": "bfill", "backfill": "bfill"}[method]
            series = frame[target].mask(blank)
            if by:
                series = series.groupby([frame[b] for b in by], observed=False).transform(lambda s: s.fillna(method=pandas_method))
            else:
                series = series.fillna(method=pandas_method)
            new_values = series
        elif method == "constant":
            const = ci_get(config, "VALUE", "")
            new_values = frame[target].where(~blank, const)
        else:
            numeric = pd.to_numeric(frame[target], errors="coerce")
            if method in {"mean", "median"}:
                if by:
                    fill = numeric.groupby([frame[b] for b in by], observed=False).transform(method)
                else:
                    fill = pd.Series(getattr(numeric, method)(), index=numeric.index)
            elif method in {"mode", "most_frequent"}:
                if by:
                    fill = frame[target].mask(blank).groupby([frame[b] for b in by], observed=False).transform(
                        lambda s: s.mode().iloc[0] if not s.mode().empty else pd.NA)
                else:
                    modes = frame[target].mask(blank).mode()
                    fill = pd.Series(modes.iloc[0] if not modes.empty else pd.NA, index=frame.index)
            else:
                raise ActionError(f"ACTION {action_name} IMPUTE unsupported METHOD: {method}")
            new_values = frame[target].copy()
            new_values[blank] = fill[blank]
        before_blank = int(blank.sum())
        frame[target] = new_values
        after_blank = int(frame[target].map(_is_blank_value).sum())
        filled = before_blank - after_blank
        rows.append({"column": target, "method": method, "missing_before": before_blank, "filled": filled, "missing_after": after_blank})

    return OperationOutput(
        name=action_name,
        dataset=dataset.replace(df=frame),
        table=pd.DataFrame(rows),
        message=f"impute method={method} columns={len(targets)}",
    )


def _execute_outlier_filter(dataset: TameDataset, action_name: str, config: dict[str, Any]) -> OperationOutput:
    column = _resolve_column(dataset, str(ci_get(config, "COLUMN", ci_get(config, "TAG", ""))) or _first_selected(dataset, config))
    method = str(ci_get(config, "METHOD", "iqr")).strip().lower()
    by = [_resolve_column(dataset, n).name for n in _string_list(ci_get(config, "BY", ci_get(config, "GROUP_BY", [])))]
    values = pd.to_numeric(dataset.df[column.name], errors="coerce")
    default_factor = 3.0 if method in {"sd", "zscore", "z"} else 1.5
    factor = float(ci_get(config, "FACTOR", default_factor))

    def bounds(series: pd.Series) -> tuple[float, float]:
        if method in {"sd", "zscore", "z"}:
            mu, sd = series.mean(), series.std(ddof=1)
            return mu - factor * sd, mu + factor * sd
        q1, q3 = series.quantile(0.25), series.quantile(0.75)
        iqr = q3 - q1
        return q1 - factor * iqr, q3 + factor * iqr

    if by:
        low = values.groupby([dataset.df[b] for b in by], observed=False).transform(lambda s: bounds(s)[0])
        high = values.groupby([dataset.df[b] for b in by], observed=False).transform(lambda s: bounds(s)[1])
    else:
        lo, hi = bounds(values)
        low = pd.Series(lo, index=values.index)
        high = pd.Series(hi, index=values.index)

    is_outlier = values.notna() & ((values < low) | (values > high))
    frame = dataset.df.loc[~is_outlier].reset_index(drop=True)
    removed = int(is_outlier.sum())
    return OperationOutput(
        name=action_name,
        dataset=dataset.replace(df=frame),
        table=pd.DataFrame([{
            "column": column.name, "method": method, "factor": factor,
            "rows_before": len(dataset.df), "removed": removed, "rows_after": len(frame),
        }]),
        message=f"outlier_filter column={column.name} method={method} removed={removed}",
    )


def _execute_recode(dataset: TameDataset, action_name: str, config: dict[str, Any]) -> OperationOutput:
    source = _resolve_column(dataset, str(ci_get(config, "COLUMN", ci_get(config, "SOURCE", ""))))
    mapping = ci_get(config, "MAP", ci_get(config, "MAPPING", None))
    if not isinstance(mapping, dict) or not mapping:
        raise ActionError(f"ACTION {action_name} RECODE requires MAP table of old=new pairs.")
    mapping = {str(key): value for key, value in mapping.items()}
    default = ci_get(config, "DEFAULT", None)  # None -> keep unmapped values
    target_name = str(ci_get(config, "NAME", "")).strip() or source.name

    def recode_value(value: Any) -> Any:
        text = "" if pd.isna(value) else str(value)
        if text in mapping:
            return mapping[text]
        return value if default is None else default

    frame = dataset.df.copy()
    recoded = frame[source.name].map(recode_value)
    changed = int((frame[source.name].map(lambda v: "" if pd.isna(v) else str(v)) != recoded.map(lambda v: "" if pd.isna(v) else str(v))).sum())

    if target_name == source.name:
        frame[source.name] = recoded
        columns = list(dataset.columns)
    else:
        if target_name in frame.columns:
            raise ActionError(f"ACTION {action_name} RECODE target already exists: {target_name}")
        frame[target_name] = recoded
        tags = merge_tags(_string_list(ci_get(config, "TAGS", []))) or source.tags
        columns = [*dataset.columns, ColumnSpec(original_header=build_header(target_name, tags), name=target_name, tags=tags)]
    return OperationOutput(
        name=action_name,
        dataset=dataset.replace(df=frame, columns=columns),
        table=pd.DataFrame([{"source": source.name, "target": target_name, "mapped_keys": len(mapping), "changed_cells": changed}]),
        message=f"recode source={source.name} target={target_name} changed={changed}",
    )


def _execute_join(dataset: TameDataset, action_name: str, config: dict[str, Any]) -> OperationOutput:
    right_path = str(ci_get(config, "RIGHT", ci_get(config, "WITH", ""))).strip()
    if not right_path:
        raise ActionError(f"ACTION {action_name} JOIN requires RIGHT (path to table).")
    on = _string_list(ci_get(config, "ON", ci_get(config, "KEY", [])))
    left_on = _string_list(ci_get(config, "LEFT_ON", []))
    right_on = _string_list(ci_get(config, "RIGHT_ON", []))
    if not on and not (left_on and right_on):
        raise ActionError(f"ACTION {action_name} JOIN requires ON key column(s).")
    how = str(ci_get(config, "HOW", "left")).strip().lower()
    if how not in {"left", "inner", "right", "outer"}:
        raise ActionError(f"ACTION {action_name} JOIN unsupported HOW: {how}")

    right_df, right_columns = _read_join_table(right_path)
    if on:
        left_specs = on
        right_specs = on
    else:
        left_specs = left_on
        right_specs = right_on
    if len(left_specs) != len(right_specs):
        raise ActionError(f"ACTION {action_name} JOIN requires the same number of LEFT_ON and RIGHT_ON keys.")
    left_keys = [_resolve_column(dataset, spec).name for spec in left_specs]
    right_keys = [_resolve_join_right_column(right_columns, spec).name for spec in right_specs]

    select = _string_list(ci_get(config, "SELECT", []))
    selected_right_cols = [_resolve_join_right_column(right_columns, spec).name for spec in select]
    right_non_keys = [c for c in right_df.columns if c not in right_keys]
    keep_cols = right_keys + [c for c in (selected_right_cols or right_non_keys) if c not in right_keys]
    missing = [
        spec
        for spec, left_key, right_key in zip(on or left_specs, left_keys, right_keys)
        if left_key not in dataset.df.columns or right_key not in right_df.columns
    ]
    if missing:
        raise ActionError(f"ACTION {action_name} JOIN key not found on both sides: {missing}")
    right_subset = right_df[[c for c in keep_cols if c in right_df.columns]].copy()
    rename_keys = {right_key: left_key for left_key, right_key in zip(left_keys, right_keys) if right_key != left_key}
    if rename_keys:
        right_subset = right_subset.rename(columns=rename_keys)
    suffix = str(ci_get(config, "SUFFIX", "_right"))

    left = dataset.df.copy()
    merged = left.merge(right_subset, on=left_keys, how=how, suffixes=("", suffix))
    # Build column specs: keep existing, add new right columns with their tags when available.
    right_tag_map = {c.name: c.tags for c in right_columns}
    columns = list(dataset.columns)
    existing = {c.name for c in columns}
    for col in merged.columns:
        if col in existing:
            continue
        tags = right_tag_map.get(col, ())
        columns.append(ColumnSpec(original_header=build_header(col, tags), name=col, tags=tags))
        existing.add(col)
    columns = [c for c in columns if c.name in merged.columns]
    return OperationOutput(
        name=action_name,
        dataset=dataset.replace(df=merged[[c.name for c in columns]], columns=columns),
        table=pd.DataFrame([{"right": right_path, "on": ", ".join(left_keys), "how": how, "rows_before": len(left), "rows_after": len(merged), "added_columns": len(merged.columns) - len(left.columns)}]),
        message=f"join right={right_path} how={how} rows_after={len(merged)}",
    )


def _read_join_table(path: str) -> tuple[pd.DataFrame, list[ColumnSpec]]:
    from .io import read_tame, read_xlsx  # local import to avoid cycle

    lower = path.lower()
    if lower.endswith(".tame"):
        ds = read_tame(path)
        return ds.df, list(ds.columns)
    if lower.endswith((".xlsx", ".xlsm")):
        ds = read_xlsx(path)
        return ds.df, list(ds.columns)
    if lower.endswith((".csv", ".tsv", ".txt")):
        sep = "\t" if lower.endswith((".tsv", ".txt")) else ","
        frame = pd.read_csv(path, sep=sep, dtype=str).fillna("")
        return frame, [ColumnSpec(original_header=c, name=c, tags=()) for c in frame.columns]
    raise ActionError(f"JOIN unsupported RIGHT file type: {path}")


def _resolve_join_right_column(columns: list[ColumnSpec], name: str) -> ColumnSpec:
    text = str(name or "").strip()
    if not text:
        raise ActionError("JOIN right key is required.")
    if text.lower().startswith("tag:"):
        tag = text.split(":", 1)[1]
        matches = [column for column in columns if any_tag_inherits(None, column.tags, tag)]
        if not matches:
            raise ActionError(f"No right JOIN column found with tag: {tag}")
        if len(matches) > 1:
            names = ", ".join(column.name for column in matches)
            raise ActionError(f"Multiple right JOIN columns found with tag {tag}: {names}. Use a more specific tag.")
        return matches[0]
    for column in columns:
        if column.name == text:
            return column
    lowered = text.lower()
    for column in columns:
        if column.name.lower() == lowered:
            return column
    raise ActionError(f"Unknown right JOIN column: {name}")


def _is_blank_value(value: Any) -> bool:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    return isinstance(value, str) and value.strip() == ""


def _resolve_optional(dataset: TameDataset, name: str) -> ColumnSpec | None:
    if not str(name or "").strip():
        return None
    try:
        return _resolve_column(dataset, name)
    except ActionError:
        return None


def _parse_unit_value(value: Any) -> dict[str, Any] | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    match = _UNIT_VALUE_RE.match(text)
    if not match:
        return None
    try:
        number = float(match.group("number"))
    except (TypeError, ValueError):
        return None
    if not np.isfinite(number):
        return None
    return {
        "op": (match.group("op") or "").strip(),
        "number": number,
        "unit": (match.group("unit") or "").strip(),
    }


def _target_unit_for_test(test_name: str, target_by_test: dict[str, Any], default_target: str) -> str:
    if target_by_test:
        normalized = _normalize_test_name(test_name)
        for key, value in target_by_test.items():
            if _normalize_test_name(str(key)) == normalized:
                return str(value).strip()
        for key in ("DEFAULT", "*", "ALL"):
            if key in target_by_test:
                return str(target_by_test[key]).strip()
    return str(default_target or "").strip()


def _unit_conversion_factor_map(raw: Any) -> dict[tuple[str, str, str], float]:
    factors: dict[tuple[str, str, str], float] = {}
    if not isinstance(raw, dict):
        return factors
    for key, value in raw.items():
        parsed_key = _parse_conversion_key(str(key))
        if parsed_key is None:
            continue
        try:
            factors[parsed_key] = float(value)
        except (TypeError, ValueError):
            continue
    return factors


def _parse_conversion_key(text: str) -> tuple[str, str, str] | None:
    if "|" in text:
        parts = [part.strip() for part in text.split("|")]
        if len(parts) == 3:
            return (_normalize_test_name(parts[0]), _normalize_unit(parts[1]), _normalize_unit(parts[2]))
    if "->" in text:
        left, right = text.split("->", 1)
        return ("*", _normalize_unit(left), _normalize_unit(right))
    return None


def _unit_factor(test_name: str, source_unit: str, target_unit: str, configured: dict[tuple[str, str, str], float]) -> float | None:
    source = _normalize_unit(source_unit)
    target = _normalize_unit(target_unit)
    if not source or not target:
        return None
    if source == target:
        return 1.0
    test = _normalize_test_name(test_name)
    for key in ((test, source, target), ("*", source, target)):
        if key in configured:
            return configured[key]
    for key in ((test, source, target), ("*", source, target)):
        if key in _BUILTIN_UNIT_FACTORS:
            return _BUILTIN_UNIT_FACTORS[key]
    return None


def _unit_value_tags(config: dict[str, Any]) -> tuple[str, ...]:
    tags = merge_tags(_string_list(ci_get(config, "VALUE_TAGS", ci_get(config, "TAGS", []))))
    return tags or ("RESULT", "<NUM>", "NULLABLE")


def _format_converted_value(value: float, op: str, *, decimals: int | None) -> str:
    if decimals is not None:
        formatted = f"{value:.{decimals}f}"
    else:
        formatted = f"{value:.6g}"
    if "." in formatted:
        formatted = formatted.rstrip("0").rstrip(".")
    if op and op != "=":
        return f"{op}{formatted}"
    return formatted


def _normalize_unit(value: Any) -> str:
    return str(value or "").strip().replace("µ", "u").replace("μ", "u").replace(" ", "").lower()


def _normalize_test_name(value: Any) -> str:
    return str(value or "").strip().upper()


def _text_or_empty(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _first_selected(dataset: TameDataset, config: dict[str, Any]) -> str:
    names = [column.name for column in dataset.columns if column.name in _selected_column_names(dataset, config)]
    return names[0] if names else ""


def _execute_row_filter(dataset: TameDataset, action_name: str, config: dict[str, Any], *, include: bool) -> OperationOutput:
    match_mask = _conditions_mask(dataset, config)
    keep_mask = match_mask if include else ~match_mask
    frame = dataset.df.loc[keep_mask].reset_index(drop=True)
    action = "include" if include else "exclude"
    rows = {
        "action": action,
        "rows_before": len(dataset.df),
        "matched_rows": int(match_mask.sum()),
        "rows_after": len(frame),
    }
    return OperationOutput(
        name=action_name,
        dataset=dataset.replace(df=frame),
        table=pd.DataFrame([rows]),
        message=f"row_{action} rows_before={rows['rows_before']} matched={rows['matched_rows']} rows_after={rows['rows_after']}",
    )


def _execute_column_filter(dataset: TameDataset, action_name: str, config: dict[str, Any], *, include: bool) -> OperationOutput:
    selected = _selected_column_names(dataset, config)
    if not selected:
        raise ActionError(f"ACTION {action_name} requires COLUMNS or TAGS.")
    if include:
        names = [column.name for column in dataset.columns if column.name in selected]
    else:
        names = [column.name for column in dataset.columns if column.name not in selected]
    columns = _reindex_columns([column for column in dataset.columns if column.name in names])
    frame = dataset.df[[column.name for column in columns]].copy()
    action = "include" if include else "exclude"
    return OperationOutput(
        name=action_name,
        dataset=dataset.replace(df=frame, columns=columns),
        table=pd.DataFrame([{"action": action, "selected_columns": len(selected), "columns_after": len(columns)}]),
        message=f"column_{action} selected={len(selected)} columns_after={len(columns)}",
    )


def _execute_column_split(dataset: TameDataset, action_name: str, config: dict[str, Any]) -> OperationOutput:
    source = _resolve_column(dataset, str(ci_get(config, "COLUMN", ci_get(config, "SOURCE", ""))))
    targets = _string_list(ci_get(config, "INTO", ci_get(config, "COLUMNS", [])))
    if not targets:
        raise ActionError(f"ACTION {action_name} COLUMN.SPLIT requires INTO/COLUMNS.")
    _assert_new_columns(dataset, targets)
    sep = str(ci_get(config, "SEP", ci_get(config, "DELIMITER", "")))
    if not sep:
        raise ActionError(f"ACTION {action_name} COLUMN.SPLIT requires SEP/DELIMITER.")
    trim = _bool(ci_get(config, "TRIM", True), default=True)
    drop_source = _bool(ci_get(config, "DROP_SOURCE", False), default=False)
    use_regex = _bool(ci_get(config, "REGEX", False), default=False)

    try:
        parts = dataset.df[source.name].map(lambda value: "" if pd.isna(value) else str(value)).str.split(
            sep,
            n=len(targets) - 1,
            expand=True,
            regex=use_regex,
        )
    except re.error as exc:
        raise ActionError(f"ACTION {action_name} COLUMN.SPLIT has invalid REGEX SEP: {exc}") from exc
    frame = dataset.df.copy()
    insert_at = list(frame.columns).index(source.name) + (0 if drop_source else 1)
    if drop_source:
        frame = frame.drop(columns=[source.name])
    for offset, target in enumerate(targets):
        values = parts[offset] if offset in parts.columns else pd.Series("", index=frame.index)
        if trim:
            values = values.map(lambda value: value.strip() if isinstance(value, str) else value)
        frame.insert(insert_at + offset, target, values)

    new_specs = [
        ColumnSpec(
            index=0,
            original_header=build_header(target, _new_column_tags(config, target)),
            name=target,
            tags=_new_column_tags(config, target),
        )
        for target in targets
    ]
    columns = _insert_column_specs(dataset.columns, source.name, new_specs, drop_source=drop_source)
    return OperationOutput(
        name=action_name,
        dataset=dataset.replace(df=frame[[column.name for column in columns]], columns=columns),
        table=pd.DataFrame([{"source": source.name, "targets": ", ".join(targets), "drop_source": drop_source}]),
        message=f"column_split source={source.name} targets={len(targets)}",
    )


def _execute_column_concat(dataset: TameDataset, action_name: str, config: dict[str, Any]) -> OperationOutput:
    source_names = [_resolve_column(dataset, name).name for name in _string_list(ci_get(config, "COLUMNS", []))]
    output = str(ci_get(config, "OUTPUT", ci_get(config, "NAME", ""))).strip()
    if not source_names or not output:
        raise ActionError(f"ACTION {action_name} COLUMN.CONCAT requires COLUMNS and OUTPUT/NAME.")
    _assert_new_columns(dataset, [output])
    sep = str(ci_get(config, "SEP", ci_get(config, "DELIMITER", "")))
    skip_empty = _bool(ci_get(config, "SKIP_EMPTY", False), default=False)

    def concat_row(row) -> str:
        values = ["" if pd.isna(row[name]) else str(row[name]) for name in source_names]
        if skip_empty:
            values = [value for value in values if value != ""]
        return sep.join(values)

    frame = dataset.df.copy()
    frame[output] = frame.apply(concat_row, axis=1)
    tags = _new_column_tags(config, output)
    columns = [
        *dataset.columns,
        ColumnSpec(original_header=build_header(output, tags), name=output, tags=tags),
    ]
    return OperationOutput(
        name=action_name,
        dataset=dataset.replace(df=frame, columns=columns),
        table=pd.DataFrame([{"output": output, "sources": ", ".join(source_names), "rows": len(frame)}]),
        message=f"column_concat output={output} sources={len(source_names)}",
    )


def _execute_pivot_longer(dataset: TameDataset, action_name: str, config: dict[str, Any]) -> OperationOutput:
    id_cols = [
        _resolve_column(dataset, name).name
        for name in _string_list(ci_get(config, "ID_COLS", ci_get(config, "ID_VARS", ci_get(config, "KEYS", []))))
    ]
    raw_value_cols = _string_list(ci_get(config, "COLS", ci_get(config, "VALUE_VARS", ci_get(config, "COLUMNS", []))))
    value_cols = (
        _resolve_columns_expanding(dataset, raw_value_cols)
        if raw_value_cols
        else [column.name for column in dataset.columns if column.name not in id_cols]
    )
    # ID 컬럼은 melt 대상에서 제외(태그 확장으로 겹칠 수 있음)
    value_cols = [name for name in value_cols if name not in id_cols]
    names_to = str(ci_get(config, "NAMES_TO", ci_get(config, "VAR_NAME", "variable"))).strip() or "variable"
    values_to = str(ci_get(config, "VALUES_TO", ci_get(config, "VALUE_NAME", "value"))).strip() or "value"
    if not value_cols:
        raise ActionError(f"ACTION {action_name} PIVOT_LONGER requires COLS or non-ID columns.")

    base = dataset.df[id_cols + value_cols].copy()
    if id_cols:
        long_frame = (
            base.set_index(id_cols, drop=True)[value_cols]
            .stack(dropna=False)
            .rename(values_to)
            .reset_index()
            .rename(columns={"level_" + str(len(id_cols)): names_to})
        )
        if names_to not in long_frame.columns:
            long_frame = long_frame.rename(columns={long_frame.columns[len(id_cols)]: names_to})
    else:
        long_frame = (
            base[value_cols]
            .stack(dropna=False)
            .rename(values_to)
            .reset_index()
            .rename(columns={"level_1": names_to})
        )[[names_to, values_to]]
    columns = _pivot_longer_column_specs(dataset, id_cols, names_to, values_to, config)
    meta = _pivot_longer_meta(dataset, action_name, id_cols, value_cols, names_to, values_to, columns)
    return OperationOutput(
        name=action_name,
        dataset=dataset.replace(df=long_frame[[column.name for column in columns]], columns=columns, meta=meta),
        table=pd.DataFrame([{"id_cols": ", ".join(id_cols), "cols": len(value_cols), "rows": len(long_frame)}]),
        message=f"pivot_longer rows={len(long_frame)} cols={len(value_cols)}",
    )


def _execute_pivot_wider(dataset: TameDataset, action_name: str, config: dict[str, Any]) -> OperationOutput:
    id_cols = [
        _resolve_column(dataset, name).name
        for name in _string_list(ci_get(config, "ID_COLS", ci_get(config, "INDEX", ci_get(config, "KEY", []))))
    ]
    names_from = _resolve_column(dataset, str(ci_get(config, "NAMES_FROM", ci_get(config, "COLUMNS", ci_get(config, "BY", ""))))).name
    values_from = _resolve_column(dataset, str(ci_get(config, "VALUES_FROM", ci_get(config, "VALUES", ci_get(config, "VALUE", ""))))).name
    if not id_cols or not names_from or not values_from:
        raise ActionError(f"ACTION {action_name} PIVOT_WIDER requires ID_COLS, NAMES_FROM, and VALUES_FROM.")
    agg = str(ci_get(config, "AGG", "first")).strip().lower() or "first"
    if agg not in {"first", "last", "min", "max", "mean", "sum"}:
        raise ActionError(f"ACTION {action_name} PIVOT_WIDER unsupported AGG: {agg}")
    work_df = dataset.df.copy()
    if agg in {"mean", "sum", "min", "max"}:
        work_df[values_from] = pd.to_numeric(work_df[values_from], errors="coerce")
    wide = pd.pivot_table(work_df, index=id_cols, columns=names_from, values=values_from, aggfunc=agg).reset_index()
    wide.columns = [str(column) for column in wide.columns]
    value_spec = _resolve_column(dataset, values_from)
    columns: list[ColumnSpec] = []
    for name in wide.columns:
        if name in id_cols:
            source = _resolve_column(dataset, name)
            columns.append(source)
        else:
            columns.append(ColumnSpec(original_header=build_header(name, value_spec.tags), name=name, tags=value_spec.tags))
    columns = _reindex_columns(columns)
    meta = _pivot_wider_meta(dataset, action_name, wide, id_cols, names_from, values_from, columns, config)
    return OperationOutput(
        name=action_name,
        dataset=dataset.replace(df=wide[[column.name for column in columns]], columns=columns, meta=meta),
        table=pd.DataFrame([{"id_cols": ", ".join(id_cols), "names_from": names_from, "values_from": values_from, "rows": len(wide)}]),
        message=f"pivot_wider rows={len(wide)} columns={len(columns)}",
    )


def _find_column_index(columns: list[ColumnSpec], name: str) -> int | None:
    for index, column in enumerate(columns):
        if column.name == name:
            return index
    target = name.lower()
    for index, column in enumerate(columns):
        if column.name.lower() == target:
            return index
    return None


def _resolve_column(dataset: TameDataset, name: str) -> ColumnSpec:
    text = str(name or "").strip()
    if not text:
        raise ActionError("Column name is required.")
    if text.lower().startswith("tag:"):
        tag = text.split(":", 1)[1]
        columns = dataset.columns_with_tag(tag)
        if not columns:
            raise ActionError(f"No column found with tag: {tag}")
        if len(columns) > 1:
            names = ", ".join(column.name for column in columns)
            raise ActionError(
                f"Multiple columns found with tag {tag}: {names}. "
                "This action requires a single column; use an exact column name or a more specific tag."
            )
        return columns[0]
    for column in dataset.columns:
        if column.name == text:
            return column
    lowered = text.lower()
    for column in dataset.columns:
        if column.name.lower() == lowered:
            return column
    raise ActionError(f"Unknown column: {name}")


def _resolve_columns_expanding(dataset: TameDataset, selectors: list[str]) -> list[str]:
    """다중 컬럼 입력용 selector 해석. `tag:X`는 매칭되는 모든 컬럼으로 확장한다.

    CSS class처럼 하나의 태그가 여러 컬럼을 가리킬 수 있어야 하는 경우(예: PIVOT_LONGER의
    COLS에서 tag:RESULT가 여러 결과 컬럼을 모두 선택)를 위한 해석기다. 단일 컬럼만 허용하는
    `_resolve_column`(모호하면 오류)과 달리, 여기서는 다중 매칭을 허용·확장한다.
    순서를 보존하고 중복은 제거한다.
    """
    names: list[str] = []
    seen: set[str] = set()
    for selector in selectors:
        text = str(selector or "").strip()
        if not text:
            continue
        if text.lower().startswith("tag:"):
            tag = text.split(":", 1)[1]
            matches = dataset.columns_with_tag(tag)
            if not matches:
                raise ActionError(f"No column found with tag: {tag}")
            resolved = [column.name for column in matches]
        else:
            resolved = [_resolve_column(dataset, text).name]
        for name in resolved:
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


def _conditions_mask(dataset: TameDataset, config: dict[str, Any]) -> pd.Series:
    conditions = ci_get(config, "CONDITIONS", None)
    if not isinstance(conditions, list):
        conditions = [config]
    masks = [_condition_mask(dataset, condition) for condition in conditions if isinstance(condition, dict)]
    if not masks:
        raise ActionError("ROW filter ACTION requires COLUMN/TAG condition or CONDITIONS.")
    logic = str(ci_get(config, "LOGIC", ci_get(config, "HOW", "AND"))).upper()
    mask = masks[0]
    for next_mask in masks[1:]:
        mask = mask | next_mask if logic == "OR" else mask & next_mask
    return mask.fillna(False)


_COMPARISON_OPS = {
    "gt": "gt", ">": "gt",
    "ge": "ge", ">=": "ge", "gte": "ge",
    "lt": "lt", "<": "lt",
    "le": "le", "<=": "le", "lte": "le",
    "between": "between",
}


def _comparison_mask(series: pd.Series, op: str, raw_values: list[Any], op_label: str) -> pd.Series:
    """Numeric or date-aware comparison. Falls back from numeric to datetime casting."""
    left = pd.to_numeric(series, errors="coerce")
    bounds = [pd.to_numeric(pd.Series([item]), errors="coerce").iloc[0] for item in raw_values]
    if all(pd.isna(b) for b in bounds) and raw_values:
        # Not numeric -> try datetime comparison.
        left = pd.to_datetime(series, errors="coerce")
        bounds = [pd.to_datetime(item, errors="coerce") for item in raw_values]
    if not bounds or all(pd.isna(b) for b in bounds):
        raise ActionError(f"Row condition OP {op_label} requires a numeric or date VALUE/VALUES.")

    if op == "between":
        if len(bounds) < 2 or pd.isna(bounds[0]) or pd.isna(bounds[1]):
            raise ActionError("Row condition OP between requires VALUES = [low, high].")
        low, high = (bounds[0], bounds[1]) if bounds[0] <= bounds[1] else (bounds[1], bounds[0])
        return (left >= low) & (left <= high)

    bound = bounds[0]
    if op == "gt":
        return left > bound
    if op == "ge":
        return left >= bound
    if op == "lt":
        return left < bound
    if op == "le":
        return left <= bound
    raise ActionError(f"Unsupported comparison OP: {op_label}")


def _condition_mask(dataset: TameDataset, condition: dict[str, Any]) -> pd.Series:
    column_name = ci_get(condition, "COLUMN", None)
    if column_name is None and ci_get(condition, "TAG", None) is not None:
        column_name = f"tag:{ci_get(condition, 'TAG', '')}"
    column = _resolve_column(dataset, str(column_name or ""))
    series = dataset.df[column.name]
    op = str(ci_get(condition, "OP", "")).strip().lower()
    values = ci_get(condition, "VALUES", None)
    value = ci_get(condition, "VALUE", None)
    if not op:
        op = "in" if isinstance(values, list) else "eq"
    if values is None:
        values = [value]
    raw_values = list(values) if isinstance(values, list) else [values]

    comparison_op = _COMPARISON_OPS.get(op)
    if comparison_op is not None:
        return _comparison_mask(series, comparison_op, raw_values, op)

    values = [str(item) for item in raw_values]
    text = series.map(lambda item: "" if pd.isna(item) else str(item))
    if not _bool(ci_get(condition, "CASE_SENSITIVE", False), default=False):
        text = text.str.lower()
        values = [value.lower() for value in values]
    if op in {"eq", "=", "=="}:
        return text == values[0]
    if op in {"ne", "!=", "not_eq"}:
        return text != values[0]
    if op in {"in", "isin"}:
        return text.isin(values)
    if op in {"not_in", "notin"}:
        return ~text.isin(values)
    if op == "contains":
        return text.str.contains(re.escape(values[0]), na=False)
    if op == "startswith":
        return text.str.startswith(values[0], na=False)
    if op == "endswith":
        return text.str.endswith(values[0], na=False)
    if op == "regex":
        try:
            return text.str.contains(values[0], regex=True, na=False)
        except re.error as exc:
            raise ActionError(f"Invalid regex in row condition: {values[0]!r} ({exc})") from exc
    if op in {"is_empty", "empty"}:
        return text == ""
    if op in {"not_empty", "nonempty"}:
        return text != ""
    raise ActionError(f"Unsupported row condition OP: {op}")


def _selected_column_names(dataset: TameDataset, config: dict[str, Any]) -> set[str]:
    names = set(_string_list(ci_get(config, "COLUMNS", ci_get(config, "COLUMN", []))))
    tags = _string_list(ci_get(config, "TAGS", ci_get(config, "TAG", [])))
    for tag in tags:
        names.update(column.name for column in dataset.columns_with_tag(tag))
    resolved: set[str] = set()
    for name in names:
        resolved.add(_resolve_column(dataset, name).name)
    return resolved


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _assert_new_columns(dataset: TameDataset, names: list[str]) -> None:
    existing = {column.name for column in dataset.columns}
    duplicates = [name for name in names if name in existing]
    if duplicates:
        raise ActionError(f"Target column(s) already exist: {duplicates}")


def _new_column_tags(config: dict[str, Any], column_name: str) -> tuple[str, ...]:
    tag_map = ci_get(config, "COLUMN_TAGS", {})
    if isinstance(tag_map, dict):
        missing = object()
        tags = ci_get(tag_map, column_name, missing)
        if tags is not missing:
            return merge_tags(_string_list(tags))
    return merge_tags(_string_list(ci_get(config, "TAGS", [])))


def _bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y", "on"}:
        return True
    if text in {"false", "0", "no", "n", "off"}:
        return False
    return default


def _insert_column_specs(source_columns: list[ColumnSpec], source_name: str, new_specs: list[ColumnSpec], *, drop_source: bool) -> list[ColumnSpec]:
    result: list[ColumnSpec] = []
    for column in source_columns:
        if column.name == source_name:
            if not drop_source:
                result.append(column)
            result.extend(new_specs)
            continue
        result.append(column)
    return _reindex_columns(result)


def _pivot_longer_column_specs(dataset: TameDataset, id_cols: list[str], names_to: str, values_to: str, config: dict[str, Any]) -> list[ColumnSpec]:
    columns: list[ColumnSpec] = []
    for name in id_cols:
        columns.append(_resolve_column(dataset, name))
    names_tags = merge_tags(ci_get(config, "NAMES_TAGS", ci_get(config, "VAR_TAGS", ["CATEGORY"])))
    values_tags = merge_tags(ci_get(config, "VALUES_TAGS", ci_get(config, "VALUE_TAGS", [])))
    columns.append(ColumnSpec(original_header=build_header(names_to, names_tags), name=names_to, tags=names_tags))
    columns.append(ColumnSpec(original_header=build_header(values_to, values_tags), name=values_to, tags=values_tags))
    return _reindex_columns(columns)


def _pivot_longer_meta(
    dataset: TameDataset,
    action_name: str,
    id_cols: list[str],
    value_cols: list[str],
    names_to: str,
    values_to: str,
    columns: list[ColumnSpec],
) -> dict[str, Any]:
    meta = _metadata_for_columns(dataset.meta, columns)
    source_tags = {name: list(_resolve_column(dataset, name).tags) for name in value_cols}
    column_section = _column_section(meta)
    for name, role in ((names_to, "name"), (values_to, "value")):
        entry = _column_entry(column_section, name)
        entry["PIVOT"] = {
            "ACTION": action_name,
            "TYPE": "LONGER",
            "ROLE": role,
            "ID_COLS": list(id_cols),
            "SOURCE_COLUMNS": list(value_cols),
            "SOURCE_TAGS": source_tags,
        }
        column_section[name] = entry
    meta["COLUMN"] = column_section
    return meta


def _pivot_wider_meta(
    dataset: TameDataset,
    action_name: str,
    wide: pd.DataFrame,
    id_cols: list[str],
    names_from: str,
    values_from: str,
    columns: list[ColumnSpec],
    config: dict[str, Any],
) -> dict[str, Any]:
    meta = _metadata_for_columns(dataset.meta, columns)
    column_section = _column_section(meta)
    context_columns = _pivot_context_columns(dataset, config, names_from, values_from, id_cols)
    names_series = dataset.df[names_from].astype(str)
    generated_names = [column.name for column in columns if column.name not in id_cols]

    for name in generated_names:
        entry = _column_entry(column_section, name)
        entry["PIVOT"] = {
            "ACTION": action_name,
            "TYPE": "WIDER",
            "ID_COLS": list(id_cols),
            "NAMES_FROM": names_from,
            "VALUES_FROM": values_from,
            "NAME_VALUE": name,
        }
        context = _pivot_context_for_name(dataset, names_series, name, context_columns)
        if context:
            entry["PIVOT_CONTEXT"] = context
        column_section[name] = entry

    meta["COLUMN"] = column_section
    return meta


def _metadata_for_columns(meta: dict[str, Any] | None, columns: list[ColumnSpec]) -> dict[str, Any]:
    updated = deepcopy(meta) if isinstance(meta, dict) else {}
    column_section = _column_section(updated)
    current_names = {column.name for column in columns}
    for name in list(column_section):
        if name not in current_names:
            column_section.pop(name, None)
    for column in columns:
        entry = _column_entry(column_section, column.name)
        if column.tags:
            entry["TAGS"] = list(column.tags)
        column_section[column.name] = entry
    if column_section:
        updated["COLUMN"] = column_section
    else:
        updated.pop("COLUMN", None)
    return updated


def _column_section(meta: dict[str, Any]) -> dict[str, Any]:
    section = ci_get(meta, "COLUMN", {})
    return dict(section) if isinstance(section, dict) else {}


def _column_entry(column_section: dict[str, Any], name: str) -> dict[str, Any]:
    entry = column_section.get(name, {})
    return dict(entry) if isinstance(entry, dict) else {}


def _pivot_context_columns(
    dataset: TameDataset,
    config: dict[str, Any],
    names_from: str,
    values_from: str,
    id_cols: list[str],
) -> list[tuple[str, ColumnSpec]]:
    context_tags = _string_list(
        ci_get(config, "CONTEXT_TAGS", ci_get(config, "PRESERVE_TAGS", ["UNIT", "REF_LOW", "REF_HIGH"]))
    )
    excluded = set(id_cols) | {names_from, values_from}
    result: list[tuple[str, ColumnSpec]] = []
    seen: set[str] = set()
    for tag in context_tags:
        for column in dataset.columns_with_tag(tag):
            if column.name in excluded or column.name in seen:
                continue
            result.append((tag, column))
            seen.add(column.name)
    return result


def _pivot_context_for_name(
    dataset: TameDataset,
    names_series: pd.Series,
    name: str,
    context_columns: list[tuple[str, ColumnSpec]],
) -> dict[str, Any]:
    mask = names_series == str(name)
    context: dict[str, Any] = {}
    if not mask.any():
        return context
    for tag, column in context_columns:
        values = [
            str(value)
            for value in dataset.df.loc[mask, column.name]
            if not pd.isna(value) and str(value) != ""
        ]
        unique = list(dict.fromkeys(values))
        if len(unique) == 1:
            context[tag] = unique[0]
    return context


def _reindex_columns(columns: list[ColumnSpec]) -> list[ColumnSpec]:
    return [ColumnSpec(original_header=column.original_header, name=column.name, tags=column.tags) for column in columns]


class _SafeFormatDict(dict):
    def __missing__(self, key):
        return ""

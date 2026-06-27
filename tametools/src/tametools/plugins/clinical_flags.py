"""Reference-range abnormal flagging and abnormal-rate summary.

MODE=FLAG  : append an H/L/N flag column (vs REF_LOW/REF_HIGH) to the data.
MODE=RATE  : summarize abnormal/high/low counts and rates by test (and optional groups).

Detected by tags: RESULT/NUM (result), REF_LOW, REF_HIGH, ITEM/TESTNAME.
Reference limits are read per-row from REF_LOW/REF_HIGH tagged columns.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

import pandas as pd

from tametools.analysis import numeric_series_for
from tametools.cellstate import NULL
from tametools.config import ci_get
from tametools.models import ColumnSpec, OperationOutput, TameDataset, merged_column_specs
from tametools.pandas_compat import concat_dataframes
from tametools.tags import build_header
from tametools.plugin_base.base import register_plugin
from tametools.plugin_base.roles import RESULT_ROLE, result_binding


FLAG_COLUMN_NAME = "이상플래그"


def _first(dataset: TameDataset, *tags: str) -> ColumnSpec | None:
    for tag in tags:
        column = dataset.first_column_with_tag(tag)
        if column is not None:
            return column
    return None


def _flag_series(value: pd.Series, low: pd.Series, high: pd.Series) -> pd.Series:
    flags = pd.Series("", index=value.index, dtype=object)
    known = value.notna()
    is_low = known & low.notna() & (value < low)
    is_high = known & high.notna() & (value > high)
    flags[known] = "N"
    flags[is_low] = "L"
    flags[is_high] = "H"
    flags[~known] = ""
    return flags


@register_plugin("ABNORMAL_FLAG", description="Flag results H/L/N vs REF_LOW/REF_HIGH and summarize abnormal rate.", roles=(RESULT_ROLE,))
@register_plugin("FLAG_ABNORMAL", description="Alias of ABNORMAL_FLAG.", roles=(RESULT_ROLE,))
def abnormal_flag_plugin(dataset: TameDataset, meta: dict, step_name: str, options: dict) -> OperationOutput:
    mode = str(ci_get(options, "MODE", ci_get(meta, "MODE", "FLAG"))).strip().upper()
    comparator_policy = str(ci_get(options, "COMPARATOR_POLICY", ci_get(dataset.settings(), "CRR", "VALUE"))).upper()

    result = _first(dataset, "RESULT", "NUM", "<NUM>")
    ref_low = _first(dataset, "REF_LOW")
    ref_high = _first(dataset, "REF_HIGH")
    context_results = _pivot_context_result_columns(dataset) if ref_low is None and ref_high is None else []

    warnings: list[str] = []
    if result is None and not context_results:
        warnings.append("Missing required RESULT/NUM column.")
    if ref_low is None and ref_high is None and not context_results:
        warnings.append("Missing REF_LOW and REF_HIGH columns; cannot flag abnormal results.")
    if warnings:
        return _empty_output(step_name, mode, options, warnings)

    if context_results:
        if mode == "FLAG":
            return _pivot_context_flag_mode(dataset, context_results, comparator_policy, step_name, options, warnings)
        if mode == "RATE":
            return _pivot_context_rate_mode(dataset, context_results, comparator_policy, step_name, options, warnings)
        raise ValueError(f"Unsupported ABNORMAL_FLAG MODE: {mode}")

    value = numeric_series_for(dataset, result, crr_policy=comparator_policy)
    low = numeric_series_for(dataset, ref_low, crr_policy=comparator_policy) if ref_low else pd.Series(float("nan"), index=value.index)
    high = numeric_series_for(dataset, ref_high, crr_policy=comparator_policy) if ref_high else pd.Series(float("nan"), index=value.index)
    flags = _flag_series(value, low, high)

    if mode == "FLAG":
        return _flag_mode(dataset, flags, step_name, options, warnings)
    if mode == "RATE":
        return _rate_mode(dataset, flags, step_name, options, warnings)
    raise ValueError(f"Unsupported ABNORMAL_FLAG MODE: {mode}")


@register_plugin("AUTOVERIFICATION", description="Rule-based autoverification review using result parsing, reference limits, critical limits, deltas, and instruments.", roles=(RESULT_ROLE,))
@register_plugin("RULE_ENGINE", description="Alias of AUTOVERIFICATION.", roles=(RESULT_ROLE,))
def autoverification_plugin(dataset: TameDataset, meta: dict, step_name: str, options: dict) -> OperationOutput:
    comparator_policy = str(ci_get(options, "COMPARATOR_POLICY", ci_get(dataset.settings(), "CRR", "VALUE"))).upper()
    result_columns = list(result_binding(dataset, options).columns)
    item = _first(dataset, "ITEM", "TESTNAME")
    patient_id = _first(dataset, "ID(patient)", "PATIENT_ID", "ID")
    sample_id = _first(dataset, "ID(sample)", "SAMPLE_ID", "SAMPLE")
    result_time = _first(dataset, "RESULT_TIME", "DATETIME", "DATE")
    instrument = _first(dataset, "INSTRUMENT")
    ref_low = _first(dataset, "REF_LOW")
    ref_high = _first(dataset, "REF_HIGH")

    warnings: list[str] = []
    if not result_columns:
        warnings.append("Missing RESULT/NUM column.")
    if warnings:
        return _empty_output(step_name, "AUTOVERIFICATION", options, warnings)

    patients = dataset.df[patient_id.name].astype(str) if patient_id is not None else pd.Series("", index=dataset.df.index)
    times = pd.to_datetime(dataset.df[result_time.name], errors="coerce") if result_time is not None else pd.Series(pd.NaT, index=dataset.df.index)
    instruments = dataset.df[instrument.name].astype(str) if instrument is not None else pd.Series("", index=dataset.df.index)
    hold_instruments = {_normalize_text(value) for value in _option_list(ci_get(options, "HOLD_INSTRUMENTS", []))}
    output_mode = str(ci_get(options, "OUTPUT", "ALL")).strip().upper()

    rows: list[dict[str, Any]] = []
    include_source = len(result_columns) > 1
    for result in result_columns:
        rows.extend(
            _autoverification_rows_for_result(
                dataset,
                result,
                item,
                sample_id,
                ref_low,
                ref_high,
                comparator_policy=comparator_policy,
                options=options,
                patients=patients,
                times=times,
                instruments=instruments,
                hold_instruments=hold_instruments,
                output_mode=output_mode,
                include_source=include_source,
            )
        )

    table = pd.DataFrame(rows)
    return _rules_output(dataset, table, step_name, "AUTOVERIFICATION", options, warnings)


def _autoverification_rows_for_result(
    dataset: TameDataset,
    result: ColumnSpec,
    item: ColumnSpec | None,
    sample_id: ColumnSpec | None,
    ref_low: ColumnSpec | None,
    ref_high: ColumnSpec | None,
    *,
    comparator_policy: str,
    options: dict,
    patients: pd.Series,
    times: pd.Series,
    instruments: pd.Series,
    hold_instruments: set[str],
    output_mode: str,
    include_source: bool,
) -> list[dict[str, Any]]:
    values = numeric_series_for(dataset, result, crr_policy=comparator_policy)
    lows = _autoverification_reference_series(dataset, result, ref_low, "REF_LOW", values, comparator_policy)
    highs = _autoverification_reference_series(dataset, result, ref_high, "REF_HIGH", values, comparator_policy)
    tests = dataset.df[item.name].astype(str) if item is not None else pd.Series(result.name, index=values.index)
    previous, delta_abs, delta_percent = _delta_series(values, tests, patients, times) if patients.map(lambda value: str(value).strip()).any() else _empty_delta(values)

    rows: list[dict[str, Any]] = []
    for index in dataset.df.index:
        test_name = tests.loc[index]
        value = values.loc[index]
        reasons: list[str] = []
        rule_ids: list[str] = []
        hold = False

        if pd.isna(value):
            reasons.append("RESULT_PARSE")
            rule_ids.append("RESULT_PARSE")

        critical_low = _option_number(options, "CRITICAL_LOW", test_name)
        critical_high = _option_number(options, "CRITICAL_HIGH", test_name)
        if pd.notna(value) and critical_low is not None and value < critical_low:
            hold = True
            reasons.append("CRITICAL_LOW")
            rule_ids.append("CRITICAL_LOW")
        if pd.notna(value) and critical_high is not None and value > critical_high:
            hold = True
            reasons.append("CRITICAL_HIGH")
            rule_ids.append("CRITICAL_HIGH")

        if pd.notna(value) and pd.notna(lows.loc[index]) and value < lows.loc[index]:
            reasons.append("REF_LOW")
            rule_ids.append("REF_LOW")
        if pd.notna(value) and pd.notna(highs.loc[index]) and value > highs.loc[index]:
            reasons.append("REF_HIGH")
            rule_ids.append("REF_HIGH")

        abs_limit = _option_number(options, "DELTA_ABS", test_name)
        percent_limit = _option_number(options, "DELTA_PERCENT", test_name, default=50.0)
        if pd.notna(delta_abs.loc[index]) and abs_limit is not None and delta_abs.loc[index] > abs_limit:
            reasons.append("DELTA_ABS")
            rule_ids.append("DELTA_ABS")
        if pd.notna(delta_percent.loc[index]) and percent_limit is not None and delta_percent.loc[index] > percent_limit:
            reasons.append("DELTA_PERCENT")
            rule_ids.append("DELTA_PERCENT")

        if _normalize_text(instruments.loc[index]) in hold_instruments:
            hold = True
            reasons.append("INSTRUMENT_HOLD")
            rule_ids.append("INSTRUMENT_HOLD")

        if hold:
            decision = "HOLD"
        elif reasons:
            decision = "REVIEW"
        else:
            decision = "PASS"
            reasons = ["NONE"]
            rule_ids = ["PASS"]

        if output_mode == "EXCEPTIONS" and decision == "PASS":
            continue

        row = {
            "patient_id": patients.loc[index] if str(patients.loc[index]).strip() else "ALL",
            "sample_id": dataset.df.loc[index, sample_id.name] if sample_id is not None else "ALL",
            "test": test_name,
            "result_time": times.loc[index].strftime("%Y-%m-%d %H:%M:%S") if pd.notna(times.loc[index]) else NULL,
            "instrument": instruments.loc[index] if str(instruments.loc[index]).strip() else "ALL",
            "result": _number_or_null(value),
            "decision": decision,
            "autoverify_status": decision,
            "review_reason": ";".join(reasons),
            "rule_id": ";".join(rule_ids),
            "previous_result": _number_or_null(previous.loc[index]),
            "delta_abs": _number_or_null(delta_abs.loc[index]),
            "delta_percent": _number_or_null(delta_percent.loc[index]),
        }
        if include_source:
            row = {"source_result_column": result.name, **row}
        rows.append(row)
    return rows


def _autoverification_reference_series(
    dataset: TameDataset,
    result: ColumnSpec,
    reference_column: ColumnSpec | None,
    context_key: str,
    values: pd.Series,
    comparator_policy: str,
) -> pd.Series:
    if reference_column is not None:
        return numeric_series_for(dataset, reference_column, crr_policy=comparator_policy)
    context = ci_get(dataset.column_metadata(result), "PIVOT_CONTEXT", {})
    if isinstance(context, dict):
        return _constant_numeric_series(ci_get(context, context_key, None), values.index)
    return pd.Series(float("nan"), index=values.index)


def _flag_mode(dataset: TameDataset, flags: pd.Series, step_name: str, options: dict, warnings: list[str]) -> OperationOutput:
    column_name = str(ci_get(options, "FLAG_COLUMN", FLAG_COLUMN_NAME)).strip() or FLAG_COLUMN_NAME
    if column_name in dataset.df.columns:
        column_name = column_name + "_flag"
    frame = dataset.df.copy()
    frame[column_name] = flags.values
    tags = ("FLAG", "CATEGORY")
    columns = [*dataset.columns, ColumnSpec(original_header=f"[[FLAG::CATEGORY]]{column_name}", name=column_name, tags=tags)]
    counts = flags.replace("", pd.NA).value_counts(dropna=True)
    table = pd.DataFrame(
        {"flag": counts.index.tolist(), "count": counts.values.tolist()}
    ) if not counts.empty else pd.DataFrame(columns=["flag", "count"])
    return OperationOutput(
        name=step_name,
        dataset=dataset.replace(df=frame, columns=columns),
        table=table,
        warnings=warnings,
        message=f"abnormal_flag mode=FLAG column={column_name} flagged={(flags != '').sum()}",
    )


def _pivot_context_flag_mode(
    dataset: TameDataset,
    result_contexts: list[tuple[ColumnSpec, dict[str, Any]]],
    comparator_policy: str,
    step_name: str,
    options: dict,
    warnings: list[str],
) -> OperationOutput:
    base_name = str(ci_get(options, "FLAG_COLUMN", FLAG_COLUMN_NAME)).strip() or FLAG_COLUMN_NAME
    frame = dataset.df.copy()
    columns = list(dataset.columns)
    meta = deepcopy(dataset.meta)
    column_meta = ci_get(meta, "COLUMN", {})
    column_meta = dict(column_meta) if isinstance(column_meta, dict) else {}
    rows: list[dict[str, Any]] = []
    created: list[str] = []

    for result_column, context in result_contexts:
        value = numeric_series_for(dataset, result_column, crr_policy=comparator_policy)
        flags = _flag_series(
            value,
            _constant_numeric_series(ci_get(context, "REF_LOW", None), value.index),
            _constant_numeric_series(ci_get(context, "REF_HIGH", None), value.index),
        )
        requested_name = base_name if len(result_contexts) == 1 else f"{result_column.name}_{base_name}"
        column_name = _unique_column_name(requested_name, frame.columns)
        frame[column_name] = flags.values

        tags = ("FLAG", "INTERPRETATION", "CATEGORY")
        columns.append(ColumnSpec(original_header=build_header(column_name, tags), name=column_name, tags=tags))
        column_meta[column_name] = {
            "TAGS": list(tags),
            "SOURCE_RESULT": result_column.name,
            "REFERENCE_SOURCE": "PIVOT_CONTEXT",
            "PIVOT_CONTEXT": _reference_context(context),
        }
        created.append(column_name)

        counts = flags.replace("", pd.NA).value_counts(dropna=True)
        for flag, count in counts.items():
            rows.append({"test": result_column.name, "flag": flag, "count": int(count)})

    meta["COLUMN"] = column_meta
    table = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["test", "flag", "count"])
    return OperationOutput(
        name=step_name,
        dataset=dataset.replace(df=frame, columns=columns, meta=meta),
        table=table,
        warnings=warnings,
        message=f"abnormal_flag mode=FLAG source=PIVOT_CONTEXT columns={len(created)} flagged_columns={created}",
    )


def _rate_mode(dataset: TameDataset, flags: pd.Series, step_name: str, options: dict, warnings: list[str]) -> OperationOutput:
    group_cols = _rate_group_columns(dataset, options, include_item=True)

    work = pd.DataFrame({"_flag": flags.values})
    for name in group_cols:
        work[name] = dataset.df[name].values
    work = work.loc[work["_flag"] != ""]
    if work.empty:
        return _empty_output(step_name, "RATE", options, warnings + ["No evaluable (in-range) results to summarize."])

    rows: list[dict[str, Any]] = []
    grouper = work.groupby(group_cols, dropna=False, observed=False) if group_cols else [((), work)]
    for key, group in grouper:
        key_tuple = key if isinstance(key, tuple) else (key,)
        n = len(group)
        high_n = int((group["_flag"] == "H").sum())
        low_n = int((group["_flag"] == "L").sum())
        abnormal_n = high_n + low_n
        record: dict[str, Any] = {}
        for col, val in zip(group_cols, key_tuple):
            record[col] = val
        record.update({
            "n": n,
            "abnormal_n": abnormal_n,
            "high_n": high_n,
            "low_n": low_n,
            "abnormal_rate": round(100.0 * abnormal_n / n, 2) if n else 0.0,
            "high_rate": round(100.0 * high_n / n, 2) if n else 0.0,
            "low_rate": round(100.0 * low_n / n, 2) if n else 0.0,
        })
        rows.append(record)
    table = pd.DataFrame(rows).sort_values("abnormal_rate", ascending=False, kind="stable").reset_index(drop=True)
    return _summary_output(dataset, table, step_name, "RATE", options, warnings)


def _pivot_context_rate_mode(
    dataset: TameDataset,
    result_contexts: list[tuple[ColumnSpec, dict[str, Any]]],
    comparator_policy: str,
    step_name: str,
    options: dict,
    warnings: list[str],
) -> OperationOutput:
    group_cols = _rate_group_columns(dataset, options, include_item=False)
    work_frames: list[pd.DataFrame] = []
    for result_column, context in result_contexts:
        value = numeric_series_for(dataset, result_column, crr_policy=comparator_policy)
        flags = _flag_series(
            value,
            _constant_numeric_series(ci_get(context, "REF_LOW", None), value.index),
            _constant_numeric_series(ci_get(context, "REF_HIGH", None), value.index),
        )
        work = pd.DataFrame({"test": result_column.name, "_flag": flags.values})
        for name in group_cols:
            work[name] = dataset.df[name].values
        work_frames.append(work)

    work = concat_dataframes(work_frames, ignore_index=True) if work_frames else pd.DataFrame(columns=["test", "_flag"])
    work = work.loc[work["_flag"] != ""]
    if work.empty:
        return _empty_output(step_name, "RATE", options, warnings + ["No evaluable pivot-context results to summarize."])

    rows: list[dict[str, Any]] = []
    group_by = ["test", *group_cols]
    for key, group in work.groupby(group_by, dropna=False, observed=False):
        key_tuple = key if isinstance(key, tuple) else (key,)
        n = len(group)
        high_n = int((group["_flag"] == "H").sum())
        low_n = int((group["_flag"] == "L").sum())
        abnormal_n = high_n + low_n
        record = {col: val for col, val in zip(group_by, key_tuple)}
        record.update({
            "n": n,
            "abnormal_n": abnormal_n,
            "high_n": high_n,
            "low_n": low_n,
            "abnormal_rate": round(100.0 * abnormal_n / n, 2) if n else 0.0,
            "high_rate": round(100.0 * high_n / n, 2) if n else 0.0,
            "low_rate": round(100.0 * low_n / n, 2) if n else 0.0,
        })
        rows.append(record)

    table = pd.DataFrame(rows).sort_values("abnormal_rate", ascending=False, kind="stable").reset_index(drop=True)
    return _summary_output(dataset, table, step_name, "RATE", options, warnings)


def _rate_group_columns(dataset: TameDataset, options: dict, *, include_item: bool) -> list[str]:
    item = _first(dataset, "ITEM", "TESTNAME") if include_item else None
    group_cols: list[str] = []
    if item is not None:
        group_cols.append(item.name)
    for tag in _string_list(ci_get(options, "GROUP_BY_TAGS", ci_get(options, "BY_TAGS", []))):
        for resolved in dataset.columns_with_tag(tag):
            if resolved.name not in group_cols:
                group_cols.append(resolved.name)
    for name in _string_list(ci_get(options, "GROUP_BY", [])):
        resolved = _resolve(dataset, name)
        if resolved and resolved.name not in group_cols:
            group_cols.append(resolved.name)
    return group_cols


def _summary_output(dataset: TameDataset, table: pd.DataFrame, step_name: str, mode: str, options: dict, warnings: list[str]) -> OperationOutput:
    safe = table.where(pd.notna(table), None)
    columns = merged_column_specs([_header(name) for name in safe.columns])
    safe.columns = [column.name for column in columns]
    meta = {
        "INFO": {"DESCRIPTION": f"Abnormal flag output: {mode}"},
        "SETTINGS": {"VALIDATE_ERROR": "REPORT"},
        "WORKS": {"DEFAULT": ["DESCRIBE"]},
        "PLUGIN": {"NAME": "ABNORMAL_FLAG", "MODE": mode, "WARNINGS": warnings, "OPTIONS": dict(options or {})},
    }
    result_dataset = TameDataset(df=safe, columns=columns, meta=meta)
    return OperationOutput(
        name=step_name,
        dataset=result_dataset,
        table=table,
        warnings=warnings,
        message=f"abnormal_flag mode={mode} rows={len(table)}",
    )


def _rules_output(dataset: TameDataset, table: pd.DataFrame, step_name: str, mode: str, options: dict, warnings: list[str]) -> OperationOutput:
    safe = table.where(pd.notna(table), NULL) if not table.empty else table
    columns = merged_column_specs([_header(name) for name in safe.columns])
    if not safe.empty:
        safe.columns = [column.name for column in columns]
    meta = {
        "INFO": {"DESCRIPTION": f"Autoverification output: {mode}"},
        "SETTINGS": {"VALIDATE_ERROR": "REPORT"},
        "WORKS": {"DEFAULT": ["DESCRIBE"]},
        "PLUGIN": {"NAME": "AUTOVERIFICATION", "MODE": mode, "WARNINGS": warnings, "OPTIONS": dict(options or {})},
        "VISUALIZATIONS": {
            "MAIN": {"TYPE": "BAR", "TITLE": "Autoverification decisions", "X": "decision", "Y": "count"}
        },
    }
    result_dataset = TameDataset(df=safe, columns=columns, meta=meta) if not safe.empty else None
    counts = table["decision"].value_counts(dropna=False).to_dict() if not table.empty and "decision" in table else {}
    return OperationOutput(
        name=step_name,
        dataset=result_dataset,
        table=table,
        warnings=warnings,
        message=f"autoverification mode={mode} rows={len(table)} decisions={counts}",
    )


def _empty_output(step_name: str, mode: str, options: dict, warnings: list[str]) -> OperationOutput:
    return OperationOutput(
        name=step_name,
        dataset=None,
        table=pd.DataFrame(),
        warnings=warnings,
        message=f"abnormal_flag mode={mode} rows=0",
    )


def _delta_series(values: pd.Series, tests: pd.Series, patients: pd.Series, times: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    work = pd.DataFrame(
        {
            "_value": values,
            "_test": tests,
            "_patient": patients,
            "_time": times,
            "_order": range(len(values)),
        },
        index=values.index,
    ).sort_values(["_patient", "_test", "_time", "_order"], kind="stable")
    previous = work.groupby(["_patient", "_test"], dropna=False, observed=False)["_value"].shift(1)
    delta_abs = (work["_value"] - previous).abs()
    delta_percent = delta_abs / previous.abs().replace(0, pd.NA) * 100
    return previous.reindex(values.index), delta_abs.reindex(values.index), delta_percent.reindex(values.index)


def _empty_delta(values: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    empty = pd.Series(float("nan"), index=values.index)
    return empty, empty.copy(), empty.copy()


def _option_number(options: dict, key: str, test_name: Any, default: float | None = None) -> float | None:
    by_test = ci_get(options, f"{key}_BY_TEST", None)
    if isinstance(by_test, dict):
        value = _lookup_mapping_value(by_test, test_name)
        if value is not None:
            return _to_float(value, default=None)
    value = ci_get(options, key, None)
    if isinstance(value, dict):
        mapped = _lookup_mapping_value(value, test_name)
        return _to_float(mapped, default=default)
    return _to_float(value, default=default)


def _lookup_mapping_value(mapping: dict, test_name: Any) -> Any:
    normalized = _normalize_text(test_name)
    for key, value in mapping.items():
        if _normalize_text(key) == normalized:
            return value
    for key in ("DEFAULT", "*", "ALL"):
        if key in mapping:
            return mapping[key]
    return None


def _to_float(value: Any, *, default: float | None) -> float | None:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _constant_numeric_series(value: Any, index: pd.Index) -> pd.Series:
    number = _to_float(value, default=None)
    fill_value = float("nan") if number is None else number
    return pd.Series(fill_value, index=index)


def _pivot_context_result_columns(dataset: TameDataset) -> list[tuple[ColumnSpec, dict[str, Any]]]:
    result: list[tuple[ColumnSpec, dict[str, Any]]] = []
    for column in dataset.columns_with_tag("RESULT"):
        context = ci_get(dataset.column_metadata(column), "PIVOT_CONTEXT", {})
        if not isinstance(context, dict):
            continue
        if _to_float(ci_get(context, "REF_LOW", None), default=None) is None and _to_float(ci_get(context, "REF_HIGH", None), default=None) is None:
            continue
        result.append((column, dict(context)))
    return result


def _reference_context(context: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("UNIT", "REF_LOW", "REF_HIGH"):
        value = ci_get(context, key, None)
        if value not in (None, ""):
            result[key] = value
    return result


def _unique_column_name(base_name: str, existing: Any) -> str:
    existing_names = set(existing)
    name = str(base_name or FLAG_COLUMN_NAME)
    if name not in existing_names:
        return name
    index = 2
    while f"{name}_{index}" in existing_names:
        index += 1
    return f"{name}_{index}"


def _number_or_null(value: Any) -> Any:
    if value is None or pd.isna(value):
        return NULL
    return round(float(value), 6)


def _option_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [part for item in value for part in _option_list(item)]
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _normalize_text(value: Any) -> str:
    return str(value or "").strip().upper()


def _header(name: str) -> str:
    tags: dict[str, tuple[str, ...]] = {
        "source_result_column": ("SOURCE", "RESULT", "CATEGORY"),
        "patient_id": ("ID(patient)", "CATEGORY"),
        "sample_id": ("ID(sample)", "CATEGORY"),
        "test": ("TESTNAME", "CATEGORY"),
        "result_time": ("RESULT_TIME", "DATETIME", "NULLABLE"),
        "instrument": ("INSTRUMENT", "CATEGORY"),
        "result": ("RESULT", "NUM", "NULLABLE"),
        "decision": ("DECISION", "CATEGORY"),
        "autoverify_status": ("AUTOVERIFY_STATUS", "CATEGORY"),
        "review_reason": ("REVIEW_REASON", "CATEGORY"),
        "rule_id": ("RULE_ID", "CATEGORY"),
        "previous_result": ("RESULT", "NUM", "NULLABLE"),
        "delta_abs": ("DELTA", "NUM", "NULLABLE"),
        "delta_percent": ("DELTA", "PERCENT", "NUM", "NULLABLE"),
        "n": ("N", "NUM"),
        "abnormal_n": ("COUNT", "NUM"),
        "high_n": ("COUNT", "NUM"),
        "low_n": ("COUNT", "NUM"),
        "abnormal_rate": ("PERCENT", "NUM"),
        "high_rate": ("PERCENT", "NUM"),
        "low_rate": ("PERCENT", "NUM"),
    }
    column_tags = tags.get(name)
    if column_tags:
        return f"[[{'::'.join(column_tags)}]]{name}"
    return f"[[CATEGORY]]{name}"


def _resolve(dataset: TameDataset, name: str) -> ColumnSpec | None:
    text = str(name or "").strip()
    if not text:
        return None
    if text.lower().startswith("tag:"):
        columns = dataset.columns_with_tag(text.split(":", 1)[1])
        return columns[0] if columns else None
    for column in dataset.columns:
        if column.name == text:
            return column
    return None


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []

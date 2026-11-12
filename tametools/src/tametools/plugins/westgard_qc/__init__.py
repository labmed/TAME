"""Westgard multirule QC against declared baselines, with a decision per run.

Measurement binding, baseline validity and z-scores come from the shared QC core
(``evaluate_qc``); the rules come from ``multirule.py`` in this package.
This file only connects them and shapes the result.

Each row is judged from its own run and earlier runs, so re-evaluating a growing
QC file leaves earlier rows unchanged. ``ANALYSIS_RESULT.ROW_KEY`` names the
columns that identify a result row, which lets ``tametools sync`` report exactly
which results are new.
"""
from __future__ import annotations

from copy import deepcopy

import pandas as pd

from tametools.plugin_api import (RESULT_ROLE, STATE_VALUE, ColumnSpec, cell_state, chart_spec, ci_get, evaluate_qc,
                                  record_effective_parameters, register_plugin, register_report, register_view,
                                  resolve_id, result_binding, table_output)

from . import commands  # noqa: F401  (registers the qc-action and qc-review commands)
from .actions import qc_notices
from .multirule import NOT_EVALUATED, REJECT, WARNING, evaluate_multirule, rule_plan
from .report import report_options, result_view, sync_qc_report


PLUGIN_NAME = "WESTGARD_QC"
register_report(PLUGIN_NAME, notices=qc_notices)(sync_qc_report)      # the report tametools sync writes
register_view(PLUGIN_NAME, title="QC 보고서")(result_view)             # the same charts for a result shown on its own
ROW_KEY = ["result_id", "instrument", "lot", "level", "run"]
SUMMARY_COLUMNS = ["value", "z", "violation", "warning", "decision", "run_decision"]
COLUMNS = ["result_id", "instrument", "lot", "level", "run", "time", "value", "unit", "mean", "sd", "z",
           "violation", "warning", "rule_scope", "error_type", "decision", "run_decision", "status",
           "series", "baseline_key", "baseline_reference", "baseline_valid_from", "baseline_valid_to",
           "source_result_column"]
NUMERIC_COLUMNS = {"value", "mean", "sd", "z"}

_SHARED = ("RUN_ID", "TIME_ID", "LEVEL_ID", "LOT_ID", "INSTRUMENT_ID", "BASELINES")
_RULE_KEYS = ("RULES", "WARNING_RULES", "SCOPE", "TRIGGER", "AFTER_REJECT", "RULE_SETTINGS", "BY_RESULT")
_OWN = (*_RULE_KEYS, "TIMEZONE", "REPORT")
_SELECTORS = ("RESULT_IDS", "RESULT_TAGS")
_RUN_COLUMN = "__westgard_run__"
_SEPARATOR = "\x1f"


@register_plugin(PLUGIN_NAME, roles=(RESULT_ROLE,),
    description="Westgard multirule (1_2s warning; 1_3s, 2_2s, R_4s, 4_1s, 10x) against declared baselines, with run decisions. Manual review support.")
def westgard_qc(dataset, meta, step_name, options):
    cfg = _settings(dataset, options)
    rule_settings = {key: cfg[key] for key in _RULE_KEYS if key in cfg}
    default_plan = rule_plan(rule_settings)
    timezone = _timezone(cfg.get("TIMEZONE"))
    report_settings = report_options(cfg.get("REPORT"))

    prepared, evaluation = _prepared(dataset, cfg, timezone)
    results = list(result_binding(prepared, evaluation).columns)
    if not results:
        raise ValueError("WESTGARD_QC needs RESULT_IDS (or RESULT/NUM result columns)")
    table = evaluate_qc(prepared, results, evaluation)
    identifiers = [ci_get(prepared.column_metadata(column), "ID") for column in results]
    by_result = rule_settings.get("BY_RESULT") or {}
    if isinstance(by_result, dict) and set(by_result) - set(identifiers):
        raise ValueError("BY_RESULT names a result that is not analysed: " + ", ".join(sorted(set(by_result) - set(identifiers))))
    plans = {identifier: rule_plan(rule_settings, result_id=identifier) for identifier in identifiers}
    rule_order = list(dict.fromkeys(name for plan in [default_plan, *plans.values()] for name in [*plan.warning_rules, *plan.rules]))

    if table.empty:
        frame = pd.DataFrame({name: pd.Series(dtype=float if name in NUMERIC_COLUMNS else object) for name in COLUMNS})
    else:
        table = table.reset_index(drop=True)
        moments = pd.to_datetime(table["time"], utc=True)
        points = pd.DataFrame({
            "group": table["result_id"] + _SEPARATOR + table["instrument"],
            "run": table["run"],
            "order": moments,
            "material": table["level"] + _SEPARATOR + table["lot"] + _SEPARATOR + table["baseline_key"],
            "z": table["z"],
        })
        # Each analyte is judged with its own rule set.
        decisions = pd.concat([
            evaluate_multirule(points.loc[table["result_id"].eq(identifier)], rules=plan.rules, warning_rules=plan.warning_rules,
                               scope=plan.scope, trigger=plan.trigger, rule_scopes=plan.rule_scopes,
                               after_reject=plan.after_reject, rule_forms=plan.rule_forms)
            for identifier, plan in plans.items()]).reindex(table.index)
        frame = pd.concat([table.drop(columns=["violation"]), decisions], axis=1)
        if timezone:
            frame["time"] = [moment.tz_convert(timezone).isoformat() for moment in moments]
        frame["_moment"] = moments
        frame = frame.sort_values(["_moment", "run", "result_id", "level", "lot", "instrument"], kind="stable")
        frame = frame[COLUMNS].reset_index(drop=True)

    runs = _run_summary(frame, rule_order)
    charts = []
    for identifier in frame["result_id"].unique():
        chart = chart_spec("WESTGARD_Z_" + str(identifier), type="line", title=str(identifier) + ": Westgard QC (z-score)",
            x="time", y="z", series="series", max_points=2000,
            reference_lines=[{"VALUE": value, "LABEL": str(value) + " SD"} for value in (-3, -2, -1, 0, 1, 2, 3)])
        chart["FILTER"] = {"result_id": identifier}
        charts.append(chart)

    counts = frame["decision"].value_counts()
    # The default set, and the set actually applied to each analyte.
    applied = {**default_plan.as_dict(), "TIMEZONE": timezone,
               "RULESETS": {identifier: plan.as_dict() for identifier, plan in plans.items()}}
    if report_settings:
        applied["REPORT"] = report_settings
    record_effective_parameters({PLUGIN_NAME: applied})
    output = table_output(step_name, frame, result_columns=("z",) if len(frame) else (), charts=charts,
        warnings=["Westgard rule output supports manual review of control runs; it is not an automatic result-release decision.",
                  "Rules use the declared baseline mean/SD. After a rejected run, consecutive-run counting follows AFTER_REJECT (default RESTART: counts start again)."],
        message=(f"westgard_qc rows={len(frame)} reject={int(counts.get(REJECT, 0))} warning={int(counts.get(WARNING, 0))} "
                 f"not_evaluated={int(counts.get(NOT_EVALUATED, 0))} rules={'/'.join(rule_order)}"))
    result_meta = deepcopy(output.dataset.meta)
    result_meta["INFO"] = {"DESCRIPTION": "Westgard multirule QC result: one row per control measurement"}
    result_meta["ANALYSIS_RESULT"] = {"PLUGIN": PLUGIN_NAME, "ROW_KEY": list(ROW_KEY), "SUMMARY_COLUMNS": list(SUMMARY_COLUMNS),
                                      "REPORT": PLUGIN_NAME}  # sync writes META[SYNC].REPORT with the writer registered below
    result_meta["PLUGIN"] = {"NAME": PLUGIN_NAME, **applied}
    output.dataset = output.dataset.replace(meta=result_meta)
    output.tables = {"runs": runs}
    return output


def _settings(dataset, options):
    cfg = {str(key).upper(): value for key, value in (options or {}).items()}
    unknown = set(cfg) - set(_SHARED) - set(_OWN) - set(_SELECTORS)
    if unknown:
        raise ValueError("Unknown WESTGARD_QC option: " + ", ".join(sorted(unknown)))
    # One QC declaration can serve both QC_ANALYSIS and this plugin.
    shared = ci_get(dataset.meta, "QC_ANALYSIS", {})
    if isinstance(shared, dict):
        for key in _SHARED:
            if key not in cfg and ci_get(shared, key, None) is not None:
                cfg[key] = deepcopy(ci_get(shared, key))
        if not any(key in cfg for key in _SELECTORS) and ci_get(shared, "RESULT_IDS", None) is not None:
            cfg["RESULT_IDS"] = deepcopy(ci_get(shared, "RESULT_IDS"))
    for key in ("TIME_ID", "LEVEL_ID", "LOT_ID", "INSTRUMENT_ID", "BASELINES"):
        if key not in cfg:
            raise ValueError("WESTGARD_QC requires " + key)
    return cfg


def _timezone(value):
    if value is None or not str(value).strip():
        return ""
    try:
        pd.Timestamp("2000-01-01").tz_localize(str(value).strip())
    except Exception as exc:
        raise ValueError(f"Unknown TIMEZONE: {value}") from exc
    return str(value).strip()


def _stamp(value, timezone):
    """ISO text with an explicit offset; local times need the declared TIMEZONE."""
    if cell_state(value) != STATE_VALUE:
        return value
    try:
        stamp = pd.Timestamp(str(value).strip())
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid QC time: {value!r}") from exc
    if pd.isna(stamp):
        raise ValueError(f"Invalid QC time: {value!r}")
    if stamp.tzinfo is None:
        if not timezone:
            raise ValueError('QC times without a UTC offset need TIMEZONE, e.g. TIMEZONE = "Asia/Seoul"')
        try:
            stamp = stamp.tz_localize(timezone)
        except Exception as exc:
            raise ValueError(f"QC time {value!r} is ambiguous or does not exist in {timezone}") from exc
    return stamp.isoformat()


def _prepared(dataset, cfg, timezone):
    """A private copy in the form the shared QC core expects; the input is untouched."""
    time_column = resolve_id(dataset, cfg["TIME_ID"])
    frame = dataset.df.copy()
    columns = list(dataset.columns)
    prepared_meta = deepcopy(dataset.meta)
    frame[time_column.name] = [_stamp(value, timezone) for value in dataset.df[time_column.name]]
    evaluation = {key: deepcopy(cfg[key]) for key in (*_SHARED, *_SELECTORS) if key in cfg}
    if "RUN_ID" not in evaluation:
        # No run column: every distinct measurement time is one run.
        if _RUN_COLUMN in frame.columns:
            raise ValueError("Column name reserved by WESTGARD_QC: " + _RUN_COLUMN)
        frame[_RUN_COLUMN] = [str(value).strip() if cell_state(value) == STATE_VALUE else value
                              for value in dataset.df[time_column.name]]
        columns.append(ColumnSpec(_RUN_COLUMN, _RUN_COLUMN, ("STR",)))
        section = next((key for key in prepared_meta if str(key).upper() == "COLUMN"), "COLUMN")
        prepared_meta[section] = {**(prepared_meta.get(section) or {}), _RUN_COLUMN: {"ID": _RUN_COLUMN}}
        evaluation["RUN_ID"] = _RUN_COLUMN
    baselines = evaluation["BASELINES"]
    if not isinstance(baselines, list) or not baselines or any(not isinstance(item, dict) for item in baselines):
        raise ValueError("WESTGARD_QC requires reviewed BASELINES (mean, SD and validity per analyte, level, lot and instrument)")
    normalized = []
    for item in baselines:
        baseline = {str(key).upper(): value for key, value in item.items()}
        for key in ("LEVEL", "LOT", "INSTRUMENT"):
            if isinstance(baseline.get(key), int) and not isinstance(baseline.get(key), bool):
                baseline[key] = str(baseline[key])
        for key in ("VALID_FROM", "VALID_TO"):
            if isinstance(baseline.get(key), str) and baseline[key].strip():
                baseline[key] = _stamp(baseline[key], timezone)
        normalized.append(baseline)
    evaluation["BASELINES"] = normalized
    return dataset.replace(df=frame, columns=columns, meta=prepared_meta), evaluation


def _run_summary(frame, rule_order):
    columns = ["result_id", "instrument", "run", "time", "measurements", "not_evaluated", "max_abs_z",
               "violation", "warning", "decision"]
    rows = []
    for (result_id, instrument, run), group in frame.groupby(["result_id", "instrument", "run"], sort=False):
        def listed(column):
            found = {name for cell in group[column] for name in str(cell).split(", ") if name not in {"NONE", "NE"}}
            return ", ".join(name for name in rule_order if name in found) or "NONE"
        magnitude = group["z"].abs().max()
        rows.append({"result_id": result_id, "instrument": instrument, "run": run, "time": group["time"].min(),
            "measurements": len(group), "not_evaluated": int(group["decision"].eq(NOT_EVALUATED).sum()),
            "max_abs_z": None if pd.isna(magnitude) else round(float(magnitude), 3),
            "violation": listed("violation"), "warning": listed("warning"), "decision": group["run_decision"].iloc[0]})
    return pd.DataFrame(rows, columns=columns)

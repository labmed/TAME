"""Clinical-laboratory statistical analyses (Tier 3).

Plugins:
- METHOD_COMPARISON : Passing-Bablok + Deming regression + Bland-Altman between two methods.
- GROUP_TEST        : t-test / Mann-Whitney / Kruskal-Wallis of a result across groups.
- CORRELATION       : Pearson/Spearman correlation between analytes (auto-wide by sample).
- QC_ANALYSIS       : precision (mean/SD/CV%), Levey-Jennings z-scores, basic Westgard, sigma.
- RESULT_TREND      : period mean/median trend over time with a moving average.

All read tag-defined columns (RESULT/NUM, ITEM/TESTNAME, INSTRUMENT, ID, DATE/DATETIME).
scipy is imported lazily; if absent, p-values degrade to NaN with a warning.
"""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any

import numpy as np
import pandas as pd

from tametools.analysis import numeric_series_for
from tametools.analysis_contract import resolve_selector
from tametools.config import ci_get
from tametools.cellstate import NULL, STATE_VALUE, STATE_EMPTY, STATE_WS, cell_state
from tametools.models import ColumnSpec, OperationOutput, TameDataset, merged_column_specs
from tametools.reporting import chart_spec, with_visualizations
from tametools.plugin_base.base import register_plugin
from tametools.plugin_base.roles import RESULT_ROLE, result_binding, result_source_record


# --------------------------------------------------------------------------- helpers

def _first(dataset: TameDataset, *tags: str, names: tuple[str, ...] = ()) -> ColumnSpec | None:
    for tag in tags:
        column = dataset.first_column_with_tag(tag)
        if column is not None:
            return column
    for name in names:
        for column in dataset.columns:
            if column.name == name:
                return column
    return None


def _resolve(dataset: TameDataset, name: str) -> ColumnSpec | None:
    return resolve_selector(dataset, name)


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _try_scipy():
    try:
        import scipy.stats as stats  # noqa: PLC0415
        return stats
    except Exception:  # pragma: no cover - optional dependency
        return None


def _result_output(table: pd.DataFrame, step_name: str, plugin: str, mode: str, options: dict, warnings: list[str]) -> OperationOutput:
    safe = table.where(pd.notna(table), None) if not table.empty else table
    columns = merged_column_specs([_header(name) for name in (safe.columns if not safe.empty else [])])
    if not safe.empty:
        safe.columns = [column.name for column in columns]
        typed = []
        for column in columns:
            states = safe[column.name].map(cell_state)
            tags = list(column.tags)
            for tag, needed in [('NULLABLE', True), ('EMPTY_OK', states.eq(STATE_EMPTY).any()), ('WS_OK', states.eq(STATE_WS).any())]:
                if needed and tag not in tags:
                    tags.append(tag)
            typed.append(ColumnSpec(f"[[{'::'.join(tags)}]]{column.name}", column.name, tuple(tags)))
        columns = typed
    meta = {
        "INFO": {"DESCRIPTION": f"{plugin} output: {mode}"},
        "COLUMN": {column.name: {"ID": column.name} for column in columns},
        "ANALYSIS_RESULT": {"PLUGIN": plugin, "MODE": mode},
        "SETTINGS": {"VALIDATE_ERROR": "REPORT"},
        "WORKS": {"DEFAULT": ["DESCRIBE"]},
        "PLUGIN": {"NAME": plugin, "MODE": mode, "WARNINGS": warnings, "OPTIONS": dict(options or {})},
    }
    charts = _charts_for(plugin, mode, table)
    dataset = TameDataset(df=safe, columns=columns, meta=meta) if not safe.empty else None
    if dataset is not None:
        dataset = with_visualizations(dataset, charts)
    return OperationOutput(name=step_name, dataset=dataset, table=table, charts=charts, warnings=warnings,
                           message=f"{plugin.lower()} mode={mode} rows={0 if table is None else len(table)}")


def _charts_for(plugin: str, mode: str, table: pd.DataFrame) -> list[dict[str, Any]]:
    if table is None or table.empty:
        return []
    columns = set(str(column) for column in table.columns)
    if plugin == "METHOD_COMPARISON" and {"test", "bias"} <= columns:
        return [
            chart_spec("METHOD_BIAS", type="bar", title="Method comparison bias", x="test", y="bias"),
            chart_spec("METHOD_LOA_WIDTH", type="bar", title="Bland-Altman LoA width", x="test", y="sd_diff"),
        ]
    if plugin == "GROUP_TEST" and {"test", "p_value"} <= columns:
        return [chart_spec("GROUP_P_VALUE", type="bar", title="Group comparison p-value", x="test", y="p_value")]
    if plugin == "CORRELATION" and {"item_x", "r"} <= columns:
        return [chart_spec("CORRELATION_R", type="bar", title="Correlation coefficient", x="item_x", y="r")]
    if plugin == "QC_ANALYSIS":
        if mode == "WESTGARD" and {"time", "z", "series"} <= columns:
            charts = []
            for identifier in table.result_id.unique():
                chart = chart_spec("LEVEY_JENNINGS_Z_"+str(identifier), type="line", title=str(identifier)+": QC against declared baselines", x="time", y="z", series="series",
                    max_points=2000, reference_lines=[{"VALUE":v, "LABEL":str(v)+" SD"} for v in [-3,-2,0,2,3]])
                chart["FILTER"] = {"result_id": identifier}
                charts.append(chart)
            return charts
        if mode == "LEVEY_JENNINGS" and {"point", "z"} <= columns:
            return [chart_spec("LEVEY_JENNINGS_Z", type="line", title="Levey-Jennings z-score", x="point", y="z", series="group" if "group" in columns else "level" if "level" in columns else "")]
        if {"level", "cv_percent"} <= columns:
            return [chart_spec("QC_CV_PERCENT", type="bar", title="QC CV%", x="group" if "group" in columns else "level", y="cv_percent")]
    if plugin == "ROC_ANALYSIS":
        if {"specificity", "sensitivity"} <= columns and mode == "CURVE":
            return [chart_spec("ROC_CURVE", type="line", title="ROC curve", x="specificity", y="sensitivity", series="score" if "score" in columns else "")]
        if {"score", "auc"} <= columns:
            return [chart_spec("ROC_AUC", type="bar", title="ROC AUC", x="score", y="auc")]
    if plugin == "RESULT_TREND" and {"period", "mean"} <= columns:
        chart = chart_spec("RESULT_TREND_MEAN", type="line", title="Result trend mean", x="period", y="mean", series="test" if "test" in columns else "")
        if "group_result_id" in columns:
            charts = []
            for identifier in table.group_result_id.unique():
                item = deepcopy(chart)
                item.update(name="RESULT_TREND_MEAN_"+str(identifier), TITLE=str(identifier)+": result trend mean", MAX_POINTS=2000,
                            FILTER={"group_result_id":identifier})
                charts.append(item)
            return charts
        return [chart]
    return []


_NUMERIC_NAMES = {
    "input_n", "missing_n", "n", "n_a", "n_b", "n1", "n2", "count", "r", "slope", "intercept", "bias", "mean", "median",
    "sd", "cv_percent", "lower_loa", "upper_loa", "statistic", "p_value", "z", "sigma",
    "moving_avg", "value", "mean_a", "mean_b", "effect_size", "df",
    "auc", "best_cutoff", "sensitivity", "specificity", "youden", "n_pos", "n_neg", "threshold",
    "pb_slope", "pb_intercept", "deming_slope", "deming_intercept", "sd_diff",
}

_OUTPUT_TAGS: dict[str, tuple[str, ...]] = {
    "source_result_column": ("SOURCE", "RESULT", "CATEGORY"),
    "test": ("TESTNAME", "CATEGORY"),
    "score": ("RESULT", "CATEGORY"),
    "label": ("LABEL", "CATEGORY"),
    "positive": ("CATEGORY",),
    "method_a": ("METHOD", "CATEGORY"),
    "method_b": ("METHOD", "CATEGORY"),
    "group": ("GROUP", "CATEGORY"),
    "period": ("DATE", "CATEGORY"),
    "n": ("N", "NUM"),
    "n_a": ("N", "NUM"),
    "n_b": ("N", "NUM"),
    "n1": ("N", "NUM"),
    "n2": ("N", "NUM"),
    "n_pos": ("N", "NUM"),
    "n_neg": ("N", "NUM"),
    "count": ("COUNT", "NUM"),
    "mean": ("MEAN", "NUM"),
    "mean_a": ("MEAN", "NUM"),
    "mean_b": ("MEAN", "NUM"),
    "median": ("MEDIAN", "NUM"),
    "sd": ("SD", "NUM"),
    "sd_diff": ("SD", "BIAS", "NUM"),
    "cv_percent": ("PERCENT", "NUM"),
    "r": ("CORRELATION", "NUM"),
    "slope": ("SLOPE", "NUM"),
    "intercept": ("INTERCEPT", "NUM"),
    "pb_slope": ("SLOPE", "NUM"),
    "pb_intercept": ("INTERCEPT", "NUM"),
    "deming_slope": ("SLOPE", "NUM"),
    "deming_intercept": ("INTERCEPT", "NUM"),
    "bias": ("BIAS", "NUM"),
    "lower_loa": ("LIMIT_OF_AGREEMENT", "REF_LOW", "NUM"),
    "upper_loa": ("LIMIT_OF_AGREEMENT", "REF_HIGH", "NUM"),
    "statistic": ("STAT", "NUM"),
    "p_value": ("P_VALUE", "NUM"),
    "z": ("STAT", "NUM"),
    "sigma": ("SIGMA", "NUM"),
    "moving_avg": ("MEAN", "NUM"),
    "value": ("RESULT", "NUM"),
    "effect_size": ("EFFECT_SIZE", "NUM"),
    "df": ("DEGREES_OF_FREEDOM", "NUM"),
    "auc": ("AUC", "NUM"),
    "best_cutoff": ("THRESHOLD", "NUM"),
    "threshold": ("THRESHOLD", "NUM", "NULLABLE"),
    "sensitivity": ("SENSITIVITY", "NUM"),
    "specificity": ("SPECIFICITY", "NUM"),
    "youden": ("YOUDEN", "NUM"),
}


def _header(name: str) -> str:
    if name.endswith("_n"):
        return f"[[COUNT::NUM::NULLABLE]]{name}"
    if name in _OUTPUT_TAGS:
        return f"[[{'::'.join(_OUTPUT_TAGS[name])}]]{name}"
    if name in _NUMERIC_NAMES:
        return f"[[NUM]]{name}"
    return f"[[CATEGORY]]{name}"


# --------------------------------------------------------------------------- METHOD_COMPARISON

@register_plugin("METHOD_COMPARISON", description="Passing-Bablok + Deming regression and Bland-Altman between two methods.", roles=(RESULT_ROLE,))
def method_comparison_plugin(dataset: TameDataset, meta: dict, step_name: str, options: dict) -> OperationOutput:
    from tametools.method_comparison import compare_methods
    bound_results = result_binding(dataset, options)
    result_columns = list(bound_results.columns)
    method_col = _resolve(dataset, str(ci_get(options, "METHOD_COLUMN", ""))) or _first(dataset, "INSTRUMENT")
    test_col = _first(dataset, "ITEM", "TESTNAME")
    keys = _string_list(ci_get(options, "KEY", [])) or _default_keys(dataset)
    keys = list(dict.fromkeys(keys))  # 중복 제거(같은 키 열 중복 시 pivot/merge index 가 깨진다 — pandas 2.x).

    table, warnings = compare_methods(dataset, options, result_columns, method_col, test_col, keys)
    return _result_output(table, step_name, "METHOD_COMPARISON", "REGRESSION", options, warnings)


def _default_keys(dataset: TameDataset) -> list[str]:
    keys: list[str] = []
    pid = _first(dataset, "ID(patient)", "PATIENT_ID", "ID")
    if pid is not None:
        keys.append(pid.name)
    sample = _first(dataset, "ID(sample)", "SAMPLE_ID", "ID(specimen)", "SPECIMEN_ID", "SAMPLE")
    if sample is not None:
        keys.append(sample.name)
    return keys


# --------------------------------------------------------------------------- GROUP_TEST

@register_plugin("GROUP_TEST", description="Compare a numeric result across groups (t-test / Mann-Whitney / Kruskal-Wallis).", roles=(RESULT_ROLE,))
def group_test_plugin(dataset: TameDataset, meta: dict, step_name: str, options: dict) -> OperationOutput:
    from tametools.observation_contract import observation_info
    observation_info(dataset, inference=True)
    if ci_get(dataset.meta, "SURVEY", None) is not None:
        raise ValueError("GROUP_TEST is not survey adjusted; use ANALYSIS_PLAN survey contrasts or models")
    comparator_policy = str(ci_get(options, "COMPARATOR_POLICY", ci_get(dataset.settings(), "CRR", "VALUE"))).upper()
    bound_results = result_binding(dataset, options)
    result_columns = list(bound_results.columns)
    group_col = _resolve(dataset, str(ci_get(options, "GROUP", ""))) or _first(dataset, "GROUP", "BY", "SEX")
    test_col = _first(dataset, "ITEM", "TESTNAME")
    requested = str(ci_get(options, "TEST_TYPE", "auto")).strip().lower()

    warnings: list[str] = []
    if not result_columns:
        warnings.append("Missing RESULT/NUM column.")
    if group_col is None:
        warnings.append("Missing GROUP column.")
    if warnings:
        return _result_output(pd.DataFrame(), step_name, "GROUP_TEST", "COMPARE", options, warnings)

    stats = _try_scipy()
    if stats is None:
        warnings.append("scipy not available; p-values reported as NaN.")

    rows: list[dict[str, Any]] = []
    include_source = len(result_columns) > 1
    for result in result_columns:
        base = pd.DataFrame({
            "_value": numeric_series_for(dataset, result, crr_policy=comparator_policy).values,
            "_group": dataset.df[group_col.name].astype(str).values,
            "_test": dataset.df[test_col.name].astype(str).values if test_col is not None else result.name,
        }).dropna(subset=["_value"])

        for test_name, group in base.groupby("_test", observed=False):
            levels = [lvl for lvl in pd.unique(group["_group"]) if lvl and lvl.lower() != "nan"]
            samples = [group.loc[group["_group"] == lvl, "_value"].to_numpy(dtype=float) for lvl in levels]
            samples = [s for s in samples if len(s) > 0]
            if len(samples) < 2:
                continue
            method, statistic, p_value, effect = _group_statistic(samples, requested, stats)
            rows.append({
                **result_source_record(result, include=include_source),
                "test": test_name, "group_column": group_col.name, "n_groups": len(samples),
                "levels": ", ".join(levels[:len(samples)]), "method": method,
                "statistic": round(statistic, 4) if statistic == statistic else float("nan"),
                "p_value": round(p_value, 6) if p_value == p_value else float("nan"),
                "effect_size": round(effect, 4) if effect == effect else float("nan"),
            })
    return _result_output(pd.DataFrame(rows), step_name, "GROUP_TEST", "COMPARE", options, warnings)


def _group_statistic(samples: list[np.ndarray], requested: str, stats) -> tuple[str, float, float, float]:
    if len(samples) > 2:
        if stats is None:
            return "kruskal", float("nan"), float("nan"), float("nan")
        stat, p = stats.kruskal(*samples)
        return "kruskal", float(stat), float(p), float("nan")
    a, b = samples
    if requested in {"ttest", "t", "welch"}:
        if stats is None:
            return "t_test", float("nan"), float("nan"), _cohens_d(a, b)
        stat, p = stats.ttest_ind(a, b, equal_var=False)
        return "welch_t", float(stat), float(p), _cohens_d(a, b)
    # default / auto -> Mann-Whitney U (nonparametric)
    if stats is None:
        return "mann_whitney", float("nan"), float("nan"), _rank_biserial(a, b)
    stat, p = stats.mannwhitneyu(a, b, alternative="two-sided")
    effect = 1.0 - (2.0 * stat) / (len(a) * len(b))  # rank-biserial
    return "mann_whitney", float(stat), float(p), float(effect)


def _cohens_d(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return float("nan")
    pooled = np.sqrt(((na - 1) * np.var(a, ddof=1) + (nb - 1) * np.var(b, ddof=1)) / (na + nb - 2))
    return float((np.mean(a) - np.mean(b)) / pooled) if pooled else float("nan")


def _rank_biserial(a: np.ndarray, b: np.ndarray) -> float:
    greater = sum(1 for x in a for y in b if x > y)
    less = sum(1 for x in a for y in b if x < y)
    total = len(a) * len(b)
    return float((greater - less) / total) if total else float("nan")


# --------------------------------------------------------------------------- CORRELATION

@register_plugin("CORRELATION", description="Pearson/Spearman correlation between analytes (auto-wide by sample).")
def correlation_plugin(dataset: TameDataset, meta: dict, step_name: str, options: dict) -> OperationOutput:
    comparator_policy = str(ci_get(options, "COMPARATOR_POLICY", ci_get(dataset.settings(), "CRR", "VALUE"))).upper()
    method = str(ci_get(options, "METHOD", "pearson")).strip().lower()
    result = _first(dataset, "RESULT", "NUM", "<NUM>")
    test_col = _first(dataset, "ITEM", "TESTNAME")
    keys = _string_list(ci_get(options, "KEY", [])) or _default_keys(dataset)
    keys = list(dict.fromkeys(keys))  # 중복 제거(같은 키 열 중복 시 pivot/merge index 가 깨진다 — pandas 2.x).

    warnings: list[str] = []
    if result is not None and test_col is not None and keys:
        values = numeric_series_for(dataset, result, crr_policy=comparator_policy)
        long = pd.DataFrame({"_value": values.values, "_test": dataset.df[test_col.name].astype(str).values})
        for key in keys:
            col = _resolve(dataset, key)
            if col is not None:
                long[key] = dataset.df[col.name].astype(str).values
        wide = long.pivot_table(index=keys, columns="_test", values="_value", aggfunc="mean")
    else:
        # Fall back to all numeric-tagged columns as variables.
        numeric_cols = [c for c in dataset.columns if any(t in ("NUM", "<NUM>", "RESULT") for t in c.tags)]
        if len(numeric_cols) < 2:
            warnings.append("Need >=2 numeric columns or ITEM+RESULT+KEY to correlate.")
            return _result_output(pd.DataFrame(), step_name, "CORRELATION", method.upper(), options, warnings)
        wide = pd.DataFrame({c.name: numeric_series_for(dataset, c, crr_policy=comparator_policy) for c in numeric_cols})

    corr = wide.corr(method=method if method in {"pearson", "spearman", "kendall"} else "pearson")
    rows: list[dict[str, Any]] = []
    cols = list(corr.columns)
    for i, a in enumerate(cols):
        for b in cols[i + 1:]:
            pair = wide[[a, b]].dropna()
            rows.append({"item_x": str(a), "item_y": str(b), "method": method,
                         "r": round(float(corr.loc[a, b]), 4) if pd.notna(corr.loc[a, b]) else float("nan"),
                         "n": int(len(pair))})
    table = pd.DataFrame(rows).sort_values("r", ascending=False, kind="stable").reset_index(drop=True) if rows else pd.DataFrame()
    return _result_output(table, step_name, "CORRELATION", method.upper(), options, warnings)


# --------------------------------------------------------------------------- QC_ANALYSIS

@register_plugin("QC_ANALYSIS", description="QC precision (mean/SD/CV%), Levey-Jennings z-scores, basic Westgard, sigma.", roles=(RESULT_ROLE,))
def qc_analysis_plugin(dataset: TameDataset, meta: dict, step_name: str, options: dict) -> OperationOutput:
    comparator_policy = str(ci_get(options, "COMPARATOR_POLICY", ci_get(dataset.settings(), "CRR", "VALUE"))).upper()
    mode = str(ci_get(options, "MODE", "PRECISION")).strip().upper()
    bound_results = result_binding(dataset, options)
    result_columns = list(bound_results.columns)

    warnings: list[str] = []
    if not result_columns:
        warnings.append("Missing RESULT/NUM column.")
        return _result_output(pd.DataFrame(), step_name, "QC_ANALYSIS", mode, options, warnings)

    # Allowable total error and bias, both in percent: one number, {level: x} or {result id: {level: x}}.
    tea_map = ci_get(options, "TEA", None)
    bias_map = ci_get(options, "BIAS", None)
    include_source = len(result_columns) > 1

    if mode == "WESTGARD":
        from tametools.quality_control import evaluate_qc
        table = evaluate_qc(dataset, result_columns, options)
        return _result_output(table, step_name, "QC_ANALYSIS", mode, options,
            ["Basic 1_3s, 2_2s and within-run R_4s against declared baselines; not a validated operational release system."])

    if mode in {"PRECISION", "SIGMA", "LEVEY_JENNINGS"}:
        from tametools.quality_control import qc_context
        rows = []
        for result in result_columns:
            work, bindings = qc_context(dataset, result, options)
            work["_value"] = numeric_series_for(dataset, result, crr_policy=comparator_policy)
            if not bindings['level']:
                warnings.append("QC level is not declared; summaries describe the supplied result groups.")
            for keys, raw in work.groupby(["result_id", "test", "level", "lot", "instrument"], sort=True, dropna=False):
                identity = dict(zip(["result_id", "test", "level", "lot", "instrument"], keys))
                v = raw['_value'].dropna().to_numpy(dtype=float)
                mean = float(np.mean(v)) if len(v) else float('nan')
                sd = float(np.std(v, ddof=1)) if len(v) > 1 else float('nan')
                cv = 100 * sd / abs(mean) if mean else float('nan')
                label = " / ".join(str(value) for value in keys if str(value))
                common = {**result_source_record(result, include=include_source), **identity,
                    "group":label, "n":len(v), "input_n":len(raw), "missing_n":len(raw)-len(v),
                    "status":"evaluated" if len(v)>1 else "insufficient_n" if len(v) else "no_numeric_results"}
                if len(v)<2:
                    warnings.append("SD and CV require at least two observations: " + label)
                if mode == "LEVEY_JENNINGS":
                    z = (v-mean)/sd if np.isfinite(sd) and sd>0 else np.full_like(v, np.nan)
                    for index, (value, zz) in enumerate(zip(v, z), 1):
                        rows.append({**common,"point":index,"value":float(value),"mean":mean,"sd":sd,"z":float(zz),"violation":"NOT_APPLIED"})
                else:
                    row = {**common,"mean":round(mean,4),"sd":round(sd,4),"cv_percent":round(cv,3)}
                    if mode == "SIGMA" or tea_map is not None:
                        # Sigma = (TEa - |bias|) / CV. A bias that was not given is not a bias of zero:
                        # without it there is no sigma, and the row says which input is missing.
                        tea = _declared_percent(tea_map, identity['result_id'], identity['level'], "TEA")
                        bias = _declared_percent(bias_map, identity['result_id'], identity['level'], "BIAS")
                        missing = "tea_missing" if tea is None else "bias_missing" if bias is None else \
                            "cv_unavailable" if not (np.isfinite(cv) and cv > 0) else ""
                        row.update(tea_percent=tea if tea is not None else float('nan'),
                                   bias_percent=bias if bias is not None else float('nan'),
                                   sigma=float('nan') if missing else round((tea - abs(bias)) / cv, 2),
                                   sigma_status=missing or "evaluated")
                        if missing in {"tea_missing", "bias_missing"}:
                            name = "TEA" if missing == "tea_missing" else "BIAS"
                            warnings.append(f"Sigma not calculated for {label}: no {name} is declared for it. "
                                + ("Declare BIAS in percent; write 0 only when the bias was measured as zero."
                                   if name == "BIAS" else "Declare TEA (allowable total error) in percent."))
                    rows.append(row)
        if mode == "LEVEY_JENNINGS":
            warnings.append("Retrospective mean/SD estimated from these observations; not fixed-baseline operational QC.")
        return _result_output(pd.DataFrame(rows), step_name, "QC_ANALYSIS", mode, options, list(dict.fromkeys(warnings)))

    raise ValueError(f"Unsupported QC mode: {mode}")


def _westgard_flags(z: np.ndarray, i: int, *, same_run: bool = False) -> list[str]:
    flags: list[str] = []
    if abs(z[i]) > 3:
        flags.append("1_3s")
    if i >= 1 and z[i] > 2 and z[i - 1] > 2:
        flags.append("2_2s")
    if i >= 1 and z[i] < -2 and z[i - 1] < -2:
        flags.append("2_2s")
    if same_run and i >= 1 and z[i] > 2 and z[i - 1] < -2:
        flags.append("R_4s")
    if same_run and i >= 1 and z[i] < -2 and z[i - 1] > 2:
        flags.append("R_4s")
    return flags


def _declared_percent(declared: Any, result_id: str, level: str, name: str) -> float | None:
    """The percentage declared for one analyte and level; None when nothing is declared for it.

    ``declared`` is one number for everything, ``{level: x}``, or ``{result id: {level: x}}``
    (``{result id: x}`` for every level of that analyte). A value that is not a number is an error.
    """
    def number(value: Any, where: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError(f"{name}{where} must be a number in percent")
        try:
            parsed = float(value)
        except ValueError:
            raise ValueError(f"{name}{where} must be a number in percent, got {value!r}") from None
        if not math.isfinite(parsed) or (name == "TEA" and parsed <= 0):
            raise ValueError(f"{name}{where} must be a finite number in percent" + (" above 0" if name == "TEA" else ""))
        return parsed

    def find(mapping: dict, key: str) -> tuple[bool, Any]:
        for candidate, value in mapping.items():
            if str(candidate) == str(key):
                return True, value
        return False, None

    if declared is None:
        return None
    if not isinstance(declared, dict):
        return number(declared, "")
    found, value = find(declared, result_id)
    if found:
        if not isinstance(value, dict):
            return number(value, f".{result_id}")
        found, inner = find(value, level)
        return number(inner, f".{result_id}.{level}") if found else None
    found, value = find(declared, level)
    if found and isinstance(value, dict):
        raise ValueError(f"{name}.{level} must be a number in percent; use {name}.<result id>.<level> to set it per analyte")
    return number(value, f".{level}") if found else None


# --------------------------------------------------------------------------- ROC_ANALYSIS

@register_plugin("ROC_ANALYSIS", description="ROC / sensitivity-specificity of a numeric score against a binary label.", roles=(RESULT_ROLE,))
def roc_analysis_plugin(dataset: TameDataset, meta: dict, step_name: str, options: dict) -> OperationOutput:
    from tametools.observation_contract import observation_info
    observation_info(dataset, inference=True)
    comparator_policy = str(ci_get(options, "COMPARATOR_POLICY", "DELETE")).upper()
    if comparator_policy not in {"DELETE", "VALUE"}:
        raise ValueError("ROC COMPARATOR_POLICY must be DELETE or explicitly VALUE")
    mode = str(ci_get(options, "MODE", "SUMMARY")).strip().upper()
    bound_scores = result_binding(dataset, options)
    score_columns = list(bound_scores.columns)
    label_col = _resolve(dataset, str(ci_get(options, "LABEL", ""))) or _first(dataset, "LABEL", "OUTCOME", "CLASS")
    positive = str(ci_get(options, "POSITIVE", "")).strip()
    direction = str(ci_get(options, "DIRECTION", "higher")).strip().lower()
    if direction not in {"higher", "lower"}:
        raise ValueError("DIRECTION must be higher or lower")
    higher_positive = direction == "higher"
    if mode not in {"SUMMARY", "CURVE"}:
        raise ValueError("ROC MODE must be SUMMARY or CURVE")

    warnings: list[str] = []
    if not score_columns:
        warnings.append("Missing SCORE/RESULT numeric column.")
    if label_col is None:
        warnings.append("Missing LABEL/OUTCOME column.")
    if warnings:
        return _result_output(pd.DataFrame(), step_name, "ROC_ANALYSIS", mode, options, warnings)

    label_raw = dataset.df[label_col.name]
    y, positive, negative = _binary_labels(label_raw, positive, ci_get(options, "NEGATIVE", None))
    label_missing_n = int(y.isna().sum())
    warnings.append("ROC is an unweighted complete-pair analysis; cutoffs are exploratory, not validated diagnostic thresholds.")
    if ci_get(dataset.meta, "SURVEY", None) is not None:
        raise ValueError("ROC_ANALYSIS does not implement survey-adjusted ROC; do not discard SURVEY")

    rows: list[dict[str, Any]] = []
    if mode == "CURVE":
        include_score = len(score_columns) > 1
        for score_col in score_columns:
            score = numeric_series_for(dataset, score_col, crr_policy=comparator_policy)
            work = pd.DataFrame({"_score": score.values, "_y": y.values}).dropna(subset=["_score", "_y"])
            if work["_y"].sum() == 0 or work["_y"].sum() == len(work):
                warnings.append(f"Label has only one class for score {score_col.name} after filtering; cannot compute ROC.")
                continue
            s = work["_score"].to_numpy(dtype=float)
            if not higher_positive:
                s = -s
            yv = work["_y"].to_numpy(dtype=int)
            curve = _roc_curve(s, yv)
            for p in curve:
                thr = p["threshold"] if higher_positive else -p["threshold"]
                row = {
                    "threshold": float(thr) if np.isfinite(thr) else NULL,
                    "threshold_kind": "finite" if np.isfinite(thr) else "all_negative",
                    "sensitivity": round(p["sensitivity"], 4),
                    "specificity": round(p["specificity"], 4),
                    "youden": round(p["sensitivity"] + p["specificity"] - 1, 4),
                }
                if include_score:
                    row = {"score": score_col.name, **row}
                rows.append(row)
        table = pd.DataFrame(rows)
    else:
        for score_col in score_columns:
            score = numeric_series_for(dataset, score_col, crr_policy=comparator_policy)
            work = pd.DataFrame({"_score": score.values, "_y": y.values}).dropna(subset=["_score", "_y"])
            if work["_y"].sum() == 0 or work["_y"].sum() == len(work):
                warnings.append(f"Label has only one class for score {score_col.name} after filtering; cannot compute ROC.")
                continue
            s = work["_score"].to_numpy(dtype=float)
            if not higher_positive:
                s = -s
            yv = work["_y"].to_numpy(dtype=int)
            curve = _roc_curve(s, yv)
            auc = _auc(curve)
            best = max((p for p in curve if np.isfinite(p["threshold"])), key=lambda p: p["sensitivity"] + p["specificity"] - 1)
            best_cut = best["threshold"] if higher_positive else -best["threshold"]
            rows.append({
                "score": score_col.name, "label": label_col.name, "positive": positive, "negative": negative,
                "input_n": len(dataset.df), "label_missing_n": label_missing_n,
                "score_missing_n": int(score.isna().sum()), "paired_n": len(work),
                "excluded_n": len(dataset.df) - len(work), "engine": "scikit-learn",
                "n_pos": int(yv.sum()), "n_neg": int(len(yv) - yv.sum()),
                "auc": auc, "best_cutoff": float(best_cut),
                "sensitivity": round(best["sensitivity"], 4), "specificity": round(best["specificity"], 4),
                "youden": round(best["sensitivity"] + best["specificity"] - 1, 4),
            })
        table = pd.DataFrame(rows)
    return _result_output(table, step_name, "ROC_ANALYSIS", mode, options, warnings)


def _binary_labels(raw, positive, negative):
    present = raw.map(lambda value: cell_state(value) == STATE_VALUE)
    labels = raw.where(present).map(lambda value: str(value) if cell_state(value) == STATE_VALUE else None)
    common = {"1": "0", "positive": "negative", "true": "false", "yes": "no"}
    if not positive:
        if set(labels.dropna()) <= {"0", "1"}:
            positive = "1"
        else:
            raise ValueError("Declare POSITIVE and NEGATIVE codes explicitly")
    if negative is None:
        negative = common.get(positive)
    if not isinstance(negative, str) or not negative or positive == negative:
        raise ValueError("Declare distinct POSITIVE and NEGATIVE codes explicitly")
    unknown = set(labels[present]) - {positive, negative}
    if unknown:
        raise ValueError("Unknown binary outcome codes: " + repr(sorted(unknown)))
    return labels.map({positive: 1., negative: 0.}), positive, negative


def _roc_curve(score: np.ndarray, y: np.ndarray) -> list[dict[str, float]]:
    try:
        from sklearn.metrics import roc_curve
    except ImportError as exc:
        raise ImportError("ROC requires tametools[stats] (scikit-learn)") from exc
    fpr, tpr, thresholds = roc_curve(y, score, pos_label=1, drop_intermediate=False)
    return [dict(threshold=float(thr), sensitivity=float(tp), specificity=float(1-fp))
            for fp, tp, thr in zip(fpr, tpr, thresholds)]


def _auc(curve: list[dict[str, float]]) -> float:
    from sklearn.metrics import auc
    return float(auc([1-p["specificity"] for p in curve], [p["sensitivity"] for p in curve]))


# --------------------------------------------------------------------------- RESULT_TREND

@register_plugin("RESULT_TREND", description="Period mean/median trend over time with a moving average.", roles=(RESULT_ROLE,))
def result_trend_plugin(dataset: TameDataset, meta: dict, step_name: str, options: dict) -> OperationOutput:
    comparator_policy = str(ci_get(options, "COMPARATOR_POLICY", ci_get(dataset.settings(), "CRR", "VALUE"))).upper()
    bound_results = result_binding(dataset, options)
    result_columns = list(bound_results.columns)
    date_col = _resolve(dataset, str(ci_get(options, "DATE", ""))) or _first(dataset, "RESULT_TIME", "DATE", "DATETIME", "RECEIVED_AT")
    test_col = _first(dataset, "ITEM", "TESTNAME")
    period = str(ci_get(options, "PERIOD", "M")).strip().upper()  # D/W/M/Q/Y
    window = int(ci_get(options, "WINDOW", 3))
    if window < 1:
        raise ValueError("WINDOW must be positive")
    group_ids = ci_get(options, "GROUP_BY_IDS", [])
    if not isinstance(group_ids, list) or len(set(group_ids)) != len(group_ids):
        raise ValueError("GROUP_BY_IDS must be a distinct list")
    from tametools.analysis_contract import resolve_id
    group_columns = [resolve_id(dataset, identifier).name for identifier in group_ids]

    warnings: list[str] = []
    if not result_columns:
        warnings.append("Missing RESULT/NUM column.")
    if date_col is None:
        warnings.append("Missing DATE/DATETIME column.")
    if warnings:
        return _result_output(pd.DataFrame(), step_name, "RESULT_TREND", period, options, warnings)

    rows: list[dict[str, Any]] = []
    include_source = len(result_columns) > 1
    for result in result_columns:
        work = pd.DataFrame({
            "_value": numeric_series_for(dataset, result, crr_policy=comparator_policy).values,
            "_date": pd.to_datetime(dataset.df[date_col.name], errors="coerce").values,
            "_test": dataset.df[test_col.name].astype(str).values if test_col is not None else result.name,
        }).dropna(subset=["_value", "_date"])
        if work.empty:
            continue

        for index, column in enumerate(group_columns):
            work[f"_group_{index}"] = dataset.df[column].reset_index(drop=True).loc[work.index]
        work["_period"] = work["_date"].dt.to_period(period).dt.start_time
        group_keys = ["_test", *[f"_group_{i}" for i in range(len(group_columns))]]
        labels_seen = set()
        for group_number, (keys, group) in enumerate(work.groupby(group_keys, observed=False, dropna=False), 1):
            keys = keys if isinstance(keys, tuple) else (keys,)
            identity = {"group_"+identifier: value for identifier, value in zip(group_ids, keys[1:])}
            label = " / ".join(str(value) for key,value in identity.items() if key != 'group_baseline_key')
            test_name = str(keys[0]) + (" / "+label if label else "")
            if test_name in labels_seen:
                test_name += f" [group {group_number}]"
            labels_seen.add(test_name)
            agg = group.groupby("_period", observed=False)["_value"].agg(["count", "mean", "median"]).reset_index().sort_values("_period")
            agg["moving_avg"] = agg["mean"].rolling(window=window, min_periods=1).mean()
            for _, r in agg.iterrows():
                rows.append({**result_source_record(result, include=include_source), **identity,
                             "test": test_name, "period": r["_period"].strftime("%Y-%m-%d"),
                             "count": int(r["count"]), "mean": round(float(r["mean"]), 4),
                             "median": round(float(r["median"]), 4), "moving_avg": round(float(r["moving_avg"]), 4)})
    if not rows:
        warnings.append("No rows with both a numeric result and a parseable date.")
    return _result_output(pd.DataFrame(rows), step_name, "RESULT_TREND", period, options, warnings)

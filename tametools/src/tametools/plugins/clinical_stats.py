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

from typing import Any

import numpy as np
import pandas as pd

from tametools.analysis import numeric_series_for
from tametools.config import ci_get
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
    meta = {
        "INFO": {"DESCRIPTION": f"{plugin} output: {mode}"},
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
        if mode in {"LEVEY_JENNINGS", "WESTGARD"} and {"point", "z"} <= columns:
            return [chart_spec("LEVEY_JENNINGS_Z", type="line", title="Levey-Jennings z-score", x="point", y="z", series="level" if "level" in columns else "")]
        if {"level", "cv_percent"} <= columns:
            return [chart_spec("QC_CV_PERCENT", type="bar", title="QC CV%", x="level", y="cv_percent")]
    if plugin == "ROC_ANALYSIS":
        if {"specificity", "sensitivity"} <= columns and mode == "CURVE":
            return [chart_spec("ROC_CURVE", type="line", title="ROC curve", x="specificity", y="sensitivity", series="score" if "score" in columns else "")]
        if {"score", "auc"} <= columns:
            return [chart_spec("ROC_AUC", type="bar", title="ROC AUC", x="score", y="auc")]
    if plugin == "RESULT_TREND" and {"period", "mean"} <= columns:
        return [chart_spec("RESULT_TREND_MEAN", type="line", title="Result trend mean", x="period", y="mean", series="test" if "test" in columns else "")]
    return []


_NUMERIC_NAMES = {
    "n", "n_a", "n_b", "n1", "n2", "count", "r", "slope", "intercept", "bias", "mean", "median",
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
    "threshold": ("THRESHOLD", "NUM"),
    "sensitivity": ("SENSITIVITY", "NUM"),
    "specificity": ("SPECIFICITY", "NUM"),
    "youden": ("YOUDEN", "NUM"),
}


def _header(name: str) -> str:
    if name in _OUTPUT_TAGS:
        return f"[[{'::'.join(_OUTPUT_TAGS[name])}]]{name}"
    if name in _NUMERIC_NAMES:
        return f"[[NUM]]{name}"
    return f"[[CATEGORY]]{name}"


# --------------------------------------------------------------------------- METHOD_COMPARISON

@register_plugin("METHOD_COMPARISON", description="Passing-Bablok + Deming regression and Bland-Altman between two methods.", roles=(RESULT_ROLE,))
def method_comparison_plugin(dataset: TameDataset, meta: dict, step_name: str, options: dict) -> OperationOutput:
    comparator_policy = str(ci_get(options, "COMPARATOR_POLICY", ci_get(dataset.settings(), "CRR", "VALUE"))).upper()
    bound_results = result_binding(dataset, options)
    result_columns = list(bound_results.columns)
    method_col = _resolve(dataset, str(ci_get(options, "METHOD_COLUMN", ""))) or _first(dataset, "INSTRUMENT")
    test_col = _first(dataset, "ITEM", "TESTNAME")
    keys = _string_list(ci_get(options, "KEY", [])) or _default_keys(dataset)
    keys = list(dict.fromkeys(keys))  # 중복 제거(같은 키 열 중복 시 pivot/merge index 가 깨진다 — pandas 2.x).

    warnings: list[str] = []
    if not result_columns:
        warnings.append("Missing RESULT/NUM column.")
    if method_col is None:
        warnings.append("Missing METHOD_COLUMN / INSTRUMENT column to define two methods.")
    if warnings:
        return _result_output(pd.DataFrame(), step_name, "METHOD_COMPARISON", "REGRESSION", options, warnings)

    rows: list[dict[str, Any]] = []
    include_source = len(result_columns) > 1
    for result in result_columns:
        work = pd.DataFrame({
            # pandas 2.x 의 pivot_table(aggfunc="mean") 은 object dtype 을 거부하므로 float 로 강제한다.
            "_value": pd.to_numeric(
                numeric_series_for(dataset, result, crr_policy=comparator_policy), errors="coerce"
            ).values,
            "_method": dataset.df[method_col.name].astype(str).values,
        })
        for key in keys:
            col = _resolve(dataset, key)
            if col is not None:
                work[key] = dataset.df[col.name].astype(str).values
        if test_col is not None:
            work["_test"] = dataset.df[test_col.name].astype(str).values
        else:
            work["_test"] = result.name

        methods = [m for m in pd.unique(work["_method"]) if m and m.lower() != "nan"]
        if len(methods) < 2:
            warnings.append(f"Need exactly two methods for {result.name}; found {len(methods)}.")
            continue
        method_a, method_b = methods[0], methods[1]
        pair_keys = [k for k in keys if k in work.columns] + ["_test"]

        for test_name, group in work.groupby("_test", observed=False):
            wide = group.pivot_table(index=[k for k in pair_keys if k != "_test"] or None,
                                     columns="_method", values="_value", aggfunc="mean")
            if method_a not in wide.columns or method_b not in wide.columns:
                continue
            paired = wide[[method_a, method_b]].dropna()
            if len(paired) < 3:
                continue
            x = paired[method_a].to_numpy(dtype=float)
            y = paired[method_b].to_numpy(dtype=float)
            pb_slope, pb_intercept = _passing_bablok(x, y)
            dm_slope, dm_intercept = _deming(x, y)
            diff = y - x
            bias = float(np.mean(diff))
            sd_diff = float(np.std(diff, ddof=1)) if len(diff) > 1 else 0.0
            r = float(np.corrcoef(x, y)[0, 1]) if len(x) > 1 else float("nan")
            rows.append({
                **result_source_record(result, include=include_source),
                "test": test_name, "method_a": method_a, "method_b": method_b, "n": len(paired),
                "r": round(r, 4),
                "pb_slope": round(pb_slope, 4), "pb_intercept": round(pb_intercept, 4),
                "deming_slope": round(dm_slope, 4), "deming_intercept": round(dm_intercept, 4),
                "bias": round(bias, 4), "sd_diff": round(sd_diff, 4),
                "lower_loa": round(bias - 1.96 * sd_diff, 4), "upper_loa": round(bias + 1.96 * sd_diff, 4),
            })
    table = pd.DataFrame(rows)
    if table.empty:
        warnings.append("No test had >=3 paired measurements across the two methods.")
    return _result_output(table, step_name, "METHOD_COMPARISON", "REGRESSION", options, warnings)


def _passing_bablok(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    n = len(x)
    slopes: list[float] = []
    for i in range(n):
        for j in range(i + 1, n):
            dx = x[j] - x[i]
            dy = y[j] - y[i]
            if dx == 0:
                continue
            s = dy / dx
            if s == -1:
                continue
            slopes.append(s)
    if not slopes:
        return float("nan"), float("nan")
    slopes_sorted = sorted(slopes)
    # Shift offset K = number of slopes < -1 (Passing-Bablok correction).
    k = sum(1 for s in slopes_sorted if s < -1)
    m = len(slopes_sorted)
    idx = (m + 1) / 2 + k
    if m % 2 == 1:
        slope = slopes_sorted[int((m + 1) / 2 - 1 + k)] if 0 <= int((m + 1) / 2 - 1 + k) < m else float(np.median(slopes_sorted))
    else:
        lo = int(m / 2 - 1 + k); hi = int(m / 2 + k)
        if 0 <= lo < m and 0 <= hi < m:
            slope = (slopes_sorted[lo] + slopes_sorted[hi]) / 2
        else:
            slope = float(np.median(slopes_sorted))
    intercept = float(np.median(y - slope * x))
    return float(slope), intercept


def _deming(x: np.ndarray, y: np.ndarray, lam: float = 1.0) -> tuple[float, float]:
    mx, my = float(np.mean(x)), float(np.mean(y))
    sxx = float(np.sum((x - mx) ** 2))
    syy = float(np.sum((y - my) ** 2))
    sxy = float(np.sum((x - mx) * (y - my)))
    if sxy == 0:
        return float("nan"), float("nan")
    slope = (syy - lam * sxx + np.sqrt((syy - lam * sxx) ** 2 + 4 * lam * sxy ** 2)) / (2 * sxy)
    intercept = my - slope * mx
    return float(slope), float(intercept)


def _default_keys(dataset: TameDataset) -> list[str]:
    keys: list[str] = []
    pid = _first(dataset, "ID(patient)", "PATIENT_ID", "ID")
    if pid is not None:
        keys.append(pid.name)
    sample = _first(dataset, "ID(sample)", "SAMPLE_ID", "ID(specimen)", "SPECIMEN_ID", "SAMPLE", "SPECIMEN")
    if sample is not None:
        keys.append(sample.name)
    return keys


# --------------------------------------------------------------------------- GROUP_TEST

@register_plugin("GROUP_TEST", description="Compare a numeric result across groups (t-test / Mann-Whitney / Kruskal-Wallis).", roles=(RESULT_ROLE,))
def group_test_plugin(dataset: TameDataset, meta: dict, step_name: str, options: dict) -> OperationOutput:
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
    level_col = _resolve(dataset, str(ci_get(options, "LEVEL", ""))) or _first(dataset, "ITEM", "TESTNAME", "QC_LEVEL")

    warnings: list[str] = []
    if not result_columns:
        warnings.append("Missing RESULT/NUM column.")
        return _result_output(pd.DataFrame(), step_name, "QC_ANALYSIS", mode, options, warnings)

    tea_map = ci_get(options, "TEA", {})  # allowable total error % per level (optional)
    include_source = len(result_columns) > 1

    if mode in {"PRECISION", "SIGMA"}:
        rows: list[dict[str, Any]] = []
        for result in result_columns:
            values = numeric_series_for(dataset, result, crr_policy=comparator_policy)
            groups = dataset.df[level_col.name].astype(str).values if level_col is not None else np.array([result.name] * len(values))
            work = pd.DataFrame({"_value": values.values, "_level": groups}).dropna(subset=["_value"])
            for level, group in work.groupby("_level", observed=False):
                v = group["_value"].to_numpy(dtype=float)
                mean = float(np.mean(v)); sd = float(np.std(v, ddof=1)) if len(v) > 1 else 0.0
                cv = 100.0 * sd / mean if mean else float("nan")
                row = {**result_source_record(result, include=include_source),
                       "level": level, "n": len(v), "mean": round(mean, 4), "sd": round(sd, 4),
                       "cv_percent": round(cv, 3) if cv == cv else float("nan")}
                tea = _lookup_number(tea_map, level)
                if tea is not None and cv:
                    bias = abs(_lookup_number(ci_get(options, "BIAS", {}), level) or 0.0)
                    row["sigma"] = round((tea - bias) / cv, 2)
                rows.append(row)
        return _result_output(pd.DataFrame(rows), step_name, "QC_ANALYSIS", mode, options, warnings)

    if mode in {"LEVEY_JENNINGS", "WESTGARD"}:
        rows = []
        for result in result_columns:
            values = numeric_series_for(dataset, result, crr_policy=comparator_policy)
            groups = dataset.df[level_col.name].astype(str).values if level_col is not None else np.array([result.name] * len(values))
            work = pd.DataFrame({"_value": values.values, "_level": groups}).dropna(subset=["_value"])
            for level, group in work.groupby("_level", observed=False):
                v = group["_value"].to_numpy(dtype=float)
                mean = float(np.mean(v)); sd = float(np.std(v, ddof=1)) if len(v) > 1 else 0.0
                z = (v - mean) / sd if sd else np.zeros_like(v)
                for index, (value, zz) in enumerate(zip(v, z), start=1):
                    violations = _westgard_flags(z, index - 1)
                    rows.append({**result_source_record(result, include=include_source),
                                 "level": level, "point": index, "value": round(float(value), 4),
                                 "mean": round(mean, 4), "sd": round(sd, 4), "z": round(float(zz), 3),
                                 "violation": ", ".join(violations) if violations else "NONE"})
        return _result_output(pd.DataFrame(rows), step_name, "QC_ANALYSIS", mode, options, warnings)

    warnings.append(f"Unsupported QC mode: {mode}")
    return _result_output(pd.DataFrame(), step_name, "QC_ANALYSIS", mode, options, warnings)


def _westgard_flags(z: np.ndarray, i: int) -> list[str]:
    flags: list[str] = []
    if abs(z[i]) > 3:
        flags.append("1_3s")
    if i >= 1 and z[i] > 2 and z[i - 1] > 2:
        flags.append("2_2s")
    if i >= 1 and z[i] < -2 and z[i - 1] < -2:
        flags.append("2_2s")
    if i >= 1 and abs(z[i] - z[i - 1]) > 4:
        flags.append("R_4s")
    return flags


def _lookup_number(mapping: Any, key: str) -> float | None:
    if isinstance(mapping, dict):
        for mk, mv in mapping.items():
            if str(mk) == str(key):
                try:
                    return float(mv)
                except (TypeError, ValueError):
                    return None
    return None


# --------------------------------------------------------------------------- ROC_ANALYSIS

@register_plugin("ROC_ANALYSIS", description="ROC / sensitivity-specificity of a numeric score against a binary label.", roles=(RESULT_ROLE,))
def roc_analysis_plugin(dataset: TameDataset, meta: dict, step_name: str, options: dict) -> OperationOutput:
    comparator_policy = str(ci_get(options, "COMPARATOR_POLICY", ci_get(dataset.settings(), "CRR", "VALUE"))).upper()
    mode = str(ci_get(options, "MODE", "SUMMARY")).strip().upper()
    bound_scores = result_binding(dataset, options)
    score_columns = list(bound_scores.columns)
    label_col = _resolve(dataset, str(ci_get(options, "LABEL", ""))) or _first(dataset, "LABEL", "OUTCOME", "CLASS")
    positive = str(ci_get(options, "POSITIVE", "")).strip()
    higher_positive = str(ci_get(options, "DIRECTION", "higher")).strip().lower() != "lower"

    warnings: list[str] = []
    if not score_columns:
        warnings.append("Missing SCORE/RESULT numeric column.")
    if label_col is None:
        warnings.append("Missing LABEL/OUTCOME column.")
    if warnings:
        return _result_output(pd.DataFrame(), step_name, "ROC_ANALYSIS", mode, options, warnings)

    label_raw = dataset.df[label_col.name].astype(str)
    if not positive:
        uniques = [u for u in pd.unique(label_raw) if u and u.lower() != "nan"]
        positive = _infer_positive(uniques)
    y = (label_raw == positive).astype(int)

    rows: list[dict[str, Any]] = []
    if mode == "CURVE":
        include_score = len(score_columns) > 1
        for score_col in score_columns:
            score = numeric_series_for(dataset, score_col, crr_policy=comparator_policy)
            work = pd.DataFrame({"_score": score.values, "_y": y.values}).dropna(subset=["_score"])
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
                    "threshold": round(float(thr), 4),
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
            work = pd.DataFrame({"_score": score.values, "_y": y.values}).dropna(subset=["_score"])
            if work["_y"].sum() == 0 or work["_y"].sum() == len(work):
                warnings.append(f"Label has only one class for score {score_col.name} after filtering; cannot compute ROC.")
                continue
            s = work["_score"].to_numpy(dtype=float)
            if not higher_positive:
                s = -s
            yv = work["_y"].to_numpy(dtype=int)
            curve = _roc_curve(s, yv)
            auc = _auc(curve)
            best = max(curve, key=lambda p: p["sensitivity"] + p["specificity"] - 1)
            best_cut = best["threshold"] if higher_positive else -best["threshold"]
            rows.append({
                "score": score_col.name, "label": label_col.name, "positive": positive,
                "n_pos": int(yv.sum()), "n_neg": int(len(yv) - yv.sum()),
                "auc": round(auc, 4), "best_cutoff": round(float(best_cut), 4),
                "sensitivity": round(best["sensitivity"], 4), "specificity": round(best["specificity"], 4),
                "youden": round(best["sensitivity"] + best["specificity"] - 1, 4),
            })
        table = pd.DataFrame(rows)
    return _result_output(table, step_name, "ROC_ANALYSIS", mode, options, warnings)


def _infer_positive(uniques: list[str]) -> str:
    for candidate in ("1", "positive", "pos", "true", "yes", "abnormal", "disease", "H"):
        for u in uniques:
            if u.lower() == candidate:
                return u
    return uniques[-1] if uniques else "1"


def _roc_curve(score: np.ndarray, y: np.ndarray) -> list[dict[str, float]]:
    pos = int(y.sum()); neg = int(len(y) - pos)
    thresholds = np.unique(score)
    points = []
    for thr in np.concatenate(([thresholds[0] - 1], thresholds)):
        predicted = score >= thr
        tp = int(np.sum(predicted & (y == 1)))
        fp = int(np.sum(predicted & (y == 0)))
        sensitivity = tp / pos if pos else 0.0
        specificity = 1 - (fp / neg) if neg else 0.0
        points.append({"threshold": float(thr), "sensitivity": sensitivity, "specificity": specificity})
    return points


def _auc(curve: list[dict[str, float]]) -> float:
    pts = sorted(((1 - p["specificity"], p["sensitivity"]) for p in curve), key=lambda t: (t[0], t[1]))
    area = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        area += (x1 - x0) * (y0 + y1) / 2
    return float(area)


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

        work["_period"] = work["_date"].dt.to_period(period).dt.start_time
        for test_name, group in work.groupby("_test", observed=False):
            agg = group.groupby("_period", observed=False)["_value"].agg(["count", "mean", "median"]).reset_index().sort_values("_period")
            agg["moving_avg"] = agg["mean"].rolling(window=window, min_periods=1).mean()
            for _, r in agg.iterrows():
                rows.append({**result_source_record(result, include=include_source),
                             "test": test_name, "period": r["_period"].strftime("%Y-%m-%d"),
                             "count": int(r["count"]), "mean": round(float(r["mean"]), 4),
                             "median": round(float(r["median"]), 4), "moving_avg": round(float(r["moving_avg"]), 4)})
    if not rows:
        warnings.append("No rows with both a numeric result and a parseable date.")
    return _result_output(pd.DataFrame(rows), step_name, "RESULT_TREND", period, options, warnings)

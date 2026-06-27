from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
from importlib import metadata as importlib_metadata
import math
import os
from pathlib import Path
from statistics import NormalDist
import tempfile
from typing import Any
import warnings as py_warnings

import numpy as np
import pandas as pd

from tametools.age import age_band_bounds, age_band_label, age_bin_width_for_column
from tametools.analysis import age_series_for, numeric_series_for
from tametools.config import ci_get
from tametools.models import ColumnSpec, OperationOutput, TameDataset, merged_column_specs
from tametools.reporting import chart_spec, render_chart_png, with_visualizations
from tametools.sex import normalize_sex
from tametools.plugin_base.base import register_plugin
from tametools.plugin_base.roles import RESULT_ROLE


@dataclass(frozen=True)
class GroupContext:
    test_column: ColumnSpec | None
    sex_column: ColumnSpec | None
    age_column: ColumnSpec | None


@dataclass(frozen=True)
class GroupLevel:
    name: str
    use_sex: bool = False
    use_age: bool = False


@dataclass(frozen=True)
class ReferenceIntervalOptions:
    comparator_policy: str
    method: str
    ci_method: str
    lower_q: float
    upper_q: float
    ci_level: float
    min_n: int
    age_bin_width: int
    outlier_method: str
    outlier_warn_rate: float
    bootstrap_n: int
    seed: int
    skew_warn: float
    clamp_nonnegative: bool
    verify: dict
    verify_low: float | None
    verify_high: float | None
    verify_max_outside: float
    decimals: int | None
    quantile_method: str
    ci_width_max_frac: float
    method_comparison_bootstrap_n: int
    normality_alpha: float
    group_diff_alpha: float
    histogram_max_groups: int


@dataclass(frozen=True)
class GroupAnalysis:
    test_name: Any
    unit: str
    partition: str
    group_level: str
    sex_group: Any
    age_group: Any
    age_low: float | None
    age_high: float | None
    source_result_column: str
    values: tuple[float, ...]


_NORMAL = NormalDist()
_METHODS = {"NONPARAMETRIC", "PARAMETRIC", "LOG_PARAMETRIC", "ROBUST", "BIWEIGHT"}
_BIWEIGHT_C_LOCATION = 6.0
_BIWEIGHT_C_SCALE = 9.0
_CI_METHODS = {"BOOTSTRAP", "RANK"}
_OUTLIER_METHODS = {"NONE", "TUKEY", "LOG_TUKEY", "DIXON"}
_QUANTILE_METHODS = {"LINEAR", "CLSI"}
_MAD_TO_SD = 1.4826
_IQR_TO_SD = 1.0 / 1.349


GROUP_LEVELS = (
    GroupLevel("TESTNAME"),
    GroupLevel("TESTNAME+SEX", use_sex=True),
    GroupLevel("TESTNAME+AGE", use_age=True),
    GroupLevel("TESTNAME+SEX+AGE", use_sex=True, use_age=True),
)


@register_plugin(
    "REFERENCE_INTERVAL",
    description="Compute clinical reference intervals with reliability summary, charts, and optional docx report.",
    roles=(RESULT_ROLE,),
)
@register_plugin(
    "RI",
    description="Alias of the reference interval plugin.",
    roles=(RESULT_ROLE,),
)
def reference_interval_plugin(dataset: TameDataset, meta: dict, step_name: str, options: dict) -> OperationOutput:
    ri_options = _parse_options(dataset, options)
    result_columns = _result_columns(dataset)
    context = GroupContext(
        test_column=_testname_column(dataset),
        sex_column=dataset.first_column_with_tag("SEX"),
        age_column=dataset.first_column_with_tag("AGE"),
    )
    if context.age_column is not None:
        ri_options = ReferenceIntervalOptions(
            **{
                **ri_options.__dict__,
                "age_bin_width": age_bin_width_for_column(dataset, context.age_column, default=ri_options.age_bin_width),
            }
        )

    warnings = _input_warnings(result_columns, context)
    interval_rows: list[dict[str, Any]] = []
    outlier_rows: list[dict[str, Any]] = []
    partition_rows: list[dict[str, Any]] = []
    group_analyses: list[GroupAnalysis] = []

    for result_column in result_columns:
        work = _prepare_work_frame(
            dataset,
            result_column,
            context,
            comparator_policy=ri_options.comparator_policy,
            age_bin_width=ri_options.age_bin_width,
        )
        if work.empty:
            continue
        partition_rows.extend(_partition_tests(work, result_column, ri_options))
        for level in _group_levels_for(context):
            rows, outliers, analyses, group_warnings = _summarize_group_level(
                work,
                dataset,
                result_column,
                level,
                options=ri_options,
            )
            interval_rows.extend(rows)
            outlier_rows.extend(outliers)
            group_analyses.extend(analyses)
            warnings.extend(group_warnings)

    result_dataset = _build_result_dataset(
        interval_rows,
        source_dataset=dataset,
        options=ri_options,
        warnings=warnings,
    )
    main_table = result_dataset.df
    summary_table = _summary_by_test(main_table)
    reliability_table = _reliability_summary(main_table)
    outlier_table = _outlier_table(outlier_rows)
    limits_table = _limits_long_table(main_table)
    normality_table = _normality_table(group_analyses, ri_options)
    method_comparison_table = _method_comparison_table(group_analyses, ri_options)
    group_difference_table = _group_difference_table(group_analyses, ri_options)
    charts = _chart_specs(main_table)
    result_dataset = with_visualizations(result_dataset, charts)

    partition_table = _partition_tests_table(partition_rows)
    tables = {
        "reference_intervals": main_table,
        "summary_by_test": summary_table,
        "reliability_summary": reliability_table,
        "outliers": outlier_table,
        "limits_long": limits_table,
        "partition_tests": partition_table,
        "normality": normality_table,
        "method_comparison": method_comparison_table,
        "group_difference_tests": group_difference_table,
    }
    files: list[str] = []
    report_path = str(ci_get(options, "REPORT_PATH", "")).strip()
    if report_path:
        written = _write_reference_interval_report(
            Path(report_path),
            main_table=main_table,
            summary_table=summary_table,
            reliability_table=reliability_table,
            outlier_table=outlier_table,
            partition_table=partition_table,
            normality_table=normality_table,
            method_comparison_table=method_comparison_table,
            group_difference_table=group_difference_table,
            group_analyses=group_analyses,
            source_path=dataset.source_path or "",
            source_rows=len(dataset.df),
            warnings=warnings,
            options=ri_options,
            max_table_rows=int(ci_get(options, "REPORT_MAX_ROWS", 1000000)),
        )
        files.append(str(written))

    message = (
        f"reference_interval rows={len(main_table)} result_columns={len(result_columns)} "
        f"ok={_reliability_count(main_table, 'ok')} report={report_path or '<none>'}"
    )
    return OperationOutput(
        name=step_name,
        dataset=result_dataset,
        table=main_table,
        tables=tables,
        charts=charts,
        files=files,
        warnings=warnings,
        message=message,
    )


def _parse_options(dataset: TameDataset, options: dict) -> ReferenceIntervalOptions:
    comparator_policy = str(ci_get(options, "COMPARATOR_POLICY", ci_get(dataset.settings(), "CRR", "VALUE"))).upper()
    method = str(ci_get(options, "METHOD", "NONPARAMETRIC")).strip().upper()
    ci_method = str(ci_get(options, "CI_METHOD", "BOOTSTRAP")).strip().upper()
    lower_q = float(ci_get(options, "LOW_Q", 0.025))
    upper_q = float(ci_get(options, "HIGH_Q", 0.975))
    ci_level = float(ci_get(options, "CI_LEVEL", 0.90))
    min_n = int(ci_get(options, "MIN_N", 120))
    age_bin_width = int(ci_get(options, "AGE_BIN_WIDTH", 10))
    outlier_method = str(ci_get(options, "OUTLIER_METHOD", "NONE")).strip().upper()
    outlier_warn_rate = float(ci_get(options, "OUTLIER_WARN_RATE", ci_get(options, "OUTLIER_WARNING_RATE", 0.02)))
    bootstrap_n = int(ci_get(options, "BOOTSTRAP_N", 500))
    seed = int(ci_get(options, "SEED", 20260625))
    skew_warn = float(ci_get(options, "SKEW_WARN", 1.0))
    clamp_nonnegative = _as_bool(ci_get(options, "CLAMP_NONNEGATIVE", True))
    verify = _parse_verify(ci_get(options, "VERIFY", {}))
    verify_low = _opt_float(ci_get(options, "VERIFY_LOW", None))
    verify_high = _opt_float(ci_get(options, "VERIFY_HIGH", None))
    verify_max_outside = float(ci_get(options, "VERIFY_MAX_OUTSIDE", 0.10))
    decimals_raw = ci_get(options, "DECIMALS", None)
    decimals = None if decimals_raw is None or (isinstance(decimals_raw, str) and not decimals_raw.strip()) else int(decimals_raw)
    quantile_method = str(ci_get(options, "QUANTILE_METHOD", "LINEAR")).strip().upper()
    if quantile_method == "WEIBULL":
        quantile_method = "CLSI"
    ci_width_max_frac = float(ci_get(options, "CI_WIDTH_MAX_FRAC", 0.25))
    method_comparison_bootstrap_n = int(
        ci_get(options, "METHOD_COMPARISON_BOOTSTRAP_N", ci_get(options, "REPORT_BOOTSTRAP_N", max(bootstrap_n, 200)))
    )
    normality_alpha = float(ci_get(options, "NORMALITY_ALPHA", 0.05))
    group_diff_alpha = float(ci_get(options, "GROUP_DIFF_ALPHA", 0.05))
    histogram_max_groups = int(ci_get(options, "HISTOGRAM_MAX_GROUPS", 80))
    if quantile_method not in _QUANTILE_METHODS:
        raise ValueError("QUANTILE_METHOD must be one of: LINEAR, CLSI.")
    if ci_width_max_frac <= 0:
        raise ValueError("CI_WIDTH_MAX_FRAC must be positive.")
    if method_comparison_bootstrap_n < 0:
        raise ValueError("METHOD_COMPARISON_BOOTSTRAP_N must be zero or positive.")
    if not 0 < normality_alpha < 1:
        raise ValueError("NORMALITY_ALPHA must satisfy 0 < NORMALITY_ALPHA < 1.")
    if not 0 < group_diff_alpha < 1:
        raise ValueError("GROUP_DIFF_ALPHA must satisfy 0 < GROUP_DIFF_ALPHA < 1.")
    if histogram_max_groups < 0:
        raise ValueError("HISTOGRAM_MAX_GROUPS must be zero or positive.")
    if verify_low is not None and verify_high is not None and not verify_low < verify_high:
        raise ValueError("VERIFY_LOW must be less than VERIFY_HIGH.")
    if not 0 <= verify_max_outside <= 1:
        raise ValueError("VERIFY_MAX_OUTSIDE must satisfy 0 <= VERIFY_MAX_OUTSIDE <= 1.")
    if method not in _METHODS:
        raise ValueError("METHOD must be one of: NONPARAMETRIC, PARAMETRIC, LOG_PARAMETRIC, ROBUST.")
    if ci_method not in _CI_METHODS:
        raise ValueError("CI_METHOD must be one of: BOOTSTRAP, RANK.")
    if ci_method == "RANK" and method != "NONPARAMETRIC":
        raise ValueError("CI_METHOD=RANK is currently supported only with METHOD=NONPARAMETRIC.")
    if not 0 < lower_q < upper_q < 1:
        raise ValueError("LOW_Q and HIGH_Q must satisfy 0 < LOW_Q < HIGH_Q < 1.")
    if not 0 < ci_level < 1:
        raise ValueError("CI_LEVEL must satisfy 0 < CI_LEVEL < 1.")
    if min_n < 1:
        raise ValueError("MIN_N must be positive.")
    if age_bin_width < 1:
        raise ValueError("AGE_BIN_WIDTH must be positive.")
    if outlier_method not in _OUTLIER_METHODS:
        raise ValueError("OUTLIER_METHOD must be one of: NONE, TUKEY, LOG_TUKEY.")
    if not 0 <= outlier_warn_rate <= 1:
        raise ValueError("OUTLIER_WARN_RATE must satisfy 0 <= OUTLIER_WARN_RATE <= 1.")
    if bootstrap_n < 0:
        raise ValueError("BOOTSTRAP_N must be zero or positive.")
    return ReferenceIntervalOptions(
        comparator_policy=comparator_policy,
        method=method,
        ci_method=ci_method,
        lower_q=lower_q,
        upper_q=upper_q,
        ci_level=ci_level,
        min_n=min_n,
        age_bin_width=age_bin_width,
        outlier_method=outlier_method,
        outlier_warn_rate=outlier_warn_rate,
        bootstrap_n=bootstrap_n,
        seed=seed,
        skew_warn=skew_warn,
        clamp_nonnegative=clamp_nonnegative,
        verify=verify,
        verify_low=verify_low,
        verify_high=verify_high,
        verify_max_outside=verify_max_outside,
        decimals=decimals,
        quantile_method=quantile_method,
        ci_width_max_frac=ci_width_max_frac,
        method_comparison_bootstrap_n=method_comparison_bootstrap_n,
        normality_alpha=normality_alpha,
        group_diff_alpha=group_diff_alpha,
        histogram_max_groups=histogram_max_groups,
    )


def _np_quantile(arr: np.ndarray, q: float, quantile_method: str) -> float:
    """비모수 분위수. LINEAR=numpy/pandas 기본, CLSI=Weibull(rank=q·(n+1), CLSI EP28 순위규약)."""
    method = "weibull" if quantile_method == "CLSI" else "linear"
    return float(np.quantile(arr, q, method=method))


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _opt_float(value: Any) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return float(value)


def _parse_verify(raw: Any) -> dict:
    """검증용 후보 참고구간 매핑. {TESTNAME: [low, high]} 형태(META/ API)만 해석한다."""
    if not isinstance(raw, dict):
        return {}
    parsed: dict[str, tuple[float, float]] = {}
    for key, value in raw.items():
        if isinstance(value, (list, tuple)) and len(value) == 2:
            try:
                low, high = float(value[0]), float(value[1])
            except (TypeError, ValueError):
                continue
            if low < high:
                parsed[str(key)] = (low, high)
    return parsed


def _skewness(values: np.ndarray) -> float:
    if values.size < 3:
        return 0.0
    mean = float(np.mean(values))
    sd = float(np.std(values, ddof=0))
    if sd <= 0:
        return 0.0
    return float(np.mean(((values - mean) / sd) ** 3))


def _input_warnings(result_columns: list[ColumnSpec], context: GroupContext) -> list[str]:
    warnings: list[str] = []
    if not result_columns:
        warnings.append("Missing RESULT::NUM or RESULT::<NUM> column. No reference interval rows were produced.")
    if context.test_column is None:
        warnings.append("Missing TESTNAME/ITEM tag. Reference intervals will be calculated per result column.")
    if context.sex_column is None:
        warnings.append("Missing SEX tag. No sex-specific reference interval groups will be calculated.")
    if context.age_column is None:
        warnings.append("Missing AGE tag. No age-specific reference interval groups will be calculated.")
    return warnings


def _result_columns(dataset: TameDataset) -> list[ColumnSpec]:
    return [
        column
        for column in dataset.columns_with_tag("RESULT")
        if dataset.column_has_tag(column, "NUM") or dataset.column_has_tag(column, "<NUM>")
    ]


def _testname_column(dataset: TameDataset) -> ColumnSpec | None:
    return dataset.first_column_with_tag("TESTNAME") or dataset.first_column_with_tag("ITEM")


def _group_levels_for(context: GroupContext) -> list[GroupLevel]:
    levels = [GROUP_LEVELS[0]]
    if context.sex_column is not None:
        levels.append(GROUP_LEVELS[1])
    if context.age_column is not None:
        levels.append(GROUP_LEVELS[2])
    if context.sex_column is not None and context.age_column is not None:
        levels.append(GROUP_LEVELS[3])
    return levels


def _prepare_work_frame(
    dataset: TameDataset,
    result_column: ColumnSpec,
    context: GroupContext,
    *,
    comparator_policy: str,
    age_bin_width: int,
) -> pd.DataFrame:
    work = dataset.df.copy()
    work["_source_row"] = list(range(2, len(dataset.df) + 2))
    work["_result_value"] = numeric_series_for(dataset, result_column, crr_policy=comparator_policy)
    work = work.loc[work["_result_value"].notna()].copy()
    if work.empty:
        return work

    if context.test_column is not None:
        work["_test_name"] = dataset.df[context.test_column.name].reindex(work.index).map(
            lambda value: _text_or_default(value, result_column.name)
        )
    else:
        work["_test_name"] = result_column.name

    if context.sex_column is not None:
        work["_sex_group"] = dataset.df[context.sex_column.name].reindex(work.index).map(normalize_sex)
    else:
        work["_sex_group"] = "ALL"

    if context.age_column is not None:
        ages = age_series_for(dataset, context.age_column).reindex(work.index)
        work["_age_group"] = ages.map(lambda value: age_band_label(value, width=age_bin_width))
        bounds = work["_age_group"].map(age_band_bounds)
        work["_age_low"] = bounds.map(lambda item: item[0] if item else None)
        work["_age_high"] = bounds.map(lambda item: item[1] if item else None)
    else:
        work["_age_group"] = "ALL"
        work["_age_low"] = None
        work["_age_high"] = None

    return work


def _summarize_group_level(
    work: pd.DataFrame,
    dataset: TameDataset,
    result_column: ColumnSpec,
    level: GroupLevel,
    *,
    options: ReferenceIntervalOptions,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[GroupAnalysis], list[str]]:
    grouped_frame = work.copy()
    group_keys = ["_test_name"]

    if level.use_sex:
        grouped_frame = grouped_frame.loc[grouped_frame["_sex_group"].notna()].copy()
        group_keys.append("_sex_group")
    else:
        grouped_frame["_sex_group"] = "ALL"

    if level.use_age:
        grouped_frame = grouped_frame.loc[grouped_frame["_age_group"].notna()].copy()
        group_keys.append("_age_group")
    else:
        grouped_frame["_age_group"] = "ALL"
        grouped_frame["_age_low"] = None
        grouped_frame["_age_high"] = None

    if grouped_frame.empty:
        return [], [], [], []

    interval_rows: list[dict[str, Any]] = []
    outlier_rows: list[dict[str, Any]] = []
    analyses: list[GroupAnalysis] = []
    warning_rows: list[str] = []
    for group_key, group in grouped_frame.groupby(group_keys, dropna=False, observed=False):
        if not isinstance(group_key, tuple):
            group_key = (group_key,)

        cursor = 0
        test_name = group_key[cursor]
        cursor += 1
        sex_group = group_key[cursor] if level.use_sex else "ALL"
        if level.use_sex:
            cursor += 1
        age_group = group_key[cursor] if level.use_age else "ALL"

        first_row = group.iloc[0]
        raw_values = group["_result_value"].astype(float)
        clean_group, removed = _remove_outliers(group, raw_values, method=options.outlier_method)
        partition = _partition_label(test_name, level.name, sex_group, age_group)
        warning = _outlier_warning(
            partition=partition,
            group_level=level.name,
            method=options.outlier_method,
            original_n=int(raw_values.shape[0]),
            removed_n=int(removed.shape[0]),
            threshold=options.outlier_warn_rate,
        )
        if warning:
            warning_rows.append(warning)
        values = clean_group["_result_value"].astype(float)
        if values.empty:
            continue
        outlier_rows.extend(
            _outlier_rows(
                removed,
                test_name=test_name,
                group_level=level.name,
                sex_group=sex_group,
                age_group=age_group,
                result_column=result_column.name,
                method=options.outlier_method,
            )
        )
        n = int(values.shape[0])
        reliability = _reliability(n, options.min_n)
        effective_method, method_selection_reason = _headline_method(n, options)
        if effective_method is None:
            low = high = float("nan")
            low_ci = high_ci = (None, None)
            method_label = "not_calculated_insufficient_n"
            ci_method_label = "not_calculated"
            ci_adequacy = "not_calculated_insufficient_n"
            warning_rows.append(
                f"Reference interval not calculated for partition={partition!r} group_level={level.name}: "
                f"n={n} is below 40; CLSI EP28-A3c requires n>=40 for robust estimation and n>=120 for nonparametric estimation."
            )
        else:
            low, high, method_label = _reference_limits(values, options, effective_method=effective_method)
            ci_options = _ci_options_for_headline(options, effective_method)
            low_ci, high_ci, ci_method_label, ci_warnings = _reference_limit_ci(
                values,
                ci_options,
                effective_method=effective_method,
                partition=partition,
                group_level=level.name,
            )
            warning_rows.extend(ci_warnings)
            adjust_warnings, low, high, low_ci, high_ci = _post_limit_adjust(
                values=values, low=low, high=high, low_ci=low_ci, high_ci=high_ci,
                options=ci_options, effective_method=effective_method,
                partition=partition, group_level=level.name,
            )
            warning_rows.extend(adjust_warnings)
            ci_adequacy, ci_adequacy_warning = _ci_adequacy(
                low, high, low_ci, high_ci, high - low, options.ci_width_max_frac,
                partition=partition, group_level=level.name,
            )
            if ci_adequacy_warning:
                warning_rows.append(ci_adequacy_warning)
        unit = _unit_for_test(dataset, result_column, test_name)
        verify_fields = _verify_fields(values, test_name, options)
        analyses.append(
            GroupAnalysis(
                test_name=test_name,
                unit=unit,
                partition=partition,
                group_level=level.name,
                sex_group=sex_group,
                age_group=age_group,
                age_low=_float_or_none(first_row.get("_age_low")),
                age_high=_float_or_none(first_row.get("_age_high")),
                source_result_column=result_column.name,
                values=tuple(float(value) for value in values.dropna().to_numpy(dtype=float)),
            )
        )
        interval_rows.append(
            {
                **verify_fields,
                "ci_adequacy": ci_adequacy,
                "test_name": test_name,
                "unit": unit,
                "partition": partition,
                "group_level": level.name,
                "sex_group": sex_group,
                "age_group": age_group,
                "age_low": _float_or_none(first_row.get("_age_low")),
                "age_high": _float_or_none(first_row.get("_age_high")),
                "source_result_column": result_column.name,
                "original_n": int(raw_values.shape[0]),
                "n": n,
                "outliers_removed": int(removed.shape[0]),
                "ref_low": low,
                "ref_high": high,
                "ref_low_ci90_low": low_ci[0],
                "ref_low_ci90_high": low_ci[1],
                "ref_high_ci90_low": high_ci[0],
                "ref_high_ci90_high": high_ci[1],
                "ref_width": high - low if math.isfinite(high) and math.isfinite(low) else None,
                "mean": float(values.mean()),
                "median": float(values.median()),
                "sd": float(values.std(ddof=1)) if values.shape[0] > 1 else 0.0,
                "min": float(values.min()),
                "max": float(values.max()),
                "reliability": reliability,
                "recommendation": _recommendation(reliability),
                "method": method_label,
                "method_selection_reason": method_selection_reason,
                "ci_method": ci_method_label,
                "outlier_method": options.outlier_method,
                "comparator_policy": options.comparator_policy,
            }
        )

    return interval_rows, outlier_rows, analyses, warning_rows


def _remove_outliers(group: pd.DataFrame, values: pd.Series, *, method: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    if method == "NONE" or values.shape[0] < 4:
        return group, group.iloc[0:0].copy()
    if method == "LOG_TUKEY":
        positive = values > 0
        if int(positive.sum()) < 4:
            return group, group.iloc[0:0].copy()
        scale = pd.Series(np.log(values.loc[positive].to_numpy(dtype=float)), index=values.loc[positive].index)
        q1 = float(scale.quantile(0.25))
        q3 = float(scale.quantile(0.75))
        iqr = q3 - q1
        if iqr <= 0:
            return group, group.iloc[0:0].copy()
        low_fence = q1 - 1.5 * iqr
        high_fence = q3 + 1.5 * iqr
        keep = pd.Series(True, index=values.index)
        keep.loc[positive] = scale.between(low_fence, high_fence, inclusive="both")
        return group.loc[keep].copy(), group.loc[~keep].copy()

    if method == "DIXON":
        keep_mask = pd.Series(True, index=values.index)
        for _ in range(values.shape[0]):
            current = values.loc[keep_mask].dropna().astype(float)
            if current.shape[0] < 3:
                break
            ordered = current.sort_values()
            low, low2 = float(ordered.iloc[0]), float(ordered.iloc[1])
            high, high2 = float(ordered.iloc[-1]), float(ordered.iloc[-2])
            span = high - low
            if span <= 0:
                break
            removed_any = False
            if (low2 - low) > span / 3.0:                 # Reed 1/3 rule, lower extreme
                keep_mask.loc[ordered.index[0]] = False
                removed_any = True
            if (high - high2) > span / 3.0:               # Reed 1/3 rule, upper extreme
                keep_mask.loc[ordered.index[-1]] = False
                removed_any = True
            if not removed_any:
                break
        return group.loc[keep_mask].copy(), group.loc[~keep_mask].copy()

    q1 = float(values.quantile(0.25))
    q3 = float(values.quantile(0.75))
    iqr = q3 - q1
    if iqr <= 0:
        return group, group.iloc[0:0].copy()
    low_fence = q1 - 1.5 * iqr
    high_fence = q3 + 1.5 * iqr
    keep = values.between(low_fence, high_fence, inclusive="both")
    return group.loc[keep].copy(), group.loc[~keep].copy()


def _outlier_warning(
    *,
    partition: str,
    group_level: str,
    method: str,
    original_n: int,
    removed_n: int,
    threshold: float,
) -> str:
    if method == "NONE" or original_n <= 0 or removed_n <= 0:
        return ""
    rate = removed_n / original_n
    if rate <= threshold:
        return ""
    return (
        f"High outlier removal rate in partition={partition!r} group_level={group_level}: "
        f"OUTLIER_METHOD={method} removed {removed_n}/{original_n} ({rate:.1%}); "
        "review skewness, partitions, and outlier policy before using the interval clinically."
    )


def _outlier_rows(
    removed: pd.DataFrame,
    *,
    test_name: Any,
    group_level: str,
    sex_group: Any,
    age_group: Any,
    result_column: str,
    method: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _index, row in removed.iterrows():
        rows.append(
            {
                "test_name": test_name,
                "group_level": group_level,
                "sex_group": sex_group,
                "age_group": age_group,
                "source_result_column": result_column,
                "source_row": int(row["_source_row"]),
                "value": float(row["_result_value"]),
                "outlier_method": method,
            }
        )
    return rows


def _effective_method(options: ReferenceIntervalOptions, reliability: str) -> str:
    if options.method != "ROBUST":
        return options.method
    if reliability == "review":
        return "ROBUST"
    return "NONPARAMETRIC"


def _headline_method(n: int, options: ReferenceIntervalOptions) -> tuple[str | None, str]:
    if n < 40:
        return None, "n<40: do not calculate RI; collect additional reference individuals"
    if n < options.min_n:
        return "ROBUST", "40<=n<120: robust RI per CLSI EP28-A3c small-sample rule"
    return "NONPARAMETRIC", "n>=120: nonparametric RI per CLSI EP28-A3c"


def _ci_options_for_headline(options: ReferenceIntervalOptions, effective_method: str) -> ReferenceIntervalOptions:
    if effective_method != "NONPARAMETRIC" and options.ci_method == "RANK":
        return _options_with(options, ci_method="BOOTSTRAP", bootstrap_n=max(options.bootstrap_n, options.method_comparison_bootstrap_n))
    if options.ci_method == "BOOTSTRAP" and options.bootstrap_n <= 0 and options.method_comparison_bootstrap_n > 0:
        return _options_with(options, bootstrap_n=options.method_comparison_bootstrap_n)
    return options


def _unit_for_test(dataset: TameDataset, result_column: ColumnSpec, test_name: Any) -> str:
    unit_text = str(ci_get(dataset.column_metadata(result_column), "UNIT", "")).strip()
    default_unit, per_test = _parse_result_unit(unit_text)
    test_unit = per_test.get(str(test_name).strip())
    return test_unit or default_unit


def _parse_result_unit(unit_text: str) -> tuple[str, dict[str, str]]:
    text = str(unit_text or "").strip()
    if not text:
        return "", {}
    default = text
    mapping: dict[str, str] = {}
    if "(" in text and ")" in text:
        default = text.split("(", 1)[0].strip()
        inner = text.split("(", 1)[1].rsplit(")", 1)[0]
        for part in inner.split(","):
            if ":" not in part:
                continue
            test_name, unit = part.split(":", 1)
            mapping[test_name.strip()] = unit.strip()
    return default, mapping


def _post_limit_adjust(
    *,
    values: pd.Series,
    low: float,
    high: float,
    low_ci: tuple[float | None, float | None],
    high_ci: tuple[float | None, float | None],
    options: ReferenceIntervalOptions,
    effective_method: str,
    partition: str,
    group_level: str,
) -> tuple[list[str], float, float, tuple[float | None, float | None], tuple[float | None, float | None]]:
    """임상 안전 보정: 모수적 정규성 경고, ROBUST 전환 경고, 음수 하한 클램프."""
    warnings: list[str] = []
    arr = values.dropna().to_numpy(dtype=float)

    if effective_method in {"PARAMETRIC", "LOG_PARAMETRIC"} and arr.size >= 3:
        skew = _skewness(np.log(arr)) if effective_method == "LOG_PARAMETRIC" and np.all(arr > 0) else _skewness(arr)
        if abs(skew) > options.skew_warn:
            warnings.append(
                f"METHOD={effective_method} applied to skewed data (skewness={skew:.2f}) in "
                f"partition={partition!r} group_level={group_level}: the normality assumption is "
                "doubtful; prefer NONPARAMETRIC or LOG_PARAMETRIC and review before clinical use."
            )

    if options.method == "ROBUST" and effective_method != "ROBUST":
        warnings.append(
            f"METHOD=ROBUST was overridden to {effective_method} in partition={partition!r} "
            f"group_level={group_level} because n is outside the review range; the robust method is "
            "applied only for review-grade partitions (CLSI uses robust for ~20-119 reference values)."
        )

    if options.clamp_nonnegative and arr.size and float(arr.min()) >= 0:
        original = (low, high, low_ci, high_ci)
        low = _clamp_nonnegative_float(low)
        high = _clamp_nonnegative_float(high)
        low_ci = tuple(_clamp_nonnegative_optional(value) for value in low_ci)
        high_ci = tuple(_clamp_nonnegative_optional(value) for value in high_ci)
        if original != (low, high, low_ci, high_ci):
            warnings.append(
                f"Negative reference limit or CI bound was produced for non-negative data in "
                f"partition={partition!r} group_level={group_level}; negative values were clipped to 0. "
                "Review the distribution and consider transformed or nonparametric estimation."
            )

    if options.decimals is not None:
        low = round(float(low), options.decimals)
        high = round(float(high), options.decimals)
        low_ci = tuple(None if v is None else round(float(v), options.decimals) for v in low_ci)
        high_ci = tuple(None if v is None else round(float(v), options.decimals) for v in high_ci)

    return warnings, low, high, low_ci, high_ci


def _clamp_nonnegative_float(value: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return value
    if not math.isfinite(numeric):
        return numeric
    return max(0.0, numeric)


def _clamp_nonnegative_optional(value: float | None) -> float | None:
    if value is None:
        return None
    return _clamp_nonnegative_float(value)


def _ci_adequacy(
    low: float, high: float,
    low_ci: tuple[float | None, float | None], high_ci: tuple[float | None, float | None],
    ref_width: float, frac: float, *, partition: str, group_level: str,
) -> tuple[str, str | None]:
    """참고한계 CI 폭이 참고구간 폭 대비 과도하면(한계 부정밀) 'wide' 플래그·경고."""
    bounds = [low_ci[0], low_ci[1], high_ci[0], high_ci[1]]
    if any(b is None for b in bounds) or ref_width is None or ref_width <= 0:
        return "not_calculated", None
    low_width = abs(float(low_ci[1]) - float(low_ci[0]))
    high_width = abs(float(high_ci[1]) - float(high_ci[0]))
    widest = max(low_width, high_width)
    if widest > frac * ref_width:
        return "inadequate", (
            f"Wide reference-limit confidence interval in partition={partition!r} group_level={group_level}: "
            f"widest limit CI ({widest:.4g}) exceeds {frac:.0%} of the interval width ({ref_width:.4g}); "
            "the limit is imprecise — consider a larger reference sample."
        )
    return "adequate", None


def _verify_fields(values: pd.Series, test_name: Any, options: ReferenceIntervalOptions) -> dict[str, Any]:
    """검증(transference) 모드: 후보 참고구간 대비 외부 비율과 EP28식 합격 판정."""
    candidate = options.verify.get(str(test_name))
    if candidate is None and options.verify_low is not None and options.verify_high is not None:
        candidate = (options.verify_low, options.verify_high)
    if candidate is None:
        return {
            "verify_low": None, "verify_high": None, "verify_n_below": None,
            "verify_n_above": None, "verify_outside_rate": None, "verify_pass": None,
        }
    low, high = candidate
    arr = values.dropna().to_numpy(dtype=float)
    n = int(arr.size)
    below = int(np.sum(arr < low))
    above = int(np.sum(arr > high))
    rate = (below + above) / n if n else None
    verdict = None if rate is None else ("pass" if rate <= options.verify_max_outside else "fail")
    return {
        "verify_low": float(low), "verify_high": float(high), "verify_n_below": below,
        "verify_n_above": above, "verify_outside_rate": rate, "verify_pass": verdict,
    }


def _reference_limits(values: pd.Series, options: ReferenceIntervalOptions, *, effective_method: str) -> tuple[float, float, str]:
    clean = values.dropna().astype(float)
    if clean.empty:
        raise ValueError("Reference interval calculation requires at least one numeric value.")
    q_label = f"{options.lower_q:.3f}_{options.upper_q:.3f}"
    if effective_method == "NONPARAMETRIC":
        arr = clean.to_numpy(dtype=float)
        suffix = "_clsi" if options.quantile_method == "CLSI" else ""
        return (
            _np_quantile(arr, options.lower_q, options.quantile_method),
            _np_quantile(arr, options.upper_q, options.quantile_method),
            f"nonparametric_quantile{suffix}_{q_label}",
        )
    if effective_method == "PARAMETRIC":
        low, high = _normal_limits(clean.to_numpy(dtype=float), options)
        return low, high, f"parametric_normal_{q_label}"
    if effective_method == "LOG_PARAMETRIC":
        low, high = _log_normal_limits(clean.to_numpy(dtype=float), options)
        return low, high, f"log_parametric_normal_{q_label}"
    if effective_method == "BIWEIGHT":
        low, high = _biweight_limits(clean.to_numpy(dtype=float), options)
        return low, high, f"robust_biweight_{q_label}"
    low, high = _robust_limits(clean.to_numpy(dtype=float), options)
    return low, high, f"robust_median_mad_{q_label}"


def _biweight_location(values: np.ndarray, c: float = _BIWEIGHT_C_LOCATION) -> float:
    """Tukey biweight 위치 추정(M-추정량). astropy 표준식과 동일."""
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    if mad <= 0:
        return float(np.mean(values))
    u = (values - median) / (c * mad)
    mask = np.abs(u) < 1
    if not np.any(mask):
        return median
    weights = (1 - u[mask] ** 2) ** 2
    return median + float(np.sum((values[mask] - median) * weights) / np.sum(weights))


def _biweight_scale(values: np.ndarray, c: float = _BIWEIGHT_C_SCALE) -> float:
    """Tukey biweight 척도 추정. astropy 표준식과 동일."""
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    if mad <= 0:
        return float(np.std(values, ddof=1)) if values.size > 1 else 0.0
    u = (values - median) / (c * mad)
    mask = np.abs(u) < 1
    n = values.size
    numerator = n * float(np.sum(((values[mask] - median) ** 2) * (1 - u[mask] ** 2) ** 4))
    denominator = abs(float(np.sum((1 - u[mask] ** 2) * (1 - 5 * u[mask] ** 2))))
    if denominator <= 0:
        return float(np.std(values, ddof=1)) if values.size > 1 else 0.0
    return float(np.sqrt(numerator) / denominator)


def _biweight_limits(values: np.ndarray, options: ReferenceIntervalOptions) -> tuple[float, float]:
    arr = np.asarray(values, dtype=float)
    if arr.size == 1:
        value = float(arr[0])
        return value, value
    location = _biweight_location(arr)
    scale = _biweight_scale(arr)
    return (
        location + _NORMAL.inv_cdf(options.lower_q) * scale,
        location + _NORMAL.inv_cdf(options.upper_q) * scale,
    )


def _normal_limits(values: np.ndarray, options: ReferenceIntervalOptions) -> tuple[float, float]:
    if values.size == 1:
        value = float(values[0])
        return value, value
    mean = float(np.mean(values))
    sd = float(np.std(values, ddof=1))
    return (
        mean + _NORMAL.inv_cdf(options.lower_q) * sd,
        mean + _NORMAL.inv_cdf(options.upper_q) * sd,
    )


def _log_normal_limits(values: np.ndarray, options: ReferenceIntervalOptions) -> tuple[float, float]:
    if np.any(values <= 0):
        raise ValueError("METHOD=LOG_PARAMETRIC requires all retained values to be positive.")
    if values.size == 1:
        value = float(values[0])
        return value, value
    log_values = np.log(values)
    mean = float(np.mean(log_values))
    sd = float(np.std(log_values, ddof=1))
    return (
        float(np.exp(mean + _NORMAL.inv_cdf(options.lower_q) * sd)),
        float(np.exp(mean + _NORMAL.inv_cdf(options.upper_q) * sd)),
    )


def _robust_limits(values: np.ndarray, options: ReferenceIntervalOptions) -> tuple[float, float]:
    if values.size == 1:
        value = float(values[0])
        return value, value
    median = float(np.median(values))
    sd = _robust_sd(values)
    return (
        median + _NORMAL.inv_cdf(options.lower_q) * sd,
        median + _NORMAL.inv_cdf(options.upper_q) * sd,
    )


def _robust_sd(values: np.ndarray) -> float:
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    sd = _MAD_TO_SD * mad
    if sd > 0:
        return sd
    q1 = float(np.quantile(values, 0.25))
    q3 = float(np.quantile(values, 0.75))
    sd = (q3 - q1) * _IQR_TO_SD
    return max(sd, 0.0)


def _reference_limit_ci(
    values: pd.Series,
    options: ReferenceIntervalOptions,
    *,
    effective_method: str,
    partition: str,
    group_level: str,
) -> tuple[tuple[float | None, float | None], tuple[float | None, float | None], str, list[str]]:
    clean = values.dropna().astype(float)
    if clean.empty:
        return (None, None), (None, None), "not_calculated", []
    if clean.shape[0] == 1:
        value = float(clean.iloc[0])
        label = _ci_method_label(options, effective_method)
        return (value, value), (value, value), label, []
    if options.ci_method == "RANK":
        return _rank_reference_limit_ci(clean, options, partition=partition, group_level=group_level)
    if options.bootstrap_n <= 0:
        return (None, None), (None, None), "not_calculated", []

    seed = int(options.seed + clean.shape[0] * 31 + round(float(clean.mean()) * 1000))
    rng = np.random.default_rng(seed)
    sample = clean.to_numpy(dtype=float)
    indexes = rng.integers(0, sample.shape[0], size=(options.bootstrap_n, sample.shape[0]))
    boot = sample[indexes]
    if effective_method == "NONPARAMETRIC":
        np_method = "weibull" if options.quantile_method == "CLSI" else "linear"
        lows = np.quantile(boot, options.lower_q, axis=1, method=np_method)
        highs = np.quantile(boot, options.upper_q, axis=1, method=np_method)
    elif effective_method == "PARAMETRIC":
        means = np.mean(boot, axis=1)
        sds = np.std(boot, axis=1, ddof=1)
        lows = means + _NORMAL.inv_cdf(options.lower_q) * sds
        highs = means + _NORMAL.inv_cdf(options.upper_q) * sds
    elif effective_method == "LOG_PARAMETRIC":
        if np.any(boot <= 0):
            raise ValueError("METHOD=LOG_PARAMETRIC requires all bootstrap samples to be positive.")
        log_boot = np.log(boot)
        means = np.mean(log_boot, axis=1)
        sds = np.std(log_boot, axis=1, ddof=1)
        lows = np.exp(means + _NORMAL.inv_cdf(options.lower_q) * sds)
        highs = np.exp(means + _NORMAL.inv_cdf(options.upper_q) * sds)
    elif effective_method == "BIWEIGHT":
        lows = np.array([_biweight_limits(row, options)[0] for row in boot])
        highs = np.array([_biweight_limits(row, options)[1] for row in boot])
    else:
        medians = np.median(boot, axis=1)
        sds = _robust_sd_matrix(boot)
        lows = medians + _NORMAL.inv_cdf(options.lower_q) * sds
        highs = medians + _NORMAL.inv_cdf(options.upper_q) * sds
    alpha = (1.0 - options.ci_level) / 2.0
    return (
        (float(np.quantile(lows, alpha)), float(np.quantile(lows, 1.0 - alpha))),
        (float(np.quantile(highs, alpha)), float(np.quantile(highs, 1.0 - alpha))),
        _ci_method_label(options, effective_method),
        [],
    )


def _robust_sd_matrix(values: np.ndarray) -> np.ndarray:
    medians = np.median(values, axis=1)
    mad = np.median(np.abs(values - medians[:, None]), axis=1)
    sd = _MAD_TO_SD * mad
    needs_fallback = sd <= 0
    if np.any(needs_fallback):
        q1 = np.quantile(values[needs_fallback], 0.25, axis=1)
        q3 = np.quantile(values[needs_fallback], 0.75, axis=1)
        sd[needs_fallback] = np.maximum((q3 - q1) * _IQR_TO_SD, 0.0)
    return sd


def _rank_reference_limit_ci(
    values: pd.Series,
    options: ReferenceIntervalOptions,
    *,
    partition: str,
    group_level: str,
) -> tuple[tuple[float | None, float | None], tuple[float | None, float | None], str, list[str]]:
    ordered = sorted(float(value) for value in values.dropna())
    low_ci = _rank_quantile_ci(ordered, options.lower_q, options.ci_level)
    high_ci = _rank_quantile_ci(ordered, options.upper_q, options.ci_level)
    warnings: list[str] = []
    if low_ci[0] is None or low_ci[1] is None or high_ci[0] is None or high_ci[1] is None:
        warnings.append(
            f"Rank CI is undefined for partition={partition!r} group_level={group_level}: "
            f"n={len(ordered)}, CI_LEVEL={options.ci_level:.2f}, LOW_Q={options.lower_q:.3f}, HIGH_Q={options.upper_q:.3f}. "
            "Use CI_METHOD=BOOTSTRAP or increase sample size."
        )
    return low_ci, high_ci, _ci_method_label(options, "NONPARAMETRIC"), warnings


def _rank_quantile_ci(ordered: list[float], quantile: float, ci_level: float) -> tuple[float | None, float | None]:
    n = len(ordered)
    if n == 0:
        return None, None
    alpha = (1.0 - ci_level) / 2.0
    pmf = [_binomial_pmf(index, n, quantile) for index in range(n + 1)]
    cumulative: list[float] = []
    running = 0.0
    for probability in pmf:
        running += probability
        cumulative.append(running)
    total = cumulative[-1]
    lower_rank: int | None = None
    for rank in range(1, n + 1):
        if cumulative[rank - 1] <= alpha:
            lower_rank = rank
        else:
            break
    upper_rank: int | None = None
    for rank in range(1, n + 1):
        if max(0.0, total - cumulative[rank - 1]) <= alpha:
            upper_rank = rank
            break
    lower = ordered[lower_rank - 1] if lower_rank is not None else None
    upper = ordered[upper_rank - 1] if upper_rank is not None else None
    return lower, upper


def _binomial_pmf(k: int, n: int, p: float) -> float:
    return float(np.exp(_log_comb(n, k) + k * np.log(p) + (n - k) * np.log1p(-p)))


def _log_comb(n: int, k: int) -> float:
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def _ci_method_label(options: ReferenceIntervalOptions, effective_method: str) -> str:
    if options.ci_method == "RANK":
        return f"rank_nonparametric_{options.ci_level:.2f}"
    return f"bootstrap_percentile_{options.ci_level:.2f}_{effective_method.lower()}"


def _reliability(n: int, min_n: int) -> str:
    if n >= min_n:
        return "ok"
    if n >= max(20, min_n // 3):
        return "review"
    return "insufficient_n"


def _recommendation(reliability: str) -> str:
    if reliability == "ok":
        return "candidate_for_verification"
    if reliability == "review":
        return "use_with_laboratory_review"
    return "do_not_use_as_final_interval"


def _partition_label(test_name: Any, group_level: str, sex_group: Any, age_group: Any) -> str:
    parts = [str(test_name)]
    if "SEX" in group_level:
        parts.append(str(sex_group))
    if "AGE" in group_level:
        parts.append(str(age_group))
    return " / ".join(parts)


def _build_result_dataset(
    rows: list[dict[str, Any]],
    source_dataset: TameDataset,
    *,
    options: ReferenceIntervalOptions,
    warnings: list[str],
) -> TameDataset:
    headers = [
        "[[TESTNAME::ITEM::CATEGORY]]test_name",
        "[[UNIT::CATEGORY::EMPTY_OK]]unit",
        "[[PARTITION::CATEGORY]]partition",
        "[[GROUP_LEVEL::CATEGORY]]group_level",
        "[[BY::SEX_GROUP::CATEGORY]]sex_group",
        "[[BY::AGE_GROUP::CATEGORY]]age_group",
        "[[AGE_LOW::NUM]]age_low",
        "[[AGE_HIGH::NUM]]age_high",
        "[[SOURCE::STR]]source_result_column",
        "[[N::NUM]]original_n",
        "[[N::NUM]]n",
        "[[COUNT::NUM]]outliers_removed",
        "[[REF_LOW::NUM]]ref_low",
        "[[REF_HIGH::NUM]]ref_high",
        "[[REF_LOW::CI_LOW::NUM]]ref_low_ci90_low",
        "[[REF_LOW::CI_HIGH::NUM]]ref_low_ci90_high",
        "[[REF_HIGH::CI_LOW::NUM]]ref_high_ci90_low",
        "[[REF_HIGH::CI_HIGH::NUM]]ref_high_ci90_high",
        "[[REF_WIDTH::NUM]]ref_width",
        "[[RESULT::NUM::STAT]]mean",
        "[[RESULT::NUM::STAT]]median",
        "[[RESULT::NUM::STAT]]sd",
        "[[RESULT::NUM::STAT]]min",
        "[[RESULT::NUM::STAT]]max",
        "[[RELIABILITY::CATEGORY]]reliability",
        "[[RECOMMENDATION::CATEGORY]]recommendation",
        "[[METHOD::STR]]method",
        "[[METHOD::STR]]method_selection_reason",
        "[[METHOD::STR]]ci_method",
        "[[METHOD::STR]]outlier_method",
        "[[SETTING::STR]]comparator_policy",
        "[[VERIFY_LOW::NUM]]verify_low",
        "[[VERIFY_HIGH::NUM]]verify_high",
        "[[COUNT::NUM]]verify_n_below",
        "[[COUNT::NUM]]verify_n_above",
        "[[RATE::NUM]]verify_outside_rate",
        "[[VERIFY::CATEGORY]]verify_pass",
        "[[CI_ADEQUACY::CATEGORY]]ci_adequacy",
    ]
    columns = merged_column_specs(headers)
    column_names = [column.name for column in columns]
    df = pd.DataFrame(rows, columns=column_names) if rows else pd.DataFrame(columns=column_names)
    df = df.where(pd.notna(df), None)
    df.columns = [column.name for column in columns]
    meta = {
        "INFO": {
            "NAME": "REFERENCE_INTERVAL",
            "FORMAT_VERSION": "tame/1",
            "CREATED_BY": "tametools",
            "DESCRIPTION": "Reference interval plugin output dataset with reliability review.",
        },
        "FORMAT": {},
        "SETTINGS": {
            "VALIDATE_ERROR": "REPORT",
        },
        "COLUMN": _result_column_metadata(),
        "WORKS": {
            "DEFAULT": ["DESCRIBE"],
        },
        "PLUGIN": {
            "NAME": "REFERENCE_INTERVAL",
            "COMPARATOR_POLICY": options.comparator_policy,
            "METHOD": options.method,
            "CI_METHOD": options.ci_method,
            "AGE_BIN_WIDTH": options.age_bin_width,
            "LOW_Q": options.lower_q,
            "HIGH_Q": options.upper_q,
            "CI_LEVEL": options.ci_level,
            "MIN_N": options.min_n,
            "OUTLIER_METHOD": options.outlier_method,
            "OUTLIER_WARN_RATE": options.outlier_warn_rate,
            "BOOTSTRAP_N": options.bootstrap_n,
            "METHOD_COMPARISON_BOOTSTRAP_N": options.method_comparison_bootstrap_n,
            "NORMALITY_ALPHA": options.normality_alpha,
            "GROUP_DIFF_ALPHA": options.group_diff_alpha,
            "HISTOGRAM_MAX_GROUPS": options.histogram_max_groups,
            "SOURCE_PATH": source_dataset.source_path or "",
            "WARNINGS": warnings,
        },
    }
    return TameDataset(
        df=df,
        columns=columns,
        meta=meta,
        schema={},
        job={},
        raw_sections={},
        source_path=source_dataset.source_path,
    )


def _result_column_metadata() -> dict[str, dict[str, Any]]:
    labels = {
        "test_name": "검사항목명",
        "unit": "단위",
        "partition": "보고그룹",
        "group_level": "그룹수준",
        "sex_group": "성별그룹",
        "age_group": "연령그룹",
        "age_low": "연령하한",
        "age_high": "연령상한",
        "source_result_column": "원본결과컬럼",
        "original_n": "이상치제거전대상수",
        "n": "대상수",
        "outliers_removed": "이상치제거수",
        "ref_low": "참고치하한",
        "ref_high": "참고치상한",
        "ref_low_ci90_low": "하한90CI하한",
        "ref_low_ci90_high": "하한90CI상한",
        "ref_high_ci90_low": "상한90CI하한",
        "ref_high_ci90_high": "상한90CI상한",
        "ref_width": "참고치폭",
        "mean": "평균",
        "median": "중앙값",
        "sd": "표준편차",
        "min": "최소값",
        "max": "최대값",
        "reliability": "신뢰도",
        "recommendation": "권고",
        "method": "계산방법",
        "method_selection_reason": "방법선택사유",
        "ci_method": "신뢰구간방법",
        "outlier_method": "이상치방법",
        "comparator_policy": "비교연산처리",
        "verify_low": "검증하한",
        "verify_high": "검증상한",
        "verify_n_below": "검증하한미만수",
        "verify_n_above": "검증상한초과수",
        "verify_outside_rate": "검증외부비율",
        "verify_pass": "검증판정",
        "ci_adequacy": "CI적정성",
    }
    return {name: {"LABEL": label} for name, label in labels.items()}


_PARTITION_MIN_N = 20
_PARTITION_COLUMNS = [
    "test_name", "source_result_column", "partition_factor", "group_a", "group_b",
    "n_a", "n_b", "mean_a", "mean_b", "z", "z_critical", "decision",
]


def _partition_tests(work: pd.DataFrame, result_column: ColumnSpec, options: ReferenceIntervalOptions) -> list[dict[str, Any]]:
    """Harris-Boyd 정규편차 검정으로 성별/연령 분할의 통계적 정당성을 평가한다.

    z = |mean_a - mean_b| / sqrt(sd_a^2/n_a + sd_b^2/n_b),
    임계값 z_c = 3 * sqrt(n_avg/120) (CLSI 기준 n=120과 연동). z > z_c 면 분할 권장.
    """
    rows: list[dict[str, Any]] = []
    for test_name, group in work.groupby("_test_name", dropna=False, observed=False):
        # 성별: male vs female
        sex = group.loc[group["_sex_group"].isin(["male", "female"])]
        groups = {key: sub["_result_value"].astype(float) for key, sub in sex.groupby("_sex_group", observed=False)}
        if {"male", "female"} <= set(groups):
            rows.append(_harris_boyd_row(test_name, result_column.name, "SEX", "male", "female",
                                         groups["male"], groups["female"]))
        # 연령: 인접 연령대 쌍
        age = group.loc[group["_age_group"].notna() & (group["_age_group"] != "ALL")]
        if not age.empty:
            bands = (
                age[["_age_group", "_age_low"]].drop_duplicates()
                .assign(_sort=lambda d: d["_age_low"].map(lambda v: float(v) if v is not None and not pd.isna(v) else float("inf")))
                .sort_values("_sort")["_age_group"].tolist()
            )
            for band_a, band_b in zip(bands, bands[1:]):
                va = age.loc[age["_age_group"] == band_a, "_result_value"].astype(float)
                vb = age.loc[age["_age_group"] == band_b, "_result_value"].astype(float)
                rows.append(_harris_boyd_row(test_name, result_column.name, "AGE", str(band_a), str(band_b), va, vb))
    return rows


def _harris_boyd_row(test_name: Any, result_column: str, factor: str, group_a: str, group_b: str,
                     values_a: pd.Series, values_b: pd.Series) -> dict[str, Any]:
    a = values_a.dropna().to_numpy(dtype=float)
    b = values_b.dropna().to_numpy(dtype=float)
    n_a, n_b = int(a.size), int(b.size)
    mean_a = float(np.mean(a)) if n_a else float("nan")
    mean_b = float(np.mean(b)) if n_b else float("nan")
    se = math.sqrt(
        (float(np.var(a, ddof=1)) / n_a if n_a > 1 else 0.0)
        + (float(np.var(b, ddof=1)) / n_b if n_b > 1 else 0.0)
    )
    z = abs(mean_a - mean_b) / se if se > 0 else float("inf")
    n_avg = (n_a + n_b) / 2.0
    z_critical = 3.0 * math.sqrt(n_avg / 120.0)
    if n_a < _PARTITION_MIN_N or n_b < _PARTITION_MIN_N:
        decision = "insufficient_n"
    elif z > z_critical:
        decision = "partition_recommended"
    else:
        decision = "combine_ok"
    return {
        "test_name": test_name, "source_result_column": result_column, "partition_factor": factor,
        "group_a": group_a, "group_b": group_b, "n_a": n_a, "n_b": n_b,
        "mean_a": round(mean_a, 4), "mean_b": round(mean_b, 4),
        "z": round(z, 4) if math.isfinite(z) else z, "z_critical": round(z_critical, 4),
        "decision": decision,
    }


def _partition_tests_table(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=_PARTITION_COLUMNS) if rows else pd.DataFrame(columns=_PARTITION_COLUMNS)


def _summary_by_test(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "test_name",
        "unit",
        "group_count",
        "ok_groups",
        "review_groups",
        "insufficient_groups",
        "min_n",
        "max_n",
        "widest_ref_width",
        "widest_relative_width",
    ]
    if frame.empty:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, Any]] = []
    for test_name, group in frame.groupby("test_name", dropna=False, observed=False):
        rows.append(
            {
                "test_name": test_name,
                "unit": _first_nonempty(group["unit"]) if "unit" in group.columns else "",
                "group_count": int(group.shape[0]),
                "ok_groups": int((group["reliability"] == "ok").sum()),
                "review_groups": int((group["reliability"] == "review").sum()),
                "insufficient_groups": int((group["reliability"] == "insufficient_n").sum()),
                "min_n": int(group["n"].min()),
                "max_n": int(group["n"].max()),
                "widest_ref_width": float(group["ref_width"].max()),
                "widest_relative_width": _widest_relative_width(group),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def _widest_relative_width(group: pd.DataFrame) -> float | None:
    widths: list[float] = []
    for row in group.itertuples(index=False):
        try:
            low = float(row.ref_low)
            high = float(row.ref_high)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(low) or not math.isfinite(high):
            continue
        midpoint = (low + high) / 2.0
        if midpoint <= 0:
            continue
        widths.append((high - low) / midpoint)
    return max(widths) if widths else None


def _reliability_summary(frame: pd.DataFrame) -> pd.DataFrame:
    columns = ["group_level", "reliability", "group_count", "min_n", "max_n"]
    if frame.empty:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, Any]] = []
    for (group_level, reliability), group in frame.groupby(["group_level", "reliability"], dropna=False, observed=False):
        rows.append(
            {
                "group_level": group_level,
                "reliability": reliability,
                "group_count": int(group.shape[0]),
                "min_n": int(group["n"].min()),
                "max_n": int(group["n"].max()),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def _outlier_table(rows: list[dict[str, Any]]) -> pd.DataFrame:
    columns = [
        "test_name",
        "group_level",
        "sex_group",
        "age_group",
        "source_result_column",
        "source_row",
        "value",
        "outlier_method",
    ]
    return pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame(columns=columns)


def _limits_long_table(frame: pd.DataFrame) -> pd.DataFrame:
    columns = ["partition", "group_level", "limit", "value"]
    if frame.empty:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, Any]] = []
    for row in frame.itertuples(index=False):
        rows.append({"partition": row.partition, "group_level": row.group_level, "limit": "ref_low", "value": row.ref_low})
        rows.append({"partition": row.partition, "group_level": row.group_level, "limit": "ref_high", "value": row.ref_high})
    return pd.DataFrame(rows, columns=columns)


def _normality_table(groups: list[GroupAnalysis], options: ReferenceIntervalOptions) -> pd.DataFrame:
    columns = [
        "test_name",
        "unit",
        "partition",
        "group_level",
        "sex_group",
        "age_group",
        "n",
        "mean",
        "sd",
        "skewness",
        "kurtosis_excess",
        "shapiro_w",
        "shapiro_p",
        "shapiro_status",
        "dagostino_k2",
        "dagostino_p",
        "dagostino_status",
        "normality_decision",
    ]
    rows: list[dict[str, Any]] = []
    stats = _scipy_stats()
    for group in groups:
        arr = np.asarray(group.values, dtype=float)
        n = int(arr.size)
        sd = float(np.std(arr, ddof=1)) if n > 1 else 0.0
        shapiro_w, shapiro_p = _shapiro_test(stats, arr)
        dagostino_k2, dagostino_p = _dagostino_test(stats, arr)
        rows.append(
            {
                "test_name": group.test_name,
                "unit": group.unit,
                "partition": group.partition,
                "group_level": group.group_level,
                "sex_group": group.sex_group,
                "age_group": group.age_group,
                "n": n,
                "mean": float(np.mean(arr)) if n else None,
                "sd": sd if n else None,
                "skewness": _skewness(arr) if n else None,
                "kurtosis_excess": _kurtosis_excess(arr) if n else None,
                "shapiro_w": shapiro_w,
                "shapiro_p": shapiro_p,
                "shapiro_status": _test_status(shapiro_p, n=n, min_n=3, stats_available=stats is not None),
                "dagostino_k2": dagostino_k2,
                "dagostino_p": dagostino_p,
                "dagostino_status": _test_status(dagostino_p, n=n, min_n=20, stats_available=stats is not None),
                "normality_decision": _normality_decision(
                    [shapiro_p, dagostino_p],
                    alpha=options.normality_alpha,
                    n=n,
                    stats_available=stats is not None,
                ),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def _method_comparison_table(groups: list[GroupAnalysis], options: ReferenceIntervalOptions) -> pd.DataFrame:
    columns = [
        "test_name",
        "unit",
        "partition",
        "group_level",
        "sex_group",
        "age_group",
        "n",
        "method_family",
        "ref_low",
        "ref_high",
        "ref_width",
        "ref_low_ci_low",
        "ref_low_ci_high",
        "ref_high_ci_low",
        "ref_high_ci_high",
        "ci_method",
    ]
    rows: list[dict[str, Any]] = []
    for group in groups:
        values = pd.Series(group.values, dtype=float)
        for method in ("PARAMETRIC", "NONPARAMETRIC", "ROBUST"):
            comparison_options = _options_with(
                options,
                method=method,
                ci_method="BOOTSTRAP",
                bootstrap_n=options.method_comparison_bootstrap_n,
            )
            low, high, method_label = _reference_limits(values, comparison_options, effective_method=method)
            low_ci, high_ci, ci_method_label, _ci_warnings = _reference_limit_ci(
                values,
                comparison_options,
                effective_method=method,
                partition=group.partition,
                group_level=group.group_level,
            )
            _adjust_warnings, low, high, low_ci, high_ci = _post_limit_adjust(
                values=values,
                low=low,
                high=high,
                low_ci=low_ci,
                high_ci=high_ci,
                options=comparison_options,
                effective_method=method,
                partition=group.partition,
                group_level=group.group_level,
            )
            rows.append(
                {
                    "test_name": group.test_name,
                    "unit": group.unit,
                    "partition": group.partition,
                    "group_level": group.group_level,
                    "sex_group": group.sex_group,
                    "age_group": group.age_group,
                    "n": len(group.values),
                    "method_family": _method_family_label(method_label),
                    "ref_low": low,
                    "ref_high": high,
                    "ref_width": high - low,
                    "ref_low_ci_low": low_ci[0],
                    "ref_low_ci_high": low_ci[1],
                    "ref_high_ci_low": high_ci[0],
                    "ref_high_ci_high": high_ci[1],
                    "ci_method": ci_method_label,
                }
            )
    return pd.DataFrame(rows, columns=columns)


def _group_difference_table(groups: list[GroupAnalysis], options: ReferenceIntervalOptions) -> pd.DataFrame:
    columns = [
        "test_name",
        "unit",
        "partition_factor",
        "stratum",
        "group_level",
        "group_a",
        "group_b",
        "n_a",
        "n_b",
        "harris_boyd_z",
        "harris_boyd_z_critical",
        "harris_boyd_decision",
        "distribution_decision",
        "ri_decision",
        "recommendation",
        "ri_overlap_fraction",
        "welch_p",
        "mannwhitney_p",
        "ks_p",
        "decision",
    ]
    rows: list[dict[str, Any]] = []
    stats = _scipy_stats()
    for group_a, group_b, factor, stratum in _planned_group_contrasts(groups):
        values_a = np.asarray(group_a.values, dtype=float)
        values_b = np.asarray(group_b.values, dtype=float)
        welch_p = _welch_p_value(stats, values_a, values_b)
        mannwhitney_p = _mannwhitney_p_value(stats, values_a, values_b)
        ks_p = _ks_p_value(stats, values_a, values_b)
        p_values = [value for value in (welch_p, mannwhitney_p, ks_p) if value is not None and math.isfinite(value)]
        distribution_decision = _distribution_difference_decision(p_values, options.group_diff_alpha, stats is not None)
        overlap_fraction, ri_decision = _reference_interval_overlap(values_a, values_b, options)
        hb = _harris_boyd_row(
            group_a.test_name,
            group_a.source_result_column,
            factor,
            _short_group_label(group_a),
            _short_group_label(group_b),
            pd.Series(values_a, dtype=float),
            pd.Series(values_b, dtype=float),
        )
        recommendation = _partition_recommendation(
            str(hb["decision"]),
            overlap_fraction,
            int(values_a.size),
            int(values_b.size),
        )
        rows.append(
            {
                "test_name": group_a.test_name,
                "unit": group_a.unit,
                "partition_factor": factor,
                "stratum": stratum,
                "group_level": group_a.group_level,
                "group_a": _short_group_label(group_a),
                "group_b": _short_group_label(group_b),
                "n_a": int(values_a.size),
                "n_b": int(values_b.size),
                "harris_boyd_z": hb["z"],
                "harris_boyd_z_critical": hb["z_critical"],
                "harris_boyd_decision": hb["decision"],
                "distribution_decision": distribution_decision,
                "ri_decision": ri_decision,
                "recommendation": recommendation,
                "ri_overlap_fraction": overlap_fraction,
                "welch_p": welch_p,
                "mannwhitney_p": mannwhitney_p,
                "ks_p": ks_p,
                "decision": recommendation,
            }
        )
    return pd.DataFrame(rows, columns=columns)


def _planned_group_contrasts(groups: list[GroupAnalysis]) -> list[tuple[GroupAnalysis, GroupAnalysis, str, str]]:
    grouped: dict[tuple[Any, str], list[GroupAnalysis]] = {}
    for group in groups:
        if group.group_level == "TESTNAME":
            continue
        grouped.setdefault((group.test_name, group.group_level), []).append(group)

    contrasts: list[tuple[GroupAnalysis, GroupAnalysis, str, str]] = []
    for (_test_name, group_level), members in grouped.items():
        if group_level == "TESTNAME+SEX":
            contrasts.extend(_sex_contrasts(members, stratum="ALL"))
        elif group_level == "TESTNAME+AGE":
            contrasts.extend(_adjacent_age_contrasts(members, stratum="ALL"))
        elif group_level == "TESTNAME+SEX+AGE":
            for sex_group, part in _group_by(members, lambda group: str(group.sex_group)).items():
                contrasts.extend(_adjacent_age_contrasts(part, stratum=f"sex={sex_group}"))
            for age_group, part in _group_by(members, lambda group: str(group.age_group)).items():
                contrasts.extend(_sex_contrasts(part, stratum=f"age={age_group}"))
    return contrasts


def _group_by(groups: list[GroupAnalysis], key_func) -> dict[str, list[GroupAnalysis]]:
    grouped: dict[str, list[GroupAnalysis]] = {}
    for group in groups:
        grouped.setdefault(key_func(group), []).append(group)
    return grouped


def _sex_contrasts(groups: list[GroupAnalysis], *, stratum: str) -> list[tuple[GroupAnalysis, GroupAnalysis, str, str]]:
    by_sex = {str(group.sex_group): group for group in groups if str(group.sex_group) in {"male", "female"}}
    if {"male", "female"} <= set(by_sex):
        return [(by_sex["male"], by_sex["female"], "SEX", stratum)]
    return []


def _adjacent_age_contrasts(groups: list[GroupAnalysis], *, stratum: str) -> list[tuple[GroupAnalysis, GroupAnalysis, str, str]]:
    ordered = sorted(
        [group for group in groups if str(group.age_group) != "ALL"],
        key=lambda group: (
            float(group.age_low) if group.age_low is not None and not pd.isna(group.age_low) else float("inf"),
            str(group.age_group),
        ),
    )
    return [(left, right, "AGE", stratum) for left, right in zip(ordered, ordered[1:])]


def _partition_recommendation(hb_decision: str, overlap_fraction: float | None, n_a: int, n_b: int) -> str:
    if n_a < _PARTITION_MIN_N or n_b < _PARTITION_MIN_N or hb_decision == "insufficient_n":
        return "collect_more_data"
    if hb_decision == "partition_recommended":
        if overlap_fraction is not None and overlap_fraction >= 0.75:
            return "review_partition_high_overlap"
        return "partition_recommended"
    if overlap_fraction is not None and overlap_fraction < 0.25:
        return "review_low_overlap"
    return "combine_ok"


def _options_with(options: ReferenceIntervalOptions, **changes: Any) -> ReferenceIntervalOptions:
    return ReferenceIntervalOptions(**{**options.__dict__, **changes})


def _method_family_label(method_label: str) -> str:
    if method_label.startswith("parametric_"):
        return "parametric"
    if method_label.startswith("nonparametric_"):
        return "nonparametric"
    return "robust"


def _kurtosis_excess(values: np.ndarray) -> float:
    arr = np.asarray(values, dtype=float)
    if arr.size < 2:
        return 0.0
    mean = float(np.mean(arr))
    sd = float(np.std(arr, ddof=0))
    if sd <= 0:
        return 0.0
    return float(np.mean(((arr - mean) / sd) ** 4) - 3.0)


_SCIPY_STATS: Any | None | bool = None


def _scipy_stats() -> Any | None:
    global _SCIPY_STATS
    if _SCIPY_STATS is False:
        return None
    if _SCIPY_STATS is not None:
        return _SCIPY_STATS
    try:
        from scipy import stats  # noqa: PLC0415
    except ImportError:
        _SCIPY_STATS = False
        return None
    _SCIPY_STATS = stats
    return stats


def _shapiro_test(stats: Any | None, values: np.ndarray) -> tuple[float | None, float | None]:
    if stats is None or values.size < 3 or values.size > 5000:
        return None, None
    try:
        with py_warnings.catch_warnings():
            py_warnings.simplefilter("ignore")
            result = stats.shapiro(values)
    except Exception:
        return None, None
    return float(result.statistic), float(result.pvalue)


def _dagostino_test(stats: Any | None, values: np.ndarray) -> tuple[float | None, float | None]:
    if stats is None or values.size < 20:
        return None, None
    try:
        with py_warnings.catch_warnings():
            py_warnings.simplefilter("ignore")
            result = stats.normaltest(values)
    except Exception:
        return None, None
    return float(result.statistic), float(result.pvalue)


def _normality_decision(
    p_values: list[float | None],
    *,
    alpha: float,
    n: int,
    stats_available: bool,
) -> str:
    if n < 3:
        return "not_tested_insufficient_n"
    if not stats_available:
        return "not_tested_scipy_unavailable"
    valid = [value for value in p_values if value is not None and math.isfinite(value)]
    if not valid:
        return "not_tested"
    if any(value < alpha for value in valid):
        return "non_normal_review"
    return "compatible_with_normal"


def _test_status(value: float | None, *, n: int, min_n: int, stats_available: bool) -> str:
    if not stats_available:
        return "scipy unavailable"
    if n < min_n:
        return "n too small to compute"
    if value is None:
        return "not computed"
    try:
        if pd.isna(value):
            return "not computed"
    except (TypeError, ValueError):
        return "not computed"
    return "computed"


def _welch_p_value(stats: Any | None, values_a: np.ndarray, values_b: np.ndarray) -> float | None:
    if stats is None or values_a.size < 2 or values_b.size < 2:
        return None
    try:
        with py_warnings.catch_warnings():
            py_warnings.simplefilter("ignore")
            result = stats.ttest_ind(values_a, values_b, equal_var=False, nan_policy="omit")
    except Exception:
        return None
    return None if not math.isfinite(float(result.pvalue)) else float(result.pvalue)


def _mannwhitney_p_value(stats: Any | None, values_a: np.ndarray, values_b: np.ndarray) -> float | None:
    if stats is None or values_a.size < 1 or values_b.size < 1:
        return None
    try:
        with py_warnings.catch_warnings():
            py_warnings.simplefilter("ignore")
            result = stats.mannwhitneyu(values_a, values_b, alternative="two-sided")
    except Exception:
        return None
    return None if not math.isfinite(float(result.pvalue)) else float(result.pvalue)


def _ks_p_value(stats: Any | None, values_a: np.ndarray, values_b: np.ndarray) -> float | None:
    if stats is None or values_a.size < 1 or values_b.size < 1:
        return None
    try:
        with py_warnings.catch_warnings():
            py_warnings.simplefilter("ignore")
            result = stats.ks_2samp(values_a, values_b, alternative="two-sided", mode="auto")
    except Exception:
        return None
    return None if not math.isfinite(float(result.pvalue)) else float(result.pvalue)


def _distribution_difference_decision(p_values: list[float], alpha: float, stats_available: bool) -> str:
    if not stats_available:
        return "not_tested_scipy_unavailable"
    if not p_values:
        return "not_tested"
    if any(value < alpha for value in p_values):
        return "different_distribution"
    return "no_significant_difference"


def _reference_interval_overlap(
    values_a: np.ndarray,
    values_b: np.ndarray,
    options: ReferenceIntervalOptions,
) -> tuple[float | None, str]:
    if values_a.size == 0 or values_b.size == 0:
        return None, "not_tested"
    series_a = pd.Series(values_a, dtype=float)
    series_b = pd.Series(values_b, dtype=float)
    low_a, high_a, _ = _reference_limits(series_a, options, effective_method="NONPARAMETRIC")
    low_b, high_b, _ = _reference_limits(series_b, options, effective_method="NONPARAMETRIC")
    width_a = high_a - low_a
    width_b = high_b - low_b
    if width_a <= 0 or width_b <= 0:
        return None, "not_tested"
    overlap = max(0.0, min(high_a, high_b) - max(low_a, low_b))
    fraction = overlap / min(width_a, width_b)
    if fraction <= 0:
        return 0.0, "intervals_separated"
    if fraction < 0.25:
        return float(fraction), "limited_interval_overlap"
    return float(fraction), "intervals_overlap"


def _analysis_sort_key(group: GroupAnalysis) -> tuple[Any, Any, Any]:
    return (
        str(group.sex_group),
        float(group.age_low) if group.age_low is not None and not pd.isna(group.age_low) else float("inf"),
        str(group.age_group),
    )


def _short_group_label(group: GroupAnalysis) -> str:
    if group.group_level == "TESTNAME+SEX":
        return str(group.sex_group)
    if group.group_level == "TESTNAME+AGE":
        return str(group.age_group)
    if group.group_level == "TESTNAME+SEX+AGE":
        return f"{group.sex_group} / {group.age_group}"
    return str(group.partition)


def _write_reference_interval_report(
    path: Path,
    *,
    main_table: pd.DataFrame,
    summary_table: pd.DataFrame,
    reliability_table: pd.DataFrame,
    outlier_table: pd.DataFrame,
    partition_table: pd.DataFrame,
    normality_table: pd.DataFrame,
    method_comparison_table: pd.DataFrame,
    group_difference_table: pd.DataFrame,
    group_analyses: list[GroupAnalysis],
    source_path: str,
    source_rows: int,
    warnings: list[str],
    options: ReferenceIntervalOptions,
    max_table_rows: int,
) -> Path:
    try:
        from docx import Document  # noqa: PLC0415
        from docx.shared import Inches  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - depends on optional env
        raise RuntimeError(
            "docx report generation requires the report extra. Install with "
            "python3 -m pip install './tametools[report]' --no-build-isolation "
            "or use './tametools[report,web]' for the web workbench."
        ) from exc

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image_dir = output_path.parent / f"{output_path.stem}_charts"
    image_dir.mkdir(parents=True, exist_ok=True)
    for stale_png in image_dir.glob("*.png"):
        stale_png.unlink()

    document = Document()
    _configure_report_document(document)
    _add_report_title(document, "Reference Interval Report", "Candidate clinical reference intervals and review summary")
    _add_table_of_contents(document)

    document.add_heading("Overview", level=1)
    document.add_paragraph(_report_summary(main_table, options))
    _add_report_table(
        document,
        "Executive Summary",
        _executive_summary_table(main_table, group_difference_table),
        max_rows=20,
    )
    _add_report_table(
        document,
        "Report Metadata",
        _report_metadata_table(source_path=source_path, source_rows=source_rows),
        max_rows=20,
    )
    _add_report_table(document, "Thresholds and Glossary", _threshold_glossary_table(options), max_rows=30)
    _add_report_table(document, "Key Metrics", _report_metrics_table(main_table), max_rows=20)
    _add_report_table(document, "Method and Settings", _report_settings_table(options), max_rows=20)
    _add_summary_charts(document, summary_table, reliability_table, image_dir)

    document.add_heading("Results", level=1)
    _add_report_table(
        document,
        "Summary by Test",
        _sort_report_frame(summary_table, ["test_name"]),
        columns=[
            "test_name",
            "unit",
            "group_count",
            "ok_groups",
            "review_groups",
            "insufficient_groups",
            "min_n",
            "max_n",
            "widest_ref_width",
            "widest_relative_width",
        ],
        max_rows=max_table_rows,
    )
    _add_report_table(
        document,
        "Reliability Summary",
        _sort_report_frame(reliability_table, ["group_level", "reliability"]),
        columns=["group_level", "reliability", "group_count", "min_n", "max_n"],
        max_rows=max_table_rows,
    )
    _add_report_table(
        document,
        "Normality Assessment",
        _normality_report_rows(normality_table),
        columns=[
            "analysis",
            "group_level",
            "n",
            "skewness",
            "kurtosis_excess",
            "shapiro_p",
            "dagostino_status",
            "normality_decision",
        ],
        max_rows=max_table_rows,
        empty_message="No normality assessment rows were produced.",
    )
    _add_report_table(
        document,
        "Candidate Reference Intervals",
        _candidate_report_rows(main_table),
        columns=[
            "analysis",
            "group_level",
            "n",
            "ref_low",
            "ref_low_ci",
            "ref_high",
            "ref_high_ci",
            "ci_adequacy",
            "method",
        ],
        max_rows=max_table_rows,
        empty_message="No groups met the candidate threshold.",
    )
    _add_report_table(
        document,
        "Review-Grade Reference Intervals",
        _review_report_rows(main_table),
        columns=[
            "analysis",
            "group_level",
            "n",
            "ref_low",
            "ref_low_ci",
            "ref_high",
            "ref_high_ci",
            "ci_adequacy",
            "method",
        ],
        max_rows=max_table_rows,
        empty_message="No review-grade groups were produced.",
    )
    _add_report_table(
        document,
        "Do Not Use / Additional Data Required",
        _do_not_use_report_rows(main_table),
        columns=[
            "test_name",
            "unit",
            "group_level",
            "insufficient_groups",
            "n_range",
            "partitions",
        ],
        max_rows=max_table_rows,
        note="Rule: groups with n<40 are not calculated. Collect additional reference individuals before clinical use.",
        empty_message="No do-not-use groups were produced.",
    )
    _add_report_table(
        document,
        "Method Comparison",
        _method_report_rows(method_comparison_table),
        columns=[
            "analysis",
            "group_level",
            "n",
            "method_family",
            "ref_low",
            "ref_high",
            "ref_low_ci",
            "ref_high_ci",
        ],
        max_rows=max_table_rows,
        empty_message="No method comparison rows were produced.",
    )
    _add_report_table(
        document,
        "Reference Interval Difference Tests",
        _difference_report_rows(group_difference_table),
        columns=[
            "analysis",
            "partition_factor",
            "group_a",
            "group_b",
            "n_a",
            "n_b",
            "ri_overlap_fraction",
            "harris_boyd_decision",
            "recommendation",
        ],
        max_rows=max_table_rows,
        empty_message="No group difference rows were produced.",
    )
    _add_report_table(
        document,
        "Outliers",
        _sort_report_frame(outlier_table, ["test_name", "group_level", "source_row"]),
        columns=["test_name", "group_level", "sex_group", "age_group", "source_row", "value", "outlier_method"],
        max_rows=max_table_rows,
        empty_message="No outliers were removed.",
    )
    _add_report_warnings(document, warnings)

    histogram_images = _render_histogram_figures(
        group_analyses,
        normality_table,
        image_dir,
        max_groups=options.histogram_max_groups,
    )
    if histogram_images:
        document.add_heading("Group Distributions", level=1)
        for title, image_path in histogram_images:
            document.add_picture(str(image_path), width=Inches(8.2))

    document.save(output_path)
    return output_path


def _configure_report_document(document: Any) -> None:
    from docx.enum.section import WD_ORIENT  # noqa: PLC0415
    from docx.shared import Inches, Pt  # noqa: PLC0415

    section = document.sections[0]
    section.orientation = WD_ORIENT.LANDSCAPE
    section.page_width, section.page_height = section.page_height, section.page_width
    section.top_margin = Inches(0.55)
    section.bottom_margin = Inches(0.55)
    section.left_margin = Inches(0.55)
    section.right_margin = Inches(0.55)

    normal = document.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(9)
    for style_name, size in (("Heading 1", 14), ("Heading 2", 11)):
        style = document.styles[style_name]
        style.font.name = "Calibri"
        style.font.size = Pt(size)
        style.font.bold = True
    _add_page_number_footer(document)
    _set_update_fields(document)


def _add_table_of_contents(document: Any) -> None:
    document.add_heading("Table of Contents", level=1)
    paragraph = document.add_paragraph()
    _add_word_field(paragraph, r'TOC \o "1-2" \h \z \u', placeholder="Update field to view table of contents")


def _add_page_number_footer(document: Any) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH  # noqa: PLC0415

    for section in document.sections:
        paragraph = section.footer.paragraphs[0]
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        paragraph.text = ""
        paragraph.add_run("Page ")
        _add_word_field(paragraph, "PAGE", placeholder="1")
        paragraph.add_run(" of ")
        _add_word_field(paragraph, "NUMPAGES", placeholder="1")


def _add_word_field(paragraph: Any, instruction: str, *, placeholder: str = "") -> None:
    from docx.oxml import OxmlElement  # noqa: PLC0415
    from docx.oxml.ns import qn  # noqa: PLC0415

    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = instruction
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    text = OxmlElement("w:t")
    text.text = placeholder
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    for element in (begin, instr, separate, text, end):
        run._r.append(element)


def _set_update_fields(document: Any) -> None:
    from docx.oxml import OxmlElement  # noqa: PLC0415
    from docx.oxml.ns import qn  # noqa: PLC0415

    settings = document.settings.element
    if settings.find(qn("w:updateFields")) is not None:
        return
    update = OxmlElement("w:updateFields")
    update.set(qn("w:val"), "true")
    settings.append(update)


def _add_report_title(document: Any, title: str, subtitle: str) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH  # noqa: PLC0415
    from docx.shared import Pt  # noqa: PLC0415

    paragraph = document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run(title)
    run.bold = True
    run.font.size = Pt(20)

    sub = document.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub_run = sub.add_run(subtitle)
    sub_run.italic = True
    sub_run.font.size = Pt(10)


def _report_metrics_table(frame: pd.DataFrame) -> pd.DataFrame:
    test_count = int(frame["test_name"].nunique()) if not frame.empty and "test_name" in frame.columns else 0
    rows = [
        {"metric": "Tests", "value": test_count},
        {"metric": "Reference interval groups", "value": len(frame)},
        {"metric": "Candidate groups", "value": _reliability_count(frame, "ok")},
        {"metric": "Review groups", "value": _reliability_count(frame, "review")},
        {"metric": "Insufficient-n groups", "value": _reliability_count(frame, "insufficient_n")},
    ]
    return pd.DataFrame(rows, columns=["metric", "value"])


def _executive_summary_table(frame: pd.DataFrame, group_difference_table: pd.DataFrame) -> pd.DataFrame:
    candidate = _reliability_count(frame, "ok")
    review = _reliability_count(frame, "review")
    insufficient = _reliability_count(frame, "insufficient_n")
    partition_recommended = _recommendation_count(group_difference_table, {"partition_recommended", "review_partition_high_overlap", "review_low_overlap"})
    collect_more = _recommendation_count(group_difference_table, {"collect_more_data"})
    rows = [
        {"item": "Candidate intervals", "finding": f"{candidate} require verification before clinical adoption"},
        {"item": "Review-grade intervals", "finding": f"{review} calculated with robust small-sample method"},
        {"item": "Do-not-use groups", "finding": f"{insufficient} groups have n<40 and need additional data"},
        {"item": "Partition recommendation", "finding": f"{partition_recommended} planned contrasts suggest partition review"},
        {"item": "Additional collection", "finding": f"{collect_more} planned contrasts are underpowered for final partition decisions"},
    ]
    return pd.DataFrame(rows, columns=["item", "finding"])


def _recommendation_count(frame: pd.DataFrame, values: set[str]) -> int:
    if frame.empty or "recommendation" not in frame.columns:
        return 0
    return int(frame["recommendation"].isin(values).sum())


def _report_metadata_table(*, source_path: str, source_rows: int) -> pd.DataFrame:
    try:
        version = importlib_metadata.version("tametools")
    except importlib_metadata.PackageNotFoundError:
        version = "local source"
    rows = [
        {"field": "Generated at", "value": datetime.now().isoformat(timespec="seconds")},
        {"field": "Dataset path", "value": source_path or "<not recorded>"},
        {"field": "Source rows", "value": source_rows},
        {"field": "Reference population", "value": "Defined by input TAME dataset and RESULT/SEX/AGE/ITEM tags"},
        {"field": "Exclusions", "value": "Non-numeric result values removed; optional outlier policy applied per settings"},
        {"field": "Software", "value": f"tametools {version}"},
    ]
    return pd.DataFrame(rows, columns=["field", "value"])


def _threshold_glossary_table(options: ReferenceIntervalOptions) -> pd.DataFrame:
    rows = [
        {"term": "candidate", "definition": f"n>={options.min_n}; headline RI uses nonparametric CLSI EP28-A3c rule"},
        {"term": "review", "definition": f"40<=n<{options.min_n}; headline RI uses robust small-sample rule"},
        {"term": "insufficient_n", "definition": "n<40; RI is not calculated and additional reference samples are required"},
        {"term": "CI adequacy", "definition": f"inadequate if widest limit CI exceeds {options.ci_width_max_frac:.0%} of RI width"},
        {"term": "All", "definition": "TESTNAME group level; all reference individuals for one analyte"},
        {"term": "Sex", "definition": "TESTNAME+SEX group level"},
        {"term": "Age", "definition": "TESTNAME+AGE group level"},
        {"term": "Sex + Age", "definition": "TESTNAME+SEX+AGE group level"},
        {"term": "Nonparametric", "definition": "nonparametric_quantile_0.025_0.975; CLSI headline method for n>=120"},
        {"term": "Robust", "definition": "robust_median_mad_0.025_0.975; headline method for 40<=n<120"},
        {"term": "Parametric", "definition": "parametric_normal_0.025_0.975"},
        {"term": "Not calculated", "definition": "not_calculated_insufficient_n; n<40"},
        {"term": "Comparator policy", "definition": f"{options.comparator_policy}; comparator result strings are interpreted by this policy"},
        {"term": "Quantile method", "definition": f"{options.quantile_method}; used for nonparametric quantile estimation"},
    ]
    return pd.DataFrame(rows, columns=["term", "definition"])


def _report_settings_table(options: ReferenceIntervalOptions) -> pd.DataFrame:
    rows = [
        {"setting": "Requested method", "value": options.method},
        {"setting": "Headline method rule", "value": f"n>={options.min_n}: nonparametric; 40<=n<{options.min_n}: robust; n<40: not calculated"},
        {"setting": "Reference limits", "value": f"{options.lower_q:.1%} to {options.upper_q:.1%}"},
        {"setting": "Confidence interval", "value": f"{options.ci_method}, {options.ci_level:.0%}"},
        {"setting": "Minimum n", "value": options.min_n},
        {"setting": "Outlier method", "value": options.outlier_method},
        {"setting": "Comparator policy", "value": options.comparator_policy},
        {"setting": "Age bin width", "value": options.age_bin_width},
        {"setting": "Quantile method", "value": options.quantile_method},
        {"setting": "CI width threshold", "value": f"{options.ci_width_max_frac:.0%} of reference interval width"},
        {"setting": "Method comparison bootstrap n", "value": options.method_comparison_bootstrap_n},
        {"setting": "Normality alpha", "value": options.normality_alpha},
        {"setting": "Group difference alpha", "value": options.group_diff_alpha},
        {"setting": "Histogram max groups", "value": options.histogram_max_groups},
    ]
    return pd.DataFrame(rows, columns=["setting", "value"])


def _normality_report_rows(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    report = (
        frame.assign(
            _group_level_rank=frame["group_level"].map(_group_level_rank),
            _sex_rank=frame["sex_group"].map(_sex_sort_rank) if "sex_group" in frame.columns else 0,
            _age_rank=frame["age_group"].map(_age_sort_rank) if "age_group" in frame.columns else 0,
        )
        .sort_values(
            ["test_name", "_group_level_rank", "_sex_rank", "_age_rank", "partition"],
            na_position="last",
            kind="mergesort",
        )
        .drop(columns=["_group_level_rank", "_sex_rank", "_age_rank"])
    )
    report = _with_analysis_column(report)
    for column in ("shapiro_p", "dagostino_p"):
        if column in report.columns:
            report[column] = report[column].map(_p_value_text)
    return report


def _group_level_rank(value: Any) -> int:
    return {
        "TESTNAME": 0,
        "TESTNAME+SEX": 1,
        "TESTNAME+AGE": 2,
        "TESTNAME+SEX+AGE": 3,
    }.get(str(value), 9)


def _sex_sort_rank(value: Any) -> tuple[int, str]:
    text = str(value)
    return {
        "ALL": (0, text),
        "female": (1, text),
        "male": (2, text),
    }.get(text, (9, text))


def _age_sort_rank(value: Any) -> tuple[float, str]:
    text = str(value)
    if text == "ALL":
        return (float("-inf"), text)
    low, _high = age_band_bounds(text)
    if low is None:
        return (float("inf"), text)
    return (float(low), text)


def _method_report_rows(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    method_rank = {"parametric": 0, "nonparametric": 1, "robust": 2}
    report = frame.copy()
    report = _format_ri_report_values(report)
    report["ref_low_ci"] = [
        _ci_interval_text(low, high, unit)
        for low, high, unit in zip(frame["ref_low_ci_low"], frame["ref_low_ci_high"], frame["unit"])
    ]
    report["ref_high_ci"] = [
        _ci_interval_text(low, high, unit)
        for low, high, unit in zip(frame["ref_high_ci_low"], frame["ref_high_ci_high"], frame["unit"])
    ]
    return (
        report.assign(_method_rank=report["method_family"].map(lambda value: method_rank.get(str(value), 9)))
        .sort_values(["test_name", "group_level", "partition", "_method_rank"], na_position="last", kind="mergesort")
        .drop(columns=["_method_rank"])
    )


def _difference_report_rows(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    recommendation_rank = {
        "collect_more_data": 0,
        "partition_recommended": 1,
        "review_partition_high_overlap": 2,
        "review_low_overlap": 3,
        "combine_ok": 4,
    }
    report = frame.copy()
    report["_min_p"] = [
        _min_p_value(row.welch_p, row.mannwhitney_p, row.ks_p)
        for row in report.itertuples(index=False)
    ]
    report["_recommendation_rank"] = report["recommendation"].map(lambda value: recommendation_rank.get(str(value), 9))
    report = _with_analysis_column(report)
    for column in ("welch_p", "mannwhitney_p", "ks_p"):
        if column in report.columns:
            report[column] = report[column].map(_p_value_text)
    return (
        report.sort_values(["_recommendation_rank", "test_name", "partition_factor", "stratum"], na_position="last", kind="mergesort")
        .drop(columns=["_recommendation_rank", "_min_p"])
    )


def _ci_interval_text(low: Any, high: Any, unit: Any = "") -> str:
    if low is None or high is None:
        return ""
    try:
        if pd.isna(low) or pd.isna(high):
            return ""
    except (TypeError, ValueError):
        return ""
    return f"{_ri_value_text(low, unit)} to {_ri_value_text(high, unit)}"


def _format_ri_report_values(frame: pd.DataFrame) -> pd.DataFrame:
    report = frame.copy()
    report = _with_analysis_column(report)
    if "unit" not in report.columns:
        return report
    if {"ref_low_ci90_low", "ref_low_ci90_high"} <= set(report.columns):
        report["ref_low_ci"] = [
            _ci_interval_text(low, high, unit)
            for low, high, unit in zip(report["ref_low_ci90_low"], report["ref_low_ci90_high"], report["unit"])
        ]
    if {"ref_high_ci90_low", "ref_high_ci90_high"} <= set(report.columns):
        report["ref_high_ci"] = [
            _ci_interval_text(low, high, unit)
            for low, high, unit in zip(report["ref_high_ci90_low"], report["ref_high_ci90_high"], report["unit"])
        ]
    for column in ("ref_low", "ref_high", "ref_width", "widest_ref_width"):
        if column in report.columns:
            report[column] = [_ri_value_text(value, unit) for value, unit in zip(report[column], report["unit"])]
    return report


def _with_analysis_column(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "analysis" in frame.columns or "test_name" not in frame.columns:
        return frame
    report = frame.copy()
    report["analysis"] = [_analysis_label(row) for row in report.to_dict("records")]
    return report


def _analysis_label(row: dict[str, Any]) -> str:
    test_name = _plain_text(row.get("test_name"))
    partition = _plain_text(row.get("partition"))
    unit = _plain_text(row.get("unit"))
    label = partition if partition and partition != test_name else test_name
    if unit:
        return f"{label} ({unit})"
    return label


def _plain_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        return ""
    return str(value)


def _ri_value_text(value: Any, unit: Any = "") -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        return ""
    numeric = float(value)
    unit_text = str(unit)
    if unit_text == "U/L":
        return f"{numeric:.0f}"
    if unit_text == "g/dL":
        return f"{numeric:.1f}"
    return f"{numeric:.3g}"


def _p_value_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        return ""
    numeric = float(value)
    if numeric < 0.001:
        return "<0.001"
    return f"{numeric:.3f}"


def _min_p_value(*values: Any) -> float:
    valid: list[float] = []
    for value in values:
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric):
            valid.append(numeric)
    return min(valid) if valid else float("inf")


def _candidate_report_rows(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "reliability" not in frame.columns:
        return frame.iloc[0:0].copy()
    candidates = frame.loc[frame["reliability"] == "ok"].copy()
    return _format_ri_report_values(_sort_report_frame(candidates, ["test_name", "group_level", "sex_group", "age_low", "partition"]))


def _review_report_rows(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "reliability" not in frame.columns:
        return frame.iloc[0:0].copy()
    review = frame.loc[frame["reliability"] == "review"].copy()
    return _format_ri_report_values(_sort_report_frame(review, ["test_name", "group_level", "sex_group", "age_low", "partition"]))


def _do_not_use_report_rows(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "reliability" not in frame.columns:
        return frame.iloc[0:0].copy()
    insufficient = frame.loc[frame["reliability"] == "insufficient_n"].copy()
    report = _sort_report_frame(insufficient, ["test_name", "group_level", "sex_group", "age_low", "partition"])
    rows: list[dict[str, Any]] = []
    group_columns = [column for column in ["test_name", "unit", "group_level"] if column in report.columns]
    if not group_columns:
        return report.iloc[0:0].copy()
    for group_key, group in report.groupby(group_columns, dropna=False, observed=False):
        if not isinstance(group_key, tuple):
            group_key = (group_key,)
        values = dict(zip(group_columns, group_key))
        ns = [int(value) for value in group["n"].dropna().tolist()] if "n" in group.columns else []
        partitions = [
            f"{_compact_partition_label(row)} (n={int(row.n)})"
            for row in group.itertuples(index=False)
            if hasattr(row, "n")
        ]
        rows.append(
            {
                "test_name": values.get("test_name"),
                "unit": values.get("unit"),
                "group_level": values.get("group_level"),
                "insufficient_groups": int(group.shape[0]),
                "n_range": _n_range_text(ns),
                "partitions": "; ".join(partitions),
            }
        )
    return pd.DataFrame(
        rows,
        columns=["test_name", "unit", "group_level", "insufficient_groups", "n_range", "partitions"],
    )


def _compact_partition_label(row: Any) -> str:
    level = str(getattr(row, "group_level", ""))
    if level == "TESTNAME":
        return "All"
    if level == "TESTNAME+SEX":
        return str(getattr(row, "sex_group", ""))
    if level == "TESTNAME+AGE":
        return str(getattr(row, "age_group", ""))
    if level == "TESTNAME+SEX+AGE":
        return f"{getattr(row, 'sex_group', '')} / {getattr(row, 'age_group', '')}"
    return str(getattr(row, "partition", ""))


def _n_range_text(values: list[int]) -> str:
    if not values:
        return ""
    low = min(values)
    high = max(values)
    return str(low) if low == high else f"{low}-{high}"


def _sort_report_frame(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    if frame.empty:
        return frame
    sort_columns = [column for column in columns if column in frame.columns]
    if not sort_columns:
        return frame
    return frame.sort_values(sort_columns, na_position="last", kind="mergesort")


def _add_report_table(
    document: Any,
    title: str,
    frame: pd.DataFrame,
    *,
    columns: list[str] | None = None,
    max_rows: int = 30,
    note: str = "",
    empty_message: str = "No rows.",
) -> None:
    document.add_heading(title, level=2)
    if note:
        document.add_paragraph(note)
    display = _report_display_frame(frame, columns=columns, max_rows=max_rows)
    if display.empty:
        document.add_paragraph(empty_message)
        return

    table = document.add_table(rows=1, cols=len(display.columns))
    try:
        table.style = "Light Grid Accent 1"
    except KeyError:
        table.style = "Table Grid"
    table.autofit = True

    header_cells = table.rows[0].cells
    _set_repeat_table_header(table.rows[0])
    for index, column in enumerate(display.columns):
        _set_report_cell_text(header_cells[index], _report_column_label(column), bold=True)
        _shade_report_cell(header_cells[index], "D9EAF7")

    for record in display.to_dict("records"):
        cells = table.add_row().cells
        for index, column in enumerate(display.columns):
            value = record.get(column)
            _set_report_cell_text(cells[index], _format_report_value(value, column=column))
            fill = _status_fill(column, value)
            if fill:
                _shade_report_cell(cells[index], fill)


def _report_display_frame(frame: pd.DataFrame, *, columns: list[str] | None, max_rows: int) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    selected = [column for column in (columns or list(frame.columns)) if column in frame.columns]
    if not selected:
        return pd.DataFrame()
    return frame.loc[:, selected].copy()


def _set_report_cell_text(cell: Any, text: str, *, bold: bool = False) -> None:
    from docx.shared import Pt  # noqa: PLC0415

    cell.text = ""
    paragraph = cell.paragraphs[0]
    paragraph.paragraph_format.space_after = Pt(0)
    run = paragraph.add_run(text)
    run.bold = bold
    run.font.size = Pt(8)


def _shade_report_cell(cell: Any, fill: str) -> None:
    from docx.oxml import OxmlElement  # noqa: PLC0415
    from docx.oxml.ns import qn  # noqa: PLC0415

    tc_pr = cell._tc.get_or_add_tcPr()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:fill"), fill)
    tc_pr.append(shading)


def _set_repeat_table_header(row: Any) -> None:
    from docx.oxml import OxmlElement  # noqa: PLC0415
    from docx.oxml.ns import qn  # noqa: PLC0415

    tr_pr = row._tr.get_or_add_trPr()
    if tr_pr.find(qn("w:tblHeader")) is not None:
        return
    header = OxmlElement("w:tblHeader")
    header.set(qn("w:val"), "true")
    tr_pr.append(header)


def _report_column_label(column: str) -> str:
    return column.replace("_", " ").title()


def _format_report_value(value: Any, *, column: str | None = None) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, (np.integer, int)) and not isinstance(value, bool):
        return str(int(value))
    label = _report_value_label(column or "", value)
    if label is not None:
        return label
    if column in {"skewness", "kurtosis_excess", "ri_overlap_fraction", "widest_relative_width"}:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return str(value)
        if not math.isfinite(numeric):
            return ""
        return f"{numeric:.2f}"
    if isinstance(value, (np.floating, float)):
        numeric = float(value)
        if not math.isfinite(numeric):
            return ""
        return f"{numeric:.4g}"
    return str(value)


def _report_value_label(column: str, value: Any) -> str | None:
    text = str(value)
    if column == "method":
        return _method_display_label(text)
    if column == "method_family":
        return _METHOD_FAMILY_LABELS.get(text, text.replace("_", " ").title())
    if column == "group_level":
        return _GROUP_LEVEL_LABELS.get(text, text.replace("+", " + "))
    if column == "partition_factor":
        return _PARTITION_FACTOR_LABELS.get(text, text.title())
    if column == "ci_adequacy":
        return _CI_ADEQUACY_LABELS.get(text, text.replace("_", " ").title())
    if column == "reliability":
        return _RELIABILITY_LABELS.get(text, text.replace("_", " ").title())
    if column == "recommendation":
        return _RECOMMENDATION_LABELS.get(text, text.replace("_", " ").title())
    if column in {"normality_decision", "harris_boyd_decision", "distribution_decision", "ri_decision", "decision"}:
        return _DECISION_LABELS.get(text, text.replace("_", " ").title())
    if column in {"dagostino_status", "shapiro_status"}:
        return _STATUS_LABELS.get(text, text.replace("_", " ").title())
    return None


def _method_display_label(value: str) -> str:
    if value.startswith("nonparametric_"):
        return "Nonparametric"
    if value.startswith("robust_"):
        return "Robust"
    if value.startswith("parametric_"):
        return "Parametric"
    if value.startswith("log_parametric_"):
        return "Log-parametric"
    if value == "not_calculated_insufficient_n":
        return "Not calculated"
    return value.replace("_", " ").title()


def _status_fill(column: str, value: Any) -> str:
    text = str(value)
    if column == "ci_adequacy":
        return {
            "adequate": "DDEEDC",
            "inadequate": "FCE4BF",
            "not_calculated": "F2F2F2",
            "not_calculated_insufficient_n": "F4CCCC",
        }.get(text, "")
    if column == "reliability":
        return {
            "ok": "DDEEDC",
            "review": "FCE4BF",
            "insufficient_n": "F4CCCC",
        }.get(text, "")
    if column in {"recommendation", "decision"}:
        return {
            "candidate_for_verification": "DDEEDC",
            "combine_ok": "DDEEDC",
            "use_with_laboratory_review": "FCE4BF",
            "review_partition_high_overlap": "FCE4BF",
            "review_low_overlap": "FCE4BF",
            "partition_recommended": "FCE4BF",
            "collect_more_data": "F4CCCC",
            "do_not_use_as_final_interval": "F4CCCC",
        }.get(text, "")
    if column in {"normality_decision", "harris_boyd_decision"}:
        return {
            "compatible_with_normal": "DDEEDC",
            "combine_ok": "DDEEDC",
            "no_partition_needed": "DDEEDC",
            "non_normal_review": "FCE4BF",
            "partition_recommended": "FCE4BF",
            "insufficient_n": "F4CCCC",
        }.get(text, "")
    if column == "method" and text == "not_calculated_insufficient_n":
        return "F4CCCC"
    return ""


_GROUP_LEVEL_LABELS = {
    "TESTNAME": "All",
    "TESTNAME+SEX": "Sex",
    "TESTNAME+AGE": "Age",
    "TESTNAME+SEX+AGE": "Sex + Age",
}

_PARTITION_FACTOR_LABELS = {
    "SEX": "Sex",
    "AGE": "Age",
}

_METHOD_FAMILY_LABELS = {
    "parametric": "Parametric",
    "nonparametric": "Nonparametric",
    "robust": "Robust",
}

_CI_ADEQUACY_LABELS = {
    "adequate": "Adequate",
    "inadequate": "Inadequate",
    "not_calculated": "Not calculated",
    "not_calculated_insufficient_n": "Not calculated",
}

_RELIABILITY_LABELS = {
    "ok": "OK",
    "review": "Review",
    "insufficient_n": "Insufficient n",
}

_RECOMMENDATION_LABELS = {
    "candidate_for_verification": "Candidate",
    "use_with_laboratory_review": "Review before use",
    "do_not_use_as_final_interval": "Do not use",
    "collect_more_data": "Collect more data",
    "partition_recommended": "Partition recommended",
    "review_partition_high_overlap": "Review partition",
    "review_low_overlap": "Review low overlap",
    "combine_ok": "Combine OK",
}

_DECISION_LABELS = {
    "compatible_with_normal": "Compatible",
    "non_normal_review": "Review non-normal",
    "not_tested": "Not tested",
    "not_tested_insufficient_n": "Not tested",
    "not_tested_scipy_unavailable": "Not tested",
    "different_distribution": "Different distribution",
    "no_significant_difference": "No significant difference",
    "intervals_overlap": "Intervals overlap",
    "limited_interval_overlap": "Limited overlap",
    "intervals_separated": "Separated",
    "partition_recommended": "Partition recommended",
    "no_partition_needed": "No partition needed",
    "insufficient_n": "Insufficient n",
    "collect_more_data": "Collect more data",
    "review_partition_high_overlap": "Review partition",
    "review_low_overlap": "Review low overlap",
    "combine_ok": "Combine OK",
}

_STATUS_LABELS = {
    "computed": "Computed",
    "n too small to compute": "n too small to compute",
    "not computed": "Not computed",
    "scipy unavailable": "SciPy unavailable",
}


def _add_report_warnings(document: Any, warnings: list[str]) -> None:
    if not warnings:
        return
    _add_report_table(
        document,
        "Quality Notes",
        _warning_summary_table(warnings),
        columns=["warning_type", "count", "example"],
        max_rows=100,
        empty_message="No quality warnings were produced.",
    )


def _add_summary_charts(
    document: Any,
    summary_table: pd.DataFrame,
    reliability_table: pd.DataFrame,
    image_dir: Path,
) -> None:
    from docx.shared import Inches  # noqa: PLC0415

    specs = _summary_chart_specs(summary_table, reliability_table)
    if not specs:
        return
    document.add_heading("Summary Charts", level=2)
    tables = {"main": pd.DataFrame()}
    for index, spec in enumerate(specs, start=1):
        image_path = render_chart_png(spec, tables, image_dir / f"summary_{index:02d}.png")
        document.add_picture(str(image_path), width=Inches(5.2))


def _summary_chart_specs(summary_table: pd.DataFrame, reliability_table: pd.DataFrame) -> list[dict[str, Any]]:
    charts: list[dict[str, Any]] = []
    if not reliability_table.empty:
        rows = []
        for row in reliability_table.to_dict("records"):
            rows.append(
                {
                    "reliability": _RELIABILITY_LABELS.get(str(row.get("reliability")), str(row.get("reliability"))),
                    "group_level": _GROUP_LEVEL_LABELS.get(str(row.get("group_level")), str(row.get("group_level"))),
                    "group_count": row.get("group_count"),
                }
            )
        charts.append(
            chart_spec(
                "RI_GROUP_COUNT_BY_RELIABILITY",
                type="bar",
                title="Group count by reliability",
                x="reliability",
                y="group_count",
                series="group_level",
                rows=rows,
                max_points=40,
            )
        )
    if not summary_table.empty:
        candidate_rows = []
        for row in summary_table.to_dict("records"):
            test_name = row.get("test_name")
            candidate_rows.append({"test_name": test_name, "group_type": "Candidate", "count": row.get("ok_groups")})
            candidate_rows.append({"test_name": test_name, "group_type": "Review", "count": row.get("review_groups")})
        charts.append(
            chart_spec(
                "RI_CANDIDATE_REVIEW_BY_TEST",
                type="bar",
                title="Candidate and review groups by test",
                x="test_name",
                y="count",
                series="group_type",
                rows=candidate_rows,
                max_points=80,
            )
        )
        width_rows = [
            {
                "test_name": row.get("test_name"),
                "widest_relative_width": row.get("widest_relative_width"),
            }
            for row in summary_table.to_dict("records")
        ]
        charts.append(
            chart_spec(
                "RI_WIDEST_RELATIVE_WIDTH_BY_TEST",
                type="bar",
                title="Widest relative reference interval width by test",
                x="test_name",
                y="widest_relative_width",
                rows=width_rows,
                max_points=80,
            )
        )
    return charts


def _warning_summary_table(warnings: list[str]) -> pd.DataFrame:
    grouped: dict[str, dict[str, Any]] = {}
    for warning in warnings:
        warning_type = _warning_type(str(warning))
        entry = grouped.setdefault(warning_type, {"warning_type": warning_type, "count": 0, "example": str(warning)})
        entry["count"] += 1
    rows = sorted(grouped.values(), key=lambda row: (-int(row["count"]), str(row["warning_type"])))
    return pd.DataFrame(rows, columns=["warning_type", "count", "example"])


def _warning_type(warning: str) -> str:
    text = warning.lower()
    if "wide reference-limit confidence interval" in text:
        return "Wide confidence interval"
    if "negative reference limit or ci bound" in text:
        return "Negative limit clipped"
    if "not calculated" in text and "n=" in text:
        return "Not calculated, n<40"
    if "high outlier removal rate" in text:
        return "High outlier removal"
    if "overridden" in text:
        return "Method override"
    if "skewed data" in text or "normality" in text:
        return "Distribution review"
    if "rank ci is undefined" in text:
        return "Rank CI unavailable"
    if "missing" in text:
        return "Missing input metadata"
    return "Other"


def _render_histogram_figures(
    groups: list[GroupAnalysis],
    normality_table: pd.DataFrame,
    image_dir: Path,
    *,
    max_groups: int,
) -> list[tuple[str, Path]]:
    if max_groups <= 0 or not groups:
        return []
    selected = sorted(groups, key=lambda group: (str(group.test_name), group.group_level, _analysis_sort_key(group), group.partition))
    selected = selected[:max_groups]
    if not selected:
        return []

    mpl_config_dir = Path(tempfile.gettempdir()) / "tametools-matplotlib"
    mpl_config_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(mpl_config_dir))
    try:
        import matplotlib  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - report extra controls this
        raise RuntimeError(
            "chart rendering requires the report extra. Install with "
            "python3 -m pip install './tametools[report]' --no-build-isolation "
            "or use './tametools[report,web]' for the web workbench."
        ) from exc

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: PLC0415

    normality_lookup = _normality_lookup(normality_table)
    grouped: dict[tuple[str, str], list[GroupAnalysis]] = {}
    for group in selected:
        if group.group_level == "TESTNAME":
            grouped.setdefault(("All tests", group.group_level), []).append(group)
        else:
            grouped.setdefault((str(group.test_name), group.group_level), []).append(group)

    images: list[tuple[str, Path]] = []
    for index, ((test_name, group_level), members) in enumerate(grouped.items(), start=1):
        path = image_dir / f"hist_{index:02d}.png"
        group_label = _GROUP_LEVEL_LABELS.get(group_level, group_level)
        title = f"{test_name} - {group_label}"
        _render_histogram_grid(plt, members, normality_lookup, title=title, path=path)
        images.append((title, path))
    return images


def _render_histogram_grid(
    plt: Any,
    groups: list[GroupAnalysis],
    normality_lookup: dict[tuple[str, str], dict[str, Any]],
    *,
    title: str,
    path: Path,
) -> None:
    cols = 3 if len(groups) > 4 else (2 if len(groups) > 1 else 1)
    rows = int(math.ceil(len(groups) / cols))
    fig_width = 8.5
    fig_height = max(2.8, 2.15 * rows)
    fig, axes = plt.subplots(rows, cols, figsize=(fig_width, fig_height), squeeze=False)
    fig.suptitle(title, fontsize=12, fontweight="bold")
    for axis, group in zip(axes.ravel(), groups):
        arr = np.asarray(group.values, dtype=float)
        axis.set_title(_histogram_title(group, normality_lookup), fontsize=8)
        if arr.size == 0:
            axis.text(0.5, 0.5, "No data", ha="center", va="center")
            axis.set_axis_off()
            continue
        if arr.size == 1 or float(np.min(arr)) == float(np.max(arr)):
            axis.axvline(float(arr[0]), color="#2F5D8C", linewidth=2)
            axis.set_ylim(0, 1)
        else:
            bins = min(24, max(6, int(math.sqrt(arr.size))))
            axis.hist(arr, bins=bins, density=True, color="#5B8DB8", edgecolor="white", alpha=0.78)
            mean = float(np.mean(arr))
            sd = float(np.std(arr, ddof=1)) if arr.size > 1 else 0.0
            if sd > 0:
                xs = np.linspace(float(np.min(arr)), float(np.max(arr)), 120)
                ys = np.exp(-0.5 * ((xs - mean) / sd) ** 2) / (sd * math.sqrt(2.0 * math.pi))
                axis.plot(xs, ys, color="#C43D3D", linewidth=1.3)
        axis.set_xlabel("value", fontsize=7)
        axis.set_ylabel("density", fontsize=7)
        axis.tick_params(axis="both", labelsize=7)
    for axis in axes.ravel()[len(groups):]:
        axis.set_axis_off()
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _normality_lookup(normality_table: pd.DataFrame) -> dict[tuple[str, str], dict[str, Any]]:
    if normality_table.empty:
        return {}
    lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for row in normality_table.to_dict("records"):
        lookup[(str(row.get("partition")), str(row.get("group_level")))] = row
    return lookup


def _histogram_title(group: GroupAnalysis, normality_lookup: dict[tuple[str, str], dict[str, Any]]) -> str:
    row = normality_lookup.get((str(group.partition), str(group.group_level)), {})
    decision = _DECISION_LABELS.get(str(row.get("normality_decision") or ""), str(row.get("normality_decision") or ""))
    shapiro_p = row.get("shapiro_p")
    p_text = ""
    if shapiro_p is not None:
        try:
            if not pd.isna(shapiro_p):
                p_text = f", Shapiro p={float(shapiro_p):.3g}"
        except (TypeError, ValueError):
            p_text = ""
    label = _short_group_label(group)
    return f"{label} (n={len(group.values)}{p_text})\n{decision}"


def _chart_specs(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    return [
        chart_spec(
            "RI_REF_WIDTH",
            type="bar",
            title="Reference interval width",
            x="partition",
            y="ref_width",
            series="group_level",
            max_points=80,
        ),
        chart_spec(
            "RI_N_BY_GROUP",
            type="bar",
            title="Reference interval N by group",
            x="partition",
            y="n",
            series="group_level",
            max_points=80,
        ),
        chart_spec(
            "RI_HIGH_LIMIT",
            type="line",
            title="Reference interval upper limit",
            x="partition",
            y="ref_high",
            series="group_level",
            max_points=80,
        ),
    ]


def _report_summary(frame: pd.DataFrame, options: ReferenceIntervalOptions) -> str:
    if frame.empty:
        return "No reference interval rows were produced."
    ok = _reliability_count(frame, "ok")
    review = _reliability_count(frame, "review")
    insufficient = _reliability_count(frame, "insufficient_n")
    return (
        "Reference intervals were calculated using automatic CLSI EP28-A3c sample-size rules "
        f"(n>={options.min_n}: nonparametric, 40<=n<{options.min_n}: robust, n<40: not calculated), "
        f"limits {options.lower_q:.1%}-{options.upper_q:.1%}, "
        f"CI_METHOD={options.ci_method} ({options.ci_level:.0%}), and OUTLIER_METHOD={options.outlier_method}. "
        f"Groups: {len(frame)} total, {ok} ok, {review} review, {insufficient} insufficient_n. "
        "Rows marked insufficient_n should not be used as final clinical reference intervals without additional validation."
    )


def _reliability_count(frame: pd.DataFrame, value: str) -> int:
    if frame.empty or "reliability" not in frame.columns:
        return 0
    return int((frame["reliability"] == value).sum())


def _first_nonempty(values: pd.Series) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text and text.lower() != "nan":
            return text
    return ""


def _text_or_default(value: Any, default: str) -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _float_or_none(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)

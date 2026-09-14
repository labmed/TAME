from __future__ import annotations

import math
import re
from typing import Any, Iterable

import pandas as pd

from .age import age_band_label, age_bin_width_for_column, parse_age_to_years
from .cellstate import STATE_ABSENT, STATE_EMPTY, STATE_NULL, STATE_VALUE, STATE_WS, cell_state, state_counts
from .config import ci_get
from .images import is_valid_image_value
from .models import ColumnSpec, EDAReport, ReferenceIntervalPlan, TameDataset, ValidationIssue, ValidationResult
from .pandas_compat import concat_dataframes
from .review_profiles import parse_temporal_value
from .sex import normalize_sex
from .measurement_tags import is_categorical_measurement, measurement_tag_issues, require_measurement_tags


STRICT_NUM_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?$")
COMPARATOR_NUM_RE = re.compile(r"^(?P<op><=|>=|<|>|=)?\s*(?P<num>[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?)$")
COMPARATOR_CODE = {"": "EQ", "=": "EQ", "<": "LT", "<=": "LE", ">": "GT", ">=": "GE"}
COMPARATOR_SYMBOL = {value: key or "=" for key, value in COMPARATOR_CODE.items()}
IMPORTANT_PERCENTILES: tuple[tuple[str, float], ...] = (
    ("P0", 0.0),
    ("P1", 0.01),
    ("P2_5", 0.025),
    ("P5", 0.05),
    ("P10", 0.10),
    ("P25", 0.25),
    ("P50", 0.50),
    ("P75", 0.75),
    ("P90", 0.90),
    ("P95", 0.95),
    ("P97_5", 0.975),
    ("P99", 0.99),
    ("P100", 1.0),
)
LOWER_LIMIT_KEYS = ("MIN", "MIN_VALUE", "LOWER_LIMIT", "LOWER_BOUND")
UPPER_LIMIT_KEYS = ("MAX", "MAX_VALUE", "UPPER_LIMIT", "UPPER_BOUND")


def validate_dataset(dataset: TameDataset) -> ValidationResult:
    issues: list[ValidationIssue] = []
    valid_mask = pd.Series(True, index=dataset.df.index)

    for column in dataset.columns:
        series = dataset.df[column.name]
        column_issues, invalid_indexes = _validate_column(dataset, column, series)
        issues.extend(column_issues)
        if invalid_indexes:
            valid_mask.loc[list(invalid_indexes)] = False

    # 사용자 정의 범주형 어휘(STRICT) 위반도 검증 이슈로 보고한다.
    from .categories import category_validation_issues

    issues.extend(category_validation_issues(dataset))
    issues.extend(measurement_tag_issues(dataset))

    from .analysis_contract import contract_validation_issues

    issues.extend(contract_validation_issues(dataset))
    from .observation_contract import observation_validation_issues
    issues.extend(observation_validation_issues(dataset))

    from .integration import integration_validation_issues

    issues.extend(integration_validation_issues(dataset))

    from .planned_analysis import analysis_plan_validation_issues

    issues.extend(analysis_plan_validation_issues(dataset))

    settings = dataset.settings()
    action = str(ci_get(settings, "VALIDATE_ERROR", "REPORT")).upper()
    cleaned = dataset.with_df(dataset.df.loc[valid_mask].reset_index(drop=True) if action == "DELETE" else dataset.df)
    return ValidationResult(issues=issues, cleaned_dataset=cleaned)


def describe_dataset(dataset: TameDataset, *, comparator_policy: str | None = None) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for column in dataset.columns:
        values = dataset.df[column.name]
        counts = state_counts(values)
        row: dict[str, Any] = {
            "column": column.name,
            "tags": "::".join(column.tags),
            "value_cells": counts[STATE_VALUE],
            "absent_cells": counts[STATE_ABSENT],
            "null_cells": counts[STATE_NULL],
            "empty_cells": counts[STATE_EMPTY],
            "ws_cells": counts[STATE_WS],
        }

        if _is_numeric_column(column, dataset):
            numeric = numeric_series_for(dataset, column, crr_policy=comparator_policy)
            if dataset.column_has_tag(column, "AGE"):
                numeric = age_series_for(dataset, column)
            row.update({"kind": "numeric", **_summary_record(numeric)})
        else:
            non_null = values.loc[values.map(lambda value: cell_state(value) == STATE_VALUE)]
            row.update(
                {
                    "kind": "categorical",
                    "count": int(non_null.shape[0]),
                    "unique": int(non_null.nunique(dropna=True)),
                    "top": None,
                    "mean": None,
                    "median": None,
                    "q2_5": None,
                    "q97_5": None,
                }
            )
        rows.append(row)

    return pd.DataFrame(rows)


def exploratory_data_analysis(dataset: TameDataset, *, comparator_policy: str | None = None) -> EDAReport:
    policy = _normalize_comparator_policy(dataset, comparator_policy)
    effective_dataset = dataset
    effective_policy = policy
    warnings: list[str] = []

    if policy == "HARMONIZE":
        from .transforms import harmonize_comparator_thresholds

        effective_dataset, harmonization = harmonize_comparator_thresholds(dataset)
        warnings.extend(
            f"Harmonized {int(row.changed_rows)} rows in result_column={row.result_column}"
            + (f" item={row.item}" if "item" in harmonization.columns and row.item not in (None, "") else "")
            for row in harmonization.itertuples(index=False)
            if int(row.changed_rows) > 0
        )
        effective_policy = "VALUE"

    summary = describe_dataset(effective_dataset, comparator_policy=effective_policy)
    category_distribution = category_distribution_table(effective_dataset)
    numeric_percentiles = numeric_percentile_table(effective_dataset, comparator_policy=effective_policy)
    numeric_percentile_bands = numeric_percentile_band_table(effective_dataset, comparator_policy=effective_policy)
    result_by_summary = result_by_summary_table(effective_dataset, comparator_policy=effective_policy)
    comparator_profile = comparator_profile_table(dataset)
    impact = comparator_policy_impact(dataset, comparator_policy=policy)
    harmonization_preview = comparator_harmonization_preview(dataset)
    warnings.extend(_comparator_warnings(comparator_profile))
    return EDAReport(
        summary=summary,
        category_distribution=category_distribution,
        numeric_percentiles=numeric_percentiles,
        numeric_percentile_bands=numeric_percentile_bands,
        result_by_summary=result_by_summary,
        comparator_profile=comparator_profile,
        comparator_policy_impact=impact,
        harmonization_preview=harmonization_preview,
        warnings=warnings,
    )


def reference_interval_plan(dataset: TameDataset) -> ReferenceIntervalPlan:
    result_columns = tuple(column for column in dataset.columns_with_tag("RESULT")
                           if not is_categorical_measurement(dataset, column))
    item_column = dataset.first_column_with_tag("TESTNAME") or dataset.first_column_with_tag("ITEM")
    age_column = dataset.first_column_with_tag("AGE")
    sex_column = dataset.first_column_with_tag("SEX")
    by_columns = tuple(dataset.columns_with_tag("BY"))
    return ReferenceIntervalPlan(
        result_columns=result_columns,
        item_column=item_column,
        age_column=age_column,
        sex_column=sex_column,
        by_columns=by_columns,
    )


def category_distribution_table(dataset: TameDataset) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    total_rows = len(dataset.df)
    for column in dataset.columns_with_tag("CATEGORY"):
        values = [
            str(value).strip()
            for value in dataset.df[column.name]
            if cell_state(value) == STATE_VALUE
        ]
        valid_count = len(values)
        counts = pd.Series(values, dtype="object").value_counts(dropna=False) if values else pd.Series(dtype="int64")
        for rank, (category, count) in enumerate(counts.items(), start=1):
            rows.append(
                {
                    "column": column.name,
                    "tags": "::".join(column.tags),
                    "category": category,
                    "count": int(count),
                    "percent_of_column": _safe_percent(int(count), valid_count),
                    "percent_of_dataset": _safe_percent(int(count), total_rows),
                    "rank": rank,
                }
            )
    columns = ["column", "tags", "category", "count", "percent_of_column", "percent_of_dataset", "rank"]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns).sort_values(
        ["column", "rank", "category"],
        ascending=[True, True, True],
        kind="stable",
    ).reset_index(drop=True)


def numeric_percentile_table(dataset: TameDataset, *, comparator_policy: str | None = None) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for column in dataset.columns:
        if not _is_numeric_column(column, dataset):
            continue
        numeric = _numeric_values_for_eda(dataset, column, comparator_policy=comparator_policy)
        clean = numeric.dropna()
        for label, q in IMPORTANT_PERCENTILES:
            rows.append(
                {
                    "column": column.name,
                    "tags": "::".join(column.tags),
                    "stat": label,
                    "percentile": q * 100,
                    "cumulative_percent": q * 100,
                    "value": _safe_quantile(clean, q),
                    "count": int(clean.shape[0]),
                    "unavailable_count": int(numeric.shape[0] - clean.shape[0]),
                }
            )
    columns = ["column", "tags", "stat", "percentile", "cumulative_percent", "value", "count", "unavailable_count"]
    return pd.DataFrame(rows, columns=columns)


def numeric_percentile_band_table(dataset: TameDataset, *, comparator_policy: str | None = None) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for column in dataset.columns:
        if not _is_numeric_column(column, dataset):
            continue
        numeric = _numeric_values_for_eda(dataset, column, comparator_policy=comparator_policy)
        clean = numeric.dropna().astype(float)
        if clean.empty:
            continue
        thresholds = [(label, q, _safe_quantile(clean, q)) for label, q in IMPORTANT_PERCENTILES]
        for index in range(len(thresholds) - 1):
            lower_label, lower_q, lower_value = thresholds[index]
            upper_label, upper_q, upper_value = thresholds[index + 1]
            if lower_value is None or upper_value is None:
                continue
            if index == 0:
                mask = clean <= upper_value
            else:
                mask = (clean > lower_value) & (clean <= upper_value)
            count = int(mask.sum())
            rows.append(
                {
                    "column": column.name,
                    "tags": "::".join(column.tags),
                    "band": f"{lower_label}-{upper_label}",
                    "lower_percentile": lower_q * 100,
                    "upper_percentile": upper_q * 100,
                    "lower_value": lower_value,
                    "upper_value": upper_value,
                    "count": count,
                    "percent": _safe_percent(count, int(clean.shape[0])),
                    "expected_percent": (upper_q - lower_q) * 100,
                }
            )
    columns = [
        "column",
        "tags",
        "band",
        "lower_percentile",
        "upper_percentile",
        "lower_value",
        "upper_value",
        "count",
        "percent",
        "expected_percent",
    ]
    return pd.DataFrame(rows, columns=columns)


def result_by_summary_table(dataset: TameDataset, *, comparator_policy: str | None = None) -> pd.DataFrame:
    plan = reference_interval_plan(dataset)
    result_columns = [column for column in plan.result_columns if _is_numeric_column(column, dataset)]
    by_columns = _eda_by_columns(dataset)
    if not result_columns or not by_columns:
        return pd.DataFrame(columns=_result_by_columns())

    rows: list[dict[str, Any]] = []
    item_name = plan.item_column.name if plan.item_column is not None else None

    def append_row(
        result_column: ColumnSpec,
        by_column: ColumnSpec,
        by_grouping: str,
        by_value: Any,
        item_value: Any,
        values: pd.Series,
    ) -> None:
        row = {
            "result_column": result_column.name,
            "result_tags": "::".join(result_column.tags),
            "by_column": by_column.name,
            "by_tags": "::".join(by_column.tags),
            "by_grouping": by_grouping,
            "by_value": by_value,
            "item": item_value,
        }
        row.update(_summary_record(values))
        rows.append(row)

    for result_column in result_columns:
        numeric = _numeric_values_for_eda(dataset, result_column, comparator_policy=comparator_policy)
        base = pd.DataFrame({"_result_value": numeric}, index=dataset.df.index)
        if item_name is not None:
            base["_item"] = dataset.df[item_name].map(_category_text)

        for by_column in by_columns:
            group_frame = base.copy()
            by_grouping = "value"
            if dataset.column_has_tag(by_column, "AGE"):
                width = age_bin_width_for_column(dataset, by_column, default=10)
                group_frame["_by"] = age_series_for(dataset, by_column).map(lambda value: age_band_label(value, width=width))
                by_grouping = f"age_band_{width}"
            else:
                group_frame["_by"] = dataset.df[by_column.name].map(_category_text)
            group_frame = group_frame.loc[group_frame["_result_value"].notna()].copy()
            if group_frame.empty:
                continue

            if item_name is not None:
                for item_value, group in group_frame.groupby("_item", dropna=False, observed=False):
                    append_row(result_column, by_column, by_grouping, "all", item_value, group["_result_value"])
            else:
                append_row(result_column, by_column, by_grouping, "all", None, group_frame["_result_value"])

            group_frame = group_frame.loc[group_frame["_by"].notna()].copy()
            if group_frame.empty:
                continue
            group_keys = ["_by"] + (["_item"] if item_name is not None else [])
            for key, group in group_frame.groupby(group_keys, dropna=False, observed=False):
                if not isinstance(key, tuple):
                    key = (key,)
                by_value = key[0]
                item_value = key[1] if item_name is not None and len(key) > 1 else None
                append_row(result_column, by_column, by_grouping, by_value, item_value, group["_result_value"])
    if not rows:
        return pd.DataFrame(columns=_result_by_columns())
    return pd.DataFrame(rows, columns=_result_by_columns()).sort_values(
        ["result_column", "by_column", "by_value", "item"],
        kind="stable",
    ).reset_index(drop=True)


def numeric_series_for(dataset: TameDataset, column: ColumnSpec | str, *, crr_policy: str | None = None) -> pd.Series:
    require_measurement_tags(dataset)
    from .observation_contract import require_capabilities, result_status
    require_capabilities(dataset)
    spec = column if isinstance(column, ColumnSpec) else _resolve_column(dataset, column)
    if is_categorical_measurement(dataset, spec):
        raise ValueError(f"{spec.name}: NOMINAL/ORDINAL codes are not quantitative measurements")
    policy = _normalize_comparator_policy(dataset, crr_policy)
    from .planned_analysis import _eligibility
    eligible = _eligibility(dataset, spec)[0]
    if ci_get(dataset.column_metadata(spec), "CENSORING_INTERVAL", None) is not None:
        from .interval_censoring import interval_values
        return interval_values(dataset, spec, policy)[0].where(eligible)
    values: list[float | None] = []

    for value in dataset.df[spec.name]:
        if cell_state(value) != STATE_VALUE:
            values.append(None)
            continue

        text = str(value).strip()
        if dataset.column_has_tag(spec, "<NUM>"):
            parsed = parse_comparator_number(text)
            if parsed is None:
                values.append(None)
                continue
            comparator, number = parsed
            if comparator and comparator != "=" and policy != "VALUE":
                values.append(None)
            else:
                values.append(number)
            continue

        parsed = parse_strict_number(text)
        values.append(parsed)

    result = pd.Series(values, index=dataset.df.index, dtype="float64")
    return result.where(eligible)


def age_series_for(dataset: TameDataset, column: ColumnSpec | str) -> pd.Series:
    spec = column if isinstance(column, ColumnSpec) else _resolve_column(dataset, column)
    values = [parse_age_to_years(value) for value in dataset.df[spec.name]]
    return pd.Series(values, index=dataset.df.index, dtype="float64")


def parse_strict_number(text: str) -> float | None:
    if not STRICT_NUM_RE.fullmatch(text):
        return None
    value = float(text)
    return value if math.isfinite(value) else None


def parse_comparator_number(text: str) -> tuple[str, float] | None:
    match = COMPARATOR_NUM_RE.match(text)
    if not match:
        return None
    value = float(match.group("num"))
    return ((match.group("op") or ""), value) if math.isfinite(value) else None


def comparator_parts_series(dataset: TameDataset, column: ColumnSpec | str) -> pd.DataFrame:
    spec = column if isinstance(column, ColumnSpec) else _resolve_column(dataset, column)
    rows: list[dict[str, Any]] = []
    for row_index, value in dataset.df[spec.name].items():
        if _is_blank(value):
            rows.append(
                {
                    "row_index": row_index,
                    "raw_value": value,
                    "comparator_code": None,
                    "comparator_symbol": None,
                    "numeric_value": None,
                }
            )
            continue

        parsed = parse_comparator_number(str(value).strip())
        if parsed is None:
            rows.append(
                {
                    "row_index": row_index,
                    "raw_value": value,
                    "comparator_code": None,
                    "comparator_symbol": None,
                    "numeric_value": None,
                }
            )
            continue

        operator, number = parsed
        code = COMPARATOR_CODE.get(operator, None)
        rows.append(
            {
                "row_index": row_index,
                "raw_value": value,
                "comparator_code": code,
                "comparator_symbol": COMPARATOR_SYMBOL.get(code),
                "numeric_value": number,
            }
        )
    return pd.DataFrame(rows)


def comparator_profile_table(dataset: TameDataset) -> pd.DataFrame:
    plan = reference_interval_plan(dataset)
    rows: list[dict[str, Any]] = []
    for result_column in plan.result_columns:
        parts = comparator_parts_series(dataset, result_column)
        work = dataset.df.copy()
        work["_raw_value"] = parts["raw_value"]
        work["_comparator_code"] = parts["comparator_code"]
        work["_comparator_symbol"] = parts["comparator_symbol"]
        work["_numeric_value"] = parts["numeric_value"]
        bounded_codes = {"LT", "LE", "GT", "GE"}
        work = work.loc[work["_comparator_code"].isin(bounded_codes)].copy()
        if work.empty:
            continue

        if plan.item_column:
            grouped = (
                work.groupby([plan.item_column.name, "_comparator_code", "_numeric_value", "_raw_value"], dropna=False, observed=False)
                .size()
                .reset_index(name="count")
            )
            grouped.insert(0, "result_column", result_column.name)
            grouped.rename(columns={plan.item_column.name: "item"}, inplace=True)
            rows.extend(grouped.to_dict("records"))
        else:
            grouped = (
                work.groupby(["_comparator_code", "_numeric_value", "_raw_value"], dropna=False, observed=False)
                .size()
                .reset_index(name="count")
            )
            grouped.insert(0, "result_column", result_column.name)
            grouped.insert(1, "item", None)
            rows.extend(grouped.to_dict("records"))

    if not rows:
        return pd.DataFrame(columns=["result_column", "item", "comparator_code", "numeric_value", "raw_value", "count"])

    frame = pd.DataFrame(rows)
    frame.rename(
        columns={
            "_comparator_code": "comparator_code",
            "_numeric_value": "numeric_value",
            "_raw_value": "raw_value",
        },
        inplace=True,
    )
    frame["comparator_symbol"] = frame["comparator_code"].map(COMPARATOR_SYMBOL)
    columns = ["result_column", "item", "comparator_code", "comparator_symbol", "numeric_value", "raw_value", "count"]
    return frame[columns].sort_values(columns[:-1], kind="stable").reset_index(drop=True)


def comparator_policy_impact(dataset: TameDataset, *, comparator_policy: str | None = None) -> pd.DataFrame:
    policy = _normalize_comparator_policy(dataset, comparator_policy)
    effective_dataset = dataset
    effective_policy = policy
    harmonized_rows_by_column: dict[str, int] = {}
    if policy == "HARMONIZE":
        from .transforms import harmonize_comparator_thresholds

        effective_dataset, harmonization = harmonize_comparator_thresholds(dataset)
        effective_policy = "VALUE"
        if not harmonization.empty:
            harmonized_rows_by_column = (
                harmonization.groupby("result_column", dropna=False, observed=False)["changed_rows"].sum().astype(int).to_dict()
            )

    plan = reference_interval_plan(dataset)
    rows: list[dict[str, Any]] = []
    for result_column in plan.result_columns:
        values = dataset.df[result_column.name]
        strict_ok = values.map(lambda value: parse_strict_number(str(value).strip()) is not None if not _is_blank(value) else False)
        comparator_ok = values.map(lambda value: parse_comparator_number(str(value).strip()) is not None if not _is_blank(value) else False)
        comparator_parts = comparator_parts_series(dataset, result_column)
        exact_count = int((comparator_parts["comparator_code"] == "EQ").sum())
        bounded_count = int(comparator_parts["comparator_code"].isin(["LT", "LE", "GT", "GE"]).sum())
        used_numeric = int(numeric_series_for(effective_dataset, result_column.name, crr_policy=effective_policy).notna().sum())
        invalid_count = int((~values.map(_is_blank) & ~comparator_ok).sum())
        rows.append(
            {
                "result_column": result_column.name,
                "policy": policy,
                "non_blank": int((~values.map(_is_blank)).sum()),
                "strict_numeric_count": int(strict_ok.sum()),
                "eq_count": exact_count,
                "bounded_count": bounded_count,
                "numeric_used_under_policy": used_numeric,
                "invalid_count": invalid_count,
                "harmonized_rows": int(harmonized_rows_by_column.get(result_column.name, 0)),
            }
        )
    return pd.DataFrame(rows)


def comparator_harmonization_preview(dataset: TameDataset) -> pd.DataFrame:
    plan = reference_interval_plan(dataset)
    rows: list[dict[str, Any]] = []
    item_column_name = plan.item_column.name if plan.item_column else None
    for result_column in plan.result_columns:
        parts = comparator_parts_series(dataset, result_column)
        work = dataset.df.copy()
        work["_comparator_code"] = parts["comparator_code"]
        work["_numeric_value"] = parts["numeric_value"]
        if item_column_name:
            # 단일 컬럼은 스칼라로 groupby 한다. pandas 2.x 에서 [col] 리스트로 묶으면 그룹 키가
            # 스칼라가 아니라 튜플((AST,))로 나와, 이후 frame[item] == item_value 비교가 깨진다.
            grouped = work.groupby(item_column_name, dropna=False, observed=False)
        else:
            grouped = [(None, work)]

        for item_value, group in grouped:
            bounded = group.loc[group["_comparator_code"].isin(["LT", "LE", "GT", "GE"])].copy()
            if bounded.empty:
                continue

            for family_codes, family_name in ((["LT", "LE"], "LT"), (["GT", "GE"], "GT")):
                subset = bounded.loc[bounded["_comparator_code"].isin(family_codes)].copy()
                if subset.empty:
                    continue

                thresholds = sorted(float(value) for value in subset["_numeric_value"].dropna().unique())
                unified = max(thresholds) if family_name == "LT" else min(thresholds)
                eq_values = group.loc[group["_comparator_code"] == "EQ", "_numeric_value"].dropna().astype(float)
                if family_name == "LT":
                    convertible_exact = int(((eq_values >= min(thresholds)) & (eq_values < unified)).sum())
                else:
                    convertible_exact = int(((eq_values <= max(thresholds)) & (eq_values > unified)).sum())

                rows.append(
                    {
                        "result_column": result_column.name,
                        "item": item_value,
                        "family": family_name,
                        "existing_thresholds": ", ".join(_format_threshold_value(value) for value in thresholds),
                        "unified_threshold": _format_threshold_value(unified),
                        "unified_value": _format_comparator_value(family_name, unified),
                        "bounded_rows": int(subset.shape[0]),
                        "convertible_exact_rows": convertible_exact,
                        "mixed_thresholds": len(thresholds) > 1,
                    }
                )

    if not rows:
        return pd.DataFrame(
            columns=[
                "result_column",
                "item",
                "family",
                "existing_thresholds",
                "unified_threshold",
                "unified_value",
                "bounded_rows",
                "convertible_exact_rows",
                "mixed_thresholds",
            ]
        )

    return pd.DataFrame(rows)


def _validate_column(
    dataset: TameDataset,
    column: ColumnSpec,
    series: pd.Series,
) -> tuple[list[ValidationIssue], set[Any]]:
    issues: list[ValidationIssue] = []
    invalid_indexes: set[Any] = set()
    states = series.map(cell_state)
    unresolved = pd.Series(True, index=series.index)

    def add(mask: pd.Series, tag: str, message: str, *, severity: str = "error") -> None:
        failed = mask.fillna(False)
        if not bool(failed.any()):
            return
        _append_validation_issues(issues, column, series, failed, tag, message, severity=severity)
        invalid_indexes.update(series.loc[failed].index)
        unresolved.loc[failed] = False

    if dataset.column_has_tag(column, "REQUIRED"):
        add(states.eq(STATE_ABSENT) & unresolved, "REQUIRED", "Expected a value but found ABSENT.")
    if not _column_accepts_any(dataset, column, ("NULLABLE", "NULL_OK")):
        add(states.eq(STATE_NULL) & unresolved, "NULL", "NULL token is not allowed for this column.")
    if not dataset.column_has_tag(column, "EMPTY_OK"):
        add(states.eq(STATE_EMPTY) & unresolved, "EMPTY", "EMPTY token is not allowed for this column.")
    if not dataset.column_has_tag(column, "WS_OK"):
        add(states.eq(STATE_WS) & unresolved, "WS", "Whitespace-only value is not allowed for this column.")

    if dataset.column_has_tag(column, "NUM"):
        text = _active_text(series, states, unresolved)
        add(
            _mask_from_index(series.index, text.index[text.map(parse_strict_number).isna()]),
            "NUM",
            "Expected a strict numeric value.",
        )

    if dataset.column_has_tag(column, "<NUM>"):
        text = _active_text(series, states, unresolved)
        add(
            _mask_from_index(series.index, text.index[text.map(parse_comparator_number).isna()]),
            "<NUM>",
            "Expected a comparator-aware numeric value.",
        )

    if dataset.column_has_tag(column, "AGE"):
        text = _active_text(series, states, unresolved)
        add(
            _mask_from_index(series.index, text.index[text.map(parse_age_to_years).isna()]),
            "AGE",
            "Expected AGE as a non-negative number with UCUM unit a, mo, or d. Bare numbers are treated as years.",
        )

    if dataset.column_has_tag(column, "SEX"):
        text = _active_text(series, states, unresolved)
        add(
            _mask_from_index(series.index, text.index[text.map(normalize_sex).isna()]),
            "SEX",
            "Expected one of: male, female, other, unknown.",
        )

    if dataset.column_has_tag(column, "DATETIME") or dataset.column_has_tag(column, "DATE") or dataset.column_has_tag(column, "TIME"):
        active = series.loc[states.eq(STATE_VALUE) & unresolved]
        tag = (
            "DATETIME"
            if dataset.column_has_tag(column, "DATETIME")
            else ("DATE" if dataset.column_has_tag(column, "DATE") else "TIME")
        )
        add(
            _mask_from_index(series.index, active.index[active.map(lambda value: parse_temporal_value(value, column.tags)).isna()]),
            tag,
            "Expected a value convertible to date/datetime/time.",
        )

    _add_column_limit_issues(dataset, column, series, states, unresolved, add, issues)

    if dataset.column_has_tag(column, "IMAGE") and dataset.column_has_tag(column, "B64"):
        active = series.loc[states.eq(STATE_VALUE) & unresolved]
        add(
            _mask_from_index(
                series.index,
                active.index[active.map(lambda value: not is_valid_image_value(value, mode="B64"))],
            ),
            "IMAGE::B64",
            "Expected a base64 image value or data URL.",
        )

    if dataset.column_has_tag(column, "IMAGE") and dataset.column_has_tag(column, "PATH"):
        active = series.loc[states.eq(STATE_VALUE) & unresolved]
        add(
            _mask_from_index(
                series.index,
                active.index[active.map(lambda value: not is_valid_image_value(value, mode="PATH"))],
            ),
            "IMAGE::PATH",
            "Expected an image path value.",
        )

    return issues, invalid_indexes


def _add_column_limit_issues(
    dataset: TameDataset,
    column: ColumnSpec,
    series: pd.Series,
    states: pd.Series,
    unresolved: pd.Series,
    add,
    issues: list[ValidationIssue],
) -> None:
    lower_raw, lower_key, upper_raw, upper_key = _column_limit_values(dataset, column)
    if lower_raw is None and upper_raw is None:
        return

    if _is_temporal_column(column, dataset):
        lower = _parse_temporal_limit(lower_raw, column) if lower_raw is not None else None
        upper = _parse_temporal_limit(upper_raw, column) if upper_raw is not None else None
        value_parser = lambda value: parse_temporal_value(value, column.tags)
    elif _is_numeric_column(column, dataset):
        lower = _parse_numeric_limit(lower_raw, column, dataset) if lower_raw is not None else None
        upper = _parse_numeric_limit(upper_raw, column, dataset) if upper_raw is not None else None
        value_parser = lambda value: parse_age_to_years(value) if dataset.column_has_tag(column, "AGE") else _numeric_limit_value(value)
    else:
        return

    if lower_raw is not None and lower is None:
        _append_limit_config_issue(issues, column, lower_key or "MIN", lower_raw)
    if upper_raw is not None and upper is None:
        _append_limit_config_issue(issues, column, upper_key or "MAX", upper_raw)
    if (lower_raw is not None and lower is None) or (upper_raw is not None and upper is None):
        return
    if lower is not None and upper is not None and lower > upper:
        issues.append(
            ValidationIssue(
                row_number=1,
                column=column.name,
                tag="LIMIT",
                value=f"{lower_raw}..{upper_raw}",
                message=f"Column metadata lower limit is greater than upper limit ({lower_raw!r} > {upper_raw!r}).",
                severity="error",
            )
        )
        return

    active = series.loc[states.eq(STATE_VALUE) & unresolved]
    if active.empty:
        return
    parsed = active.map(value_parser)
    if lower is not None:
        lower_mask = pd.Series(False, index=series.index)
        lower_mask.loc[active.index] = parsed.map(lambda value: value is not None and value < lower)
        add(lower_mask, "MIN", f"Expected value >= {lower_raw}.")
    if upper is not None:
        upper_mask = pd.Series(False, index=series.index)
        upper_mask.loc[active.index] = parsed.map(lambda value: value is not None and value > upper)
        add(upper_mask, "MAX", f"Expected value <= {upper_raw}.")


def _column_limit_values(dataset: TameDataset, column: ColumnSpec) -> tuple[Any | None, str | None, Any | None, str | None]:
    config = dataset.column_metadata(column)
    limits = ci_get(config, "LIMITS", None)
    if isinstance(limits, dict):
        merged = dict(config)
        merged.update(limits)
        config = merged
    lower_key, lower_value = _first_present_config_value(config, LOWER_LIMIT_KEYS)
    upper_key, upper_value = _first_present_config_value(config, UPPER_LIMIT_KEYS)
    return lower_value, lower_key, upper_value, upper_key


def _first_present_config_value(config: dict[str, Any], keys: Iterable[str]) -> tuple[str | None, Any | None]:
    for key in keys:
        value = ci_get(config, key, None)
        if value is not None:
            return key, value
    return None, None


def _is_temporal_column(column: ColumnSpec, dataset: TameDataset) -> bool:
    return dataset.column_has_tag(column, "DATETIME") or dataset.column_has_tag(column, "DATE") or dataset.column_has_tag(column, "TIME")


def _parse_temporal_limit(value: Any, column: ColumnSpec) -> pd.Timestamp | None:
    return parse_temporal_value(value, column.tags)


def _parse_numeric_limit(value: Any, column: ColumnSpec, dataset: TameDataset) -> float | None:
    if dataset.column_has_tag(column, "AGE"):
        parsed_age = parse_age_to_years(value)
        return None if pd.isna(parsed_age) else float(parsed_age)
    parsed = _numeric_limit_value(value)
    return None if pd.isna(parsed) else parsed


def _numeric_limit_value(value: Any) -> float | None:
    if cell_state(value) != STATE_VALUE:
        return None
    text = str(value).strip()
    parsed = parse_comparator_number(text)
    if parsed is not None:
        return float(parsed[1])
    strict = parse_strict_number(text)
    return None if strict is None else float(strict)


def _append_limit_config_issue(
    issues: list[ValidationIssue],
    column: ColumnSpec,
    key: str,
    value: Any,
) -> None:
    issues.append(
        ValidationIssue(
            row_number=1,
            column=column.name,
            tag="LIMIT",
            value=value,
            message=f"Column metadata {key} limit could not be parsed.",
            severity="error",
        )
    )


def _active_text(series: pd.Series, states: pd.Series, unresolved: pd.Series) -> pd.Series:
    return series.loc[states.eq(STATE_VALUE) & unresolved].astype(str).str.strip()


def _mask_from_index(base_index: pd.Index, invalid_index: pd.Index) -> pd.Series:
    mask = pd.Series(False, index=base_index)
    if not invalid_index.empty:
        mask.loc[invalid_index] = True
    return mask


def _column_accepts_any(dataset: TameDataset, column: ColumnSpec, tags: Iterable[str]) -> bool:
    return any(dataset.column_has_tag(column, tag) for tag in tags)


def _append_validation_issues(
    issues: list[ValidationIssue],
    column: ColumnSpec,
    series: pd.Series,
    mask: pd.Series,
    tag: str,
    message: str,
    *,
    severity: str = "error",
) -> None:
    for row_idx, value in series.loc[mask].items():
        issues.append(
            ValidationIssue(
                row_number=_row_number(series, row_idx),
                column=column.name,
                tag=tag,
                value=value,
                message=message,
                severity=severity,
            )
        )


def _row_number(series: pd.Series, row_idx: Any) -> int:
    try:
        return int(row_idx) + 2
    except (TypeError, ValueError):
        return int(series.index.get_loc(row_idx)) + 2


def _resolve_column(dataset: TameDataset, name: str) -> ColumnSpec:
    for column in dataset.columns:
        if column.name == name:
            return column
    raise KeyError(name)


def _summary_record(series: pd.Series) -> dict[str, Any]:
    return {
        "count": int(series.notna().sum()),
        "mean": _safe_stat(series, "mean"),
        "std": _safe_stat(series, "std"),
        "min": _safe_stat(series, "min"),
        "q1": _safe_quantile(series, 0.01),
        "q2_5": _safe_quantile(series, 0.025),
        "q5": _safe_quantile(series, 0.05),
        "q10": _safe_quantile(series, 0.10),
        "q25": _safe_quantile(series, 0.25),
        "median": _safe_stat(series, "median"),
        "q75": _safe_quantile(series, 0.75),
        "q90": _safe_quantile(series, 0.90),
        "q95": _safe_quantile(series, 0.95),
        "q97_5": _safe_quantile(series, 0.975),
        "q99": _safe_quantile(series, 0.99),
        "max": _safe_stat(series, "max"),
    }


def _result_by_columns() -> list[str]:
    return [
        "result_column",
        "result_tags",
        "by_column",
        "by_tags",
        "by_grouping",
        "by_value",
        "item",
        *list(_summary_record(pd.Series(dtype="float64")).keys()),
    ]


def _is_numeric_column(column: ColumnSpec, dataset: TameDataset | None = None) -> bool:
    if dataset is not None:
        if is_categorical_measurement(dataset, column):
            return False
        return (
            dataset.column_has_tag(column, "NUM")
            or dataset.column_has_tag(column, "<NUM>")
            or dataset.column_has_tag(column, "AGE")
            or (dataset.column_has_tag(column, "RESULT") and not dataset.column_has_tag(column, "TXT"))
        )
    if column.has_any_tag(("NOMINAL", "ORDINAL")):
        return False
    return (
        column.has_tag("NUM")
        or column.has_tag("<NUM>")
        or column.has_tag("AGE")
        or (column.has_tag("RESULT") and not column.has_tag("TXT"))
    )


def _numeric_values_for_eda(dataset: TameDataset, column: ColumnSpec, *, comparator_policy: str | None = None) -> pd.Series:
    if dataset.column_has_tag(column, "AGE"):
        return age_series_for(dataset, column)
    return numeric_series_for(dataset, column, crr_policy=comparator_policy)


def _eda_by_columns(dataset: TameDataset) -> list[ColumnSpec]:
    plan = reference_interval_plan(dataset)
    excluded = {column.name for column in plan.result_columns}
    if plan.item_column is not None:
        excluded.add(plan.item_column.name)
    seen: set[str] = set()
    columns: list[ColumnSpec] = []
    for column in dataset.columns:
        if column.name in excluded or column.name in seen:
            continue
        if dataset.column_has_tag(column, "BY") or dataset.column_has_any_tag(column, ("SEX", "INSTRUMENT", "GROUP", "COHORT", "AGE")):
            columns.append(column)
            seen.add(column.name)
    return columns


def _category_text(value: Any) -> str | None:
    if cell_state(value) != STATE_VALUE:
        return None
    text = str(value).strip()
    return text or None


def _safe_percent(count: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return float(count) / float(denominator) * 100.0


def _safe_stat(series: pd.Series, method: str) -> float | None:
    clean = series.dropna()
    if clean.empty:
        return None
    value = getattr(clean, method)()
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return float(value)


def _safe_quantile(series: pd.Series, q: float) -> float | None:
    clean = series.dropna()
    if clean.empty:
        return None
    return float(clean.quantile(q))


def _is_blank(value: Any) -> bool:
    return cell_state(value) != STATE_VALUE


def _normalize_comparator_policy(dataset: TameDataset, policy: str | None) -> str:
    if policy is None:
        policy = ci_get(dataset.settings(), "CRR", "DELETE")
    normalized = str(policy).upper()
    if normalized not in {"DELETE", "VALUE", "KEEP", "HARMONIZE"}:
        return "DELETE"
    return normalized


def _comparator_warnings(profile: pd.DataFrame) -> list[str]:
    if profile.empty:
        return []
    warnings: list[str] = []
    grouped = (
        profile.loc[profile["comparator_code"].isin(["LT", "LE", "GT", "GE"])]
        .groupby(["result_column", "item", "comparator_code"], dropna=False, observed=False)["numeric_value"]
        .nunique(dropna=True)
        .reset_index(name="distinct_thresholds")
    )
    for row in grouped.itertuples(index=False):
        if int(row.distinct_thresholds) > 1:
            item_text = "" if row.item in (None, "") else f" item={row.item}"
            warnings.append(
                f"Multiple comparator thresholds detected for result_column={row.result_column}{item_text} comparator={row.comparator_code}."
            )
    return warnings


def _format_threshold_value(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return str(float(value))


def _format_comparator_value(family: str, threshold: float) -> str:
    symbol = "<" if family == "LT" else ">"
    return f"{symbol}{_format_threshold_value(threshold)}"

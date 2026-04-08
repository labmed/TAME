from __future__ import annotations

import math
import re
from typing import Any, Iterable

import pandas as pd

from .cellstate import STATE_ABSENT, STATE_EMPTY, STATE_NULL, STATE_VALUE, STATE_WS, cell_state, state_counts
from .config import ci_get
from .images import is_valid_image_value
from .models import ColumnSpec, EDAReport, ReferenceIntervalPlan, TameDataset, ValidationIssue, ValidationResult


STRICT_NUM_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)$")
COMPARATOR_NUM_RE = re.compile(r"^(?P<op><=|>=|<|>|=)?\s*(?P<num>[+-]?(?:\d+(?:\.\d+)?|\.\d+))$")
AGE_RE = re.compile(r"^(?P<num>\d+)(?P<unit>[dDmM]?)$")
COMPARATOR_CODE = {"": "EQ", "=": "EQ", "<": "LT", "<=": "LE", ">": "GT", ">=": "GE"}
COMPARATOR_SYMBOL = {value: key or "=" for key, value in COMPARATOR_CODE.items()}

GENDER_MAP = {
    "M": "M",
    "MALE": "M",
    "남": "M",
    "남자": "M",
    "F": "F",
    "FEMALE": "F",
    "여": "F",
    "여자": "F",
}


def validate_dataset(dataset: TameDataset) -> ValidationResult:
    issues: list[ValidationIssue] = []
    valid_mask = pd.Series(True, index=dataset.df.index)

    for column in dataset.columns:
        series = dataset.df[column.name]
        for row_idx, value in series.items():
            issue = _validate_value(column, value, dataset)
            if issue is None:
                continue

            issues.append(
                ValidationIssue(
                    row_number=int(row_idx) + 2,
                    column=column.name,
                    tag=issue["tag"],
                    value=value,
                    message=issue["message"],
                )
            )
            valid_mask.loc[row_idx] = False

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

        if _is_numeric_column(column):
            numeric = numeric_series_for(dataset, column, crr_policy=comparator_policy)
            if column.has_tag("AGE"):
                numeric = age_series_for(dataset, column)
            row.update(
                {
                    "kind": "numeric",
                    "count": int(numeric.notna().sum()),
                    "mean": _safe_stat(numeric, "mean"),
                    "std": _safe_stat(numeric, "std"),
                    "min": _safe_stat(numeric, "min"),
                    "median": _safe_stat(numeric, "median"),
                    "max": _safe_stat(numeric, "max"),
                }
            )
        else:
            non_null = values.loc[values.map(lambda value: cell_state(value) == STATE_VALUE)]
            row.update(
                {
                    "kind": "categorical",
                    "count": int(non_null.shape[0]),
                    "unique": int(non_null.nunique(dropna=True)),
                    "top": None if non_null.empty else non_null.mode(dropna=True).iloc[0],
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
    comparator_profile = comparator_profile_table(dataset)
    impact = comparator_policy_impact(dataset, comparator_policy=policy)
    harmonization_preview = comparator_harmonization_preview(dataset)
    warnings.extend(_comparator_warnings(comparator_profile))
    return EDAReport(
        summary=summary,
        comparator_profile=comparator_profile,
        comparator_policy_impact=impact,
        harmonization_preview=harmonization_preview,
        warnings=warnings,
    )


def reference_interval_plan(dataset: TameDataset) -> ReferenceIntervalPlan:
    result_columns = tuple(dataset.columns_with_tag("RESULT"))
    item_column = dataset.first_column_with_tag("TESTNAME") or dataset.first_column_with_tag("ITEM")
    age_column = dataset.first_column_with_tag("AGE")
    gender_column = dataset.first_column_with_tag("GENDER")
    by_columns = tuple(dataset.columns_with_tag("BY"))
    return ReferenceIntervalPlan(
        result_columns=result_columns,
        item_column=item_column,
        age_column=age_column,
        gender_column=gender_column,
        by_columns=by_columns,
    )


def reference_interval_summary(dataset: TameDataset, age_bins: Iterable[float] | None = None) -> pd.DataFrame:
    plan = reference_interval_plan(dataset)
    frames: list[pd.DataFrame] = []
    for result_column in plan.result_columns:
        numeric = numeric_series_for(dataset, result_column)
        work = dataset.df.copy()
        work["_result_value"] = numeric
        work = work.loc[work["_result_value"].notna()].copy()
        if work.empty:
            continue

        group_columns: list[str] = []
        if plan.item_column:
            group_columns.append(plan.item_column.name)
        if plan.gender_column:
            group_columns.append(plan.gender_column.name)

        for column in plan.by_columns:
            if column.name not in group_columns:
                group_columns.append(column.name)

        if age_bins and plan.age_column:
            ages = dataset.df[plan.age_column.name].map(parse_age_to_years)
            work["_age_group"] = pd.cut(ages, bins=list(age_bins), include_lowest=True)
            group_columns.append("_age_group")

        if not group_columns:
            summary = _summary_frame(work["_result_value"])
            summary["result_column"] = result_column.name
            frames.append(summary)
            continue

        grouped = (
            work.groupby(group_columns, dropna=False)["_result_value"]
            .apply(_summary_frame)
            .reset_index()
        )
        grouped["result_column"] = result_column.name
        frames.append(grouped)

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def numeric_series_for(dataset: TameDataset, column: ColumnSpec | str, *, crr_policy: str | None = None) -> pd.Series:
    spec = column if isinstance(column, ColumnSpec) else _resolve_column(dataset, column)
    policy = _normalize_comparator_policy(dataset, crr_policy)
    values: list[float | None] = []

    for value in dataset.df[spec.name]:
        if cell_state(value) != STATE_VALUE:
            values.append(None)
            continue

        text = str(value).strip()
        if spec.has_tag("<NUM>"):
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

    return pd.Series(values, index=dataset.df.index, dtype="float64")


def age_series_for(dataset: TameDataset, column: ColumnSpec | str) -> pd.Series:
    spec = column if isinstance(column, ColumnSpec) else _resolve_column(dataset, column)
    values = [parse_age_to_years(value) for value in dataset.df[spec.name]]
    return pd.Series(values, index=dataset.df.index, dtype="float64")


def parse_strict_number(text: str) -> float | None:
    return float(text) if STRICT_NUM_RE.match(text) else None


def parse_comparator_number(text: str) -> tuple[str, float] | None:
    match = COMPARATOR_NUM_RE.match(text)
    if not match:
        return None
    return (match.group("op") or ""), float(match.group("num"))


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
        work = work.loc[work["_comparator_code"].notna()].copy()
        if work.empty:
            continue

        if plan.item_column:
            grouped = (
                work.groupby([plan.item_column.name, "_comparator_code", "_numeric_value", "_raw_value"], dropna=False)
                .size()
                .reset_index(name="count")
            )
            grouped.insert(0, "result_column", result_column.name)
            grouped.rename(columns={plan.item_column.name: "item"}, inplace=True)
            rows.extend(grouped.to_dict("records"))
        else:
            grouped = (
                work.groupby(["_comparator_code", "_numeric_value", "_raw_value"], dropna=False)
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
                harmonization.groupby("result_column", dropna=False)["changed_rows"].sum().astype(int).to_dict()
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
            grouped = work.groupby([item_column_name], dropna=False)
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


def parse_age_to_years(value: Any) -> float | None:
    if cell_state(value) != STATE_VALUE:
        return None
    text = str(value).strip()
    match = AGE_RE.match(text)
    if not match:
        return None
    number = float(match.group("num"))
    unit = match.group("unit").lower()
    if unit == "d":
        return number / 365.25
    if unit == "m":
        return number / 12.0
    return number


def normalize_gender(value: Any) -> str | None:
    if cell_state(value) != STATE_VALUE:
        return None
    normalized = str(value).strip().upper()
    return GENDER_MAP.get(normalized) or GENDER_MAP.get(str(value).strip()) or None


def _validate_value(column: ColumnSpec, value: Any, dataset: TameDataset) -> dict[str, str] | None:
    state = cell_state(value)
    if state == STATE_ABSENT:
        if column.has_tag("REQUIRED"):
            return {"tag": "REQUIRED", "message": "Expected a value but found ABSENT."}
        return None
    if state == STATE_NULL:
        if column.has_tag("NULLABLE") or column.has_tag("NULL_OK"):
            return None
        return {"tag": "NULL", "message": "NULL token is not allowed for this column."}
    if state == STATE_EMPTY:
        if column.has_tag("EMPTY_OK"):
            return None
        return {"tag": "EMPTY", "message": "EMPTY token is not allowed for this column."}
    if state == STATE_WS:
        if column.has_tag("WS_OK"):
            return None
        return {"tag": "WS", "message": "Whitespace-only value is not allowed for this column."}

    text = str(value).strip()

    if column.has_tag("NUM") and parse_strict_number(text) is None:
        return {"tag": "NUM", "message": "Expected a strict numeric value."}

    if column.has_tag("<NUM>") and parse_comparator_number(text) is None:
        return {"tag": "<NUM>", "message": "Expected a comparator-aware numeric value."}

    if column.has_tag("AGE") and parse_age_to_years(text) is None:
        return {"tag": "AGE", "message": "Expected age values such as 10, 2m, or 1d."}

    if column.has_tag("GENDER") and normalize_gender(text) is None:
        return {"tag": "GENDER", "message": "Expected a recognized gender code."}

    if column.has_tag("IMAGE") and column.has_tag("B64") and not is_valid_image_value(value, mode="B64"):
        return {"tag": "IMAGE::B64", "message": "Expected a base64 image value or data URL."}

    if column.has_tag("IMAGE") and column.has_tag("PATH") and not is_valid_image_value(value, mode="PATH"):
        return {"tag": "IMAGE::PATH", "message": "Expected an image path value."}

    return None


def _resolve_column(dataset: TameDataset, name: str) -> ColumnSpec:
    for column in dataset.columns:
        if column.name == name:
            return column
    raise KeyError(name)


def _summary_frame(series: pd.Series) -> pd.DataFrame:
    stats = {
        "count": int(series.notna().sum()),
        "mean": _safe_stat(series, "mean"),
        "std": _safe_stat(series, "std"),
        "min": _safe_stat(series, "min"),
        "q2_5": _safe_quantile(series, 0.025),
        "median": _safe_stat(series, "median"),
        "q97_5": _safe_quantile(series, 0.975),
        "max": _safe_stat(series, "max"),
    }
    return pd.DataFrame([stats])


def _is_numeric_column(column: ColumnSpec) -> bool:
    return (
        column.has_tag("NUM")
        or column.has_tag("<NUM>")
        or column.has_tag("AGE")
        or (column.has_tag("RESULT") and not column.has_tag("TXT"))
    )


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
        .groupby(["result_column", "item", "comparator_code"], dropna=False)["numeric_value"]
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

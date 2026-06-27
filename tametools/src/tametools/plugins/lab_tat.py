from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from tametools.config import ci_get
from tametools.models import ColumnSpec, OperationOutput, TameDataset, merged_column_specs
from tametools.reporting import chart_spec, with_visualizations, write_docx_report
from tametools.review_profiles import parse_temporal_value
from tametools.plugin_base.base import register_plugin


@dataclass(frozen=True)
class TimePoint:
    tag: str
    label: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Stage:
    key: str
    label: str
    start_tag: str
    end_tag: str
    order: int


TIME_POINTS = (
    TimePoint("ORDER_PLACED_AT", "처방시간", aliases=("ORDER_AT",)),
    TimePoint("SPECIMEN_COLLECTED_AT", "채혈시간", aliases=("COLLECTION_AT",)),
    TimePoint("LAB_RECEIVED_AT", "검사실 도착시간", aliases=("RECEIVED_AT",)),
    TimePoint("LAB_SECTION_RECEIVED_AT", "검사실 검사파트 도착시간"),
    TimePoint("TEST_STARTED_AT", "검사시행시간"),
    TimePoint("RESULT_CREATED_AT", "결과생성시간", aliases=("RESULT_TIME",)),
    TimePoint("PRELIMINARY_REPORTED_AT", "중간보고시간"),
    TimePoint("FINAL_REPORTED_AT", "최종보고시간", aliases=("REPORT_DATE",)),
)

STAGES = (
    Stage("order_to_collection", "처방-채혈", "ORDER_PLACED_AT", "SPECIMEN_COLLECTED_AT", 1),
    Stage("collection_to_lab", "채혈-검사실도착", "SPECIMEN_COLLECTED_AT", "LAB_RECEIVED_AT", 2),
    Stage("lab_to_section", "검사실도착-검사파트도착", "LAB_RECEIVED_AT", "LAB_SECTION_RECEIVED_AT", 3),
    Stage("section_to_test_start", "검사파트도착-검사시행", "LAB_SECTION_RECEIVED_AT", "TEST_STARTED_AT", 4),
    Stage("test_start_to_result", "검사시행-결과생성", "TEST_STARTED_AT", "RESULT_CREATED_AT", 5),
    Stage("result_to_preliminary", "결과생성-중간보고", "RESULT_CREATED_AT", "PRELIMINARY_REPORTED_AT", 6),
    Stage("preliminary_to_final", "중간보고-최종보고", "PRELIMINARY_REPORTED_AT", "FINAL_REPORTED_AT", 7),
    Stage("order_to_final", "처방-최종보고", "ORDER_PLACED_AT", "FINAL_REPORTED_AT", 8),
    Stage("collection_to_final", "채혈-최종보고", "SPECIMEN_COLLECTED_AT", "FINAL_REPORTED_AT", 9),
)

IDENTITY_TAGS = ("ORDER_ID", "SAMPLE_ID", "SPECIMEN_ID", "PATIENT_ID", "TESTNAME")


@register_plugin(
    "LAB_TAT_ANALYSIS",
    description="Analyze laboratory turnaround-time stages from order, collection, arrival, testing, and reporting timestamps.",
)
@register_plugin(
    "TAT_ANALYSIS",
    description="Alias of LAB_TAT_ANALYSIS.",
)
def lab_tat_analysis_plugin(dataset: TameDataset, meta: dict, step_name: str, options: dict) -> OperationOutput:
    columns = _time_columns(dataset)
    warnings = _missing_time_warnings(columns)
    group_columns = _group_columns(dataset, options)
    identity_columns = _identity_columns(dataset)
    targets = _target_minutes(options)

    row_stage_table = _row_stage_table(dataset, columns, group_columns, identity_columns, targets)
    stage_summary = _stage_summary(row_stage_table)
    invalid_intervals = row_stage_table.loc[row_stage_table["status"] != "ok"].copy()
    bottlenecks = _bottleneck_table(stage_summary)
    timestamp_columns = _timestamp_column_table(columns)

    result_dataset = _build_result_dataset(stage_summary, source_dataset=dataset, options=options, warnings=warnings)
    charts = _chart_specs(stage_summary)
    result_dataset = with_visualizations(result_dataset, charts)

    tables = {
        "stage_summary": stage_summary,
        "row_stage_times": row_stage_table,
        "invalid_intervals": invalid_intervals,
        "bottlenecks": bottlenecks,
        "timestamp_columns": timestamp_columns,
    }

    files: list[str] = []
    report_path = str(ci_get(options, "REPORT_PATH", "")).strip()
    if report_path:
        report_output = OperationOutput(
            name=step_name,
            tables={
                "stage_summary": stage_summary,
                "bottlenecks": bottlenecks,
                "invalid_intervals": invalid_intervals,
                "timestamp_columns": timestamp_columns,
            },
            charts=charts,
            warnings=warnings,
            message=_summary_message(stage_summary),
        )
        written = write_docx_report(
            Path(report_path),
            title="Laboratory TAT Stage Analysis Report",
            outputs=[report_output],
            summary=_summary_message(stage_summary),
            max_table_rows=int(ci_get(options, "REPORT_MAX_ROWS", 80)),
        )
        files.append(str(written))

    return OperationOutput(
        name=step_name,
        dataset=result_dataset,
        table=stage_summary,
        tables=tables,
        charts=charts,
        files=files,
        warnings=warnings,
        message=_summary_message(stage_summary),
    )


def _time_columns(dataset: TameDataset) -> dict[str, ColumnSpec | None]:
    result: dict[str, ColumnSpec | None] = {}
    for point in TIME_POINTS:
        result[point.tag] = _first_with_any_tag(dataset, (point.tag, *point.aliases))
    return result


def _first_with_any_tag(dataset: TameDataset, tags: tuple[str, ...]) -> ColumnSpec | None:
    for tag in tags:
        column = dataset.first_column_with_tag(tag)
        if column is not None:
            return column
    return None


def _missing_time_warnings(columns: dict[str, ColumnSpec | None]) -> list[str]:
    warnings: list[str] = []
    for point in TIME_POINTS:
        if columns.get(point.tag) is None:
            warnings.append(f"Missing timestamp tag {point.tag} ({point.label}). Related stage durations will be marked missing.")
    return warnings


def _group_columns(dataset: TameDataset, options: dict) -> list[ColumnSpec]:
    raw = ci_get(options, "GROUP_BY", None)
    if raw is None:
        default = dataset.first_column_with_tag("TESTNAME") or dataset.first_column_with_tag("ITEM")
        return [default] if default is not None else []
    tokens = _option_tokens(raw)
    if not tokens or any(token.upper() in {"NONE", "ALL"} for token in tokens):
        return []
    columns: list[ColumnSpec] = []
    for token in tokens:
        column = _resolve_column(dataset, token)
        if column is not None and column.name not in {item.name for item in columns}:
            columns.append(column)
    return columns


def _identity_columns(dataset: TameDataset) -> list[ColumnSpec]:
    columns: list[ColumnSpec] = []
    for tag in IDENTITY_TAGS:
        column = dataset.first_column_with_tag(tag)
        if column is not None and column.name not in {item.name for item in columns}:
            columns.append(column)
    return columns


def _resolve_column(dataset: TameDataset, token: str) -> ColumnSpec | None:
    text = str(token).strip()
    if not text:
        return None
    if text.lower().startswith("tag:"):
        return dataset.first_column_with_tag(text.split(":", 1)[1])
    for column in dataset.columns:
        if column.name == text:
            return column
    return dataset.first_column_with_tag(text)


def _option_tokens(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _target_minutes(options: dict) -> dict[str, float | None]:
    raw = ci_get(options, "TARGETS", {})
    targets: dict[str, float | None] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            parsed = _float_or_none(value)
            if parsed is not None:
                targets[str(key).strip().lower()] = parsed
    for stage in STAGES:
        for key in (f"TARGET_{stage.key.upper()}", f"TARGET_{stage.key.upper()}_MINUTES"):
            value = ci_get(options, key, None)
            parsed = _float_or_none(value)
            if parsed is not None:
                targets[stage.key] = parsed
    return {stage.key: targets.get(stage.key) for stage in STAGES}


def _row_stage_table(
    dataset: TameDataset,
    columns: dict[str, ColumnSpec | None],
    group_columns: list[ColumnSpec],
    identity_columns: list[ColumnSpec],
    targets: dict[str, float | None],
) -> pd.DataFrame:
    parsed_times = {
        tag: _parsed_time_series(dataset, column) if column is not None else pd.Series([None] * len(dataset.df), index=dataset.df.index)
        for tag, column in columns.items()
    }
    rows: list[dict[str, Any]] = []
    for row_index, source_row in enumerate(dataset.df.index):
        source_row_number = int(row_index) + 2
        group_value = _group_value(dataset, source_row, group_columns)
        identity_values = _identity_values(dataset, source_row, identity_columns)
        for stage in STAGES:
            start_value = parsed_times[stage.start_tag].iloc[row_index]
            end_value = parsed_times[stage.end_tag].iloc[row_index]
            duration = _duration_minutes(start_value, end_value)
            status = _duration_status(start_value, end_value, duration)
            target = targets.get(stage.key)
            rows.append(
                {
                    "source_row": source_row_number,
                    **identity_values,
                    "group": group_value,
                    "stage_order": stage.order,
                    "stage_key": stage.key,
                    "stage": stage.label,
                    "start_event": stage.start_tag,
                    "end_event": stage.end_tag,
                    "start_time": _iso_or_none(start_value),
                    "end_time": _iso_or_none(end_value),
                    "duration_minutes": duration,
                    "status": status,
                    "target_minutes": target,
                    "breached": bool(target is not None and duration is not None and duration > target and status == "ok"),
                }
            )
    return pd.DataFrame(rows, columns=_row_stage_columns(identity_columns))


def _parsed_time_series(dataset: TameDataset, column: ColumnSpec) -> pd.Series:
    return dataset.df[column.name].map(lambda value: parse_temporal_value(value, column.tags))


def _group_value(dataset: TameDataset, row_index: Any, group_columns: list[ColumnSpec]) -> str:
    if not group_columns:
        return "ALL"
    values = [str(dataset.df.at[row_index, column.name]) for column in group_columns]
    return " / ".join(values)


def _identity_values(dataset: TameDataset, row_index: Any, identity_columns: list[ColumnSpec]) -> dict[str, Any]:
    values = {"record_id": ""}
    for column in identity_columns:
        values[column.name] = dataset.df.at[row_index, column.name]
        if not values["record_id"]:
            values["record_id"] = str(dataset.df.at[row_index, column.name])
    return values


def _duration_minutes(start: Any, end: Any) -> float | None:
    if start is None or end is None or pd.isna(start) or pd.isna(end):
        return None
    return round((pd.Timestamp(end) - pd.Timestamp(start)).total_seconds() / 60.0, 3)


def _duration_status(start: Any, end: Any, duration: float | None) -> str:
    if (start is None or pd.isna(start)) and (end is None or pd.isna(end)):
        return "missing_both"
    if start is None or pd.isna(start):
        return "missing_start"
    if end is None or pd.isna(end):
        return "missing_end"
    if duration is None:
        return "invalid"
    if duration < 0:
        return "negative"
    return "ok"


def _iso_or_none(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).isoformat(sep=" ")


def _row_stage_columns(identity_columns: list[ColumnSpec]) -> list[str]:
    return [
        "source_row",
        "record_id",
        *[column.name for column in identity_columns],
        "group",
        "stage_order",
        "stage_key",
        "stage",
        "start_event",
        "end_event",
        "start_time",
        "end_time",
        "duration_minutes",
        "status",
        "target_minutes",
        "breached",
    ]


def _stage_summary(row_stage_table: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "group",
        "stage_order",
        "stage_key",
        "stage",
        "start_event",
        "end_event",
        "n_total",
        "n_valid",
        "missing_count",
        "negative_count",
        "breached_count",
        "breach_rate",
        "mean_minutes",
        "median_minutes",
        "p90_minutes",
        "p95_minutes",
        "min_minutes",
        "max_minutes",
        "target_minutes",
    ]
    if row_stage_table.empty:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, Any]] = []
    for (group, stage_key), part in row_stage_table.groupby(["group", "stage_key"], sort=False, dropna=False, observed=False):
        valid = part.loc[part["status"] == "ok", "duration_minutes"].dropna().astype(float)
        first = part.iloc[0]
        target_values = part["target_minutes"].dropna()
        rows.append(
            {
                "group": group,
                "stage_order": int(first["stage_order"]),
                "stage_key": stage_key,
                "stage": first["stage"],
                "start_event": first["start_event"],
                "end_event": first["end_event"],
                "n_total": int(part.shape[0]),
                "n_valid": int(valid.shape[0]),
                "missing_count": int(part["status"].isin(["missing_start", "missing_end", "missing_both"]).sum()),
                "negative_count": int((part["status"] == "negative").sum()),
                "breached_count": int(part["breached"].sum()),
                "breach_rate": _safe_percent(int(part["breached"].sum()), int(valid.shape[0])),
                "mean_minutes": _series_stat(valid, "mean"),
                "median_minutes": _series_stat(valid, "median"),
                "p90_minutes": _series_quantile(valid, 0.90),
                "p95_minutes": _series_quantile(valid, 0.95),
                "min_minutes": _series_stat(valid, "min"),
                "max_minutes": _series_stat(valid, "max"),
                "target_minutes": None if target_values.empty else float(target_values.iloc[0]),
            }
        )
    return pd.DataFrame(rows, columns=columns).sort_values(["group", "stage_order"], kind="stable").reset_index(drop=True)


def _series_stat(series: pd.Series, method: str) -> float | None:
    if series.empty:
        return None
    if method == "mean":
        value = series.mean()
    elif method == "median":
        value = series.median()
    elif method == "min":
        value = series.min()
    elif method == "max":
        value = series.max()
    else:
        return None
    return round(float(value), 3)


def _series_quantile(series: pd.Series, q: float) -> float | None:
    if series.empty:
        return None
    return round(float(series.quantile(q)), 3)


def _safe_percent(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(100.0 * numerator / denominator, 3)


def _bottleneck_table(stage_summary: pd.DataFrame) -> pd.DataFrame:
    if stage_summary.empty:
        return stage_summary.copy()
    return (
        stage_summary.sort_values(["p95_minutes", "median_minutes"], ascending=[False, False], na_position="last", kind="stable")
        .reset_index(drop=True)
        .head(10)
    )


def _timestamp_column_table(columns: dict[str, ColumnSpec | None]) -> pd.DataFrame:
    rows = []
    for point in TIME_POINTS:
        column = columns.get(point.tag)
        rows.append(
            {
                "tag": point.tag,
                "label": point.label,
                "column": None if column is None else column.name,
                "tags": "" if column is None else "::".join(column.tags),
                "found": column is not None,
            }
        )
    return pd.DataFrame(rows, columns=["tag", "label", "column", "tags", "found"])


def _build_result_dataset(stage_summary: pd.DataFrame, *, source_dataset: TameDataset, options: dict, warnings: list[str]) -> TameDataset:
    headers = [
        "[[GROUP::CATEGORY]]group",
        "[[RANK::NUM]]stage_order",
        "[[STAGE::CATEGORY]]stage_key",
        "[[STAGE::CATEGORY]]stage",
        "[[EVENT::CATEGORY]]start_event",
        "[[EVENT::CATEGORY]]end_event",
        "[[N::NUM]]n_total",
        "[[N::NUM]]n_valid",
        "[[COUNT::NUM]]missing_count",
        "[[COUNT::NUM]]negative_count",
        "[[COUNT::NUM]]breached_count",
        "[[PERCENT::NUM]]breach_rate",
        "[[DURATION::MEAN::NUM]]mean_minutes",
        "[[DURATION::MEDIAN::NUM]]median_minutes",
        "[[DURATION::PERCENTILE::NUM]]p90_minutes",
        "[[DURATION::PERCENTILE::NUM]]p95_minutes",
        "[[DURATION::MIN::NUM]]min_minutes",
        "[[DURATION::MAX::NUM]]max_minutes",
        "[[DURATION::THRESHOLD::NUM]]target_minutes",
    ]
    columns = merged_column_specs(headers)
    names = [column.name for column in columns]
    frame = stage_summary.reindex(columns=names).copy() if not stage_summary.empty else pd.DataFrame(columns=names)
    meta = {
        "INFO": {
            "NAME": "LAB_TAT_ANALYSIS",
            "FORMAT_VERSION": "tame/1",
            "CREATED_BY": "tametools",
            "DESCRIPTION": "Laboratory turnaround-time stage summary output.",
        },
        "SETTINGS": {"VALIDATE_ERROR": "REPORT"},
        "COLUMN": _column_metadata(),
        "WORKS": {"DEFAULT": ["DESCRIBE"]},
        "PLUGIN": {
            "NAME": "LAB_TAT_ANALYSIS",
            "OPTIONS": dict(options or {}),
            "SOURCE_PATH": source_dataset.source_path or "",
            "WARNINGS": warnings,
        },
    }
    return TameDataset(df=frame.where(pd.notna(frame), None), columns=columns, meta=meta, source_path=source_dataset.source_path)


def _column_metadata() -> dict[str, dict[str, Any]]:
    labels = {
        "group": "그룹",
        "stage_order": "단계순서",
        "stage_key": "단계키",
        "stage": "단계",
        "start_event": "시작이벤트",
        "end_event": "종료이벤트",
        "n_total": "전체건수",
        "n_valid": "유효건수",
        "missing_count": "누락건수",
        "negative_count": "음수소요시간건수",
        "breached_count": "목표초과건수",
        "breach_rate": "목표초과율",
        "mean_minutes": "평균분",
        "median_minutes": "중앙값분",
        "p90_minutes": "P90분",
        "p95_minutes": "P95분",
        "min_minutes": "최소분",
        "max_minutes": "최대분",
        "target_minutes": "목표분",
    }
    return {name: {"LABEL": label} for name, label in labels.items()}


def _chart_specs(stage_summary: pd.DataFrame) -> list[dict[str, Any]]:
    if stage_summary.empty:
        return []
    series = "group" if stage_summary["group"].nunique(dropna=False) > 1 else ""
    return [
        chart_spec(
            "LAB_TAT_MEDIAN",
            type="bar",
            title="Median turnaround time by stage",
            x="stage",
            y="median_minutes",
            series=series,
            table="stage_summary",
            max_points=80,
        ),
        chart_spec(
            "LAB_TAT_P95",
            type="bar",
            title="P95 turnaround time by stage",
            x="stage",
            y="p95_minutes",
            series=series,
            table="stage_summary",
            max_points=80,
        ),
        chart_spec(
            "LAB_TAT_BREACH_RATE",
            type="bar",
            title="Target breach rate by stage",
            x="stage",
            y="breach_rate",
            series=series,
            table="stage_summary",
            max_points=80,
        ),
    ]


def _summary_message(stage_summary: pd.DataFrame) -> str:
    if stage_summary.empty:
        return "lab_tat_analysis rows=0; no stage summaries were produced."
    valid = int(stage_summary["n_valid"].sum())
    negatives = int(stage_summary["negative_count"].sum())
    missing = int(stage_summary["missing_count"].sum())
    slowest = stage_summary.sort_values("p95_minutes", ascending=False, na_position="last", kind="stable").iloc[0]
    return (
        f"lab_tat_analysis groups={stage_summary['group'].nunique(dropna=False)} stages={stage_summary['stage_key'].nunique()} "
        f"valid_intervals={valid} missing={missing} negative={negatives} "
        f"slowest_p95={slowest['stage']}:{slowest['p95_minutes']}min"
    )


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        text = str(value).strip()
        if not text:
            return None
        return float(text)
    except (TypeError, ValueError):
        return None

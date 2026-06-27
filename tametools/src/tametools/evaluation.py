from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from .analysis import comparator_parts_series, reference_interval_plan, validate_dataset
from .cellstate import STATE_ABSENT, STATE_EMPTY, STATE_NULL, STATE_VALUE, STATE_WS, serialize_cell, state_counts
from .models import TameDataset


QUALITY_COLUMNS = [
    "dataset",
    "rows",
    "columns",
    "tagged_columns",
    "untagged_columns",
    "validation_issues",
    "invalid_rows",
    "cleaned_rows",
    "result_columns",
    "comparator_aware_result_columns",
    "bounded_result_values",
    "absent_cells",
    "null_cells",
    "empty_cells",
    "ws_cells",
]
WORKFLOW_COLUMNS = [
    "method",
    "rows",
    "columns",
    "tagged_columns",
    "validation_issues",
    "invalid_rows",
    "preprocessing_steps",
    "turnaround_minutes",
    "metadata_sections",
    "defined_works",
]
OUTPUT_COMPARISON_COLUMNS = [
    "baseline_rows",
    "tame_rows",
    "row_count_delta",
    "baseline_columns",
    "tame_columns",
    "common_columns",
    "missing_in_tame",
    "extra_in_tame",
    "comparable_cells",
    "matched_cells",
    "cell_mismatches",
]
IMPROVEMENT_COLUMNS = ["metric", "baseline", "tame", "delta", "percent_change"]


@dataclass(frozen=True)
class EvaluationReport:
    dataset_quality: pd.DataFrame
    workflow_comparison: pd.DataFrame
    output_comparison: pd.DataFrame
    improvements: pd.DataFrame
    warnings: tuple[str, ...] = ()

    def tables(self) -> dict[str, pd.DataFrame]:
        return {
            "dataset_quality": self.dataset_quality,
            "workflow_comparison": self.workflow_comparison,
            "output_comparison": self.output_comparison,
            "improvements": self.improvements,
        }


def evaluate_dataset(
    dataset: TameDataset,
    *,
    baseline_dataset: TameDataset | None = None,
    baseline_steps: int | None = None,
    tame_steps: int | None = None,
    baseline_minutes: float | None = None,
    tame_minutes: float | None = None,
    baseline_label: str = "existing",
    tame_label: str = "TAME",
) -> EvaluationReport:
    """Build reusable tables for reusable workflow and data quality evaluation."""

    quality_rows = []
    if baseline_dataset is not None:
        quality_rows.append(_dataset_quality_row(baseline_dataset, baseline_label))
    quality_rows.append(_dataset_quality_row(dataset, tame_label))
    dataset_quality = pd.DataFrame(quality_rows, columns=QUALITY_COLUMNS)

    output_comparison = _output_comparison_table(baseline_dataset, dataset)
    workflow_comparison = _workflow_comparison_table(
        dataset_quality,
        dataset=dataset,
        baseline_dataset=baseline_dataset,
        baseline_label=baseline_label,
        tame_label=tame_label,
        baseline_steps=baseline_steps,
        tame_steps=tame_steps,
        baseline_minutes=baseline_minutes,
        tame_minutes=tame_minutes,
    )
    improvements = _improvement_table(
        dataset_quality,
        baseline_label=baseline_label,
        tame_label=tame_label,
        baseline_steps=baseline_steps,
        tame_steps=tame_steps,
        baseline_minutes=baseline_minutes,
        tame_minutes=tame_minutes,
    )

    warnings = _report_warnings(baseline_dataset, output_comparison)
    return EvaluationReport(
        dataset_quality=dataset_quality,
        workflow_comparison=workflow_comparison,
        output_comparison=output_comparison,
        improvements=improvements,
        warnings=warnings,
    )


def write_evaluation_report(report: EvaluationReport, output_dir: str | Path) -> tuple[str, ...]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    for name, table in report.tables().items():
        path = output_path / f"{name}.csv"
        table.to_csv(path, index=False, encoding="utf-8")
        written.append(str(path))
    if report.warnings:
        path = output_path / "warnings.txt"
        path.write_text("\n".join(report.warnings) + "\n", encoding="utf-8")
        written.append(str(path))
    return tuple(written)


def _dataset_quality_row(dataset: TameDataset, label: str) -> dict[str, Any]:
    validation = validate_dataset(dataset)
    state_totals = _state_totals(dataset)
    result_plan = reference_interval_plan(dataset)
    result_columns = result_plan.result_columns
    bounded_values = 0
    for column in result_columns:
        if column.has_tag("<NUM>"):
            parts = comparator_parts_series(dataset, column)
            bounded_values += int(parts["comparator_code"].isin(["LT", "LE", "GT", "GE"]).sum())

    tagged_columns = sum(1 for column in dataset.columns if column.tags)
    return {
        "dataset": label,
        "rows": len(dataset.df),
        "columns": len(dataset.columns),
        "tagged_columns": tagged_columns,
        "untagged_columns": len(dataset.columns) - tagged_columns,
        "validation_issues": len(validation.issues),
        "invalid_rows": len({issue.row_number for issue in validation.issues}),
        "cleaned_rows": len(validation.cleaned_dataset.df),
        "result_columns": len(result_columns),
        "comparator_aware_result_columns": sum(1 for column in result_columns if column.has_tag("<NUM>")),
        "bounded_result_values": bounded_values,
        "absent_cells": state_totals[STATE_ABSENT],
        "null_cells": state_totals[STATE_NULL],
        "empty_cells": state_totals[STATE_EMPTY],
        "ws_cells": state_totals[STATE_WS],
    }


def _state_totals(dataset: TameDataset) -> dict[str, int]:
    totals = {STATE_VALUE: 0, STATE_ABSENT: 0, STATE_NULL: 0, STATE_EMPTY: 0, STATE_WS: 0}
    for column in dataset.columns:
        counts = state_counts(dataset.df[column.name])
        for state, count in counts.items():
            totals[state] = totals.get(state, 0) + int(count)
    return totals


def _workflow_comparison_table(
    dataset_quality: pd.DataFrame,
    *,
    dataset: TameDataset,
    baseline_dataset: TameDataset | None,
    baseline_label: str,
    tame_label: str,
    baseline_steps: int | None,
    tame_steps: int | None,
    baseline_minutes: float | None,
    tame_minutes: float | None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if baseline_dataset is not None:
        rows.append(
            _workflow_row(
                dataset_quality,
                baseline_dataset,
                baseline_label,
                preprocessing_steps=baseline_steps,
                turnaround_minutes=baseline_minutes,
            )
        )
    rows.append(
        _workflow_row(
            dataset_quality,
            dataset,
            tame_label,
            preprocessing_steps=tame_steps,
            turnaround_minutes=tame_minutes,
        )
    )
    return pd.DataFrame(rows, columns=WORKFLOW_COLUMNS)


def _workflow_row(
    dataset_quality: pd.DataFrame,
    dataset: TameDataset,
    label: str,
    *,
    preprocessing_steps: int | None,
    turnaround_minutes: float | None,
) -> dict[str, Any]:
    quality = dataset_quality.loc[dataset_quality["dataset"] == label].iloc[0].to_dict()
    return {
        "method": label,
        "rows": quality["rows"],
        "columns": quality["columns"],
        "tagged_columns": quality["tagged_columns"],
        "validation_issues": quality["validation_issues"],
        "invalid_rows": quality["invalid_rows"],
        "preprocessing_steps": _nullable_number(preprocessing_steps),
        "turnaround_minutes": _nullable_number(turnaround_minutes),
        "metadata_sections": len([name for name in dataset.raw_sections if name != "DATA"]),
        "defined_works": len(dataset.meta.get("WORKS", {})) if isinstance(dataset.meta.get("WORKS", {}), dict) else 0,
    }


def _output_comparison_table(baseline_dataset: TameDataset | None, dataset: TameDataset) -> pd.DataFrame:
    if baseline_dataset is None:
        return pd.DataFrame(columns=OUTPUT_COMPARISON_COLUMNS)

    baseline_frame = _serialized_frame(baseline_dataset)
    tame_frame = _serialized_frame(dataset)
    baseline_columns = list(baseline_frame.columns)
    tame_columns = list(tame_frame.columns)
    common_columns = [name for name in baseline_columns if name in tame_columns]
    comparable_rows = min(len(baseline_frame), len(tame_frame))

    comparable_cells = comparable_rows * len(common_columns)
    mismatches = 0
    if comparable_cells:
        baseline_common = baseline_frame.loc[: comparable_rows - 1, common_columns].reset_index(drop=True)
        tame_common = tame_frame.loc[: comparable_rows - 1, common_columns].reset_index(drop=True)
        mismatches = int((baseline_common != tame_common).to_numpy().sum())

    row = {
        "baseline_rows": len(baseline_frame),
        "tame_rows": len(tame_frame),
        "row_count_delta": len(tame_frame) - len(baseline_frame),
        "baseline_columns": len(baseline_columns),
        "tame_columns": len(tame_columns),
        "common_columns": len(common_columns),
        "missing_in_tame": ", ".join(name for name in baseline_columns if name not in tame_columns),
        "extra_in_tame": ", ".join(name for name in tame_columns if name not in baseline_columns),
        "comparable_cells": comparable_cells,
        "matched_cells": comparable_cells - mismatches,
        "cell_mismatches": mismatches,
    }
    return pd.DataFrame([row], columns=OUTPUT_COMPARISON_COLUMNS)


def _serialized_frame(dataset: TameDataset) -> pd.DataFrame:
    settings = dataset.settings()
    frame = pd.DataFrame(index=dataset.df.index)
    for column in dataset.columns:
        frame[column.name] = dataset.df[column.name].map(lambda value: str(serialize_cell(value, settings, for_excel=False)))
    return frame


def _improvement_table(
    dataset_quality: pd.DataFrame,
    *,
    baseline_label: str,
    tame_label: str,
    baseline_steps: int | None,
    tame_steps: int | None,
    baseline_minutes: float | None,
    tame_minutes: float | None,
) -> pd.DataFrame:
    if baseline_label not in set(dataset_quality["dataset"]):
        return pd.DataFrame(columns=IMPROVEMENT_COLUMNS)

    baseline = dataset_quality.loc[dataset_quality["dataset"] == baseline_label].iloc[0].to_dict()
    tame = dataset_quality.loc[dataset_quality["dataset"] == tame_label].iloc[0].to_dict()

    rows = [
        _improvement_row("validation_issues", baseline["validation_issues"], tame["validation_issues"]),
        _improvement_row("invalid_rows", baseline["invalid_rows"], tame["invalid_rows"]),
        _improvement_row("tagged_columns", baseline["tagged_columns"], tame["tagged_columns"]),
    ]
    if baseline_steps is not None and tame_steps is not None:
        rows.append(_improvement_row("preprocessing_steps", baseline_steps, tame_steps))
    if baseline_minutes is not None and tame_minutes is not None:
        rows.append(_improvement_row("turnaround_minutes", baseline_minutes, tame_minutes))
    return pd.DataFrame(rows, columns=IMPROVEMENT_COLUMNS)


def _improvement_row(metric: str, baseline: Any, tame: Any) -> dict[str, Any]:
    baseline_value = float(baseline)
    tame_value = float(tame)
    delta = tame_value - baseline_value
    percent_change = None if baseline_value == 0 else (delta / baseline_value) * 100
    return {
        "metric": metric,
        "baseline": baseline,
        "tame": tame,
        "delta": delta,
        "percent_change": percent_change,
    }


def _report_warnings(
    baseline_dataset: TameDataset | None,
    output_comparison: pd.DataFrame,
) -> tuple[str, ...]:
    warnings: list[str] = []
    if baseline_dataset is None:
        warnings.append("No baseline dataset was provided; workflow comparison is TAME-only.")
    elif not output_comparison.empty:
        row = output_comparison.iloc[0]
        if int(row["row_count_delta"]) != 0:
            warnings.append("Baseline and TAME outputs have different row counts.")
        if row["missing_in_tame"]:
            warnings.append("Some baseline columns are missing in the TAME output.")
        if row["extra_in_tame"]:
            warnings.append("The TAME output contains columns absent from the baseline.")
        if int(row["cell_mismatches"]) > 0:
            warnings.append("Common output cells differ between baseline and TAME outputs.")
    return tuple(warnings)


def _nullable_number(value: int | float | None) -> int | float | None:
    return None if value is None else value

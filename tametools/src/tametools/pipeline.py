from __future__ import annotations

from .analysis import describe_dataset, exploratory_data_analysis, reference_interval_summary, validate_dataset
from .config import ci_get
from .embedded_functions import run_embedded_function
from .models import OperationOutput, PipelineResult, TameDataset
from .plugins.manager import run_plugin
from .transforms import anonymize_dataset, harmonize_comparator_thresholds, sample_dataset, split_comparator_columns


def execute_work(dataset: TameDataset, work_name: str = "DEFAULT", *, allow_functions: bool = False) -> PipelineResult:
    steps = _resolve_work(dataset.meta, work_name)
    outputs: list[OperationOutput] = []
    current = dataset

    for step in steps:
        output = _execute_step(current, step, dataset.meta, allow_functions=allow_functions)
        outputs.append(output)
        if output.dataset is not None:
            current = output.dataset

    return PipelineResult(work_name=work_name, final_dataset=current, outputs=outputs)


def available_works(meta: dict) -> list[str]:
    section = ci_get(meta, "WORKS", {})
    if not isinstance(section, dict):
        return []
    return [str(name) for name in section.keys()]


def _resolve_work(meta: dict, work_name: str) -> list[str]:
    works = ci_get(meta, "WORKS", {})
    subworks = ci_get(meta, "SUBWORKS", {})
    pipeline = ci_get(works, work_name, None)
    if not isinstance(pipeline, list):
        raise KeyError(f"Undefined work: {work_name}")

    resolved: list[str] = []
    for step in pipeline:
        nested = ci_get(subworks, str(step), None)
        if isinstance(nested, list):
            resolved.extend(str(item) for item in nested)
        else:
            resolved.append(str(step))
    return resolved


def _execute_step(dataset: TameDataset, step_name: str, meta: dict, *, allow_functions: bool = False) -> OperationOutput:
    normalized = step_name.upper()
    options = ci_get(meta, step_name, {})
    if not isinstance(options, dict):
        options = {}

    if normalized == "VALIDATE":
        result = validate_dataset(dataset)
        return OperationOutput(
            name=step_name,
            dataset=result.cleaned_dataset,
            issues=result.issues,
            message=f"issues={len(result.issues)} cleaned_rows={len(result.cleaned_dataset.df)}",
        )

    if normalized == "DESCRIBE":
        return OperationOutput(name=step_name, dataset=dataset, table=describe_dataset(dataset))

    if normalized == "EDA":
        policy = ci_get(options, "CRR", None)
        report = exploratory_data_analysis(dataset, comparator_policy=policy)
        return OperationOutput(
            name=step_name,
            dataset=dataset,
            tables={
                "summary": report.summary,
                "comparator_profile": report.comparator_profile,
                "comparator_policy_impact": report.comparator_policy_impact,
                "harmonization_preview": report.harmonization_preview,
            },
            warnings=report.warnings,
            message=f"eda tables={4} warnings={len(report.warnings)}",
        )

    if normalized == "REFERENCE_INTERVAL_SUMMARY":
        age_bins = ci_get(options, "AGE_BINS", None)
        return OperationOutput(
            name=step_name,
            dataset=dataset,
            table=reference_interval_summary(dataset, age_bins=age_bins),
        )

    if normalized == "ANONYMIZE":
        hashed_columns = ci_get(options, "HASH_COLUMNS", [])
        dropped_columns = ci_get(options, "DROP_COLUMNS", [])
        hashed_tags = ci_get(options, "HASH_TAGS", ["ID", "HOSPITAL_ID"])
        dropped_tags = ci_get(options, "DROP_TAGS", ["NAME"])
        salt = str(ci_get(options, "SALT", ""))
        transformed, mappings = anonymize_dataset(
            dataset,
            hash_columns=hashed_columns,
            drop_columns=dropped_columns,
            hash_tags=hashed_tags,
            drop_tags=dropped_tags,
            salt=salt,
        )
        return OperationOutput(
            name=step_name,
            dataset=transformed,
            tables=mappings,
            message=f"hashed_columns={len(mappings)} dropped_columns={len(dropped_columns)}",
        )

    if normalized == "SAMPLE":
        rows = ci_get(options, "ROWS", None)
        frac = ci_get(options, "FRAC", ci_get(options, "FRACTION", None))
        seed = ci_get(options, "SEED", None)
        by_columns = ci_get(options, "BY_COLUMNS", [])
        by_tags = ci_get(options, "BY_TAGS", [])
        replace = bool(ci_get(options, "REPLACE", False))
        transformed = sample_dataset(
            dataset,
            rows=rows,
            frac=frac,
            seed=seed,
            by_columns=by_columns,
            by_tags=by_tags,
            replace=replace,
        )
        mode = f"rows={rows}" if rows is not None else f"frac={frac}"
        return OperationOutput(
            name=step_name,
            dataset=transformed,
            message=f"{mode} grouped_by={list(by_columns or []) + list(by_tags or [])} sampled_rows={len(transformed.df)}",
        )

    if normalized in {"SPLIT_COMPARATOR", "SPLIT_CNUM"}:
        target_columns = ci_get(options, "COLUMNS", [])
        drop_original = bool(ci_get(options, "DROP_ORIGINAL", False))
        transformed = split_comparator_columns(dataset, target_columns=target_columns, drop_original=drop_original)
        return OperationOutput(
            name=step_name,
            dataset=transformed,
            message=f"columns={target_columns or '<NUM>'} drop_original={drop_original}",
        )

    if normalized in {"HARMONIZE_COMPARATOR", "HARMONIZE_CNUM"}:
        target_columns = ci_get(options, "COLUMNS", [])
        exact_handling = str(ci_get(options, "EXACT_HANDLING", "between"))
        transformed, preview = harmonize_comparator_thresholds(
            dataset,
            target_columns=target_columns,
            exact_handling=exact_handling,
        )
        return OperationOutput(
            name=step_name,
            dataset=transformed,
            tables={"harmonization_preview": preview},
            message=f"columns={target_columns or '<NUM>'} exact_handling={exact_handling}",
        )

    if normalized in {"COLUMNS", "INFO"}:
        return OperationOutput(name=step_name, dataset=dataset, table=dataset.column_frame())

    embedded_output = run_embedded_function(
        dataset,
        meta,
        step_name,
        options,
        allow_functions=allow_functions,
    )
    if embedded_output is not None:
        return embedded_output

    plugin_name = str(ci_get(options, "PLUGIN", ci_get(options, "FUNCTION", step_name)))
    plugin_output = run_plugin(dataset, plugin_name, meta, step_name, options)
    if plugin_output is not None:
        return plugin_output

    return OperationOutput(name=step_name, dataset=dataset, message="step skipped: no built-in handler")

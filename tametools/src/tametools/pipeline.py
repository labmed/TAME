from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .analysis import describe_dataset, exploratory_data_analysis, validate_dataset
from .config import ci_get
from .embedded_functions import run_embedded_function
from .evaluation import evaluate_dataset
from .models import OperationOutput, PipelineResult, TameDataset
from .plugin_base.manager import configured_plugin_modules, run_plugin
from .audit import dataset_content_hash
from .provenance import append_log_entry
from .transforms import anonymize_dataset, harmonize_comparator_thresholds, sample_dataset, split_comparator_columns


def execute_work(
    dataset: TameDataset,
    work_name: str = "DEFAULT",
    *,
    allow_functions: bool = False,
    allow_plugins: bool = False,
    log_level: str = "detailed",
) -> PipelineResult:
    steps = _resolve_work(dataset.meta, work_name)
    outputs: list[OperationOutput] = []
    current = dataset
    detailed_log = str(log_level or "detailed").strip().lower() == "detailed"

    for step in steps:
        input_hash = dataset_content_hash(current)
        output = _execute_step(current, step, dataset.meta, allow_functions=allow_functions, allow_plugins=allow_plugins)
        if detailed_log and output.dataset is not None:
            output.dataset = _logged_work_dataset(
                output.dataset,
                step=step,
                options=ci_get(dataset.meta, step, {}),
                input_hash=input_hash,
                output=output,
            )
        outputs.append(output)
        if output.dataset is not None:
            current = output.dataset

    return PipelineResult(work_name=work_name, final_dataset=current, outputs=outputs)


def _logged_work_dataset(
    dataset: TameDataset,
    *,
    step: str,
    options: object,
    input_hash: str,
    output: OperationOutput,
) -> TameDataset:
    output_hash = dataset_content_hash(dataset)
    parameters = {
        "step": step,
        "options": options if isinstance(options, dict) else {},
        "input_hash": input_hash,
        "output_hash": output_hash,
        "issues": len(output.issues or []),
        "warnings": len(output.warnings or []),
        "tables": sorted((output.tables or {}).keys()),
        "files": list(output.files or []),
    }
    return append_log_entry(
        dataset,
        action=f"WORK:{step}",
        message=output.message or "",
        parameters=parameters,
        warnings=list(output.warnings or []),
    )


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


@dataclass(frozen=True)
class StepContext:
    dataset: TameDataset
    step_name: str
    normalized_name: str
    meta: dict
    options: dict
    allow_functions: bool
    allow_plugins: bool


class StepHandler(Protocol):
    def matches(self, context: StepContext) -> bool:
        ...

    def execute(self, context: StepContext) -> OperationOutput:
        ...


class NamedStepHandler:
    names: frozenset[str] = frozenset()

    def matches(self, context: StepContext) -> bool:
        return context.normalized_name in self.names


class ValidateStepHandler(NamedStepHandler):
    names = frozenset({"VALIDATE"})

    def execute(self, context: StepContext) -> OperationOutput:
        result = validate_dataset(context.dataset)
        return OperationOutput(
            name=context.step_name,
            dataset=result.cleaned_dataset,
            issues=result.issues,
            message=f"issues={len(result.issues)} cleaned_rows={len(result.cleaned_dataset.df)}",
        )


class DescribeStepHandler(NamedStepHandler):
    names = frozenset({"DESCRIBE"})

    def execute(self, context: StepContext) -> OperationOutput:
        return OperationOutput(name=context.step_name, dataset=context.dataset, table=describe_dataset(context.dataset))


class EdaStepHandler(NamedStepHandler):
    names = frozenset({"EDA"})

    def execute(self, context: StepContext) -> OperationOutput:
        policy = ci_get(context.options, "CRR", None)
        report = exploratory_data_analysis(context.dataset, comparator_policy=policy)
        return OperationOutput(
            name=context.step_name,
            dataset=context.dataset,
            tables={
                "summary": report.summary,
                "category_distribution": report.category_distribution,
                "numeric_percentiles": report.numeric_percentiles,
                "numeric_percentile_bands": report.numeric_percentile_bands,
                "result_by_summary": report.result_by_summary,
                "comparator_profile": report.comparator_profile,
                "comparator_policy_impact": report.comparator_policy_impact,
                "harmonization_preview": report.harmonization_preview,
            },
            warnings=report.warnings,
            message=f"eda tables={8} warnings={len(report.warnings)}",
        )


class EvaluateStepHandler(NamedStepHandler):
    names = frozenset({"EVALUATE"})

    def execute(self, context: StepContext) -> OperationOutput:
        report = evaluate_dataset(
            context.dataset,
            baseline_steps=ci_get(context.options, "BASELINE_STEPS", None),
            tame_steps=ci_get(context.options, "TAME_STEPS", None),
            baseline_minutes=ci_get(context.options, "BASELINE_MINUTES", None),
            tame_minutes=ci_get(context.options, "TAME_MINUTES", None),
        )
        return OperationOutput(
            name=context.step_name,
            dataset=context.dataset,
            tables=report.tables(),
            warnings=list(report.warnings),
            message=f"evaluation tables={len(report.tables())}",
        )


class AnonymizeStepHandler(NamedStepHandler):
    names = frozenset({"ANONYMIZE"})

    def execute(self, context: StepContext) -> OperationOutput:
        hashed_columns = ci_get(context.options, "HASH_COLUMNS", [])
        dropped_columns = ci_get(context.options, "DROP_COLUMNS", [])
        hashed_tags = ci_get(context.options, "HASH_TAGS", ["ID", "HOSPITAL_ID"])
        dropped_tags = ci_get(context.options, "DROP_TAGS", ["NAME"])
        salt = str(ci_get(context.options, "SALT", ""))
        transformed, mappings = anonymize_dataset(
            context.dataset,
            hash_columns=hashed_columns,
            drop_columns=dropped_columns,
            hash_tags=hashed_tags,
            drop_tags=dropped_tags,
            salt=salt,
        )
        return OperationOutput(
            name=context.step_name,
            dataset=transformed,
            tables=mappings,
            message=f"hashed_columns={len(mappings)} dropped_columns={len(dropped_columns)}",
        )


class SampleStepHandler(NamedStepHandler):
    names = frozenset({"SAMPLE"})

    def execute(self, context: StepContext) -> OperationOutput:
        rows = ci_get(context.options, "ROWS", None)
        frac = ci_get(context.options, "FRAC", ci_get(context.options, "FRACTION", None))
        seed = ci_get(context.options, "SEED", None)
        by_columns = ci_get(context.options, "BY_COLUMNS", [])
        by_tags = ci_get(context.options, "BY_TAGS", [])
        replace = bool(ci_get(context.options, "REPLACE", False))
        transformed = sample_dataset(
            context.dataset,
            rows=rows,
            frac=frac,
            seed=seed,
            by_columns=by_columns,
            by_tags=by_tags,
            replace=replace,
        )
        mode = f"rows={rows}" if rows is not None else f"frac={frac}"
        return OperationOutput(
            name=context.step_name,
            dataset=transformed,
            message=f"{mode} grouped_by={list(by_columns or []) + list(by_tags or [])} sampled_rows={len(transformed.df)}",
        )


class SplitComparatorStepHandler(NamedStepHandler):
    names = frozenset({"SPLIT_COMPARATOR", "SPLIT_CNUM"})

    def execute(self, context: StepContext) -> OperationOutput:
        target_columns = ci_get(context.options, "COLUMNS", [])
        drop_original = bool(ci_get(context.options, "DROP_ORIGINAL", False))
        transformed = split_comparator_columns(
            context.dataset,
            target_columns=target_columns,
            drop_original=drop_original,
        )
        return OperationOutput(
            name=context.step_name,
            dataset=transformed,
            message=f"columns={target_columns or '<NUM>'} drop_original={drop_original}",
        )


class HarmonizeComparatorStepHandler(NamedStepHandler):
    names = frozenset({"HARMONIZE_COMPARATOR", "HARMONIZE_CNUM"})

    def execute(self, context: StepContext) -> OperationOutput:
        target_columns = ci_get(context.options, "COLUMNS", [])
        exact_handling = str(ci_get(context.options, "EXACT_HANDLING", "between"))
        transformed, preview = harmonize_comparator_thresholds(
            context.dataset,
            target_columns=target_columns,
            exact_handling=exact_handling,
        )
        return OperationOutput(
            name=context.step_name,
            dataset=transformed,
            tables={"harmonization_preview": preview},
            message=f"columns={target_columns or '<NUM>'} exact_handling={exact_handling}",
        )


class ColumnsStepHandler(NamedStepHandler):
    names = frozenset({"COLUMNS", "INFO"})

    def execute(self, context: StepContext) -> OperationOutput:
        return OperationOutput(name=context.step_name, dataset=context.dataset, table=context.dataset.column_frame())


class ExtensionStepHandler:
    def matches(self, context: StepContext) -> bool:
        return True

    def execute(self, context: StepContext) -> OperationOutput:
        embedded_output = run_embedded_function(
            context.dataset,
            context.meta,
            context.step_name,
            context.options,
            allow_functions=context.allow_functions,
        )
        if embedded_output is not None:
            return embedded_output

        plugin_name = str(ci_get(context.options, "PLUGIN", ci_get(context.options, "FUNCTION", context.step_name)))
        plugin_output = run_plugin(
            context.dataset,
            plugin_name,
            context.meta,
            context.step_name,
            context.options,
            allow_external=context.allow_plugins,
        )
        if plugin_output is not None:
            return plugin_output
        if configured_plugin_modules(context.meta) and not context.allow_plugins:
            raise PermissionError(
                "External plugin modules are disabled. Re-run with allow_plugins=True only for trusted local files."
            )

        return OperationOutput(name=context.step_name, dataset=context.dataset, message="step skipped: no built-in handler")


STEP_HANDLERS: tuple[StepHandler, ...] = (
    ValidateStepHandler(),
    DescribeStepHandler(),
    EdaStepHandler(),
    EvaluateStepHandler(),
    AnonymizeStepHandler(),
    SampleStepHandler(),
    SplitComparatorStepHandler(),
    HarmonizeComparatorStepHandler(),
    ColumnsStepHandler(),
    ExtensionStepHandler(),
)


def _execute_step(
    dataset: TameDataset,
    step_name: str,
    meta: dict,
    *,
    allow_functions: bool = False,
    allow_plugins: bool = False,
) -> OperationOutput:
    normalized = step_name.upper()
    options = ci_get(meta, step_name, {})
    if not isinstance(options, dict):
        options = {}

    context = StepContext(
        dataset=dataset,
        step_name=step_name,
        normalized_name=normalized,
        meta=meta,
        options=options,
        allow_functions=allow_functions,
        allow_plugins=allow_plugins,
    )
    for handler in STEP_HANDLERS:
        if handler.matches(context):
            return handler.execute(context)
    raise RuntimeError("No pipeline step handler is registered.")

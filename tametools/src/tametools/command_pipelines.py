from __future__ import annotations

import argparse
from pathlib import Path
import shlex

import pandas as pd

from .analysis import (
    describe_dataset,
    exploratory_data_analysis,
    reference_interval_plan,
    reference_interval_summary,
    validate_dataset,
)
from .config import ci_get
from .embedded_functions import EmbeddedFunctionError, EmbeddedFunctionsDisabledError
from .exporters import available_export_formats, export_dataset
from .models import OperationOutput, PipelineResult, TameDataset
from .pipeline import execute_work
from .plugins.manager import run_plugin
from .transforms import (
    anonymize_dataset,
    harmonize_comparator_thresholds,
    sample_dataset,
    split_comparator_columns,
    write_mapping_tables,
)


class CommandPipelineError(ValueError):
    pass


def available_command_pipelines(meta: dict | None) -> list[str]:
    section = ci_get(meta, "PIPELINES", {})
    if not isinstance(section, dict):
        return []
    return [str(name) for name, commands in section.items() if isinstance(commands, list)]


def execute_command_pipeline(
    dataset: TameDataset,
    pipeline_name: str = "DEFAULT",
    *,
    allow_functions: bool = False,
) -> PipelineResult:
    commands = _resolve_pipeline_commands(dataset.meta, pipeline_name)
    outputs: list[OperationOutput] = []
    current = dataset
    for command_text in commands:
        output = _execute_command_text(current, command_text, allow_functions=allow_functions)
        outputs.append(output)
        if output.dataset is not None:
            current = output.dataset
    return PipelineResult(work_name=pipeline_name, final_dataset=current, outputs=outputs)


def _resolve_pipeline_commands(meta: dict, pipeline_name: str) -> list[str]:
    pipelines = ci_get(meta, "PIPELINES", {})
    pipeline = ci_get(pipelines, pipeline_name, None)
    if not isinstance(pipeline, list):
        raise CommandPipelineError(f"Undefined command pipeline: {pipeline_name}")
    commands = [str(item).strip() for item in pipeline if str(item).strip()]
    if not commands:
        raise CommandPipelineError(f"Command pipeline '{pipeline_name}' is empty.")
    return commands


def _execute_command_text(
    dataset: TameDataset,
    command_text: str,
    *,
    allow_functions: bool,
) -> OperationOutput:
    try:
        tokens = shlex.split(command_text)
    except ValueError as exc:
        raise CommandPipelineError(f"Invalid pipeline command syntax: {command_text}") from exc
    if not tokens:
        raise CommandPipelineError("Empty command in META[PIPELINES].")
    if tokens[0] == "tametools":
        tokens = tokens[1:]
    if not tokens:
        raise CommandPipelineError("A pipeline command cannot be just 'tametools'.")

    command = tokens[0]
    parser = _build_pipeline_parser(command)
    try:
        args = parser.parse_args(tokens[1:])
    except SystemExit as exc:
        raise CommandPipelineError(f"Invalid arguments for pipeline command '{command_text}'.") from exc
    return _dispatch_pipeline_command(dataset, command_text, command, args, allow_functions=allow_functions)


def _build_pipeline_parser(command: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"pipeline:{command}", add_help=False)
    normalized = command.lower()

    if normalized == "run":
        parser.add_argument("work", nargs="?", default="DEFAULT")
        parser.add_argument("--allow-functions", action="store_true")
        return parser
    if normalized == "run-plugin":
        parser.add_argument("plugin")
        return parser
    if normalized == "validate":
        return parser
    if normalized == "describe":
        return parser
    if normalized == "columns":
        return parser
    if normalized == "states":
        return parser
    if normalized == "info":
        return parser
    if normalized == "ri-plan":
        return parser
    if normalized == "ri-summary":
        return parser
    if normalized == "eda":
        parser.add_argument("--comparator-policy", choices=["DELETE", "VALUE", "KEEP", "HARMONIZE"], default=None)
        return parser
    if normalized == "anonymize":
        parser.add_argument("--hash-column", action="append", default=[])
        parser.add_argument("--drop-column", action="append", default=[])
        parser.add_argument("--hash-tag", action="append", default=["ID", "HOSPITAL_ID"])
        parser.add_argument("--drop-tag", action="append", default=["NAME"])
        parser.add_argument("--salt", default="")
        parser.add_argument("--mapping-output-dir")
        return parser
    if normalized == "sample":
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--rows", type=int)
        mode.add_argument("--frac", type=float)
        parser.add_argument("--seed", type=int)
        parser.add_argument("--by-column", action="append", default=[])
        parser.add_argument("--by-tag", action="append", default=[])
        parser.add_argument("--replace", action="store_true")
        return parser
    if normalized == "split-comparator":
        parser.add_argument("--column", action="append", default=[])
        parser.add_argument("--drop-original", action="store_true")
        return parser
    if normalized == "harmonize-comparator":
        parser.add_argument("--column", action="append", default=[])
        parser.add_argument("--exact-handling", choices=["between", "all"], default="between")
        return parser
    if normalized == "export":
        parser.add_argument("output")
        parser.add_argument("--format", choices=available_export_formats(), default=None)
        parser.add_argument("--bundle-data-format", choices=["csv", "tsv", "jsonl", "parquet", "feather"], default="csv")
        parser.add_argument("--no-schema", action="store_true")
        parser.add_argument("--no-job", action="store_true")
        return parser
    raise CommandPipelineError(f"Unsupported command in META[PIPELINES]: {command}")


def _dispatch_pipeline_command(
    dataset: TameDataset,
    command_text: str,
    command: str,
    args: argparse.Namespace,
    *,
    allow_functions: bool,
) -> OperationOutput:
    normalized = command.lower()

    if normalized == "run":
        try:
            nested = execute_work(dataset, args.work, allow_functions=allow_functions or args.allow_functions)
        except (EmbeddedFunctionError, EmbeddedFunctionsDisabledError, KeyError) as exc:
            raise CommandPipelineError(str(exc)) from exc
        message = f"nested_work={args.work} outputs={len(nested.outputs)} final_rows={len(nested.final_dataset.df)}"
        return OperationOutput(
            name=command_text,
            dataset=nested.final_dataset,
            message=message,
        )

    if normalized == "run-plugin":
        options = ci_get(dataset.meta, args.plugin, {})
        if not isinstance(options, dict):
            options = {}
        output = run_plugin(dataset, args.plugin, dataset.meta, args.plugin, options)
        if output is None:
            raise CommandPipelineError(f"Unknown plugin in command pipeline: {args.plugin}")
        output.name = command_text
        return output

    if normalized == "validate":
        result = validate_dataset(dataset)
        return OperationOutput(
            name=command_text,
            dataset=result.cleaned_dataset,
            issues=result.issues,
            message=f"issues={len(result.issues)} cleaned_rows={len(result.cleaned_dataset.df)}",
        )

    if normalized == "describe":
        return OperationOutput(name=command_text, dataset=dataset, table=describe_dataset(dataset))

    if normalized == "columns":
        return OperationOutput(name=command_text, dataset=dataset, table=dataset.column_frame())

    if normalized == "states":
        return OperationOutput(name=command_text, dataset=dataset, table=dataset.state_frame())

    if normalized == "info":
        return OperationOutput(
            name=command_text,
            dataset=dataset,
            message=f"source={dataset.source_path} rows={len(dataset.df)} columns={len(dataset.columns)}",
        )

    if normalized == "ri-plan":
        plan = reference_interval_plan(dataset)
        table = describe_plan(plan)
        return OperationOutput(name=command_text, dataset=dataset, table=table)

    if normalized == "ri-summary":
        return OperationOutput(name=command_text, dataset=dataset, table=reference_interval_summary(dataset))

    if normalized == "eda":
        report = exploratory_data_analysis(dataset, comparator_policy=args.comparator_policy)
        return OperationOutput(
            name=command_text,
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

    if normalized == "anonymize":
        transformed, mappings = anonymize_dataset(
            dataset,
            hash_columns=args.hash_column,
            drop_columns=args.drop_column,
            hash_tags=args.hash_tag,
            drop_tags=args.drop_tag,
            salt=args.salt,
        )
        warnings: list[str] = []
        if args.mapping_output_dir:
            output_dir = _resolve_pipeline_path(dataset, args.mapping_output_dir)
            manifest = write_mapping_tables(mappings, output_dir)
            warnings.append(f"saved_mappings={output_dir}")
            return OperationOutput(
                name=command_text,
                dataset=transformed,
                tables={**mappings, "mapping_files": manifest},
                warnings=warnings,
                message=f"hashed_columns={len(mappings)}",
            )
        return OperationOutput(
            name=command_text,
            dataset=transformed,
            tables=mappings if mappings else None,
            message=f"hashed_columns={len(mappings)}",
        )

    if normalized == "sample":
        transformed = sample_dataset(
            dataset,
            rows=args.rows,
            frac=args.frac,
            seed=args.seed,
            by_columns=args.by_column,
            by_tags=args.by_tag,
            replace=args.replace,
        )
        return OperationOutput(
            name=command_text,
            dataset=transformed,
            message=f"sampled_rows={len(transformed.df)}",
        )

    if normalized == "split-comparator":
        transformed = split_comparator_columns(
            dataset,
            target_columns=args.column,
            drop_original=args.drop_original,
        )
        return OperationOutput(
            name=command_text,
            dataset=transformed,
            message=f"columns={args.column or '<NUM>'} drop_original={args.drop_original}",
        )

    if normalized == "harmonize-comparator":
        transformed, preview = harmonize_comparator_thresholds(
            dataset,
            target_columns=args.column,
            exact_handling=args.exact_handling,
        )
        return OperationOutput(
            name=command_text,
            dataset=transformed,
            tables={"harmonization_preview": preview},
            message=f"columns={args.column or '<NUM>'} exact_handling={args.exact_handling}",
        )

    if normalized == "export":
        output_path = _resolve_pipeline_path(dataset, args.output)
        result = export_dataset(
            dataset,
            output_path,
            format=args.format,
            bundle_data_format=args.bundle_data_format,
            include_schema=not args.no_schema,
            include_job=not args.no_job,
        )
        warnings = [f"file={path}" for path in result.files]
        return OperationOutput(
            name=command_text,
            dataset=dataset,
            warnings=warnings,
            message=f"saved={result.output_path} format={result.format}",
        )

    raise CommandPipelineError(f"Unsupported command in META[PIPELINES]: {command}")


def _resolve_pipeline_path(dataset: TameDataset, value: str) -> str:
    path = Path(value)
    if path.is_absolute():
        return str(path)
    if dataset.source_path:
        return str(Path(dataset.source_path).resolve().parent / path)
    return str(path.resolve())


def describe_plan(plan) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "field": ["result_columns", "item_column", "age_column", "gender_column", "by_columns"],
            "value": [
                ", ".join(column.name for column in plan.result_columns),
                plan.item_column.name if plan.item_column else "",
                plan.age_column.name if plan.age_column else "",
                plan.gender_column.name if plan.gender_column else "",
                ", ".join(column.name for column in plan.by_columns),
            ],
        }
    )

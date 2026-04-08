from __future__ import annotations

import argparse
from pathlib import Path

from .analysis import (
    describe_dataset,
    exploratory_data_analysis,
    reference_interval_plan,
    reference_interval_summary,
    validate_dataset,
)
from .command_pipelines import CommandPipelineError, available_command_pipelines, execute_command_pipeline
from .config import ci_get
from .embedded_functions import EmbeddedFunctionError, EmbeddedFunctionsDisabledError
from .exporters import available_export_formats, export_dataset
from .images import embed_image_columns, extract_image_columns
from .io import import_xlsx_into_tame, read_tame, read_xlsx, write_data_tame, write_meta_tame, write_tame, write_xlsx
from .merge import merge_datasets
from .pipeline import available_works, execute_work
from .plugins.manager import list_plugins, run_plugin
from .transforms import (
    anonymize_dataset,
    harmonize_comparator_thresholds,
    sample_dataset,
    split_comparator_columns,
    write_mapping_tables,
)


def main() -> int:
    parser = argparse.ArgumentParser(prog="tametools")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("info", "columns", "states", "validate", "describe", "ri-plan", "ri-summary"):
        sub = subparsers.add_parser(name)
        sub.add_argument("path")
        sub.add_argument("--meta")

    eda_parser = subparsers.add_parser("eda")
    eda_parser.add_argument("path")
    eda_parser.add_argument("--meta")
    eda_parser.add_argument("--comparator-policy", choices=["DELETE", "VALUE", "KEEP", "HARMONIZE"], default=None)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("path")
    run_parser.add_argument("--meta")
    run_parser.add_argument("work", nargs="?", default="DEFAULT")
    run_parser.add_argument("--output")
    run_parser.add_argument("--allow-functions", action="store_true")

    pipelines_parser = subparsers.add_parser("pipelines")
    pipelines_parser.add_argument("path")
    pipelines_parser.add_argument("--meta")

    run_pipeline_parser = subparsers.add_parser("run-pipeline")
    run_pipeline_parser.add_argument("path")
    run_pipeline_parser.add_argument("--meta")
    run_pipeline_parser.add_argument("pipeline", nargs="?", default="DEFAULT")
    run_pipeline_parser.add_argument("--output")
    run_pipeline_parser.add_argument("--allow-functions", action="store_true")

    plugins_parser = subparsers.add_parser("plugins")
    plugins_parser.add_argument("path", nargs="?")
    plugins_parser.add_argument("--meta")

    run_plugin_parser = subparsers.add_parser("run-plugin")
    run_plugin_parser.add_argument("path")
    run_plugin_parser.add_argument("--meta")
    run_plugin_parser.add_argument("plugin")
    run_plugin_parser.add_argument("--output")

    anonymize_parser = subparsers.add_parser("anonymize")
    anonymize_parser.add_argument("path")
    anonymize_parser.add_argument("--meta")
    anonymize_parser.add_argument("--output")
    anonymize_parser.add_argument("--hash-column", action="append", default=[])
    anonymize_parser.add_argument("--drop-column", action="append", default=[])
    anonymize_parser.add_argument("--hash-tag", action="append", default=["ID", "HOSPITAL_ID"])
    anonymize_parser.add_argument("--drop-tag", action="append", default=["NAME"])
    anonymize_parser.add_argument("--salt", default="")
    anonymize_parser.add_argument("--mapping-output-dir")

    sample_parser = subparsers.add_parser("sample")
    sample_parser.add_argument("path")
    sample_parser.add_argument("--meta")
    sample_parser.add_argument("--output")
    sample_mode = sample_parser.add_mutually_exclusive_group(required=True)
    sample_mode.add_argument("--rows", type=int)
    sample_mode.add_argument("--frac", type=float)
    sample_parser.add_argument("--seed", type=int)
    sample_parser.add_argument("--by-column", action="append", default=[])
    sample_parser.add_argument("--by-tag", action="append", default=[])
    sample_parser.add_argument("--replace", action="store_true")

    split_parser = subparsers.add_parser("split-comparator")
    split_parser.add_argument("path")
    split_parser.add_argument("--meta")
    split_parser.add_argument("--output")
    split_parser.add_argument("--column", action="append", default=[])
    split_parser.add_argument("--drop-original", action="store_true")

    harmonize_parser = subparsers.add_parser("harmonize-comparator")
    harmonize_parser.add_argument("path")
    harmonize_parser.add_argument("--meta")
    harmonize_parser.add_argument("--output")
    harmonize_parser.add_argument("--column", action="append", default=[])
    harmonize_parser.add_argument("--exact-handling", choices=["between", "all"], default="between")

    extract_images_parser = subparsers.add_parser("extract-images")
    extract_images_parser.add_argument("path")
    extract_images_parser.add_argument("--meta")
    extract_images_parser.add_argument("--output-dir", required=True)
    extract_images_parser.add_argument("--output")
    extract_images_parser.add_argument("--column", action="append", default=[])

    embed_images_parser = subparsers.add_parser("embed-images")
    embed_images_parser.add_argument("path")
    embed_images_parser.add_argument("--meta")
    embed_images_parser.add_argument("--output", required=True)
    embed_images_parser.add_argument("--base-dir")
    embed_images_parser.add_argument("--column", action="append", default=[])

    merge_parser = subparsers.add_parser("merge")
    merge_parser.add_argument("inputs", nargs="+")
    merge_parser.add_argument("--output", required=True)
    merge_parser.add_argument("--label", action="append", default=[])
    merge_parser.add_argument("--num-conflict", choices=["strict", "promote", "split", "harmonize"], default="promote")
    merge_parser.add_argument("--no-source-column", action="store_true")

    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("path")
    export_parser.add_argument("--meta")
    export_parser.add_argument("output")
    export_parser.add_argument("--format", choices=available_export_formats(), default=None)
    export_parser.add_argument("--bundle-data-format", choices=["csv", "tsv", "jsonl", "parquet", "feather"], default="csv")
    export_parser.add_argument("--no-schema", action="store_true")
    export_parser.add_argument("--no-job", action="store_true")

    split_tame_parser = subparsers.add_parser("split-tame")
    split_tame_parser.add_argument("path")
    split_tame_parser.add_argument("--meta-output")
    split_tame_parser.add_argument("--data-output")

    attach_meta_parser = subparsers.add_parser("attach-meta")
    attach_meta_parser.add_argument("path")
    attach_meta_parser.add_argument("meta")
    attach_meta_parser.add_argument("--output", required=True)

    import_xlsx_parser = subparsers.add_parser("import-xlsx")
    import_xlsx_parser.add_argument("template")
    import_xlsx_parser.add_argument("xlsx")
    import_xlsx_parser.add_argument("--output", required=True)

    args = parser.parse_args()

    if args.command == "info":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        print(f"source: {dataset.source_path}")
        print(f"rows: {len(dataset.df)}")
        print(f"columns: {len(dataset.columns)}")
        print(f"sections: {', '.join(sorted(dataset.raw_sections)) or 'META, DATA'}")
        print(f"works: {available_works(dataset.meta)}")
        print(f"pipelines: {available_command_pipelines(dataset.meta)}")
        return 0

    if args.command == "columns":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        print(dataset.column_frame().to_string(index=False))
        return 0

    if args.command == "states":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        print(dataset.state_frame().to_string(index=False))
        return 0

    if args.command == "validate":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        result = validate_dataset(dataset)
        print(f"issues: {len(result.issues)}")
        if result.issues:
            for issue in result.issues[:20]:
                print(f"row={issue.row_number} column={issue.column} tag={issue.tag} value={issue.value!r} message={issue.message}")
        print(f"cleaned_rows: {len(result.cleaned_dataset.df)}")
        return 0

    if args.command == "describe":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        print(describe_dataset(dataset).to_string(index=False))
        return 0

    if args.command == "eda":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        report = exploratory_data_analysis(dataset, comparator_policy=args.comparator_policy)
        _print_table("summary", report.summary)
        _print_table("comparator_profile", report.comparator_profile)
        _print_table("comparator_policy_impact", report.comparator_policy_impact)
        _print_table("harmonization_preview", report.harmonization_preview)
        for warning in report.warnings:
            print(f"warning: {warning}")
        return 0

    if args.command == "ri-plan":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        plan = reference_interval_plan(dataset)
        print(f"result_columns: {[column.name for column in plan.result_columns]}")
        print(f"item_column: {plan.item_column.name if plan.item_column else None}")
        print(f"age_column: {plan.age_column.name if plan.age_column else None}")
        print(f"gender_column: {plan.gender_column.name if plan.gender_column else None}")
        print(f"by_columns: {[column.name for column in plan.by_columns]}")
        return 0

    if args.command == "ri-summary":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        summary = reference_interval_summary(dataset)
        print(summary.to_string(index=False) if not summary.empty else "No numeric result columns found.")
        return 0

    if args.command == "run":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        try:
            result = execute_work(dataset, args.work, allow_functions=args.allow_functions)
        except (EmbeddedFunctionError, EmbeddedFunctionsDisabledError) as exc:
            print(f"error: {exc}")
            return 1
        print(f"work: {result.work_name}")
        for output in result.outputs:
            print(f"[{output.name}]")
            if output.message:
                print(output.message)
            if output.warnings:
                for warning in output.warnings:
                    print(f"warning: {warning}")
            if output.issues:
                for issue in output.issues[:20]:
                    print(f"row={issue.row_number} column={issue.column} tag={issue.tag} value={issue.value!r} message={issue.message}")
            if output.table is not None:
                print(output.table.to_string(index=False))
            if output.tables:
                for name, table in output.tables.items():
                    _print_table(name, table)
        if args.output:
            _save_dataset(result.final_dataset, args.output)
            print(f"saved: {args.output}")
        print(f"final_rows: {len(result.final_dataset.df)}")
        return 0

    if args.command == "pipelines":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        pipelines = available_command_pipelines(dataset.meta)
        for name in pipelines:
            commands = ci_get(ci_get(dataset.meta, "PIPELINES", {}), name, [])
            print(f"[{name}]")
            for command in commands:
                print(str(command))
        return 0

    if args.command == "run-pipeline":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        try:
            result = execute_command_pipeline(dataset, args.pipeline, allow_functions=args.allow_functions)
        except (CommandPipelineError, EmbeddedFunctionError, EmbeddedFunctionsDisabledError, ImportError) as exc:
            print(f"error: {exc}")
            return 1
        print(f"pipeline: {args.pipeline}")
        for output in result.outputs:
            print(f"[{output.name}]")
            if output.message:
                print(output.message)
            if output.warnings:
                for warning in output.warnings:
                    print(f"warning: {warning}")
            if output.issues:
                for issue in output.issues[:20]:
                    print(f"row={issue.row_number} column={issue.column} tag={issue.tag} value={issue.value!r} message={issue.message}")
            if output.table is not None:
                print(output.table.to_string(index=False))
            if output.tables:
                for name, table in output.tables.items():
                    _print_table(name, table)
        if args.output:
            _save_dataset(result.final_dataset, args.output)
            print(f"saved: {args.output}")
        print(f"final_rows: {len(result.final_dataset.df)}")
        return 0

    if args.command == "plugins":
        meta = _load_dataset(args.path, meta_path=args.meta).meta if args.path else None
        plugins = list_plugins(meta)
        for plugin in plugins:
            print(f"{plugin.name}: {plugin.description}")
        return 0

    if args.command == "run-plugin":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        options = ci_get(dataset.meta, args.plugin, {})
        if not isinstance(options, dict):
            options = {}
        output = run_plugin(dataset, args.plugin, dataset.meta, args.plugin, options)
        if output is None:
            raise ValueError(f"Unknown plugin: {args.plugin}")
        print(f"[{output.name}]")
        if output.message:
            print(output.message)
        if output.warnings:
            for warning in output.warnings:
                print(f"warning: {warning}")
        if output.table is not None:
            print(output.table.to_string(index=False))
        if output.tables:
            for name, table in output.tables.items():
                _print_table(name, table)
        if output.dataset is not None and args.output:
            _save_dataset(output.dataset, args.output)
            print(f"saved: {args.output}")
        return 0

    if args.command == "anonymize":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        transformed, mappings = anonymize_dataset(
            dataset,
            hash_columns=args.hash_column,
            drop_columns=args.drop_column,
            hash_tags=args.hash_tag,
            drop_tags=args.drop_tag,
            salt=args.salt,
        )
        if args.output:
            _save_dataset(transformed, args.output)
        print(f"rows: {len(transformed.df)}")
        print(f"columns: {len(transformed.columns)}")
        if args.mapping_output_dir:
            manifest = write_mapping_tables(mappings, args.mapping_output_dir)
            _print_table("mapping_files", manifest)
            print(f"saved_mappings: {args.mapping_output_dir}")
        for name, table in mappings.items():
            _print_table(f"mapping:{name}", table)
        if args.output:
            print(f"saved: {args.output}")
        return 0

    if args.command == "sample":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        transformed = sample_dataset(
            dataset,
            rows=args.rows,
            frac=args.frac,
            seed=args.seed,
            by_columns=args.by_column,
            by_tags=args.by_tag,
            replace=args.replace,
        )
        if args.output:
            _save_dataset(transformed, args.output)
            print(f"saved: {args.output}")
        print(f"rows: {len(transformed.df)}")
        print(f"columns: {len(transformed.columns)}")
        return 0

    if args.command == "split-comparator":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        transformed = split_comparator_columns(
            dataset,
            target_columns=args.column,
            drop_original=args.drop_original,
        )
        if args.output:
            _save_dataset(transformed, args.output)
            print(f"saved: {args.output}")
        print(transformed.column_frame().to_string(index=False))
        return 0

    if args.command == "harmonize-comparator":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        transformed, preview = harmonize_comparator_thresholds(
            dataset,
            target_columns=args.column,
            exact_handling=args.exact_handling,
        )
        _print_table("harmonization_preview", preview)
        if args.output:
            _save_dataset(transformed, args.output)
            print(f"saved: {args.output}")
        return 0

    if args.command == "merge":
        datasets = [_load_dataset(path) for path in args.inputs]
        result = merge_datasets(
            datasets,
            source_labels=args.label or None,
            add_source_column=not args.no_source_column,
            numeric_conflict=args.num_conflict,
        )
        _save_dataset(result.dataset, args.output)
        for warning in result.warnings:
            print(f"warning: {warning}")
        print(f"saved: {args.output}")
        print(f"rows: {len(result.dataset.df)}")
        print(f"columns: {len(result.dataset.columns)}")
        return 0

    if args.command == "export":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        try:
            result = export_dataset(
                dataset,
                args.output,
                format=args.format,
                bundle_data_format=args.bundle_data_format,
                include_schema=not args.no_schema,
                include_job=not args.no_job,
            )
        except ImportError as exc:
            print(f"error: {exc}")
            return 1
        print(f"format: {result.format}")
        for warning in result.warnings:
            print(f"warning: {warning}")
        for path in result.files:
            print(f"file: {path}")
        print(f"saved: {result.output_path}")
        return 0

    if args.command == "extract-images":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        transformed, manifest = extract_image_columns(
            dataset,
            output_dir=args.output_dir,
            target_columns=args.column,
        )
        _print_table("images", manifest)
        if args.output:
            _save_dataset(transformed, args.output)
            print(f"saved: {args.output}")
        return 0

    if args.command == "embed-images":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        transformed, manifest = embed_image_columns(
            dataset,
            base_dir=args.base_dir,
            target_columns=args.column,
        )
        _print_table("images", manifest)
        _save_dataset(transformed, args.output)
        print(f"saved: {args.output}")
        return 0

    if args.command == "split-tame":
        if not args.meta_output and not args.data_output:
            raise ValueError("split-tame requires --meta-output and/or --data-output.")
        dataset = _load_dataset(args.path)
        if args.meta_output:
            _save_dataset(dataset, args.meta_output)
            print(f"saved_meta: {args.meta_output}")
        if args.data_output:
            _save_dataset(dataset, args.data_output)
            print(f"saved_data: {args.data_output}")
        print(f"rows: {len(dataset.df)}")
        print(f"columns: {len(dataset.columns)}")
        return 0

    if args.command == "attach-meta":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        _save_dataset(dataset, args.output)
        print(f"saved: {args.output}")
        print(f"rows: {len(dataset.df)}")
        print(f"columns: {len(dataset.columns)}")
        return 0

    if args.command == "import-xlsx":
        template = _load_dataset(args.template)
        dataset = import_xlsx_into_tame(template, args.xlsx)
        _save_dataset(dataset, args.output)
        print(f"saved: {args.output}")
        print(f"rows: {len(dataset.df)}")
        print(f"columns: {len(dataset.columns)}")
        return 0

    return 1


def _load_dataset(path: str, *, meta_path: str | None = None):
    kind = _path_kind(path)
    if kind in {"tame", "meta_tame", "data_tame"}:
        return read_tame(path, meta_path=meta_path)
    if kind == "xlsx":
        return read_xlsx(path, meta_path=meta_path)
    raise ValueError(f"Unsupported input format: {path}")


def _save_dataset(dataset, path: str) -> None:
    kind = _path_kind(path)
    if kind == "meta_tame":
        write_meta_tame(path, dataset)
        return
    if kind == "data_tame":
        write_data_tame(path, dataset)
        return
    if kind == "tame":
        write_tame(path, dataset)
        return
    if kind == "xlsx":
        write_xlsx(path, dataset)
        return
    raise ValueError(f"Unsupported output format: {path}")


def _path_kind(path: str) -> str:
    lowered = str(path).lower()
    if lowered.endswith(".meta.tame"):
        return "meta_tame"
    if lowered.endswith(".data.tame"):
        return "data_tame"
    suffix = Path(path).suffix.lower()
    if suffix == ".tame":
        return "tame"
    if suffix in {".xlsx", ".xlsm"}:
        return "xlsx"
    return "unknown"


def _print_table(name: str, table) -> None:
    print(f"[{name}]")
    if table is None or table.empty:
        print("(empty)")
        return
    print(table.to_string(index=False))

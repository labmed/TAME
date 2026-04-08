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


class TametoolsHelpFormatter(argparse.RawTextHelpFormatter, argparse.ArgumentDefaultsHelpFormatter):
    pass


def build_parser() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    parser = argparse.ArgumentParser(
        prog="tametools",
        description=(
            "Read, validate, transform, and analyze TAME or XLSX datasets.\n\n"
            "Use `tametools help COMMAND` or `tametools COMMAND --help` for command-specific help."
        ),
        epilog=(
            "Examples:\n"
            "  tametools info sample.tame\n"
            "  tametools run sample.tame DEFAULT --output validated.tame\n"
            "  tametools help run-pipeline"
        ),
        formatter_class=TametoolsHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    command_parsers: dict[str, argparse.ArgumentParser] = {}

    def add_command(name: str, *, help_text: str, description: str) -> argparse.ArgumentParser:
        sub = subparsers.add_parser(
            name,
            help=help_text,
            description=description,
            formatter_class=TametoolsHelpFormatter,
        )
        command_parsers[name] = sub
        return sub

    help_parser = add_command(
        "help",
        help_text="Show top-level help or help for a specific command.",
        description="Show `tametools` help text or the detailed help for a single command.",
    )
    help_parser.add_argument("topic", nargs="?", help="Command name to describe, for example `run` or `export`.")

    info_parser = add_command(
        "info",
        help_text="Show dataset-level metadata summary.",
        description="Print source path, row count, column count, available sections, works, and command pipelines.",
    )
    _add_path_arguments(info_parser)

    columns_parser = add_command(
        "columns",
        help_text="List columns and resolved tags.",
        description="Print the resolved column table including original header, display name, and effective tags.",
    )
    _add_path_arguments(columns_parser)

    states_parser = add_command(
        "states",
        help_text="Show cell-state matrix.",
        description="Print per-cell logical states such as ABSENT, NULL, EMPTY, WS, and VALUE.",
    )
    _add_path_arguments(states_parser)

    validate_parser = add_command(
        "validate",
        help_text="Validate values against tags and settings.",
        description="Validate a dataset using its effective tags and report the first validation issues.",
    )
    _add_path_arguments(validate_parser)

    describe_parser = add_command(
        "describe",
        help_text="Describe columns and inferred data kinds.",
        description="Print per-column descriptive statistics based on resolved tags and normalized values.",
    )
    _add_path_arguments(describe_parser)

    ri_plan_parser = add_command(
        "ri-plan",
        help_text="Show how reference-interval grouping will resolve.",
        description="Print the result, age, gender, and grouping columns that would be used for reference-interval workflows.",
    )
    _add_path_arguments(ri_plan_parser)

    ri_summary_parser = add_command(
        "ri-summary",
        help_text="Summarize numeric result columns for RI workflows.",
        description="Print summary statistics for numeric or comparator-aware result columns.",
    )
    _add_path_arguments(ri_summary_parser)

    eda_parser = add_command(
        "eda",
        help_text="Run exploratory analysis with comparator-aware summaries.",
        description="Print summary tables, comparator profiles, policy impact, and harmonization preview for a dataset.",
    )
    _add_path_arguments(eda_parser)
    eda_parser.add_argument(
        "--comparator-policy",
        choices=["DELETE", "VALUE", "KEEP", "HARMONIZE"],
        default=None,
        help="Override comparator handling policy for this analysis run.",
    )

    run_parser = add_command(
        "run",
        help_text="Execute a named META[WORKS] pipeline.",
        description="Execute a step-based pipeline defined in META[WORKS] and print each operation output.",
    )
    _add_path_arguments(run_parser)
    run_parser.add_argument("work", nargs="?", default="DEFAULT", help="Name of the work pipeline to execute.")
    run_parser.add_argument("--output", help="Optional output dataset path (`.tame`, `.meta.tame`, `.data.tame`, `.xlsx`).")
    run_parser.add_argument(
        "--allow-functions",
        action="store_true",
        help="Allow embedded META[FUNCTIONS] execution for this run.",
    )

    pipelines_parser = add_command(
        "pipelines",
        help_text="List META[PIPELINES] command chains.",
        description="Print the named command pipelines defined in META[PIPELINES].",
    )
    _add_path_arguments(pipelines_parser)

    run_pipeline_parser = add_command(
        "run-pipeline",
        help_text="Execute a named META[PIPELINES] command chain.",
        description="Execute a command-chain pipeline defined in META[PIPELINES] on the current dataset.",
    )
    _add_path_arguments(run_pipeline_parser)
    run_pipeline_parser.add_argument("pipeline", nargs="?", default="DEFAULT", help="Name of the command pipeline to execute.")
    run_pipeline_parser.add_argument("--output", help="Optional output dataset path after the final pipeline step.")
    run_pipeline_parser.add_argument(
        "--allow-functions",
        action="store_true",
        help="Allow embedded META[FUNCTIONS] execution for nested `run` steps.",
    )

    plugins_parser = add_command(
        "plugins",
        help_text="List available built-in and configured plugins.",
        description="Print plugin names and descriptions. When a dataset is provided, META-driven external plugins are also loaded.",
    )
    plugins_parser.add_argument("path", nargs="?", help="Optional dataset path used to load META-configured plugins.")
    plugins_parser.add_argument("--meta", help="Optional sidecar metadata path to apply while loading the dataset.")

    run_plugin_parser = add_command(
        "run-plugin",
        help_text="Execute a single plugin by name.",
        description="Run one plugin and optionally save the resulting dataset.",
    )
    _add_path_arguments(run_plugin_parser)
    run_plugin_parser.add_argument("plugin", help="Plugin name, for example `REFERENCE_INTERVAL`.")
    run_plugin_parser.add_argument("--output", help="Optional output dataset path.")

    anonymize_parser = add_command(
        "anonymize",
        help_text="Hash identifier columns and drop sensitive columns.",
        description="Anonymize a dataset using explicit columns or tag-driven defaults, and optionally save mapping tables.",
    )
    _add_path_arguments(anonymize_parser)
    anonymize_parser.add_argument("--output", help="Optional output dataset path.")
    anonymize_parser.add_argument("--hash-column", action="append", default=[], help="Column name to hash. Repeat as needed.")
    anonymize_parser.add_argument("--drop-column", action="append", default=[], help="Column name to drop. Repeat as needed.")
    anonymize_parser.add_argument(
        "--hash-tag",
        action="append",
        default=["ID", "HOSPITAL_ID"],
        help="Tag to hash when present. Repeat as needed.",
    )
    anonymize_parser.add_argument(
        "--drop-tag",
        action="append",
        default=["NAME"],
        help="Tag to drop when present. Repeat as needed.",
    )
    anonymize_parser.add_argument("--salt", default="", help="Optional salt string for stable pseudonymization.")
    anonymize_parser.add_argument("--mapping-output-dir", help="Directory where column mapping CSV files will be written.")

    sample_parser = add_command(
        "sample",
        help_text="Sample rows from a dataset.",
        description="Create a smaller dataset by row count or fraction, optionally grouped by specific columns or tags.",
    )
    _add_path_arguments(sample_parser)
    sample_parser.add_argument("--output", help="Optional output dataset path.")
    sample_mode = sample_parser.add_mutually_exclusive_group(required=True)
    sample_mode.add_argument("--rows", type=int, help="Number of rows to sample.")
    sample_mode.add_argument("--frac", type=float, help="Fraction of rows to sample.")
    sample_parser.add_argument("--seed", type=int, help="Random seed for reproducible sampling.")
    sample_parser.add_argument("--by-column", action="append", default=[], help="Group by column name before sampling. Repeat as needed.")
    sample_parser.add_argument("--by-tag", action="append", default=[], help="Group by tag before sampling. Repeat as needed.")
    sample_parser.add_argument("--replace", action="store_true", help="Sample with replacement.")

    split_parser = add_command(
        "split-comparator",
        help_text="Split `<NUM>` values into comparator and numeric columns.",
        description="Create `__cmp` and `__num` columns for comparator-aware numeric result values.",
    )
    _add_path_arguments(split_parser)
    split_parser.add_argument("--output", help="Optional output dataset path.")
    split_parser.add_argument("--column", action="append", default=[], help="Target column name. Repeat as needed.")
    split_parser.add_argument("--drop-original", action="store_true", help="Drop the original comparator-aware column after splitting.")

    harmonize_parser = add_command(
        "harmonize-comparator",
        help_text="Unify mixed comparator thresholds.",
        description="Rewrite comparator-aware numeric values to a unified threshold representation and print a preview table.",
    )
    _add_path_arguments(harmonize_parser)
    harmonize_parser.add_argument("--output", help="Optional output dataset path.")
    harmonize_parser.add_argument("--column", action="append", default=[], help="Target column name. Repeat as needed.")
    harmonize_parser.add_argument(
        "--exact-handling",
        choices=["between", "all"],
        default="between",
        help="How to treat exact numeric values when applying harmonization.",
    )

    extract_images_parser = add_command(
        "extract-images",
        help_text="Extract IMAGE::B64 columns to files.",
        description="Write embedded images to a directory and optionally save a PATH-based dataset.",
    )
    _add_path_arguments(extract_images_parser)
    extract_images_parser.add_argument("--output-dir", required=True, help="Directory where extracted image files will be written.")
    extract_images_parser.add_argument("--output", help="Optional output dataset path.")
    extract_images_parser.add_argument("--column", action="append", default=[], help="Target column name. Repeat as needed.")

    embed_images_parser = add_command(
        "embed-images",
        help_text="Embed IMAGE::PATH columns as data URLs.",
        description="Read image files referenced by PATH-based image columns and write a B64-embedded dataset.",
    )
    _add_path_arguments(embed_images_parser)
    embed_images_parser.add_argument("--output", required=True, help="Output dataset path.")
    embed_images_parser.add_argument("--base-dir", help="Base directory used to resolve relative image paths.")
    embed_images_parser.add_argument("--column", action="append", default=[], help="Target column name. Repeat as needed.")

    merge_parser = add_command(
        "merge",
        help_text="Merge multiple datasets by tag semantics.",
        description="Merge multiple datasets and resolve NUM vs <NUM> conflicts according to the chosen policy.",
    )
    merge_parser.add_argument("inputs", nargs="+", help="Input dataset paths to merge.")
    merge_parser.add_argument("--output", required=True, help="Output dataset path.")
    merge_parser.add_argument("--label", action="append", default=[], help="Optional source label. Repeat to match input order.")
    merge_parser.add_argument(
        "--num-conflict",
        choices=["strict", "promote", "split", "harmonize"],
        default="promote",
        help="How to resolve numeric tag conflicts across inputs.",
    )
    merge_parser.add_argument("--no-source-column", action="store_true", help="Do not add a source_dataset provenance column.")

    export_parser = add_command(
        "export",
        help_text="Export a dataset to flat files or an R bundle.",
        description="Export to CSV, TSV, JSONL, SQL, Parquet, Feather, or an `r_bundle` directory.",
    )
    _add_path_arguments(export_parser)
    export_parser.add_argument("output", help="Export target path or bundle directory.")
    export_parser.add_argument("--format", choices=available_export_formats(), default=None, help="Explicit export format.")
    export_parser.add_argument(
        "--bundle-data-format",
        choices=["csv", "tsv", "jsonl", "parquet", "feather"],
        default="csv",
        help="Data file format used inside an `r_bundle` export.",
    )
    export_parser.add_argument("--no-schema", action="store_true", help="Exclude SCHEMA from bundle-style exports.")
    export_parser.add_argument("--no-job", action="store_true", help="Exclude JOB from bundle-style exports.")

    split_tame_parser = add_command(
        "split-tame",
        help_text="Split a dataset into `.meta.tame` and `.data.tame` sidecars.",
        description="Write control sections and data section to separate sidecar files.",
    )
    split_tame_parser.add_argument("path", help="Input `.tame` dataset path.")
    split_tame_parser.add_argument("--meta-output", help="Output path for `.meta.tame`.")
    split_tame_parser.add_argument("--data-output", help="Output path for `.data.tame`.")

    attach_meta_parser = add_command(
        "attach-meta",
        help_text="Attach a `.meta.tame` sidecar to a dataset or spreadsheet.",
        description="Load a data-bearing file together with explicit metadata and write the combined dataset.",
    )
    attach_meta_parser.add_argument("path", help="Input data-bearing file (`.tame`, `.data.tame`, `.xlsx`).")
    attach_meta_parser.add_argument("meta", help="Input `.meta.tame` path.")
    attach_meta_parser.add_argument("--output", required=True, help="Output dataset path.")

    import_xlsx_parser = add_command(
        "import-xlsx",
        help_text="Replace the data rows of a template `.tame` with a new spreadsheet.",
        description="Validate spreadsheet headers against a template TAME dataset and replace only the data table.",
    )
    import_xlsx_parser.add_argument("template", help="Template `.tame` path.")
    import_xlsx_parser.add_argument("xlsx", help="Input spreadsheet path.")
    import_xlsx_parser.add_argument("--output", required=True, help="Output `.tame` path.")

    return parser, command_parsers


def main(argv: list[str] | None = None) -> int:
    parser, command_parsers = build_parser()
    args = parser.parse_args(argv)

    if args.command == "help":
        if args.topic:
            target = command_parsers.get(args.topic)
            if target is None:
                print(f"error: unknown command '{args.topic}'")
                print()
                parser.print_help()
                return 1
            target.print_help()
            return 0
        parser.print_help()
        return 0

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


def _add_path_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", help="Input dataset path (`.tame`, `.data.tame`, `.meta.tame`, `.xlsx`).")
    parser.add_argument("--meta", help="Optional `.meta.tame` sidecar path to apply while loading the dataset.")

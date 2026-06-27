from __future__ import annotations

import argparse
from importlib import metadata as importlib_metadata
from importlib.util import find_spec
import json
from numbers import Number
from pathlib import Path
import platform
import shlex
import sys
import tempfile
import traceback
import unicodedata
from zipfile import BadZipFile

from .exceptions import TameError, TameFormatError, TameImportError, TameTagError, TameValidationError
from .toml_compat import dumps as dumps_toml


_RUNTIME_LOADED = False
_CURRENT_CLI_COMMAND_TEXT = ""
_CURRENT_CLI_OPERATION = ""
_CURRENT_CLI_SOURCE_PATHS: list[str] = []
_CURRENT_CLI_LOG_LEVEL = "simple"
_EXPORT_FORMATS = ("csv", "tsv", "jsonl", "sql", "parquet", "feather", "r_bundle")
_BUNDLE_DATA_FORMATS = ("csv", "tsv", "jsonl", "parquet", "feather")


def _load_runtime() -> None:
    """Import data-processing dependencies only after help/version handling."""
    global _RUNTIME_LOADED
    if _RUNTIME_LOADED:
        return

    global ActionError, available_action_pipelines, available_actions, execute_action, execute_action_pipeline
    global describe_dataset, exploratory_data_analysis, reference_interval_plan, validate_dataset
    global AGE_CANONICAL_UNITS, AGE_DEFAULT_UNIT, AGE_UCUM_SYSTEM, age_value_profile, standardize_age_dataset
    global STATE_VALUE, cell_state, state_counts
    global CommandPipelineError, available_command_pipelines, execute_command_pipeline
    global ci_get
    global EmbeddedFunctionError, EmbeddedFunctionsDisabledError
    global evaluate_dataset, write_evaluation_report
    global export_dataset
    global embed_image_columns, extract_image_columns
    global import_xlsx_into_tame, read_tame, read_xlsx, write_data_tame, write_meta_tame, write_tame, write_xlsx
    global merge_datasets
    global available_works, execute_work
    global configured_plugin_modules, list_plugins, run_plugin
    global apply_preset, preset_payloads
    global category_value_profile, datetime_value_profile
    global SEX_CANONICAL_VALUES, parse_sex_binary_map, sex_value_profile, standardize_sex_dataset
    global flat_tag_names, tag_catalog_payload, tags_to_header, tags_to_meta
    global normalize_tag, parse_header
    global anonymize_dataset, fix_num_comparator_values, harmonize_comparator_thresholds, sample_dataset
    global split_comparator_columns, write_mapping_tables

    from .actions import ActionError, available_action_pipelines, available_actions, execute_action, execute_action_pipeline
    from .analysis import (
        describe_dataset,
        exploratory_data_analysis,
        reference_interval_plan,
        validate_dataset,
    )
    from .age import AGE_CANONICAL_UNITS, AGE_DEFAULT_UNIT, AGE_UCUM_SYSTEM, age_value_profile, standardize_age_dataset
    from .cellstate import STATE_VALUE, cell_state, state_counts
    from .command_pipelines import CommandPipelineError, available_command_pipelines, execute_command_pipeline
    from .config import ci_get
    from .embedded_functions import EmbeddedFunctionError, EmbeddedFunctionsDisabledError
    from .evaluation import evaluate_dataset, write_evaluation_report
    from .exporters import export_dataset
    from .images import embed_image_columns, extract_image_columns
    from .io import import_xlsx_into_tame, read_tame, read_xlsx, write_data_tame, write_meta_tame, write_tame, write_xlsx
    from .merge import merge_datasets
    from .pipeline import available_works, execute_work
    from .plugin_base.manager import configured_plugin_modules, list_plugins, run_plugin
    from .presets import apply_preset, preset_payloads
    from .review_profiles import category_value_profile, datetime_value_profile
    from .sex import SEX_CANONICAL_VALUES, parse_sex_binary_map, sex_value_profile, standardize_sex_dataset
    from .tag_catalog import flat_tag_names, tag_catalog_payload
    from .tag_placement import tags_to_header, tags_to_meta
    from .tags import normalize_tag, parse_header
    from .transforms import (
        anonymize_dataset,
        fix_num_comparator_values,
        harmonize_comparator_thresholds,
        sample_dataset,
        split_comparator_columns,
        write_mapping_tables,
    )

    _RUNTIME_LOADED = True


def main() -> int:
    argv = list(sys.argv[1:])
    debug = "--debug" in argv
    if debug:
        argv = [item for item in argv if item != "--debug"]
    try:
        return _main(argv)
    except SystemExit as exc:
        if exc.code is None:
            return 0
        if isinstance(exc.code, int):
            return exc.code
        print(exc.code, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Error: interrupted by user.", file=sys.stderr)
        return 130
    except Exception as exc:
        if not _is_known_cli_error(exc):
            raise
        if debug:
            traceback.print_exc()
            return 1
        print(_format_cli_error(exc), file=sys.stderr)
        print("Re-run with --debug for a Python traceback.", file=sys.stderr)
        return 1


_KNOWN_CLI_ERRORS = (
    FileNotFoundError,
    PermissionError,
    BadZipFile,
    TameError,
    ImportError,
    KeyError,
    OSError,
    ValueError,
)

_KNOWN_CLI_ERROR_NAMES = {
    "ActionError",
    "CommandPipelineError",
    "EmbeddedFunctionError",
    "EmbeddedFunctionsDisabledError",
}


def _is_known_cli_error(exc: BaseException) -> bool:
    return isinstance(exc, _KNOWN_CLI_ERRORS) or exc.__class__.__name__ in _KNOWN_CLI_ERROR_NAMES


class _TameArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: {message}\nHelp: {self.prog} --help\n")


def _main(argv: list[str] | None = None) -> int:
    argv = list(argv) if argv is not None else list(sys.argv[1:])
    parser, command_parsers = _build_parser()

    if not argv:
        parser.print_help()
        return 0

    if argv[0] == "help":
        if len(argv) == 1:
            parser.print_help()
            return 0
        if len(argv) == 2 and argv[1] in {"-h", "--help"}:
            _print_command_help(command_parsers["help"], "help")
            return 0
        topic = argv[1]
        topic_parser = command_parsers.get(topic)
        if topic_parser is None:
            print(f"Error: unknown help topic: {topic}", file=sys.stderr)
            print("Examples: tametools help review, tametools help analyze", file=sys.stderr)
            return 2
        _print_command_help(topic_parser, topic)
        return 0

    if len(argv) >= 2 and argv[0] in command_parsers and any(item in {"-h", "--help"} for item in argv[1:]):
        _print_command_help(command_parsers[argv[0]], argv[0])
        return 0

    if argv[0] not in command_parsers and not argv[0].startswith("-"):
        print(f"Error: unknown command: {argv[0]}", file=sys.stderr)
        print("Run `tametools --help` to see available commands.", file=sys.stderr)
        print("If you are new, run `tametools quickstart` or `tametools examples`.", file=sys.stderr)
        return 2

    args = parser.parse_args(argv)
    args.command = _canonical_command(args.command)
    global _CURRENT_CLI_COMMAND_TEXT, _CURRENT_CLI_OPERATION, _CURRENT_CLI_SOURCE_PATHS, _CURRENT_CLI_LOG_LEVEL
    # 감사로그용 명령 문자열은 가독성 우선이다. shlex.join 은 POSIX 규칙으로 Windows 경로의
    # 백슬래시까지 따옴표 처리해(예: '\') 사람이 읽기 불편하고 OS별로 달라진다. 공백이 있는 인자만
    # 큰따옴표로 감싼다(공백 없는 경로는 그대로).
    _CURRENT_CLI_COMMAND_TEXT = "tametools " + " ".join(
        f'"{arg}"' if (" " in arg or "\t" in arg) else arg for arg in argv
    )
    _CURRENT_CLI_OPERATION = f"CLI:{str(args.command).upper()}"
    _CURRENT_CLI_SOURCE_PATHS = _command_source_paths(args)
    _CURRENT_CLI_LOG_LEVEL = _normalize_log_level(getattr(args, "log_level", "simple"))

    if args.command == "quickstart":
        _print_quickstart()
        return 0
    if args.command == "examples":
        _print_examples()
        return 0
    if args.command == "completion":
        _print_completion(args.shell)
        return 0
    if args.command == "doctor":
        return _run_doctor(strict=args.strict)

    _load_runtime()

    if args.command == "check":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        validation = validate_dataset(dataset)
        if args.json:
            _print_json(
                {
                    "dataset": _dataset_info_payload(dataset),
                    "columns": _frame_records(dataset.column_frame()),
                    "summary": _frame_records(describe_dataset(dataset)),
                    "validation": _validation_payload(validation, max_issues=args.max_issues),
                }
            )
            return 1 if args.fail_on_issues and validation.issues else 0
        _print_review_overview(dataset)
        _print_table("check:columns", dataset.column_frame(), view=args.view)
        _print_table("check:summary", describe_dataset(dataset), view=args.view)
        _print_review_validation_summary(validation, args.path)
        return 1 if args.fail_on_issues and validation.issues else 0

    return _run_runtime_command(args)


def _build_parser() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    parser = _TameArgumentParser(
        prog="tametools",
        description="Tag-first clinical data review, preprocessing, analysis, and export tools.",
        epilog=_top_level_help_epilog(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--debug", action="store_true", help="Show Python traceback for troubleshooting.")
    parser.add_argument("--version", action="version", version=_version_text())
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    command_parsers: dict[str, argparse.ArgumentParser] = {}

    def add_command(name: str, *, aliases: tuple[str, ...] = (), **kwargs) -> argparse.ArgumentParser:
        kwargs.setdefault("formatter_class", argparse.RawDescriptionHelpFormatter)
        sub = subparsers.add_parser(name, aliases=list(aliases), **kwargs)
        command_parsers[name] = sub
        for alias in aliases:
            command_parsers[alias] = sub
        return sub

    def add_input_arg(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("path", metavar="FILE", help="Input .tame, .xlsx, .xlsm file, or '-' for TAME text from stdin.")

    def add_meta_arg(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--meta", metavar="META", help="Optional external META .tame file.")

    def add_json_arg(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of human text.")

    def add_view_arg(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--view", choices=["compact", "full"], default="compact", help="Human table width. Default: compact.")

    def add_log_level_arg(sub: argparse.ArgumentParser) -> None:
        sub.add_argument(
            "--log-level",
            choices=["none", "simple", "detailed"],
            default="simple",
            help="Logging written to output datasets. Default: simple (CLI command only); detailed also records internal ACTION/WORK steps.",
        )

    help_parser = add_command(
        "help",
        help="Show help for tametools or one command.",
        description="Show help for tametools or one command.",
        epilog="Examples:\n  tametools help\n  tametools help review\n  tametools help analyze",
    )
    help_parser.add_argument("topic", nargs="?", help="Command name, e.g. review or analyze.")

    add_command(
        "quickstart",
        help="Print the recommended beginner workflow.",
        description="Print the shortest safe workflow for checking, fixing, validating, and saving clinical data.",
    )
    add_command(
        "examples",
        help="Print common command examples.",
        description="Print common tametools command examples for inspection, preprocessing, analysis, and export.",
    )
    completion_parser = add_command(
        "completion",
        help="Print a shell completion script.",
        description="Print a basic shell completion script for tametools commands and common options.",
        epilog=(
            "Examples:\n"
            "  tametools completion bash > ~/.local/share/bash-completion/completions/tametools\n"
            "  tametools completion zsh > ~/.zfunc/_tametools"
        ),
    )
    completion_parser.add_argument("shell", choices=["bash", "zsh"], help="Shell type.")
    doctor_parser = add_command(
        "doctor",
        help="Check local installation and optional extras.",
        description="Check Python version, installed dependencies, and optional report/web/statistics extras.",
    )
    doctor_parser.add_argument("--strict", action="store_true", help="Return exit code 1 when optional extras are missing.")

    simple_commands = {
        "info": "Show dataset size, META sections, and available works/actions.",
        "columns": "List columns with effective tags and metadata.",
        "states": "Summarize VALUE/NULL/EMPTY/WS cell states by column.",
        "describe": "Print a compact statistical and state summary.",
        "ri-plan": "Show columns that will be used for reference interval analysis.",
    }
    for name, help_text in simple_commands.items():
        sub = add_command(name, help=help_text, description=help_text)
        add_input_arg(sub)
        add_meta_arg(sub)
        add_json_arg(sub)
        add_view_arg(sub)

    tags_parser = add_command(
        "tags",
        help="List built-in and META custom tags.",
        description="List the tag catalog. Pass FILE to include META[TAG_DEFINITIONS] custom tags.",
    )
    tags_parser.add_argument("path", nargs="?", metavar="FILE", help="Optional dataset to include custom META tag definitions.")
    add_meta_arg(tags_parser)
    add_json_arg(tags_parser)

    check_parser = add_command(
        "check",
        help="Beginner shortcut: review structure and validation issues.",
        description="Review dataset structure and validation issues without changing data.",
        epilog=(
            "Examples:\n"
            "  tametools check data.tame\n"
            "  tametools check data.xlsx --meta meta.tame\n"
            "  tametools check data.tame --fail-on-issues"
        ),
    )
    add_input_arg(check_parser)
    add_meta_arg(check_parser)
    add_json_arg(check_parser)
    add_view_arg(check_parser)
    check_parser.add_argument("--max-issues", type=int, default=20, help="Maximum validation issues to include in JSON output.")
    check_parser.add_argument("--fail-on-issues", action="store_true", help="Return exit code 1 when validation issues are found.")

    review_parser = add_command(
        "review",
        aliases=("inspect",),
        help="Review tags and value profiles without changing data.",
        description="Review data state, tags, and tag-aware value profiles without changing data.",
        epilog=(
            "Target syntax:\n"
            "  FILE                 overview for the whole dataset\n"
            "  FILE COLUMN          detail for one column\n"
            "  FILE tag:TAG         detail for all columns with TAG\n"
            "  detail FILE COLUMN   same detail form, explicit mode\n\n"
            "Examples:\n"
            "  tametools review data.tame\n"
            "  tametools review data.tame tag:SEX\n"
            "  tametools review detail data.tame patient_age\n"
            "  tametools inspect data.tame tag:RESULT"
        ),
    )
    review_parser.add_argument("review_args", nargs="+", metavar="FILE", help="Review target. See examples below.")
    add_meta_arg(review_parser)
    add_json_arg(review_parser)
    add_view_arg(review_parser)
    review_parser.add_argument("--sex-code", action="append", default=[], metavar="CODE=VALUE", help="Confirmed SEX binary mapping, e.g. 1=male or 0=female.")
    review_parser.add_argument("--max-issues", type=int, default=20, help="Maximum validation issues to print per section.")
    review_parser.add_argument("--max-values", type=int, default=50, help="Maximum distinct values to print in detail mode.")

    fix_parser = add_command(
        "fix",
        help="Apply explicit safe fixes and write a new dataset.",
        description="Apply explicit safe fixes and write a new dataset. The input file is not modified.",
    )
    add_input_arg(fix_parser)
    add_meta_arg(fix_parser)
    fix_parser.add_argument("--output", required=True, metavar="OUTPUT", help="Output .tame, .xlsx file, or '-' for TAME text to stdout.")
    add_log_level_arg(fix_parser)
    fix_parser.add_argument("--standardize-sex", action="store_true", help="Convert recognized SEX values to male/female/other/unknown.")
    fix_parser.add_argument("--standardize-age", action="store_true", help="Convert recognized AGE values to storage form: years without suffix, months with mo, days with d.")
    fix_parser.add_argument("--fix-num-comparator", nargs="?", choices=["delete", "value"], const="delete", default=None, help="Fix inequality values in NUM columns. Default handling is delete; use value to strip the comparator.")
    fix_parser.add_argument("--all-safe", action="store_true", help="Apply all currently supported safe fixes.")
    fix_parser.add_argument("--sex-code", action="append", default=[], metavar="CODE=VALUE", help="Confirmed SEX binary mapping, e.g. 1=male or 0=female.")

    validate_parser = add_command(
        "validate",
        help="Strictly validate tagged values without changing data.",
        description="Strictly validate tagged values without changing or saving data.",
    )
    add_input_arg(validate_parser)
    add_meta_arg(validate_parser)
    add_json_arg(validate_parser)
    validate_parser.add_argument("--max-issues", type=int, default=20, help="Maximum validation issues to print.")
    validate_parser.add_argument("--fail-on-issues", action="store_true", help="Return exit code 1 when validation issues are found.")
    validate_parser.add_argument("--fail-on", choices=["none", "error", "warning", "info", "any"], default="none", help="Severity threshold for non-zero exit. Use with or without --fail-on-issues.")

    save_parser = add_command(
        "save",
        aliases=("convert",),
        help="Save or convert a dataset.",
        description="Save or convert a dataset using standard writer rules.",
        epilog="Examples:\n  tametools save data.tame data.xlsx\n  tametools convert data.xlsx converted.tame",
    )
    add_input_arg(save_parser)
    save_parser.add_argument("output", metavar="OUTPUT", help="Output .tame, .xlsx, .xlsm file, or '-' for TAME text to stdout.")
    add_meta_arg(save_parser)
    add_log_level_arg(save_parser)

    tags_to_meta_parser = add_command("tags-to-meta", help="Move DATA header tags into META[COLUMN].", description="Move effective DATA header tags into META[COLUMN] and write bare DATA headers.")
    add_input_arg(tags_to_meta_parser)
    add_meta_arg(tags_to_meta_parser)
    tags_to_meta_parser.add_argument("--output", required=True, metavar="OUTPUT", help="Output .tame/.xlsx file or '-' for TAME text to stdout.")
    add_log_level_arg(tags_to_meta_parser)

    tags_to_header_parser = add_command("tags-to-header", help="Move META column tags into DATA headers.", description="Move META column tags into DATA headers and remove matching META tag entries.")
    add_input_arg(tags_to_header_parser)
    add_meta_arg(tags_to_header_parser)
    tags_to_header_parser.add_argument("--output", required=True, metavar="OUTPUT", help="Output .tame/.xlsx file or '-' for TAME text to stdout.")
    add_log_level_arg(tags_to_header_parser)

    eda_parser = add_command("eda", help="Run built-in exploratory data analysis.", description="Run built-in exploratory data analysis tables.")
    add_input_arg(eda_parser)
    eda_parser.add_argument("--meta", help="Optional external META .tame file.")
    add_view_arg(eda_parser)
    eda_parser.add_argument("--comparator-policy", choices=["DELETE", "VALUE", "KEEP", "HARMONIZE"], default=None, help="How to treat numeric values with inequality comparators.")

    evaluate_parser = add_command("evaluate", help="Evaluate workflow effort and reproducibility metadata.", description="Evaluate a dataset and optional baseline workflow metrics.")
    add_input_arg(evaluate_parser)
    evaluate_parser.add_argument("--meta", help="Optional external META .tame file.")
    evaluate_parser.add_argument("--baseline", help="Optional baseline dataset.")
    evaluate_parser.add_argument("--baseline-meta", help="Optional external META for the baseline dataset.")
    evaluate_parser.add_argument("--work", help="META[WORKS] work to execute before evaluation.")
    evaluate_parser.add_argument("--allow-functions", action="store_true", help="Allow embedded Python/R functions declared in META.")
    evaluate_parser.add_argument("--baseline-steps", type=int, help="Manual baseline step count.")
    evaluate_parser.add_argument("--tame-steps", type=int, help="TAME workflow step count.")
    evaluate_parser.add_argument("--baseline-minutes", type=float, help="Manual baseline elapsed minutes.")
    evaluate_parser.add_argument("--tame-minutes", type=float, help="TAME workflow elapsed minutes.")
    evaluate_parser.add_argument("--output-dir", help="Directory for evaluation report files.")

    logs_parser = add_command(
        "logs",
        aliases=("log",),
        help="Show execution LOG entries stored in META.",
        description="Show TAME META.LOG entries recorded by tametools workflows and preprocessing actions.",
    )
    add_input_arg(logs_parser)
    logs_parser.add_argument("--meta", help="Optional external META .tame file.")
    logs_parser.add_argument("--json", action="store_true", help="Print full log entries as JSON.")
    logs_parser.add_argument("--limit", type=int, default=0, help="Show only the last N entries. Default: all.")
    logs_parser.add_argument("--reverse", action="store_true", help="Show newest entries first.")

    clear_log_parser = add_command(
        "clear-log",
        help="Write a copy of a dataset with META.LOG removed.",
        description="Remove execution LOG entries from META and write a new dataset. The input file is not modified.",
    )
    add_input_arg(clear_log_parser)
    clear_log_parser.add_argument("--meta", help="Optional external META .tame file.")
    clear_log_parser.add_argument("--output", required=True, help="Output .tame/.xlsx file or '-' for TAME text to stdout.")

    run_parser = add_command("run", help="Compatibility: execute a META[WORKS] work.", description="Compatibility command for executing a META[WORKS] work. Prefer analyze/preprocess for new routine use.")
    add_input_arg(run_parser)
    run_parser.add_argument("--meta", help="Optional external META .tame file.")
    run_parser.add_argument("work", nargs="?", default="DEFAULT", help="META[WORKS] name. Default: DEFAULT.")
    run_parser.add_argument("--output", help="Optional output file for the final dataset.")
    add_log_level_arg(run_parser)
    run_parser.add_argument("--allow-functions", action="store_true", help="Allow embedded Python/R functions declared in META.")
    run_parser.add_argument("--allow-plugins", action="store_true", help="Allow external Python plugin modules declared in META.")

    pipelines_parser = add_command("pipelines", help="List META[PIPELINES] command pipelines.", description="List executable META[PIPELINES] command pipelines.")
    add_input_arg(pipelines_parser)
    pipelines_parser.add_argument("--meta", help="Optional external META .tame file.")

    run_pipeline_parser = add_command("run-pipeline", help="Compatibility: execute a META[PIPELINES] command pipeline.", description="Compatibility command for executing a META[PIPELINES] command pipeline.")
    add_input_arg(run_pipeline_parser)
    run_pipeline_parser.add_argument("--meta", help="Optional external META .tame file.")
    run_pipeline_parser.add_argument("pipeline", nargs="?", metavar="PIPELINE", default="DEFAULT", help="META[PIPELINES] name. Default: DEFAULT.")
    run_pipeline_parser.add_argument("--output", help="Optional output file for the final dataset.")
    add_log_level_arg(run_pipeline_parser)
    run_pipeline_parser.add_argument("--allow-functions", action="store_true", help="Allow embedded Python/R functions declared in META.")
    run_pipeline_parser.add_argument("--allow-plugins", action="store_true", help="Allow external Python plugin modules declared in META.")

    plugins_parser = add_command("plugins", help="List available analysis plugins.", description="List built-in and META-enabled analysis plugins.")
    plugins_parser.add_argument("path", nargs="?", metavar="FILE", help="Optional input dataset for META-aware plugin discovery.")
    plugins_parser.add_argument("--meta", help="Optional external META .tame file.")
    plugins_parser.add_argument("--allow-plugins", action="store_true", help="Import external plugin modules declared in META.")

    run_plugin_parser = add_command(
        "run-plugin",
        aliases=("analyze",),
        help="Run an analysis plugin. Alias for new users: analyze.",
        description="Run an analysis plugin. Use 'tametools plugins FILE' to discover plugin names.",
        epilog=(
            "Examples:\n"
            "  tametools plugins data.tame\n"
            "  tametools analyze data.tame CLINICAL_STATS\n"
            "  tametools run-plugin data.tame REFERENCE_INTERVAL --option MIN_N=120"
        ),
    )
    add_input_arg(run_plugin_parser)
    run_plugin_parser.add_argument("--meta", help="Optional external META .tame file.")
    run_plugin_parser.add_argument("plugin", metavar="PLUGIN", help="Plugin name from 'tametools plugins FILE'.")
    run_plugin_parser.add_argument("--output", help="Optional output file when the plugin returns a transformed dataset.")
    add_log_level_arg(run_plugin_parser)
    run_plugin_parser.add_argument("--allow-plugins", action="store_true", help="Allow external Python plugin modules declared in META.")
    run_plugin_parser.add_argument(
        "--option",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Plugin option as KEY=VALUE (repeatable). Overrides META options, e.g. --option MODE=TAT_BY_TEST.",
    )

    check_excel_parser = add_command(
        "check-excel",
        help="Report values that Excel general-format would corrupt (leading-zero/precision/date).",
        description="Detect cells that spreadsheet general-format import would auto-coerce (data integrity check).",
    )
    add_input_arg(check_excel_parser)
    check_excel_parser.add_argument("--meta", help="Optional external META .tame file.")

    verify_parser = add_command(
        "verify",
        help="Run a META[WORKS] pipeline twice and report reproducibility (content-hash match).",
        description="Reproducibility check: run the named WORKS pipeline twice and compare output content hashes.",
    )
    add_input_arg(verify_parser)
    verify_parser.add_argument("--meta", help="Optional external META .tame file.")
    verify_parser.add_argument("--work", default=None, help="META[WORKS] name to run twice. Default: first available work.")
    verify_parser.add_argument("--allow-functions", action="store_true", help="Allow embedded Python/R functions declared in META.")
    verify_parser.add_argument("--allow-plugins", action="store_true", help="Allow external Python plugin modules declared in META.")

    stamp_parser = add_command(
        "stamp",
        help="Write a content-hash integrity stamp into META.INTEGRITY (tamper-evident).",
        description="Record a content hash so later modification of the data can be detected with check-integrity.",
    )
    add_input_arg(stamp_parser)
    stamp_parser.add_argument("--meta", help="Optional external META .tame file.")
    stamp_parser.add_argument("--output", help="Output file. Default: overwrite the input file.")

    check_integrity_parser = add_command(
        "check-integrity",
        help="Verify the data against its META.INTEGRITY content-hash stamp.",
        description="Detect modification by comparing the stored stamp to the recomputed content hash.",
    )
    add_input_arg(check_integrity_parser)
    check_integrity_parser.add_argument("--meta", help="Optional external META .tame file.")

    init_parser = add_command(
        "init",
        help="Auto-generate a starter .tame from any xlsx/csv/tsv (infers header tags).",
        description="Bootstrap for new users: read a plain spreadsheet and write a starter TAME with inferred header tags to refine.",
    )
    init_parser.add_argument("input", metavar="INPUT", help="Input .xlsx/.csv/.tsv table.")
    init_parser.add_argument("--output", required=True, help="Output .tame file.")
    init_parser.add_argument("--sheet", help="Worksheet name to use for a multi-sheet xlsx.")

    normalize_categories_parser = add_command(
        "normalize-categories",
        help="Normalize categorical columns to META[CATEGORIES] vocabularies.",
        description="Map institution-specific category spellings (Positive/+/양성 ...) to declared canonical values.",
    )
    add_input_arg(normalize_categories_parser)
    normalize_categories_parser.add_argument("--meta", help="Optional external META .tame file.")
    normalize_categories_parser.add_argument("--output", help="Output file. Default: overwrite the input file.")

    normalize_datetimes_parser = add_command(
        "normalize-datetimes",
        help="Normalize DATE/DATETIME/TIME columns to ISO 8601.",
        description="Parse mixed date/time spellings (and Excel serials) and rewrite DATE/DATETIME/TIME columns as ISO 8601.",
    )
    add_input_arg(normalize_datetimes_parser)
    normalize_datetimes_parser.add_argument("--meta", help="Optional external META .tame file.")
    normalize_datetimes_parser.add_argument("--output", help="Output file. Default: overwrite the input file.")

    actions_parser = add_command("actions", help="List executable META[ACTIONS].", description="List executable META[ACTIONS] preprocessing actions.")
    add_input_arg(actions_parser)
    actions_parser.add_argument("--meta", help="Optional external META .tame file.")

    run_action_parser = add_command("run-action", help="Execute one META[ACTIONS] action.", description="Execute one META[ACTIONS] preprocessing action.")
    add_input_arg(run_action_parser)
    run_action_parser.add_argument("--meta", help="Optional external META .tame file.")
    run_action_parser.add_argument("action", metavar="ACTION", help="Action name from 'tametools actions FILE'.")
    run_action_parser.add_argument("--output", help="Output file for transformed data.")
    add_log_level_arg(run_action_parser)

    action_pipelines_parser = add_command("action-pipelines", help="List META[ACTION_PIPELINES].", description="List executable META[ACTION_PIPELINES] preprocessing pipelines.")
    add_input_arg(action_pipelines_parser)
    action_pipelines_parser.add_argument("--meta", help="Optional external META .tame file.")

    run_action_pipeline_parser = add_command(
        "run-action-pipeline",
        aliases=("preprocess",),
        help="Run a preprocessing pipeline. Alias for new users: preprocess.",
        description="Run a META[ACTION_PIPELINES] preprocessing pipeline. Use 'tametools action-pipelines FILE' to discover names.",
        epilog="Examples:\n  tametools action-pipelines data.tame\n  tametools preprocess data.tame DEFAULT --output cleaned.tame",
    )
    add_input_arg(run_action_pipeline_parser)
    run_action_pipeline_parser.add_argument("--meta", help="Optional external META .tame file.")
    run_action_pipeline_parser.add_argument("pipeline", nargs="?", metavar="PIPELINE", default="DEFAULT", help="META[ACTION_PIPELINES] name. Default: DEFAULT.")
    run_action_pipeline_parser.add_argument("--output", help="Output file for transformed data.")
    add_log_level_arg(run_action_pipeline_parser)

    presets_parser = add_command("presets", help="List built-in metadata presets.", description="List built-in metadata presets.")
    presets_parser.add_argument("path", nargs="?", metavar="FILE", help="Reserved for future dataset-aware preset discovery.")
    presets_parser.add_argument("--meta", help="Reserved for future external META-aware preset discovery.")

    apply_preset_parser = add_command("apply-preset", help="Apply a built-in metadata preset.", description="Apply a built-in metadata preset and write a new dataset.")
    add_input_arg(apply_preset_parser)
    apply_preset_parser.add_argument("--meta", help="Optional external META .tame file.")
    apply_preset_parser.add_argument("preset", nargs="?", default="CLINICAL_CHEMISTRY", help="Preset name from 'tametools presets'. Default: CLINICAL_CHEMISTRY.")
    apply_preset_parser.add_argument("--output", required=True, help="Output .tame or .xlsx file.")
    add_log_level_arg(apply_preset_parser)

    anonymize_parser = add_command("anonymize", help="Hash/drop identifier columns by name or tag.", description="Hash or drop identifier columns by explicit column name or tag.")
    add_input_arg(anonymize_parser)
    anonymize_parser.add_argument("--meta", help="Optional external META .tame file.")
    anonymize_parser.add_argument("--output", help="Output file for anonymized data.")
    add_log_level_arg(anonymize_parser)
    anonymize_parser.add_argument("--hash-column", action="append", default=[], help="Column name to hash. Repeatable.")
    anonymize_parser.add_argument("--drop-column", action="append", default=[], help="Column name to drop. Repeatable.")
    anonymize_parser.add_argument("--hash-tag", action="append", default=["ID", "HOSPITAL_ID"], help="Tag to hash. Repeatable; defaults include ID and HOSPITAL_ID.")
    anonymize_parser.add_argument("--drop-tag", action="append", default=["NAME"], help="Tag to drop. Repeatable; default: NAME.")
    anonymize_parser.add_argument("--salt", default="", help="Optional hashing salt.")
    anonymize_parser.add_argument("--mapping-output-dir", help="Directory for mapping files.")

    sample_parser = add_command("sample", help="Sample rows from a dataset.", description="Sample rows from a dataset, optionally stratified by columns or tags.")
    add_input_arg(sample_parser)
    sample_parser.add_argument("--meta", help="Optional external META .tame file.")
    sample_parser.add_argument("--output", help="Output sampled dataset.")
    add_log_level_arg(sample_parser)
    sample_mode = sample_parser.add_mutually_exclusive_group(required=True)
    sample_mode.add_argument("--rows", type=int, help="Number of rows to sample.")
    sample_mode.add_argument("--frac", type=float, help="Fraction of rows to sample.")
    sample_parser.add_argument("--seed", type=int, help="Random seed.")
    sample_parser.add_argument("--by-column", action="append", default=[], help="Stratify by column name. Repeatable.")
    sample_parser.add_argument("--by-tag", action="append", default=[], help="Stratify by tag. Repeatable.")
    sample_parser.add_argument("--replace", action="store_true", help="Sample with replacement.")

    split_parser = add_command("split-comparator", help="Split numeric comparator values into value/comparator columns.", description="Split inequality comparator values such as <5 into separate numeric and comparator columns.")
    add_input_arg(split_parser)
    split_parser.add_argument("--meta", help="Optional external META .tame file.")
    split_parser.add_argument("--output", help="Output transformed dataset.")
    add_log_level_arg(split_parser)
    split_parser.add_argument("--column", action="append", default=[], help="Target column. Repeatable; defaults to tagged NUM columns.")
    split_parser.add_argument("--drop-original", action="store_true", help="Drop original comparator columns after splitting.")

    harmonize_parser = add_command("harmonize-comparator", help="Harmonize comparator thresholds.", description="Harmonize numeric comparator thresholds for analysis.")
    add_input_arg(harmonize_parser)
    harmonize_parser.add_argument("--meta", help="Optional external META .tame file.")
    harmonize_parser.add_argument("--output", help="Output transformed dataset.")
    add_log_level_arg(harmonize_parser)
    harmonize_parser.add_argument("--column", action="append", default=[], help="Target column. Repeatable; defaults to tagged NUM columns.")
    harmonize_parser.add_argument("--exact-handling", choices=["between", "all"], default="between", help="How exact values are represented in harmonized thresholds.")

    extract_images_parser = add_command("extract-images", help="Extract embedded/base64 image columns to files.", description="Extract image columns to files and optionally save a transformed dataset.")
    add_input_arg(extract_images_parser)
    extract_images_parser.add_argument("--meta", help="Optional external META .tame file.")
    extract_images_parser.add_argument("--output-dir", required=True, help="Directory for extracted image files.")
    extract_images_parser.add_argument("--output", help="Output transformed dataset.")
    add_log_level_arg(extract_images_parser)
    extract_images_parser.add_argument("--column", action="append", default=[], help="Target image column. Repeatable.")

    embed_images_parser = add_command("embed-images", help="Embed image file references into a dataset.", description="Embed image file references into DATA cells.")
    add_input_arg(embed_images_parser)
    embed_images_parser.add_argument("--meta", help="Optional external META .tame file.")
    embed_images_parser.add_argument("--output", required=True, help="Output transformed dataset.")
    add_log_level_arg(embed_images_parser)
    embed_images_parser.add_argument("--base-dir", help="Base directory for relative image paths.")
    embed_images_parser.add_argument("--column", action="append", default=[], help="Target image column. Repeatable.")

    merge_parser = add_command("merge", help="Merge multiple datasets.", description="Merge multiple datasets and write a combined output.")
    merge_parser.add_argument("inputs", nargs="+", help="Input .tame/.xlsx files.")
    merge_parser.add_argument("--output", required=True, help="Output merged dataset.")
    add_log_level_arg(merge_parser)
    merge_parser.add_argument("--label", action="append", default=[], help="Source label for an input dataset. Repeatable.")
    merge_parser.add_argument("--num-conflict", choices=["strict", "promote", "split", "harmonize"], default="promote", help="How to resolve numeric tag conflicts.")
    merge_parser.add_argument("--no-source-column", action="store_true", help="Do not add a source column.")

    export_parser = add_command("export", help="Export data and metadata to external formats.", description="Export data and metadata to CSV/TSV/JSONL/SQL/Parquet/Feather/R bundle formats.")
    add_input_arg(export_parser)
    export_parser.add_argument("--meta", help="Optional external META .tame file.")
    export_parser.add_argument("output", metavar="OUTPUT", help="Output file or directory.")
    export_parser.add_argument("--format", choices=_EXPORT_FORMATS, default=None, help="Output format. Inferred from suffix when omitted.")
    export_parser.add_argument("--bundle-data-format", choices=_BUNDLE_DATA_FORMATS, default="csv", help="Data format inside r_bundle output.")
    export_parser.add_argument("--no-schema", action="store_true", help="Do not write schema metadata in bundle outputs.")
    export_parser.add_argument("--no-job", action="store_true", help="Do not write job metadata in bundle outputs.")

    split_tame_parser = add_command("split-tame", help="Split a TAME file into separate META/DATA outputs.", description="Split a TAME file into separate META and/or DATA outputs.")
    split_tame_parser.add_argument("path", metavar="FILE", help="Input .tame file.")
    split_tame_parser.add_argument("--meta-output", help="Output file for META.")
    split_tame_parser.add_argument("--data-output", help="Output file for DATA.")
    add_log_level_arg(split_tame_parser)

    attach_meta_parser = add_command("attach-meta", help="Attach an external META file to data.", description="Attach an external META file to data and save one combined dataset.")
    attach_meta_parser.add_argument("path", metavar="FILE", help="Input data .tame/.xlsx file.")
    attach_meta_parser.add_argument("meta", metavar="META", help="External META .tame file.")
    attach_meta_parser.add_argument("--output", required=True, help="Output combined dataset.")
    add_log_level_arg(attach_meta_parser)

    import_xlsx_parser = add_command("import-xlsx", help="Import an xlsx workbook into a TAME template.", description="Import an xlsx workbook into a TAME template and save the result.")
    import_xlsx_parser.add_argument("template", help="Template .tame file.")
    import_xlsx_parser.add_argument("xlsx", help="Input .xlsx/.xlsm workbook.")
    import_xlsx_parser.add_argument("--output", required=True, help="Output imported dataset.")
    add_log_level_arg(import_xlsx_parser)

    return parser, command_parsers


def _print_command_help(parser: argparse.ArgumentParser, command_name: str) -> None:
    old_prog = parser.prog
    parser.prog = f"tametools {command_name}"
    try:
        parser.print_help()
    finally:
        parser.prog = old_prog


def _top_level_help_epilog() -> str:
    return """Recommended beginner workflow:
  tametools quickstart
  tametools check FILE
  tametools fix FILE --standardize-sex --standardize-age --output fixed.tame
  tametools validate fixed.tame --fail-on-issues
  tametools analyze fixed.tame PLUGIN
  tametools convert fixed.tame fixed.xlsx

Discovery:
  tametools help COMMAND
  tametools examples
  tametools plugins FILE
  tametools actions FILE
  tametools action-pipelines FILE
  tametools doctor

Command groups:
  First checks:       check, review/inspect, validate, info, columns, states, describe
  Fix/preprocess:    fix, preprocess, actions, run-action, action-pipelines
  Analyze/report:    plugins, analyze, eda, ri-plan, evaluate
  Convert/export:    convert/save, export, tags-to-meta, tags-to-header, merge
  Audit/logs:        logs/log, clear-log, verify
  Utilities:         anonymize, sample, split-comparator, harmonize-comparator,
                     extract-images, embed-images, split-tame, attach-meta, import-xlsx

Compatibility notes:
  Existing scripts can keep using review, save, run-plugin, run-action-pipeline,
  run-pipeline, and run. For new routine use, prefer inspect/check, convert,
  analyze, and preprocess where they express the user's intent more clearly."""


def _canonical_command(command: str) -> str:
    aliases = {
        "inspect": "review",
        "convert": "save",
        "analyze": "run-plugin",
        "preprocess": "run-action-pipeline",
        "log": "logs",
    }
    return aliases.get(command, command)


def _version_text() -> str:
    try:
        version = importlib_metadata.version("tametools")
    except importlib_metadata.PackageNotFoundError:
        version = "0.2.0"
    return f"tametools {version}"


def _print_quickstart() -> None:
    print(
        """tametools quickstart

1. 설치와 선택 기능 확인
   tametools doctor

2. 데이터 구조와 태그 검토
   tametools check data.tame
   tametools review data.tame tag:RESULT

3. 명시적으로 안전한 전처리만 적용
   tametools fix data.tame --standardize-sex --standardize-age --output fixed.tame

4. 검증 실패를 종료 코드로 고정
   tametools validate fixed.tame --fail-on-issues

5. 사용 가능한 분석을 확인하고 실행
   tametools plugins fixed.tame
   tametools analyze fixed.tame PLUGIN_NAME

6. 결과 저장 또는 외부 포맷 내보내기
   tametools convert fixed.tame fixed.xlsx
   tametools export fixed.tame out.csv
"""
    )


def _print_examples() -> None:
    print(
        """Common tametools examples

Inspect and validate:
  tametools check data.tame
  tametools review data.tame tag:AGE
  tametools validate data.tame --fail-on-issues

Fix and preprocess:
  tametools fix data.tame --all-safe --output fixed.tame
  tametools actions data.tame
  tametools preprocess data.tame DEFAULT --output cleaned.tame

Analyze:
  tametools plugins data.tame
  tametools analyze data.tame CLINICAL_STATS
  tametools analyze data.tame REFERENCE_INTERVAL --option MIN_N=120

Audit:
  tametools logs cleaned.tame
  tametools clear-log cleaned.tame --output cleaned.public.tame

Convert and export:
  tametools convert data.tame data.xlsx
  tametools export data.tame out.csv
  tametools export data.tame r_output --format r_bundle

Legacy command equivalents:
  tametools analyze FILE PLUGIN        = tametools run-plugin FILE PLUGIN
  tametools preprocess FILE PIPELINE   = tametools run-action-pipeline FILE PIPELINE
  tametools convert FILE OUTPUT        = tametools save FILE OUTPUT
  tametools inspect FILE               = tametools review FILE
"""
    )


def _run_doctor(*, strict: bool = False) -> int:
    required = [
        ("pandas", "pandas"),
        ("numpy", "numpy"),
        ("openpyxl", "openpyxl"),
        ("tomli/tomllib", "tomli"),
    ]
    optional = [
        ("report: matplotlib", "matplotlib"),
        ("report: python-docx", "docx"),
        ("web: fastapi", "fastapi"),
        ("web: uvicorn", "uvicorn"),
        ("stats: scipy", "scipy"),
        ("parquet: pyarrow", "pyarrow"),
    ]

    print("[doctor]")
    print(f"tametools: {_version_text().removeprefix('tametools ')}")
    print(f"python: {platform.python_version()} ({sys.executable})")
    print(f"platform: {platform.platform()}")

    print("[required]")
    required_ok = True
    for label, module in required:
        ok = _module_available(module) or (module == "tomli" and sys.version_info >= (3, 11))
        required_ok = required_ok and ok
        print(f"{label}: {'ok' if ok else 'missing'}")

    print("[optional]")
    optional_ok = True
    for label, module in optional:
        ok = _module_available(module)
        optional_ok = optional_ok and ok
        print(f"{label}: {'ok' if ok else 'missing'}")

    if not required_ok:
        print("status: required dependencies missing")
        return 1
    if strict and not optional_ok:
        print("status: optional extras missing")
        return 1
    print("status: ok")
    return 0


def _module_available(module: str) -> bool:
    try:
        return find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _run_runtime_command(args) -> int:
    if args.command == "info":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        payload = _dataset_info_payload(dataset)
        if args.json:
            _print_json(payload)
            return 0
        print(f"source: {dataset.source_path}")
        print(f"rows: {payload['rows']}")
        print(f"columns: {payload['columns']}")
        print(f"sections: {', '.join(payload['sections']) or 'META, DATA'}")
        print(f"works: {payload['works']}")
        print(f"pipelines: {payload['pipelines']}")
        print(f"actions: {payload['actions']}")
        print(f"action_pipelines: {payload['action_pipelines']}")
        return 0

    if args.command == "columns":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        frame = dataset.column_frame()
        if args.json:
            _print_json({"columns": _frame_records(frame)})
        else:
            _print_table("columns", frame, view=args.view)
        return 0

    if args.command == "states":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        frame = dataset.state_frame()
        if args.json:
            state_frame = frame.reset_index().rename(columns={"index": "row_index"})
            _print_json({"states": _frame_records(state_frame)})
        else:
            _print_table("states", frame, view=args.view)
        return 0

    if args.command == "tags":
        dataset = _load_dataset(args.path, meta_path=args.meta) if args.path else None
        catalog = tag_catalog_payload(dataset.meta if dataset is not None else None)
        if args.json:
            _print_json({"tags": catalog, "names": flat_tag_names(dataset.meta if dataset is not None else None)})
            return 0
        _print_tag_catalog(catalog)
        return 0

    if args.command == "review":
        review_mode, review_path, review_column = _parse_review_args(args.review_args)
        dataset = _load_dataset(review_path, meta_path=args.meta)
        sex_map = _parse_sex_map_arg(args.sex_code)
        if review_mode == "detail":
            if args.json:
                _print_json(
                    _review_detail_payload(
                        dataset,
                        review_column or "",
                        binary_map=sex_map,
                        max_issues=args.max_issues,
                        max_values=args.max_values,
                    )
                )
                return 0
            _print_review_detail(
                dataset,
                review_column or "",
                binary_map=sex_map,
                max_issues=args.max_issues,
                max_values=args.max_values,
            )
        else:
            validation = validate_dataset(dataset)
            if args.json:
                _print_json(_review_overview_payload(dataset, validation=validation, path=review_path, binary_map=sex_map))
                return 0
            _print_review_overview(dataset)
            print()
            _print_table("review:columns", dataset.column_frame(), view=args.view)
            print()
            _print_table("review:summary", describe_dataset(dataset), view=args.view)
            print()
            _print_sex_profile(sex_value_profile(dataset, binary_map=sex_map))
            print()
            _print_age_profile(age_value_profile(dataset))
            print()
            _print_category_profile(category_value_profile(dataset))
            datetime_profile = datetime_value_profile(dataset)
            if not datetime_profile.empty:
                print()
                _print_datetime_profile(datetime_profile)
            print()
            _print_review_validation_summary(validation, review_path)
        return 0

    if args.command == "fix":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        sex_map = _parse_sex_map_arg(args.sex_code)
        actions: list[str] = []
        if args.standardize_sex or args.all_safe:
            before = sex_value_profile(dataset, binary_map=sex_map)
            dataset = standardize_sex_dataset(dataset, binary_map=sex_map)
            changed_values = int(before.loc[before["will_change"], "raw_count"].sum()) if not before.empty else 0
            actions.append(f"standardize-sex changed_values={changed_values}")
        if args.standardize_age or args.all_safe:
            before = age_value_profile(dataset)
            dataset = standardize_age_dataset(dataset)
            changed_values = int(before.loc[before["will_change"], "raw_count"].sum()) if not before.empty else 0
            actions.append(f"standardize-age changed_values={changed_values}")
        if args.fix_num_comparator:
            before_rows = len(dataset.df)
            dataset, num_table = fix_num_comparator_values(dataset, handling=args.fix_num_comparator)
            changed_cells = int(num_table["affected_cells"].sum()) if not num_table.empty else 0
            deleted_rows = before_rows - len(dataset.df)
            actions.append(
                f"fix-num-comparator handling={args.fix_num_comparator} affected_cells={changed_cells} deleted_rows={deleted_rows}"
            )
        if not actions:
            print("error: fix requires --standardize-sex, --standardize-age, --fix-num-comparator, or --all-safe")
            return 1
        _save_dataset(dataset, args.output)
        for action in actions:
            print(f"fix: {action}")
        print(f"saved: {args.output}")
        validation = validate_dataset(dataset)
        print(f"issues_after_fix: {len(validation.issues)}")
        return 0

    if args.command == "validate":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        result = validate_dataset(dataset)
        if args.json:
            _print_json(_validation_payload(result, max_issues=args.max_issues))
        else:
            _print_validation_result(result, max_issues=args.max_issues)
        if args.fail_on_issues:
            return 1 if result.issues else 0
        return 1 if _issues_meet_severity(result.issues, args.fail_on) else 0

    if args.command == "save":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        _save_dataset(dataset, args.output)
        if _is_stdout_path(args.output):
            return 0
        print(f"saved: {args.output}")
        print(f"rows: {len(dataset.df)}")
        print(f"columns: {len(dataset.columns)}")
        return 0

    if args.command == "tags-to-meta":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        output = tags_to_meta(dataset)
        if output.table is not None:
            _print_table("tag-placement", output.table)
        _save_dataset(output.dataset or dataset, args.output, tag_storage="meta")
        if output.message:
            print(output.message)
        print(f"saved: {args.output}")
        return 0

    if args.command == "tags-to-header":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        output = tags_to_header(dataset)
        if output.table is not None:
            _print_table("tag-placement", output.table)
        for warning in output.warnings or []:
            print(f"warning: {warning}")
        _save_dataset(output.dataset or dataset, args.output, tag_storage="header")
        if output.message:
            print(output.message)
        print(f"saved: {args.output}")
        return 0

    if args.command == "describe":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        frame = describe_dataset(dataset)
        if args.json:
            _print_json({"summary": _frame_records(frame)})
        else:
            _print_table("summary", frame, view=args.view)
        return 0

    if args.command == "eda":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        report = exploratory_data_analysis(dataset, comparator_policy=args.comparator_policy)
        _print_table("summary", report.summary, view=args.view)
        _print_table("category_distribution", report.category_distribution, view=args.view)
        _print_table("numeric_percentiles", report.numeric_percentiles, view=args.view)
        _print_table("numeric_percentile_bands", report.numeric_percentile_bands, view=args.view)
        _print_table("result_by_summary", report.result_by_summary, view=args.view)
        _print_table("comparator_profile", report.comparator_profile, view=args.view)
        _print_table("comparator_policy_impact", report.comparator_policy_impact, view=args.view)
        _print_table("harmonization_preview", report.harmonization_preview, view=args.view)
        for warning in report.warnings:
            print(f"warning: {warning}")
        return 0

    if args.command == "evaluate":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        if args.work:
            try:
                dataset = execute_work(dataset, args.work, allow_functions=args.allow_functions).final_dataset
            except (EmbeddedFunctionError, EmbeddedFunctionsDisabledError, KeyError, PermissionError) as exc:
                print(f"error: {exc}")
                return 1
        baseline = _load_dataset(args.baseline, meta_path=args.baseline_meta) if args.baseline else None
        report = evaluate_dataset(
            dataset,
            baseline_dataset=baseline,
            baseline_steps=args.baseline_steps,
            tame_steps=args.tame_steps,
            baseline_minutes=args.baseline_minutes,
            tame_minutes=args.tame_minutes,
        )
        for name, table in report.tables().items():
            _print_table(name, table)
        for warning in report.warnings:
            print(f"warning: {warning}")
        if args.output_dir:
            for path in write_evaluation_report(report, args.output_dir):
                print(f"file: {path}")
            print(f"saved: {args.output_dir}")
        return 0

    if args.command == "logs":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        entries = _dataset_log_entries(dataset)
        if args.reverse:
            entries = list(reversed(entries))
        if args.limit and args.limit > 0:
            entries = entries[: args.limit] if args.reverse else entries[-args.limit:]
        if args.json:
            _print_json(entries)
        else:
            _print_table("logs", _log_entries_frame(entries))
            print(f"entries: {len(entries)}")
        return 0

    if args.command == "clear-log":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        before = len(_dataset_log_entries(dataset))
        cleared = _dataset_without_logs(dataset)
        _save_dataset(cleared, args.output, audit=False, source_paths=[args.path, args.meta])
        print(f"removed_log_entries: {before}")
        print(f"saved: {args.output}")
        return 0

    if args.command == "ri-plan":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        plan = reference_interval_plan(dataset)
        payload = {
            "result_columns": [column.name for column in plan.result_columns],
            "item_column": plan.item_column.name if plan.item_column else None,
            "age_column": plan.age_column.name if plan.age_column else None,
            "sex_column": plan.sex_column.name if plan.sex_column else None,
            "by_columns": [column.name for column in plan.by_columns],
        }
        if args.json:
            _print_json(payload)
            return 0
        print(f"result_columns: {payload['result_columns']}")
        print(f"item_column: {payload['item_column']}")
        print(f"age_column: {payload['age_column']}")
        print(f"sex_column: {payload['sex_column']}")
        print(f"by_columns: {payload['by_columns']}")
        return 0

    if args.command == "run":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        try:
            result = execute_work(
                dataset,
                args.work,
                allow_functions=args.allow_functions,
                allow_plugins=args.allow_plugins,
                log_level=args.log_level,
            )
        except (EmbeddedFunctionError, EmbeddedFunctionsDisabledError, PermissionError) as exc:
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
                    print(
                        f"severity={getattr(issue, 'severity', 'error')} row={issue.row_number} "
                        f"column={issue.column} tag={issue.tag} value={issue.value!r} message={issue.message}"
                    )
            if output.table is not None:
                print(output.table.to_string(index=False))
            if output.tables:
                for name, table in output.tables.items():
                    _print_table(name, table)
            _print_operation_assets(output)
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
            result = execute_command_pipeline(
                dataset,
                args.pipeline,
                allow_functions=args.allow_functions,
                allow_plugins=args.allow_plugins,
                log_level=args.log_level,
            )
        except (CommandPipelineError, EmbeddedFunctionError, EmbeddedFunctionsDisabledError, ImportError, PermissionError) as exc:
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
                    print(
                        f"severity={getattr(issue, 'severity', 'error')} row={issue.row_number} "
                        f"column={issue.column} tag={issue.tag} value={issue.value!r} message={issue.message}"
                    )
            if output.table is not None:
                print(output.table.to_string(index=False))
            if output.tables:
                for name, table in output.tables.items():
                    _print_table(name, table)
            _print_operation_assets(output)
        if args.output:
            _save_dataset(result.final_dataset, args.output)
            print(f"saved: {args.output}")
        print(f"final_rows: {len(result.final_dataset.df)}")
        return 0

    if args.command == "plugins":
        meta = _load_dataset(args.path, meta_path=args.meta).meta if args.path else None
        plugins = list_plugins(meta, allow_external=args.allow_plugins)
        for plugin in plugins:
            print(f"{plugin.name}: {plugin.description}")
        if args.allow_plugins:
            from .plugin_base.manager import plugin_directories, plugin_load_errors

            scanned = [str(directory) for directory in plugin_directories() if directory.is_dir()]
            if scanned:
                print("drop-in plugin directories: " + ", ".join(scanned))
            for error in plugin_load_errors():
                print(f"warning: drop-in plugin not loaded: {error}")
        if meta and not args.allow_plugins:
            _print_external_plugin_notice(meta)
        return 0

    if args.command == "run-plugin":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        options = ci_get(dataset.meta, args.plugin, {})
        if not isinstance(options, dict):
            options = {}
        options = dict(options)
        for item in getattr(args, "option", []) or []:
            if "=" not in item:
                raise ValueError(f"--option must be KEY=VALUE: {item}")
            key, value = item.split("=", 1)
            options[key.strip()] = value.strip()
        output = run_plugin(dataset, args.plugin, dataset.meta, args.plugin, options, allow_external=args.allow_plugins)
        if output is None:
            if configured_plugin_modules(dataset.meta) and not args.allow_plugins:
                print("error: external plugin modules are disabled. Re-run with --allow-plugins after trusting the file.")
                _print_external_plugin_notice(dataset.meta)
                return 1
            available = ", ".join(plugin.name for plugin in list_plugins(dataset.meta, allow_external=args.allow_plugins))
            print(f"error: unknown plugin: {args.plugin}")
            print(f"available_plugins: {available}")
            return 1
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
        _print_operation_assets(output)
        if output.dataset is not None and args.output:
            _save_dataset(_dataset_with_inherited_log(output.dataset, dataset), args.output)
            print(f"saved: {args.output}")
        return 0

    if args.command == "check-excel":
        from .excel_integrity import check_excel_roundtrip

        dataset = _load_dataset(args.path, meta_path=args.meta)
        report = check_excel_roundtrip(dataset)
        print(f"cells_checked: {report.total_cells}")
        print(f"altered_cells: {report.altered_cells} ({report.altered_rate * 100:.1f}%)")
        if report.by_category:
            for category, count in sorted(report.by_category.items()):
                examples = report.examples.get(category, [])
                hint = ", ".join(f"{original}->{coerced}" for original, coerced in examples[:2])
                print(f"  {category}: {count}  e.g. {hint}")
        else:
            print("no spreadsheet general-format corruption detected")
        return 0

    if args.command == "verify":
        from .audit import verify_reproducible

        # execute_work/available_works 는 모듈 전역(lazy import). 여기서 지역 import 하면
        # 같은 함수 앞쪽의 run 핸들러에서 UnboundLocalError 가 나므로 전역을 그대로 사용한다.
        dataset = _load_dataset(args.path, meta_path=args.meta)
        work = args.work
        if not work:
            works = available_works(dataset.meta)
            if not works:
                print("error: no META[WORKS] pipeline to verify. Use --work or add a WORKS pipeline.")
                return 1
            work = works[0]

        def _run_once():
            return execute_work(
                dataset, work, allow_functions=args.allow_functions, allow_plugins=args.allow_plugins
            ).final_dataset

        result = verify_reproducible(_run_once, runs=2)
        print(f"work: {work}")
        print(f"hashes: {', '.join(result['hashes'])}")
        print(f"reproducible: {str(result['reproducible']).lower()}")
        return 0 if result["reproducible"] else 1

    if args.command == "stamp":
        from .audit import stamp_integrity, dataset_content_hash

        dataset = _load_dataset(args.path, meta_path=args.meta)
        stamped = stamp_integrity(dataset)
        out = args.output or args.path
        _save_dataset(stamped, out)
        print(f"stamped: {out}")
        print(f"content_hash: {dataset_content_hash(stamped)}")
        return 0

    if args.command == "check-integrity":
        from .audit import check_integrity

        dataset = _load_dataset(args.path, meta_path=args.meta)
        report = check_integrity(dataset)
        if not report["present"]:
            print("no integrity stamp found (run 'tametools stamp' first)")
            return 2
        print(f"stored:     {report['stored']}")
        print(f"recomputed: {report['recomputed']}")
        print(f"intact: {str(report['intact']).lower()}")
        return 0 if report["intact"] else 1

    if args.command == "init":
        import sys as _sys
        from .bootstrap import init_from_table, list_sheets

        sheet = args.sheet
        sheets = list_sheets(args.input)
        if sheets and len(sheets) > 1 and not sheet:
            print(f"multiple sheets in {args.input}:")
            for i, name in enumerate(sheets, 1):
                print(f"  {i}. {name}")
            try:
                if not _sys.stdin.isatty():
                    raise EOFError
                choice = input("select sheet (number or name): ").strip()
            except EOFError:
                print("error: multiple sheets found; re-run with --sheet NAME")
                return 2
            if choice.isdigit() and 1 <= int(choice) <= len(sheets):
                sheet = sheets[int(choice) - 1]
            elif choice in sheets:
                sheet = choice
            else:
                print("error: invalid selection")
                return 2
        if sheet and sheets and sheet not in sheets:
            print(f"error: sheet '{sheet}' not found. available: {', '.join(sheets)}")
            return 2

        result = init_from_table(args.input, args.output, sheet=sheet)
        print(f"created: {args.output}")
        if result["sheet"]:
            print(f"source_sheet: {result['sheet']}")
        print("inferred (column -> [[tag]] : datatype) — review before use:")
        for name, tag, dtype in result["columns"]:
            print(f"  {name} -> [[{tag}]] : {dtype}")
        return 0

    if args.command == "normalize-categories":
        from .categories import normalize_categories

        dataset = _load_dataset(args.path, meta_path=args.meta)
        normalized, report = normalize_categories(dataset)
        out = args.output or args.path
        _save_dataset(normalized, out)
        print(f"saved: {out}")
        if report.empty:
            print("no category vocabularies declared in META[CATEGORIES].")
        else:
            print(report.to_string(index=False))
        return 0

    if args.command == "normalize-datetimes":
        from .datetimes import normalize_datetimes

        dataset = _load_dataset(args.path, meta_path=args.meta)
        normalized, report = normalize_datetimes(dataset)
        out = args.output or args.path
        _save_dataset(normalized, out)
        print(f"saved: {out}")
        if report.empty:
            print("no DATE/DATETIME/TIME columns found.")
        else:
            print(report.to_string(index=False))
        return 0

    if args.command == "actions":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        actions = available_actions(dataset.meta)
        if not actions:
            print("(no actions)")
            return 0
        for action in actions:
            print(f"[{action.name}]")
            print(f"label: {action.label}")
            print(f"type: {action.type}")
            print(f"mutates: {str(action.mutates).lower()}")
            if action.description:
                print(f"description: {action.description}")
        return 0

    if args.command == "run-action":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        try:
            output = execute_action(dataset, args.action)
        except ActionError as exc:
            print(f"error: {exc}")
            return 1
        print(f"action: {args.action}")
        if output.message:
            print(output.message)
        if output.warnings:
            for warning in output.warnings:
                print(f"warning: {warning}")
        if output.table is not None:
            print(output.table.to_string(index=False))
        if args.output:
            output_dataset = output.dataset or dataset
            if _normalize_log_level(args.log_level) == "detailed" and output.dataset is not None:
                output_dataset = _dataset_with_single_action_log(output_dataset, dataset, args.action, output)
            _save_dataset(output_dataset, args.output)
            print(f"saved: {args.output}")
        return 0

    if args.command == "action-pipelines":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        names = available_action_pipelines(dataset.meta)
        if not names:
            print("(no action pipelines)")
            return 0
        for name in names:
            actions = ci_get(ci_get(dataset.meta, "ACTION_PIPELINES", {}), name, [])
            if isinstance(actions, dict):
                actions = ci_get(actions, "ACTIONS", [])
            print(f"[{name}]")
            action_list = actions if isinstance(actions, list) else []
            for action in action_list:
                print(str(action))
        return 0

    if args.command == "run-action-pipeline":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        try:
            result = execute_action_pipeline(dataset, args.pipeline, log_level=args.log_level)
        except ActionError as exc:
            print(f"error: {exc}")
            return 1
        print(f"action_pipeline: {args.pipeline}")
        for output in result.outputs:
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
            _print_operation_assets(output)
        if args.output:
            _save_dataset(result.final_dataset, args.output)
            print(f"saved: {args.output}")
        print(f"final_rows: {len(result.final_dataset.df)}")
        return 0

    if args.command == "presets":
        for preset in preset_payloads():
            print(f"[{preset['name']}]")
            print(f"label: {preset['label']}")
            print(f"description: {preset['description']}")
        return 0

    if args.command == "apply-preset":
        dataset = _load_dataset(args.path, meta_path=args.meta)
        try:
            output = apply_preset(dataset, args.preset)
        except ValueError as exc:
            print(f"error: {exc}")
            return 1
        print(f"preset: {args.preset}")
        if output.message:
            print(output.message)
        if output.warnings:
            for warning in output.warnings:
                print(f"warning: {warning}")
        if output.table is not None:
            print(output.table.to_string(index=False))
        _save_dataset(output.dataset or dataset, args.output, tag_storage="meta")
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
        _save_dataset(result.dataset, args.output, source_paths=args.inputs)
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
        _save_dataset(dataset, args.output, source_paths=[args.path, args.meta])
        print(f"saved: {args.output}")
        print(f"rows: {len(dataset.df)}")
        print(f"columns: {len(dataset.columns)}")
        return 0

    if args.command == "import-xlsx":
        template = _load_dataset(args.template)
        dataset = import_xlsx_into_tame(template, args.xlsx)
        _save_dataset(dataset, args.output, source_paths=[args.template, args.xlsx])
        print(f"saved: {args.output}")
        print(f"rows: {len(dataset.df)}")
        print(f"columns: {len(dataset.columns)}")
        return 0

    return 1


def _print_json(payload) -> None:
    print(json.dumps(_json_ready(payload), ensure_ascii=False, indent=2))


def _json_ready(value):
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return None if value != value else value
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_ready(item) for item in value]
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except (TypeError, ValueError):
            pass
    if hasattr(value, "item"):
        try:
            return _json_ready(value.item())
        except (TypeError, ValueError):
            pass
    try:
        if value != value:
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def _frame_records(frame) -> list[dict]:
    if frame is None or frame.empty:
        return []
    return frame.to_dict(orient="records")


def _dataset_info_payload(dataset) -> dict:
    return {
        "source": dataset.source_path,
        "rows": len(dataset.df),
        "columns": len(dataset.columns),
        "sections": sorted(dataset.raw_sections) or ["META", "DATA"],
        "works": available_works(dataset.meta),
        "pipelines": available_command_pipelines(dataset.meta),
        "actions": [action.name for action in available_actions(dataset.meta)],
        "action_pipelines": available_action_pipelines(dataset.meta),
    }


def _validation_payload(result, *, max_issues: int = 20) -> dict:
    return _issues_payload(result.issues, max_issues=max_issues)


def _issues_payload(issues: list, *, max_issues: int = 20) -> dict:
    severity_counts: dict[str, int] = {}
    for issue in issues:
        severity = str(getattr(issue, "severity", "error")).lower() or "error"
        severity_counts[severity] = severity_counts.get(severity, 0) + 1
    return {
        "issues": len(issues),
        "severity_counts": severity_counts,
        "truncated": len(issues) > max_issues,
        "items": [
            {
                "row_number": issue.row_number,
                "column": issue.column,
                "tag": issue.tag,
                "value": issue.value,
                "message": issue.message,
                "severity": getattr(issue, "severity", "error"),
            }
            for issue in issues[:max_issues]
        ],
    }


def _review_overview_payload(dataset, *, validation, path: str, binary_map: dict[str, str]) -> dict:
    issue_columns = sorted({issue.column for issue in validation.issues})
    return {
        "dataset": _dataset_info_payload(dataset),
        "duplicate_columns": [
            {"original_name": original_name, "resolved_names": resolved_names}
            for original_name, resolved_names in _duplicate_column_rows(dataset)
        ],
        "columns": _frame_records(dataset.column_frame()),
        "summary": _frame_records(describe_dataset(dataset)),
        "profiles": {
            "sex": _frame_records(sex_value_profile(dataset, binary_map=binary_map)),
            "age": _frame_records(age_value_profile(dataset)),
            "category": _frame_records(category_value_profile(dataset)),
            "datetime": _frame_records(datetime_value_profile(dataset)),
        },
        "validation": {
            **_validation_payload(validation),
            "issue_tags": sorted({issue.tag for issue in validation.issues}),
            "issue_columns": issue_columns,
            "detail_commands": [f"tametools review detail {path} {column}" for column in issue_columns[:5]],
        },
    }


def _review_detail_payload(dataset, selector: str, *, binary_map: dict[str, str], max_issues: int, max_values: int) -> dict:
    columns, tag = _resolve_review_targets(dataset, selector)
    validation = validate_dataset(dataset)
    return {
        "target": selector,
        "tag": tag,
        "columns": [
            _review_column_detail_payload(
                dataset,
                column,
                validation=validation,
                binary_map=binary_map,
                max_issues=max_issues,
                max_values=max_values,
            )
            for column in columns
        ],
    }


def _review_column_detail_payload(dataset, column, *, validation, binary_map: dict[str, str], max_issues: int, max_values: int) -> dict:
    values = dataset.df[column.name]
    issues = [issue for issue in validation.issues if issue.column == column.name]
    payload = {
        "column": column.name,
        "index": _column_position(dataset, column),
        "tags": list(column.tags),
        "states": state_counts(values),
        "values": [
            {"value": raw_value, "count": count}
            for raw_value, count in _value_counts(values)[:max_values]
        ],
        "issues": _issues_payload(issues, max_issues=max_issues),
    }
    if dataset.column_has_tag(column, "SEX"):
        payload["sex_profile"] = _frame_records(sex_value_profile(dataset, binary_map=binary_map).loc[lambda frame: frame["column"] == column.name])
    if dataset.column_has_tag(column, "AGE"):
        payload["age_profile"] = _frame_records(age_value_profile(dataset).loc[lambda frame: frame["column"] == column.name])
    if dataset.column_has_tag(column, "CATEGORY"):
        payload["category_profile"] = _frame_records(category_value_profile(dataset).loc[lambda frame: frame["column"] == column.name])
    if dataset.column_has_tag(column, "DATETIME") or dataset.column_has_tag(column, "DATE"):
        payload["datetime_profile"] = _frame_records(datetime_value_profile(dataset).loc[lambda frame: frame["column"] == column.name])
    return payload


def _print_completion(shell: str) -> None:
    if shell == "bash":
        print(_bash_completion_script())
        return
    if shell == "zsh":
        print(_zsh_completion_script())
        return
    raise ValueError(f"Unsupported shell completion target: {shell}")


def _completion_commands() -> str:
    return " ".join(
        [
            "help",
            "quickstart",
            "examples",
            "completion",
            "doctor",
            "info",
            "columns",
            "states",
            "tags",
            "describe",
            "ri-plan",
            "check",
            "review",
            "inspect",
            "fix",
            "validate",
            "save",
            "convert",
            "tags-to-meta",
            "tags-to-header",
            "eda",
            "evaluate",
            "run",
            "pipelines",
            "run-pipeline",
            "plugins",
            "run-plugin",
            "analyze",
            "actions",
            "run-action",
            "action-pipelines",
            "run-action-pipeline",
            "preprocess",
            "presets",
            "apply-preset",
            "anonymize",
            "sample",
            "split-comparator",
            "harmonize-comparator",
            "extract-images",
            "embed-images",
            "merge",
            "export",
            "split-tame",
            "attach-meta",
            "import-xlsx",
        ]
    )


def _bash_completion_script() -> str:
    commands = _completion_commands()
    common = "-h --help --meta --json"
    return f"""# bash completion for tametools
_tametools_completion() {{
  local cur cmd opts
  COMPREPLY=()
  cur="${{COMP_WORDS[COMP_CWORD]}}"
  cmd="${{COMP_WORDS[1]}}"
  if [[ $COMP_CWORD -eq 1 ]]; then
    COMPREPLY=( $(compgen -W "{commands}" -- "$cur") )
    return 0
  fi
  case "$cmd" in
    check|review|inspect|validate|columns|states|tags|describe|ri-plan|info)
      opts="{common} --view --fail-on --fail-on-issues --sex-code --max-issues --max-values"
      ;;
    fix)
      opts="-h --help --meta --output --standardize-sex --standardize-age --fix-num-comparator --all-safe --sex-code"
      ;;
    analyze|run-plugin)
      opts="-h --help --meta --output --option --allow-plugins"
      ;;
    plugins)
      opts="-h --help --meta --allow-plugins"
      ;;
    run|run-pipeline)
      opts="-h --help --meta --output --allow-functions --allow-plugins"
      ;;
    preprocess|run-action-pipeline)
      opts="-h --help --meta --output"
      ;;
    export)
      opts="-h --help --meta --format --bundle-data-format --no-schema --no-job"
      ;;
    *)
      opts="-h --help"
      ;;
  esac
  COMPREPLY=( $(compgen -W "$opts" -- "$cur") )
}}
complete -F _tametools_completion tametools
"""


def _zsh_completion_script() -> str:
    commands = "\n    ".join(f"'{command}:tametools command'" for command in _completion_commands().split())
    return f"""#compdef tametools
_tametools() {{
  local -a commands
  commands=(
    {commands}
  )
  _arguments \\
    '1:command:->command' \\
    '*::arg:->args'
  case $state in
    command)
      _describe -t commands 'tametools command' commands
      ;;
    args)
      _arguments '*: :_files'
      ;;
  esac
}}
_tametools "$@"
"""


def _format_cli_error(exc: BaseException) -> str:
    if isinstance(exc, FileNotFoundError):
        path = getattr(exc, "filename", None) or str(exc)
        return f"Error: file not found: {path}"
    if isinstance(exc, PermissionError):
        path = getattr(exc, "filename", None) or str(exc)
        return f"Error: permission denied: {path}"
    if isinstance(exc, BadZipFile):
        return f"Error: the Excel file is corrupt or is not a valid xlsx/xlsm workbook. {exc}"
    if isinstance(exc, TameFormatError):
        return f"Error: invalid TAME file format. {exc}"
    if isinstance(exc, TameImportError):
        return f"Error: failed to import the external file into the TAME template. {exc}"
    if isinstance(exc, TameTagError):
        return f"Error: invalid tag configuration. {exc}"
    if isinstance(exc, TameValidationError):
        return f"Error: data validation failed. {exc}"
    if isinstance(exc, ActionError):
        return f"Error: preprocessing ACTION failed. {exc}"
    if isinstance(exc, CommandPipelineError):
        return f"Error: command pipeline failed. {exc}"
    if isinstance(exc, EmbeddedFunctionsDisabledError):
        return f"Error: embedded function execution is disabled. {exc}"
    if isinstance(exc, EmbeddedFunctionError):
        return f"Error: embedded function execution failed. {exc}"
    if isinstance(exc, ImportError):
        return f"Error: failed to import a required module. {exc}"
    if isinstance(exc, KeyError):
        return f"Error: required setting or column not found. {exc}"
    if isinstance(exc, ValueError):
        return f"Error: invalid input or configuration. {exc}"
    if isinstance(exc, OSError):
        return f"Error: file or system operation failed. {exc}"
    return f"Error: operation failed. {exc}"


def _load_dataset(path: str, *, meta_path: str | None = None):
    if _is_stdin_path(path):
        if _is_stdin_path(meta_path):
            raise ValueError("stdin '-' can be used for the data file or META file, not both.")
        text = sys.stdin.read()
        if not text.strip():
            raise ValueError("stdin '-' did not contain TAME text.")
        with tempfile.TemporaryDirectory(prefix="tametools_stdin_") as tmpdir:
            tmp_path = Path(tmpdir) / "stdin.tame"
            tmp_path.write_text(text, encoding="utf-8")
            dataset = read_tame(tmp_path, meta_path=meta_path)
        return dataset.replace(source_path="-")

    if _is_stdin_path(meta_path):
        text = sys.stdin.read()
        if not text.strip():
            raise ValueError("stdin '-' did not contain META TAME text.")
        with tempfile.TemporaryDirectory(prefix="tametools_meta_stdin_") as tmpdir:
            tmp_meta_path = Path(tmpdir) / "stdin.meta.tame"
            tmp_meta_path.write_text(text, encoding="utf-8")
            return _load_dataset(path, meta_path=str(tmp_meta_path))

    kind = _path_kind(path)
    if kind in {"tame", "meta_tame", "data_tame"}:
        return read_tame(path, meta_path=meta_path)
    if kind == "xlsx":
        return read_xlsx(path, meta_path=meta_path)
    raise ValueError(f"Unsupported input format: {path}")


def _dataset_log_entries(dataset) -> list[dict]:
    log = dataset.meta.get("LOG")
    if isinstance(log, list):
        return [dict(entry) for entry in log if isinstance(entry, dict)]
    if isinstance(log, dict):
        entries = log.get("ENTRIES", [])
        if isinstance(entries, list):
            return [dict(entry) for entry in entries if isinstance(entry, dict)]
    return []


def _dataset_without_logs(dataset):
    meta = dict(dataset.meta)
    meta.pop("LOG", None)
    raw_sections = dict(dataset.raw_sections)
    raw_sections["META"] = dumps_toml(meta) if meta else ""
    return dataset.replace(meta=meta, raw_sections=raw_sections)


def _dataset_with_inherited_log(dataset, parent_dataset):
    parent_entries = _dataset_log_entries(parent_dataset)
    if not parent_entries:
        return dataset
    entries = _dataset_log_entries(dataset)
    if entries[: len(parent_entries)] == parent_entries:
        return dataset
    meta = dict(dataset.meta)
    meta["LOG"] = [*parent_entries, *entries]
    raw_sections = dict(dataset.raw_sections)
    raw_sections["META"] = dumps_toml(meta)
    return dataset.replace(meta=meta, raw_sections=raw_sections)


def _dataset_with_single_action_log(dataset, input_dataset, action_name: str, output):
    from .audit import dataset_content_hash
    from .provenance import append_log_entry

    action_config = _action_config_for_log(input_dataset.meta, action_name)
    params = {
        "action": str(action_name),
        "type": str(ci_get(action_config, "TYPE", "")),
        "config": action_config,
        "input_hash": dataset_content_hash(input_dataset),
        "output_hash": dataset_content_hash(dataset),
        "issues": len(output.issues or []),
        "warnings": len(output.warnings or []),
        "tables": sorted((output.tables or {}).keys()),
    }
    return append_log_entry(
        dataset,
        action=f"ACTION:{action_name}",
        message=output.message or "",
        parameters=params,
        warnings=list(output.warnings or []),
    )


def _action_config_for_log(meta: dict | None, action_name: str) -> dict:
    actions = ci_get(meta, "ACTIONS", {})
    if not isinstance(actions, dict):
        return {}
    for name, config in actions.items():
        if str(name).lower() == str(action_name).lower() and isinstance(config, dict):
            return dict(config)
    return {}


def _log_entries_frame(entries: list[dict]):
    import pandas as pd

    columns = [
        "index",
        "timestamp",
        "operation",
        "tool",
        "parent",
        "output",
        "command",
        "notes",
        "step",
        "input_hash",
        "output_hash",
        "issues",
        "warnings",
    ]
    rows: list[dict[str, object]] = []
    for index, entry in enumerate(entries, start=1):
        params = entry.get("PARAMS", {})
        params = params if isinstance(params, dict) else {}
        warnings = entry.get("WARNINGS", [])
        warning_count = len(warnings) if isinstance(warnings, list) else (1 if warnings else 0)
        rows.append(
            {
                "index": index,
                "timestamp": entry.get("TIMESTAMP", entry.get("TIME", "")),
                "operation": entry.get("OPERATION", entry.get("ACTION", "")),
                "tool": entry.get("TOOL", entry.get("ACTOR", "")),
                "parent": entry.get("PARENT", ""),
                "output": entry.get("OUTPUT", ""),
                "command": params.get("command", ""),
                "notes": entry.get("NOTES", entry.get("MESSAGE", "")),
                "step": params.get("step", params.get("action", "")),
                "input_hash": params.get("input_hash", ""),
                "output_hash": params.get("output_hash", ""),
                "issues": params.get("issues", ""),
                "warnings": params.get("warnings", warning_count),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def _save_dataset(
    dataset,
    path: str,
    *,
    tag_storage: str = "preserve",
    audit: bool = True,
    source_paths=None,
) -> None:
    if _is_stdout_path(path):
        with tempfile.TemporaryDirectory(prefix="tametools_stdout_") as tmpdir:
            tmp_path = Path(tmpdir) / "stdout.tame"
            write_tame(tmp_path, dataset, tag_storage=tag_storage)
            sys.stdout.write(tmp_path.read_text(encoding="utf-8"))
        return

    kind = _path_kind(path)
    _assert_output_is_new_file(path, dataset, source_paths=source_paths)
    if audit and kind in {"tame", "xlsx"} and _CURRENT_CLI_LOG_LEVEL != "none":
        dataset = _dataset_with_cli_save_log(dataset, path, source_paths=source_paths)
    if kind == "meta_tame":
        write_meta_tame(path, dataset)
        return
    if kind == "data_tame":
        write_data_tame(path, dataset)
        return
    if kind == "tame":
        write_tame(path, dataset, tag_storage=tag_storage)
        return
    if kind == "xlsx":
        write_xlsx(path, dataset, tag_storage=tag_storage)
        return
    raise ValueError(f"Unsupported output format: {path}")


def _dataset_with_cli_save_log(dataset, path: str, *, source_paths=None):
    from .provenance import append_log_entry

    params = {
        "command": _CURRENT_CLI_COMMAND_TEXT,
    }
    parent = ""
    output_path = ""
    message = ""
    if _CURRENT_CLI_LOG_LEVEL == "detailed":
        from .audit import dataset_content_hash

        sources = _audit_source_paths(dataset, source_paths=source_paths)
        output_path = str(path)
        content_hash = dataset_content_hash(dataset)
        params.update(
            {
                "source_path": sources[0] if len(sources) == 1 else "",
                "source_paths": sources,
                "output_path": output_path,
                "content_hash": content_hash,
                "output_hash": content_hash,
                "meta_preserved": True,
                "meta_keys": sorted(str(key) for key in dataset.meta if str(key).upper() != "LOG"),
                "raw_sections": sorted(str(key) for key in dataset.raw_sections),
            }
        )
        parent = ", ".join(sources)
        message = f"created output={output_path}"
        if parent:
            message += f" from={parent}"
    return append_log_entry(
        dataset,
        action=_CURRENT_CLI_OPERATION or "CLI:SAVE",
        message=message,
        parent=parent,
        output=output_path,
        parameters=params,
    )


def _normalize_log_level(level: object) -> str:
    normalized = str(level or "simple").strip().lower()
    if normalized not in {"none", "simple", "detailed"}:
        raise ValueError("log level must be one of: none, simple, detailed")
    return normalized


def _assert_output_is_new_file(path: str, dataset, *, source_paths=None) -> None:
    if _is_stdout_path(path):
        return
    output_path = Path(path)
    for source in _audit_source_paths(dataset, source_paths=source_paths):
        if source == "-":
            continue
        try:
            if Path(source).resolve() == output_path.resolve():
                raise ValueError(
                    f"Output path must be different from input path to preserve the original file: {path}"
                )
        except OSError:
            continue


def _command_source_paths(args) -> list[str]:
    candidates: list[object] = []
    for name in ("path", "meta", "template", "xlsx", "baseline", "baseline_meta"):
        value = getattr(args, name, None)
        if value:
            candidates.append(value)
    for name in ("inputs", "paths"):
        values = getattr(args, name, None)
        if values:
            candidates.extend(values)
    return _dedupe_paths(candidates)


def _audit_source_paths(dataset, *, source_paths=None) -> list[str]:
    if source_paths is None:
        candidates = list(_CURRENT_CLI_SOURCE_PATHS)
    elif isinstance(source_paths, (str, Path)):
        candidates = [source_paths]
    else:
        candidates = list(source_paths)
    if getattr(dataset, "source_path", None):
        candidates.append(dataset.source_path)

    return _dedupe_paths(candidates)


def _dedupe_paths(paths) -> list[str]:
    sources: list[str] = []
    seen: set[str] = set()
    for candidate in paths:
        if candidate is None:
            continue
        source = str(candidate)
        if not source:
            continue
        key = source.lower()
        if key in seen:
            continue
        seen.add(key)
        sources.append(source)
    return sources


def _is_stdin_path(path: str | None) -> bool:
    return str(path) == "-"


def _is_stdout_path(path: str | None) -> bool:
    return str(path) == "-"


def _path_kind(path: str) -> str:
    if str(path) == "-":
        return "tame_stream"
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


def _issues_meet_severity(issues: list, threshold: str) -> bool:
    normalized = str(threshold or "none").lower()
    if normalized == "none":
        return False
    if normalized == "any":
        return bool(issues)
    ranks = {"info": 0, "warning": 1, "error": 2}
    minimum = ranks.get(normalized, 2)
    return any(ranks.get(str(getattr(issue, "severity", "error")).lower(), 2) >= minimum for issue in issues)


def _print_external_plugin_notice(meta: dict | None) -> None:
    modules = configured_plugin_modules(meta)
    if modules:
        print("external_plugins_disabled: " + ", ".join(modules))
        print("hint: re-run with --allow-plugins only for trusted local files")


def _print_tag_catalog(catalog: list[dict]) -> None:
    print("[tags]")
    for group in catalog:
        print(f"{group.get('name')}: {group.get('label', '')}")
        description = str(group.get("description") or "").strip()
        if description:
            print(f"  {description}")
        for child in group.get("children", []) or []:
            _print_tag_node(child, indent=2)


def _print_tag_node(node: dict, *, indent: int) -> None:
    prefix = " " * indent
    line = f"{prefix}{node.get('name')}: {node.get('label', '')}"
    description = str(node.get("description") or "").strip()
    if description:
        line += f" - {description}"
    print(line)
    for child in node.get("children", []) or []:
        _print_tag_node(child, indent=indent + 2)


def _print_table(name: str, table, *, view: str = "full") -> None:
    print(f"[{name}]")
    if table is None or table.empty:
        print("(empty)")
        return
    display = _compact_table(table, name=name) if view == "compact" else table
    print(_format_text_table(display))


def _format_text_table(table) -> str:
    columns = [str(column) for column in table.columns]
    rows = [[_format_table_value(value) for value in row] for row in table.itertuples(index=False, name=None)]
    widths = [
        max([_display_width(column), *(_display_width(row[index]) for row in rows)])
        for index, column in enumerate(columns)
    ]
    numeric_columns = [_is_numeric_column(table.iloc[:, index].tolist()) for index in range(len(columns))]
    lines = [_format_text_table_row(columns, widths, numeric_columns)]
    for row in rows:
        lines.append(_format_text_table_row(row, widths, numeric_columns))
    return "\n".join(lines)


def _format_text_table_row(values: list[str], widths: list[int], numeric_columns: list[bool]) -> str:
    cells: list[str] = []
    last_index = len(values) - 1
    for index, value in enumerate(values):
        if index == last_index:
            cells.append(value)
        else:
            cells.append(_pad_display(value, widths[index], align="right" if numeric_columns[index] else "left"))
    return "  ".join(cells)


def _format_table_value(value) -> str:
    if value is None:
        return ""
    try:
        if bool(value != value):
            return ""
    except Exception:
        pass
    return str(value).replace("\n", "\\n").expandtabs(4)


def _is_numeric_column(values: list) -> bool:
    non_missing = [value for value in values if _format_table_value(value) != ""]
    return bool(non_missing) and all(isinstance(value, Number) and not isinstance(value, bool) for value in non_missing)


def _pad_display(text: str, width: int, *, align: str) -> str:
    padding = max(width - _display_width(text), 0)
    if align == "right":
        return " " * padding + text
    return text + " " * padding


def _display_width(text: str) -> int:
    width = 0
    for char in str(text):
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    return width


def _compact_table(table, *, name: str):
    if table is None or table.empty:
        return table
    preferred_by_name = {
        "columns": ["index", "name", "tags", "effective_tags", "metadata"],
        "states": ["column", "VALUE", "NULL", "EMPTY", "WS", "ABSENT"],
        "summary": ["column", "tags", "kind", "value_cells", "count", "unique", "top", "mean", "median", "q2_5", "q97_5"],
        "check:columns": ["index", "name", "tags", "effective_tags", "metadata"],
        "review:columns": ["index", "name", "tags", "effective_tags", "metadata"],
        "check:summary": ["column", "tags", "kind", "value_cells", "count", "unique", "top", "mean", "median", "q2_5", "q97_5"],
        "review:summary": ["column", "tags", "kind", "value_cells", "count", "unique", "top", "mean", "median", "q2_5", "q97_5"],
        "category_distribution": ["column", "category", "count", "percent_of_column", "rank"],
        "numeric_percentiles": ["column", "stat", "value", "count", "unavailable_count"],
        "numeric_percentile_bands": ["column", "band", "count", "percent_of_column"],
        "result_by_summary": ["result_column", "by_column", "by_value", "item", "count", "mean", "median", "q2_5", "q97_5"],
        "comparator_profile": ["result_column", "item", "raw_value", "comparator_code", "numeric_value", "count"],
        "comparator_policy_impact": ["result_column", "policy", "strict_numeric_count", "numeric_used_under_policy", "invalid_count", "harmonized_rows"],
        "harmonization_preview": ["result_column", "item", "family", "existing_thresholds", "unified_value", "bounded_rows", "mixed_thresholds"],
    }
    columns = [column for column in preferred_by_name.get(name, preferred_by_name.get(name.split(":")[-1], [])) if column in table.columns]
    if not columns:
        columns = list(table.columns[: min(len(table.columns), 8)])
    return table.loc[:, columns]


def _print_operation_assets(output) -> None:
    if output.charts:
        print("[charts]")
        for chart in output.charts:
            name = str(chart.get("name") or chart.get("NAME") or "chart")
            chart_type = str(chart.get("TYPE") or chart.get("type") or "BAR")
            x = str(chart.get("X") or chart.get("x") or "")
            y = str(chart.get("Y") or chart.get("y") or "")
            table = str(chart.get("TABLE") or chart.get("table") or "")
            suffix = f" table={table}" if table else ""
            print(f"chart: {name} type={chart_type} x={x} y={y}{suffix}")
    if output.files:
        print("[files]")
        for path in output.files:
            print(f"file: {path}")


def _parse_sex_map_arg(values: list[str]) -> dict[str, str]:
    try:
        return parse_sex_binary_map(values)
    except ValueError as exc:
        raise SystemExit(f"error: {exc}") from exc


def _parse_review_args(values: list[str]) -> tuple[str, str, str | None]:
    if not values:
        raise SystemExit("error: review requires FILE or detail FILE COLUMN")
    if values[0] == "detail":
        if len(values) != 3:
            raise SystemExit("error: use tametools review detail FILE COLUMN")
        return "detail", values[1], values[2]
    if len(values) == 1:
        return "overview", values[0], None
    if len(values) == 2:
        return "detail", values[0], values[1]
    if len(values) == 3 and values[1] == "detail":
        return "detail", values[0], values[2]
    raise SystemExit("error: use tametools review FILE, tametools review FILE COLUMN, or tametools review detail FILE COLUMN")


def _print_validation_result(result, *, max_issues: int = 20) -> None:
    print("[validate]")
    print(f"issues: {len(result.issues)}")
    for issue in result.issues[:max_issues]:
        print(
            f"severity={getattr(issue, 'severity', 'error')} row={issue.row_number} "
            f"column={issue.column} tag={issue.tag} value={issue.value!r} message={issue.message}"
        )
    if len(result.issues) > max_issues:
        print(f"... {len(result.issues) - max_issues} more issue(s)")


def _print_review_overview(dataset) -> None:
    print("[review:dataset]")
    print(f"source: {dataset.source_path}")
    print(f"rows: {len(dataset.df)}")
    print(f"columns: {len(dataset.columns)}")
    print(f"sections: {', '.join(sorted(dataset.raw_sections)) or 'META, DATA'}")
    _print_duplicate_columns(dataset)


def _print_review_validation_summary(result, path: str) -> None:
    print("[review:validation-summary]")
    print(f"issues_detected: {len(result.issues)}")
    if result.issues:
        tags = sorted({issue.tag for issue in result.issues})
        columns = sorted({issue.column for issue in result.issues})
        print(f"issue_tags: {', '.join(tags)}")
        print(f"issue_columns: {', '.join(columns)}")
        for column in columns[:5]:
            print(f"detail_command: tametools review detail {path} {column}")


def _print_duplicate_columns(dataset) -> None:
    rows = _duplicate_column_rows(dataset)
    if not rows:
        return
    print("[review:duplicate-columns]")
    print(f"duplicates: {len(rows)}")
    for original_name, resolved_names in rows:
        print(f"original_name: {original_name} -> {', '.join(resolved_names)}")


def _duplicate_column_rows(dataset) -> list[tuple[str, list[str]]]:
    grouped: dict[str, list[str]] = {}
    for column in dataset.columns:
        original_name, _ = parse_header(column.original_header)
        grouped.setdefault(original_name, []).append(column.name)
    return [(name, names) for name, names in grouped.items() if len(names) > 1]


def _column_position(dataset, column) -> int:
    for index, candidate in enumerate(dataset.columns):
        if candidate.name == column.name:
            return index
    return -1


def _print_review_detail(dataset, selector: str, *, binary_map: dict[str, str], max_issues: int, max_values: int) -> None:
    columns, tag = _resolve_review_targets(dataset, selector)
    validation = validate_dataset(dataset)
    sex_profile = sex_value_profile(dataset, binary_map=binary_map) if any(dataset.column_has_tag(column, "SEX") for column in columns) else None
    age_profile = age_value_profile(dataset) if any(dataset.column_has_tag(column, "AGE") for column in columns) else None
    category_profile = category_value_profile(dataset) if any(dataset.column_has_tag(column, "CATEGORY") for column in columns) else None
    datetime_profile = (
        datetime_value_profile(dataset)
        if any(dataset.column_has_tag(column, "DATETIME") or dataset.column_has_tag(column, "DATE") for column in columns)
        else None
    )

    if tag is not None:
        print("[review:tag]")
        print(f"tag: {tag}")
        print(f"columns: {len(columns)}")
        print(f"column_names: {', '.join(column.name for column in columns)}")

    for index, column in enumerate(columns):
        if index:
            print()
        _print_review_column_detail(
            dataset,
            column,
            validation=validation,
            sex_profile=sex_profile,
            age_profile=age_profile,
            category_profile=category_profile,
            datetime_profile=datetime_profile,
            max_issues=max_issues,
            max_values=max_values,
        )


def _print_review_column_detail(dataset, column, *, validation, sex_profile, age_profile, category_profile, datetime_profile, max_issues: int, max_values: int) -> None:
    values = dataset.df[column.name]
    counts = state_counts(values)

    print("[review:detail]")
    print(f"column: {column.name}")
    print(f"index: {_column_position(dataset, column)}")
    print(f"tags: {'::'.join(column.tags) or 'untagged'}")

    print("[review:detail:states]")
    for state, count in counts.items():
        print(f"{state}: {count}")

    print("[review:detail:values]")
    raw_counts = _value_counts(values)
    if raw_counts:
        for raw_value, count in raw_counts[:max_values]:
            print(f"{raw_value}: {count}")
        if len(raw_counts) > max_values:
            print(f"... {len(raw_counts) - max_values} more value(s)")
    else:
        print("(no VALUE cells)")

    if dataset.column_has_tag(column, "SEX"):
        profile = sex_profile.loc[sex_profile["column"] == column.name].copy() if sex_profile is not None else None
        _print_sex_profile(profile)

    if dataset.column_has_tag(column, "AGE"):
        profile = age_profile.loc[age_profile["column"] == column.name].copy() if age_profile is not None else None
        _print_age_profile(profile)

    if dataset.column_has_tag(column, "CATEGORY"):
        profile = category_profile.loc[category_profile["column"] == column.name].copy() if category_profile is not None else None
        _print_category_profile(profile)

    if dataset.column_has_tag(column, "DATETIME") or dataset.column_has_tag(column, "DATE"):
        profile = datetime_profile.loc[datetime_profile["column"] == column.name].copy() if datetime_profile is not None else None
        _print_datetime_profile(profile)

    column_issues = [issue for issue in validation.issues if issue.column == column.name]
    print("[review:detail:issues]")
    print(f"issues: {len(column_issues)}")
    for issue in column_issues[:max_issues]:
        print(
            f"severity={getattr(issue, 'severity', 'error')} row={issue.row_number} "
            f"tag={issue.tag} value={issue.value!r} message={issue.message}"
        )
    if len(column_issues) > max_issues:
        print(f"... {len(column_issues) - max_issues} more issue(s)")


def _resolve_review_targets(dataset, value: str):
    if value.startswith("tag:"):
        tag = normalize_tag(value.removeprefix("tag:"))
        tagged = dataset.columns_with_tag(tag)
        if not tagged:
            names = ", ".join(column.name for column in dataset.columns)
            raise SystemExit(f"error: tag '{tag}' matches no columns. Available columns: {names}")
        return tagged, tag

    exact = [column for column in dataset.columns if column.name == value]
    if len(exact) == 1:
        return exact, None
    tagged = dataset.columns_with_tag(value)
    if tagged:
        return tagged, normalize_tag(value)
    names = ", ".join(column.name for column in dataset.columns)
    raise SystemExit(f"error: unknown review target '{value}'. Use a column name or tag:TAG. Available columns: {names}")


def _value_counts(values) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for value in values:
        if cell_state(value) != STATE_VALUE:
            continue
        raw_value = str(value).strip()
        counts[raw_value] = counts.get(raw_value, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def _print_sex_profile(profile) -> None:
    print("[review:SEX]")
    if profile is None or profile.empty:
        print("(no SEX columns)")
        return

    for column_name, column_frame in profile.groupby("column", sort=False, observed=False):
        print(f"column: {column_name}")
        for canonical in SEX_CANONICAL_VALUES:
            group = column_frame.loc[column_frame["canonical_value"] == canonical].copy()
            if group.empty:
                continue
            total = int(group["raw_count"].sum())
            raw_counts = _format_raw_counts(group)
            print(f"  {canonical}: {total} ({raw_counts})")

        unrecognized = column_frame.loc[~column_frame["recognized"]].copy()
        if not unrecognized.empty:
            print(f"  unrecognized: {_format_raw_counts(unrecognized)}")

        change_count = int(column_frame.loc[column_frame["will_change"], "raw_count"].sum())
        if change_count:
            print(f"  suggested_fix: tametools fix FILE --standardize-sex --output fixed.tame")


def _print_age_profile(profile) -> None:
    print("[review:AGE]")
    if profile is None or profile.empty:
        print("(no AGE columns)")
        return

    print(f"default_unit: {AGE_DEFAULT_UNIT} ({AGE_CANONICAL_UNITS[AGE_DEFAULT_UNIT]['label']}), stored without suffix")
    print("storage_units: year=no suffix, month=mo, day=d")
    print("accepted_units: " + ", ".join(f"{code} ({unit['label']})" for code, unit in AGE_CANONICAL_UNITS.items()))
    for column_name, column_frame in profile.groupby("column", sort=False, observed=False):
        print(f"column: {column_name}")
        band_label, band_column = _preferred_age_distribution(column_frame)
        print(f"  {band_label}: {_format_age_band_counts(column_frame, band_column)}")
        for unit_code, unit in AGE_CANONICAL_UNITS.items():
            group = column_frame.loc[column_frame["unit_code"] == unit_code].copy()
            if group.empty:
                continue
            total = int(group["raw_count"].sum())
            raw_counts = _format_age_raw_counts(group)
            print(f"  {unit['label']} ({unit_code}): {total} ({raw_counts})")

        unrecognized = column_frame.loc[~column_frame["recognized"]].copy()
        if not unrecognized.empty:
            print(f"  unrecognized: {_format_raw_counts(unrecognized)}")

        change_count = int(column_frame.loc[column_frame["will_change"], "raw_count"].sum())
        if change_count:
            print(f"  suggested_fix: tametools fix FILE --standardize-age --output fixed.tame")


def _format_raw_counts(frame) -> str:
    return ", ".join(f"{row.raw_value}: {int(row.raw_count)}" for row in frame.sort_values("raw_value").itertuples(index=False))


def _format_age_raw_counts(frame) -> str:
    parts: list[str] = []
    for row in frame.sort_values(["canonical_value", "raw_value"]).itertuples(index=False):
        if row.raw_value == row.canonical_value:
            parts.append(f"{row.raw_value}: {int(row.raw_count)}")
        else:
            parts.append(f"{row.raw_value} -> {row.canonical_value}: {int(row.raw_count)}")
    return ", ".join(parts)


def _preferred_age_distribution(frame) -> tuple[str, str]:
    if "preferred_band" not in frame.columns or "preferred_band_width" not in frame.columns:
        return "distribution_10y", "band_10"

    widths = frame.loc[frame["recognized"] & frame["preferred_band_width"].notna(), "preferred_band_width"].drop_duplicates()
    if widths.empty:
        return "distribution_10y", "band_10"

    try:
        width = int(widths.iloc[0])
    except (TypeError, ValueError):
        return "distribution_10y", "band_10"
    if width <= 0:
        return "distribution_10y", "band_10"
    return f"distribution_{width}y", "preferred_band"


def _format_age_band_counts(frame, column_name: str) -> str:
    recognized = frame.loc[frame["recognized"] & frame[column_name].notna()].copy()
    if recognized.empty:
        return "(none)"
    totals = recognized.groupby(column_name, sort=False, observed=False)["raw_count"].sum()
    return ", ".join(f"{band}: {int(count)}" for band, count in totals.items())


def _print_category_profile(profile) -> None:
    print("[review:CATEGORY]")
    if profile is None or profile.empty:
        print("(no CATEGORY columns)")
        return

    for column_name, column_frame in profile.groupby("column", sort=False, observed=False):
        total = int(column_frame["raw_count"].sum())
        unique = int(column_frame.shape[0])
        print(f"column: {column_name}")
        print(f"  value_cells: {total}")
        print(f"  categories: {unique}")
        print(f"  counts: {_format_raw_counts(column_frame)}")


def _print_datetime_profile(profile) -> None:
    print("[review:DATETIME]")
    if profile is None or profile.empty:
        print("(no DATE/DATETIME columns)")
        return

    for column_name, column_frame in profile.groupby("column", sort=False, observed=False):
        recognized = column_frame.loc[column_frame["recognized"]].copy()
        unrecognized = column_frame.loc[~column_frame["recognized"]].copy()
        parseable_count = int(recognized["raw_count"].sum()) if not recognized.empty else 0
        unparseable_count = int(unrecognized["raw_count"].sum()) if not unrecognized.empty else 0
        total = parseable_count + unparseable_count
        print(f"column: {column_name}")
        print(f"  value_cells: {total}")
        print(f"  parseable: {parseable_count}")
        print(f"  unparseable: {unparseable_count}")
        if not recognized.empty:
            parsed = recognized["parsed_value"].dropna().sort_values()
            if not parsed.empty:
                print(f"  min: {parsed.iloc[0]}")
                print(f"  max: {parsed.iloc[-1]}")
        if not unrecognized.empty:
            print(f"  unrecognized: {_format_raw_counts(unrecognized)}")

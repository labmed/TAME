from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

import openpyxl

from tametools.cli import main as cli_main


BASE_TAME = textwrap.dedent(
    """
    <META>
    [SETTINGS]
    VALIDATE_ERROR = "REPORT"

    [WORKS]
    DEFAULT = ["DESCRIBE"]
    EDA_WORK = ["EDA"]
    RI_WORK = ["REFERENCE_INTERVAL"]

    [PIPELINES]
    DEFAULT = ["describe"]
    SAMPLE_DESCRIBE = ["sample --rows 2 --seed 7", "describe"]
    PLUGIN_COUNTS = ["run-plugin CHEMISTRY_ANALYSIS"]

    [CHEMISTRY_ANALYSIS]
    MODE = "ITEM_COUNTS"

    [ACTIONS.KEEP_A]
    TYPE = "ROW.INCLUDE"
    COLUMN = "group"
    VALUE = "A"

    [ACTIONS.ADD_BATCH]
    TYPE = "ADD_COLUMN"
    NAME = "batch"
    VALUE = "one"

    [ACTION_PIPELINES]
    DEFAULT = ["KEEP_A", "ADD_BATCH"]
    </META>
    <DATA>
    [[ID(patient)::STR]]patient_id\t[[ID(sample)::STR]]sample_id\t[[SEX]]sex\t[[AGE]]age\t[[ITEM]]item\t[[RESULT::NUM]]result\t[[REF_LOW::NUM]]ref_low\t[[REF_HIGH::NUM]]ref_high\t[[GROUP]]group\t[[RESULT_TIME::DATETIME]]result_time\t[[COLLECTION_AT::DATETIME]]collection_at\t[[RECEIVED_AT::DATETIME]]received_at\t[[INSTRUMENT]]instrument
    P1\tS1\tmale\t30\tAST\t25\t0\t40\tA\t2026-01-01 10:00\t2026-01-01 08:00\t2026-01-01 09:00\tA1
    P2\tS2\tfemale\t40\tAST\t45\t0\t40\tB\t2026-01-02 10:00\t2026-01-02 08:00\t2026-01-02 09:00\tA2
    P3\tS3\tmale\t50\tALT\t20\t0\t35\tA\t2026-01-03 10:00\t2026-01-03 08:00\t2026-01-03 09:00\tA1
    </DATA>
    """
).strip()

INVALID_TAME = textwrap.dedent(
    """
    <META>
    [SETTINGS]
    VALIDATE_ERROR = "REPORT"
    </META>
    <DATA>
    [[AGE]]age\t[[RESULT::NUM]]result
    bad\tpositive
    </DATA>
    """
).strip()

COMPARATOR_TAME = textwrap.dedent(
    """
    <META></META>
    <DATA>
    [[ITEM]]item\t[[RESULT::NUM]]result
    AST\t<3
    AST\t5
    ALT\t>10
    </DATA>
    """
).strip()

IMAGE_DATA_URL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7Z6gAAAABJRU5ErkJggg=="
IMAGE_TAME = textwrap.dedent(
    f"""
    <META></META>
    <DATA>
    [[ID::STR]]id\t[[IMAGE::B64]]image\t[[LABEL::STR]]label
    P001\t{IMAGE_DATA_URL}\tok
    </DATA>
    """
).strip()

TEMPLATE_TAME = textwrap.dedent(
    """
    <META>
    [COLUMN.id]
    TAGS = ["ID", "STR"]
    [COLUMN.result]
    TAGS = ["RESULT", "NUM"]
    </META>
    <DATA>
    id\tresult
    A\t1
    </DATA>
    """
).strip()

COMMANDS = [
    "help",
    "quickstart",
    "examples",
    "completion",
    "doctor",
    "info",
    "columns",
    "states",
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
    "logs",
    "clear-log",
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


class CliMatrixBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.base = self.root / "base.tame"
        self.invalid = self.root / "invalid.tame"
        self.comparator = self.root / "comparator.tame"
        self.image = self.root / "image.tame"
        self.template = self.root / "template.tame"
        self.meta_only = self.root / "sidecar.meta.tame"
        self.import_xlsx = self.root / "import.xlsx"
        self.base.write_text(BASE_TAME, encoding="utf-8")
        self.invalid.write_text(INVALID_TAME, encoding="utf-8")
        self.comparator.write_text(COMPARATOR_TAME, encoding="utf-8")
        self.image.write_text(IMAGE_TAME, encoding="utf-8")
        self.template.write_text(TEMPLATE_TAME, encoding="utf-8")
        self.meta_only.write_text(
            "<META>\n[COLUMN.id]\nTAGS = [\"ID\", \"STR\"]\n[COLUMN.result]\nTAGS = [\"RESULT\", \"NUM\"]\n</META>\n",
            encoding="utf-8",
        )
        self._write_import_workbook()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_import_workbook(self) -> None:
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = "DATA"
        sheet.append(["id", "result"])
        sheet.append(["B", 2])
        workbook.save(self.import_xlsx)

    def run_cli(self, *args: str, stdin_text: str | None = None) -> tuple[int, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        stdin = io.StringIO(stdin_text or "")
        with (
            patch.object(sys, "argv", ["tametools", *map(str, args)]),
            patch.object(sys, "stdin", stdin),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = cli_main()
        return code, stdout.getvalue() + stderr.getvalue()

    def assert_ok_contains(self, args: list[str], expected: str, *, stdin_text: str | None = None) -> str:
        code, output = self.run_cli(*args, stdin_text=stdin_text)
        self.assertEqual(code, 0, output)
        self.assertIn(expected, output)
        self.assertNotIn("Traceback", output)
        return output


class CliHelpMatrixTest(CliMatrixBase):
    pass


def _make_help_topic_test(command: str):
    def test(self: CliHelpMatrixTest) -> None:
        output = self.assert_ok_contains(["help", command], "usage: tametools")
        self.assertIn(command.split()[0], output)

    return test


def _make_direct_help_test(command: str):
    def test(self: CliHelpMatrixTest) -> None:
        output = self.assert_ok_contains([command, "--help"], "usage: tametools")
        self.assertIn(command, output)

    return test


for _command in COMMANDS:
    setattr(CliHelpMatrixTest, f"test_help_topic_{_command.replace('-', '_')}", _make_help_topic_test(_command))
    setattr(CliHelpMatrixTest, f"test_direct_help_{_command.replace('-', '_')}", _make_direct_help_test(_command))


class CliExecutionMatrixTest(CliMatrixBase):
    pass


def _case(name: str, args_fn, expected: str, *, code: int = 0, stdin_fn=None, output_files_fn=None, json_output: bool = False):
    def test(self: CliExecutionMatrixTest) -> None:
        stdin_text = stdin_fn(self) if stdin_fn else None
        actual_code, output = self.run_cli(*args_fn(self), stdin_text=stdin_text)
        self.assertEqual(actual_code, code, output)
        self.assertIn(expected, output)
        self.assertNotIn("Traceback", output)
        if json_output:
            json.loads(output)
        if output_files_fn:
            for path in output_files_fn(self):
                self.assertTrue(Path(path).exists(), f"expected output file missing: {path}")

    test.__name__ = f"test_exec_{name}"
    return test


EXECUTION_CASES = [
    ("root_help", lambda s: [], "Recommended beginner workflow:"),
    ("version", lambda s: ["--version"], "tametools"),
    ("unknown_command", lambda s: ["does-not-exist"], "Error: unknown command", 2),
    ("unknown_help_topic", lambda s: ["help", "does-not-exist"], "Error: unknown help topic", 2),
    ("quickstart", lambda s: ["quickstart"], "tametools quickstart"),
    ("examples", lambda s: ["examples"], "Common tametools examples"),
    ("completion_bash", lambda s: ["completion", "bash"], "complete -F _tametools_completion tametools"),
    ("completion_zsh", lambda s: ["completion", "zsh"], "#compdef tametools"),
    ("doctor", lambda s: ["doctor"], "status: ok"),
    ("info_text", lambda s: ["info", str(s.base)], "rows: 3"),
    ("info_json", lambda s: ["info", str(s.base), "--json"], '"rows": 3', 0, None, None, True),
    ("info_stdin_json", lambda s: ["info", "-", "--json"], '"source": "-"', 0, lambda s: BASE_TAME, None, True),
    ("columns_text", lambda s: ["columns", str(s.base)], "patient_id"),
    ("columns_json", lambda s: ["columns", str(s.base), "--json"], '"columns"', 0, None, None, True),
    ("states_text", lambda s: ["states", str(s.base)], "VALUE"),
    ("states_json", lambda s: ["states", str(s.base), "--json"], '"states"', 0, None, None, True),
    ("describe_text", lambda s: ["describe", str(s.base)], "column"),
    ("describe_json", lambda s: ["describe", str(s.base), "--json"], '"summary"', 0, None, None, True),
    ("ri_plan_text", lambda s: ["ri-plan", str(s.base)], "result_columns:"),
    ("ri_plan_json", lambda s: ["ri-plan", str(s.base), "--json"], '"result_columns"', 0, None, None, True),
    ("check_text", lambda s: ["check", str(s.base)], "[check:columns]"),
    ("check_json", lambda s: ["check", str(s.base), "--json"], '"validation"', 0, None, None, True),
    ("check_fail_on_issues", lambda s: ["check", str(s.invalid), "--fail-on-issues"], "issues_detected:", 1),
    ("review_overview", lambda s: ["review", str(s.base)], "[review:dataset]"),
    ("review_overview_json", lambda s: ["review", str(s.base), "--json"], '"profiles"', 0, None, None, True),
    ("review_detail_column", lambda s: ["review", "detail", str(s.base), "age"], "[review:detail]"),
    ("review_detail_tag", lambda s: ["review", str(s.base), "tag:SEX"], "[review:tag]"),
    ("review_detail_json", lambda s: ["review", str(s.base), "tag:RESULT", "--json"], '"columns"', 0, None, None, True),
    ("inspect_alias", lambda s: ["inspect", str(s.base), "tag:AGE"], "[review:tag]"),
    ("validate_clean_text", lambda s: ["validate", str(s.base)], "issues: 0"),
    ("validate_clean_json", lambda s: ["validate", str(s.base), "--json"], '"issues": 0', 0, None, None, True),
    ("validate_invalid_fail", lambda s: ["validate", str(s.invalid), "--fail-on-issues"], "issues: 2", 1),
    ("fix_standardize_sex", lambda s: ["fix", str(s.base), "--standardize-sex", "--output", str(s.root / "fix_sex.tame")], "saved:", 0, None, lambda s: [s.root / "fix_sex.tame"]),
    ("fix_standardize_age", lambda s: ["fix", str(s.base), "--standardize-age", "--output", str(s.root / "fix_age.tame")], "saved:", 0, None, lambda s: [s.root / "fix_age.tame"]),
    ("fix_all_safe", lambda s: ["fix", str(s.base), "--all-safe", "--output", str(s.root / "fix_all.tame")], "issues_after_fix:", 0, None, lambda s: [s.root / "fix_all.tame"]),
    ("fix_comparator_delete", lambda s: ["fix", str(s.comparator), "--fix-num-comparator", "--output", str(s.root / "fix_cmp.tame")], "fix-num-comparator", 0, None, lambda s: [s.root / "fix_cmp.tame"]),
    ("save_tame", lambda s: ["save", str(s.base), str(s.root / "saved.tame")], "saved:", 0, None, lambda s: [s.root / "saved.tame"]),
    ("convert_alias_xlsx", lambda s: ["convert", str(s.base), str(s.root / "saved.xlsx")], "saved:", 0, None, lambda s: [s.root / "saved.xlsx"]),
    ("convert_stdout", lambda s: ["convert", str(s.base), "-"], "<DATA>"),
    ("tags_to_meta", lambda s: ["tags-to-meta", str(s.base), "--output", str(s.root / "meta_tags.tame")], "saved:", 0, None, lambda s: [s.root / "meta_tags.tame"]),
    ("tags_to_header", lambda s: ["tags-to-header", str(s.base), "--output", str(s.root / "header_tags.tame")], "saved:", 0, None, lambda s: [s.root / "header_tags.tame"]),
    ("eda_default", lambda s: ["eda", str(s.base)], "[summary]"),
    ("eda_comparator_delete", lambda s: ["eda", str(s.comparator), "--comparator-policy", "DELETE"], "[comparator_policy_impact]"),
    ("evaluate_basic", lambda s: ["evaluate", str(s.base), "--baseline-steps", "5", "--tame-steps", "2"], "[workflow_comparison]"),
    ("evaluate_output_dir", lambda s: ["evaluate", str(s.base), "--output-dir", str(s.root / "eval")], "saved:", 0, None, lambda s: [s.root / "eval"]),
    ("logs_empty", lambda s: ["logs", str(s.base)], "entries: 0"),
    ("clear_log_empty", lambda s: ["clear-log", str(s.base), "--output", str(s.root / "public.tame")], "removed_log_entries: 0", 0, None, lambda s: [s.root / "public.tame"]),
    ("run_default", lambda s: ["run", str(s.base)], "work: DEFAULT"),
    ("run_named_work", lambda s: ["run", str(s.base), "RI_WORK"], "work: RI_WORK"),
    ("run_output", lambda s: ["run", str(s.base), "DEFAULT", "--output", str(s.root / "run_out.tame")], "saved:", 0, None, lambda s: [s.root / "run_out.tame"]),
    ("pipelines", lambda s: ["pipelines", str(s.base)], "[DEFAULT]"),
    ("run_pipeline_default", lambda s: ["run-pipeline", str(s.base)], "pipeline: DEFAULT"),
    ("run_pipeline_named", lambda s: ["run-pipeline", str(s.base), "SAMPLE_DESCRIBE"], "pipeline: SAMPLE_DESCRIBE"),
    ("run_pipeline_output", lambda s: ["run-pipeline", str(s.base), "DEFAULT", "--output", str(s.root / "pipeline_out.tame")], "saved:", 0, None, lambda s: [s.root / "pipeline_out.tame"]),
    ("plugins_without_file", lambda s: ["plugins"], "CHEMISTRY_ANALYSIS"),
    ("plugins_with_file", lambda s: ["plugins", str(s.base)], "REFERENCE_INTERVAL"),
    ("run_plugin_counts", lambda s: ["run-plugin", str(s.base), "CHEMISTRY_ANALYSIS", "--option", "MODE=ITEM_COUNTS"], "chemistry_analysis mode=ITEM_COUNTS"),
    ("analyze_alias_counts", lambda s: ["analyze", str(s.base), "CHEMISTRY_ANALYSIS", "--option", "MODE=ITEM_COUNTS"], "chemistry_analysis mode=ITEM_COUNTS"),
    ("run_plugin_unknown", lambda s: ["run-plugin", str(s.base), "NO_SUCH_PLUGIN"], "error: unknown plugin", 1),
    ("actions", lambda s: ["actions", str(s.base)], "[KEEP_A]"),
    ("run_action", lambda s: ["run-action", str(s.base), "ADD_BATCH", "--output", str(s.root / "action_out.tame")], "action: ADD_BATCH", 0, None, lambda s: [s.root / "action_out.tame"]),
    ("action_pipelines", lambda s: ["action-pipelines", str(s.base)], "[DEFAULT]"),
    ("run_action_pipeline", lambda s: ["run-action-pipeline", str(s.base), "DEFAULT", "--output", str(s.root / "ap_out.tame")], "action_pipeline: DEFAULT", 0, None, lambda s: [s.root / "ap_out.tame"]),
    ("preprocess_alias", lambda s: ["preprocess", str(s.base), "DEFAULT", "--output", str(s.root / "pre_out.tame")], "action_pipeline: DEFAULT", 0, None, lambda s: [s.root / "pre_out.tame"]),
    ("presets", lambda s: ["presets"], "[CLINICAL_CHEMISTRY]"),
    ("apply_preset", lambda s: ["apply-preset", str(s.base), "--output", str(s.root / "preset.tame")], "preset:", 0, None, lambda s: [s.root / "preset.tame"]),
    ("anonymize_default", lambda s: ["anonymize", str(s.base), "--output", str(s.root / "anon.tame")], "saved:", 0, None, lambda s: [s.root / "anon.tame"]),
    ("anonymize_mapping", lambda s: ["anonymize", str(s.base), "--mapping-output-dir", str(s.root / "maps")], "saved_mappings:", 0, None, lambda s: [s.root / "maps"]),
    ("sample_rows", lambda s: ["sample", str(s.base), "--rows", "2", "--seed", "3", "--output", str(s.root / "sample_rows.tame")], "rows: 2", 0, None, lambda s: [s.root / "sample_rows.tame"]),
    ("sample_frac", lambda s: ["sample", str(s.base), "--frac", "0.5", "--seed", "3"], "rows:"),
    ("sample_by_tag", lambda s: ["sample", str(s.base), "--rows", "1", "--by-tag", "ITEM"], "rows:"),
    ("split_comparator", lambda s: ["split-comparator", str(s.comparator), "--output", str(s.root / "split_cmp.tame")], "result", 0, None, lambda s: [s.root / "split_cmp.tame"]),
    ("split_comparator_drop", lambda s: ["split-comparator", str(s.comparator), "--drop-original"], "result"),
    ("harmonize_comparator", lambda s: ["harmonize-comparator", str(s.comparator), "--output", str(s.root / "harm_cmp.tame")], "[harmonization_preview]", 0, None, lambda s: [s.root / "harm_cmp.tame"]),
    ("harmonize_comparator_all", lambda s: ["harmonize-comparator", str(s.comparator), "--exact-handling", "all"], "[harmonization_preview]"),
    ("extract_images", lambda s: ["extract-images", str(s.image), "--output-dir", str(s.root / "images"), "--output", str(s.root / "image_paths.tame")], "[images]", 0, None, lambda s: [s.root / "images", s.root / "image_paths.tame"]),
    ("embed_images", lambda s: ["embed-images", str(s.root / "image_paths.tame"), "--base-dir", str(s.root), "--output", str(s.root / "image_embedded.tame")], "[images]", 0, lambda s: _prepare_extracted_image_fixture(s), lambda s: [s.root / "image_embedded.tame"]),
    ("merge_two_files", lambda s: ["merge", str(s.base), str(s.base), "--output", str(s.root / "merged.tame")], "saved:", 0, None, lambda s: [s.root / "merged.tame"]),
    ("merge_no_source", lambda s: ["merge", str(s.base), str(s.base), "--no-source-column", "--output", str(s.root / "merged_no_source.tame")], "rows:", 0, None, lambda s: [s.root / "merged_no_source.tame"]),
    ("export_csv", lambda s: ["export", str(s.base), str(s.root / "out.csv")], "format: csv", 0, None, lambda s: [s.root / "out.csv"]),
    ("export_jsonl", lambda s: ["export", str(s.base), str(s.root / "out.jsonl")], "format: jsonl", 0, None, lambda s: [s.root / "out.jsonl"]),
    ("export_sql", lambda s: ["export", str(s.base), str(s.root / "out.sql"), "--format", "sql"], "format: sql", 0, None, lambda s: [s.root / "out.sql"]),
    ("export_r_bundle", lambda s: ["export", str(s.base), str(s.root / "bundle"), "--format", "r_bundle"], "format: r_bundle", 0, None, lambda s: [s.root / "bundle"]),
    ("split_tame_meta", lambda s: ["split-tame", str(s.base), "--meta-output", str(s.root / "split.meta.tame")], "saved_meta:", 0, None, lambda s: [s.root / "split.meta.tame"]),
    ("split_tame_data", lambda s: ["split-tame", str(s.base), "--data-output", str(s.root / "split.data.tame")], "saved_data:", 0, None, lambda s: [s.root / "split.data.tame"]),
    ("split_tame_both", lambda s: ["split-tame", str(s.base), "--meta-output", str(s.root / "both.meta.tame"), "--data-output", str(s.root / "both.data.tame")], "rows:", 0, None, lambda s: [s.root / "both.meta.tame", s.root / "both.data.tame"]),
    ("attach_meta", lambda s: ["attach-meta", str(s.template), str(s.meta_only), "--output", str(s.root / "attached.tame")], "saved:", 0, None, lambda s: [s.root / "attached.tame"]),
    ("import_xlsx", lambda s: ["import-xlsx", str(s.template), str(s.import_xlsx), "--output", str(s.root / "imported.tame")], "saved:", 0, None, lambda s: [s.root / "imported.tame"]),
    ("missing_file_error", lambda s: ["info", str(s.root / "missing.tame")], "Error: file not found", 1),
    ("unsupported_file_error", lambda s: ["info", str(s.root / "notes.txt")], "Unsupported input format", 1),
    ("bad_option_error", lambda s: ["validate", str(s.base), "--bad-option"], "error:", 2),
]


def _prepare_extracted_image_fixture(testcase: CliExecutionMatrixTest) -> str:
    code, output = testcase.run_cli(
        "extract-images",
        str(testcase.image),
        "--output-dir",
        str(testcase.root / "images"),
        "--output",
        str(testcase.root / "image_paths.tame"),
    )
    testcase.assertEqual(code, 0, output)
    return ""


for _name, _args_fn, _expected, *_rest in EXECUTION_CASES:
    _code = _rest[0] if len(_rest) > 0 else 0
    _stdin_fn = _rest[1] if len(_rest) > 1 else None
    _output_files_fn = _rest[2] if len(_rest) > 2 else None
    _json_output = _rest[3] if len(_rest) > 3 else False
    setattr(
        CliExecutionMatrixTest,
        f"test_exec_{_name}",
        _case(_name, _args_fn, _expected, code=_code, stdin_fn=_stdin_fn, output_files_fn=_output_files_fn, json_output=_json_output),
    )

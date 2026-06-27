from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import tempfile
import textwrap
import unittest
from typing import Callable

import pandas as pd

from tametools.actions import ActionError, execute_action, execute_action_pipeline
from tametools.analysis import describe_dataset, exploratory_data_analysis, validate_dataset
from tametools.exceptions import TameFormatError
from tametools.io import read_tame, write_tame
from tametools.models import ColumnSpec, TameDataset
from tametools.plugin_base import run_plugin
from tametools.tags import build_header, normalize_tag, parse_header


@dataclass(frozen=True)
class EdgeCase:
    category: str
    name: str
    run: Callable[[unittest.TestCase, Path], None]


BASE_DATA = textwrap.dedent(
    """
    <DATA>
    [[ID(patient)::STR]]patient_id\t[[ID(sample)::STR]]sample_id\t[[SEX]]sex\t[[AGE]]age\t[[ITEM::TESTNAME::CATEGORY]]test\t[[RESULT::<NUM>]]result\t[[UNIT::CATEGORY]]unit\t[[REF_LOW::NUM]]ref_low\t[[REF_HIGH::NUM]]ref_high\t[[COLLECTION_AT::DATETIME]]collection_time\t[[RECEIVED_AT::DATETIME]]received_time\t[[RESULT_TIME::DATETIME]]result_time\t[[INSTRUMENT::CATEGORY]]instrument\t[[GROUP::CATEGORY]]dept
    P1\tS1\tF\t32\tAST\t10\tU/L\t0\t40\t2026-01-01 08:00\t2026-01-01 08:15\t2026-01-01 09:00\tA\tIM
    P1\tS2\tF\t32\tAST\t60\tU/L\t0\t40\t2026-01-02 08:00\t2026-01-02 08:15\t2026-01-02 09:10\tB\tIM
    P2\tS3\tM\t2mo\tALT\t5\tU/L\t10\t40\t2026-01-01 10:00\t2026-01-01 10:25\t2026-01-01 11:00\tA\tGS
    P3\tS4\tunknown\t70\tCr\t<0.6\tmg/dL\t0.6\t1.2\t2026-01-03 07:30\t2026-01-03 07:45\t2026-01-03 08:20\tB\tER
    P4\tS5\tother\t0d\tGLU\t7.0\tmmol/L\t3.9\t5.6\t2026-01-04 06:30\t2026-01-04 06:45\t2026-01-04 07:20\tA\tIM
    </DATA>
    """
).strip()


def _safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text)[:80] or "case"


def _write_text(tmp_path: Path, name: str, text: str, suffix: str = ".tame") -> Path:
    path = tmp_path / f"{_safe_name(name)}{suffix}"
    path.write_text(text, encoding="utf-8")
    return path


def _read_inline(tmp_path: Path, name: str, text: str) -> TameDataset:
    return read_tame(_write_text(tmp_path, name, text))


def _base_dataset(tmp_path: Path, name: str = "base") -> TameDataset:
    return _read_inline(tmp_path, name, BASE_DATA)


def _with_action(dataset: TameDataset, name: str, config: dict) -> TameDataset:
    meta = dict(dataset.meta)
    meta["ACTIONS"] = {name: config}
    return dataset.replace(meta=meta)


def _assert_dataset_usable(test: unittest.TestCase, dataset: TameDataset) -> None:
    test.assertEqual(list(dataset.df.columns), [column.name for column in dataset.columns])
    validate_dataset(dataset)
    describe_dataset(dataset)


def _expect_clean_error(test: unittest.TestCase, fn: Callable[[], object], allowed: tuple[type[BaseException], ...]) -> None:
    with test.assertRaises(allowed) as ctx:
        fn()
    message = str(ctx.exception)
    test.assertTrue(message.strip(), "clean errors must include a user-facing message")


def _validation_tame(tags: tuple[str, ...], value: str, *, settings: str = "") -> str:
    tag_text = "::".join(tags)
    meta = f"<META>\n{settings}\n</META>\n" if settings.strip() else ""
    return f"{meta}<DATA>\n[[{tag_text}]]value\n{value}\n</DATA>"


def _validation_case(category: str, tags: tuple[str, ...], value: str, expected_issue_tags: set[str] | None = None) -> EdgeCase:
    tag_label = "::".join(tags)
    expected_issue_tags = expected_issue_tags or set()

    def run(test: unittest.TestCase, tmp_path: Path) -> None:
        dataset = _read_inline(tmp_path, f"{category}_{tag_label}_{value}", _validation_tame(tags, value))
        result = validate_dataset(dataset)
        issue_tags = {issue.tag for issue in result.issues}
        test.assertTrue(expected_issue_tags <= issue_tags, f"expected {expected_issue_tags}, observed {issue_tags}")
        describe_dataset(dataset)

    return EdgeCase(category, f"{tag_label} value={value!r}", run)


def _action_success_case(name: str, config: dict, check: Callable[[unittest.TestCase, object], None] | None = None) -> EdgeCase:
    def run(test: unittest.TestCase, tmp_path: Path) -> None:
        dataset = _with_action(_base_dataset(tmp_path, name), name, config)
        output = execute_action(dataset, name)
        test.assertIsNotNone(output.dataset)
        _assert_dataset_usable(test, output.dataset)
        if check is not None:
            check(test, output)

    return EdgeCase("ACTION success", name, run)


def _action_error_case(name: str, config: dict) -> EdgeCase:
    def run(test: unittest.TestCase, tmp_path: Path) -> None:
        dataset = _with_action(_base_dataset(tmp_path, name), name, config)
        _expect_clean_error(test, lambda: execute_action(dataset, name), (ActionError,))

    return EdgeCase("ACTION clean error", name, run)


def _plugin_case(name: str, plugin: str, options: dict | None = None, dataset_text: str = BASE_DATA) -> EdgeCase:
    def run(test: unittest.TestCase, tmp_path: Path) -> None:
        dataset = _read_inline(tmp_path, name, dataset_text)
        output = run_plugin(dataset, plugin, dataset.meta, name, options or {})
        test.assertIsNotNone(output, f"plugin not found: {plugin}")
        if output.dataset is not None:
            _assert_dataset_usable(test, output.dataset)
        if output.table is not None:
            test.assertIsInstance(output.table, pd.DataFrame)

    return EdgeCase("PLUGIN", name, run)


def _build_cases() -> list[EdgeCase]:
    cases: list[EdgeCase] = []

    normalize_inputs = [
        "id(patient)", "ID(PATIENT)", "id(sample)", "DATE(%Y-%m-%d)", "datetime(%Y-%m-%d %H:%M:%S)",
        "percentile(97.5)", "<num>", " result ", "cat", "sex", "age", "ref_low", "ref_high",
        "instrument", "collection_at", "received_at", "result_time", "group", "mean", "sd",
        "p_value", "auc", "sensitivity", "specificity", "youden",
    ]
    for value in normalize_inputs:
        cases.append(EdgeCase("TAG normalize", value, lambda test, tmp_path, value=value: test.assertTrue(normalize_tag(value))))

    header_specs = [
        ("patient_id", ("ID(patient)", "STR")),
        ("sample id", ("ID(sample)", "STR")),
        ("검사항목명", ("ITEM", "TESTNAME", "CATEGORY")),
        ("보고값", ("RESULT", "<NUM>")),
        ("참고치하한", ("REF_LOW", "NUM")),
        ("참고치상한", ("REF_HIGH", "NUM")),
        ("채취일", ("DATE(%Y-%m-%d)",)),
        ("보고일시", ("DATETIME(%Y-%m-%d %H:%M:%S)",)),
        ("P97_5", ("PERCENTILE(97.5)", "NUM")),
        ("중앙값", ("MEDIAN", "PERCENTILE(50)", "NUM")),
        ("판정", ("FLAG", "INTERPRETATION", "CATEGORY")),
        ("장비-1", ("INSTRUMENT", "CATEGORY")),
        ("부서/그룹", ("GROUP", "CATEGORY")),
        ("AUC", ("AUC", "NUM")),
        ("cutoff", ("THRESHOLD", "NUM")),
        ("환자/검체", ("ID(patient)", "ID(sample)", "STR")),
        ("비교자결과", ("RESULT", "<NUM>", "NULLABLE")),
        ("성별", ("SEX",)),
        ("나이", ("AGE",)),
        ("단위", ("UNIT", "CATEGORY")),
    ]
    for name, tags in header_specs:
        def run_header(test: unittest.TestCase, tmp_path: Path, name=name, tags=tags) -> None:
            header = build_header(name, tags)
            parsed_name, parsed_tags = parse_header(header)
            test.assertEqual(parsed_name, name)
            test.assertEqual(parsed_tags, tuple(normalize_tag(tag) for tag in tags))
            test.assertEqual(build_header(parsed_name, parsed_tags), header)

        cases.append(EdgeCase("TAG header roundtrip", name, run_header))

    for header in ["plain", "[[BAD TAG]]x", "[[RESULT]x", "_RESULT::<NUM>_보고값", "[[ID(patient)::STR]]"]:
        cases.append(
            EdgeCase(
                "TAG parse tolerant",
                header,
                lambda test, tmp_path, header=header: test.assertIsInstance(parse_header(header), tuple),
            )
        )

    numeric_valid = ["0", "-1", "+2", "1.25", ".5", "0007"]
    numeric_invalid = ["1,000", "1e3", "<5", "positive", "NaN"]
    for value in numeric_valid:
        cases.append(_validation_case("VALIDATE NUM valid", ("NUM",), value))
    for value in numeric_invalid:
        cases.append(_validation_case("VALIDATE NUM invalid", ("NUM",), value, {"NUM"}))

    comparator_valid = ["0", "<5", "<=5", "> 7", ">=7", "=8", "-0.5"]
    comparator_invalid = ["~5", "1e3", "positive", "<<5", "5 mg/dL"]
    for value in comparator_valid:
        cases.append(_validation_case("VALIDATE <NUM> valid", ("<NUM>",), value))
    for value in comparator_invalid:
        cases.append(_validation_case("VALIDATE <NUM> invalid", ("<NUM>",), value, {"<NUM>"}))

    age_valid = ["0", "32", "2mo", "3m", "4d", "70세", "1.5year", "0005"]
    age_invalid = ["-1", "abc", "1wk", "NaN", "1,000"]
    for value in age_valid:
        cases.append(_validation_case("VALIDATE AGE valid", ("AGE",), value))
    for value in age_invalid:
        cases.append(_validation_case("VALIDATE AGE invalid", ("AGE",), value, {"AGE"}))

    sex_valid = ["F", "M", "female", "male", "여", "남", "unknown", "other", "1", "2"]
    sex_invalid = ["?", "남성?", "3", "not recorded", "중성"]
    for value in sex_valid:
        cases.append(_validation_case("VALIDATE SEX valid", ("SEX",), value))
    for value in sex_invalid:
        cases.append(_validation_case("VALIDATE SEX invalid", ("SEX",), value, {"SEX"}))

    date_valid = ["2026-01-01", "2026/01/01", "20260101"]
    datetime_valid = ["2026-01-01 08:30", "2026/01/01 08:30:00", "2026-01-01T08:30:00"]
    time_valid = ["08:30", "08:30:00", "083000"]
    date_invalid = ["2026-13-01", "not-a-date", "2026-02-30"]
    time_invalid = ["2026-01-01", "not-a-time", "25:00"]
    for value in date_valid:
        cases.append(_validation_case("VALIDATE DATE valid", ("DATE",), value))
    for value in datetime_valid:
        cases.append(_validation_case("VALIDATE DATETIME valid", ("DATETIME",), value))
    for value in time_valid:
        cases.append(_validation_case("VALIDATE TIME valid", ("TIME",), value))
    for value in date_invalid:
        cases.append(_validation_case("VALIDATE DATE invalid", ("DATE",), value, {"DATE"}))
    for value in time_invalid:
        cases.append(_validation_case("VALIDATE TIME invalid", ("TIME",), value, {"TIME"}))

    state_cases = [
        (("NUM", "NULLABLE"), "<<NULL>>", set()),
        (("STR", "EMPTY_OK"), "<<EMPTY>>", set()),
        (("STR", "WS_OK"), "<<WS:3>>", set()),
        (("NUM",), "<<NULL>>", {"NULL"}),
        (("STR",), "<<EMPTY>>", {"EMPTY"}),
        (("STR",), "<<WS:2>>", {"WS"}),
    ]
    for tags, value, expected in state_cases:
        cases.append(_validation_case("VALIDATE cell state", tags, value, expected))

    io_cases: list[tuple[str, str]] = [
        ("header_only", "<DATA>\n[[ID(patient)::STR]]patient_id\t[[RESULT::NUM]]result\n</DATA>"),
        ("empty_data_section", "<DATA>\n</DATA>"),
        ("duplicate_headers", "<DATA>\na\ta\n1\t2\n</DATA>"),
        ("short_row", "<DATA>\na\tb\tc\n1\t2\n</DATA>"),
        ("long_row", "<DATA>\na\tb\n1\t2\t3\n</DATA>"),
        ("quoted_tab", '<DATA>\na\tb\n"one\\ttwo"\t3\n</DATA>'),
        ("meta_column_tags", '<META>\n[COLUMN.result]\nTAGS = ["RESULT", "NUM"]\n</META>\n<DATA>\nresult\n1\n</DATA>'),
        ("legacy_tags", '<META>\n[TAGS]\nresult = ["RESULT", "NUM"]\n</META>\n<DATA>\nresult\n1\n</DATA>'),
        ("two_header_rows", '<META>\n[DATA]\nheaders = 2\n</META>\n<DATA>\npatient\tresult\nid\tvalue\nP1\t1\n</DATA>'),
        ("custom_null_token", '<META>\n[FORMAT]\nNULL_TOKEN = "NA_NULL"\n</META>\n<DATA>\n[[NULLABLE]]x\nNA_NULL\n</DATA>'),
    ]
    for name, text in io_cases:
        def run_io(test: unittest.TestCase, tmp_path: Path, name=name, text=text) -> None:
            dataset = _read_inline(tmp_path, name, text)
            _assert_dataset_usable(test, dataset)
            out = tmp_path / f"{name}_roundtrip.tame"
            write_tame(out, dataset)
            _assert_dataset_usable(test, read_tame(out))

        cases.append(EdgeCase("IO tolerant", name, run_io))

    error_io_cases = [
        ("unsupported_extension", lambda tmp: read_tame(tmp / "bad.txt")),
        ("meta_only_as_dataset", lambda tmp: read_tame(_write_text(tmp, "only_meta", "[SETTINGS]\nVALIDATE_ERROR = \"REPORT\"\n", ".meta.tame"))),
        ("missing_data_section", lambda tmp: read_tame(_write_text(tmp, "missing_data", "<META>\n[SETTINGS]\nVALIDATE_ERROR = \"REPORT\"\n</META>"))),
        ("malformed_toml", lambda tmp: read_tame(_write_text(tmp, "bad_toml", "<META>\n[SETTINGS\nx = 1\n</META>\n<DATA>\na\n1\n</DATA>"))),
        ("bad_header_rows", lambda tmp: read_tame(_write_text(tmp, "bad_headers", "<META>\n[DATA]\nheaders = \"abc\"\n</META>\n<DATA>\na\n1\n</DATA>"))),
    ]
    for name, fn in error_io_cases:
        cases.append(EdgeCase("IO clean error", name, lambda test, tmp_path, fn=fn: _expect_clean_error(test, lambda: fn(tmp_path), (TameFormatError,))))

    cases.extend(
        [
            _action_success_case("add_column_constant", {"TYPE": "ADD_COLUMN", "NAME": "batch", "VALUE": "2026Q2", "TAGS": ["BATCH", "CATEGORY"]}),
            _action_success_case("add_column_from_tag", {"TYPE": "ADD_COLUMN", "NAME": "test_copy", "FROM": "tag:ITEM", "TAGS": ["ITEM", "CATEGORY"]}),
            _action_success_case("add_column_template_tags", {"TYPE": "ADD_COLUMN", "NAME": "label", "TEMPLATE": "{tag:ID(patient)}:{tag:ITEM}:{tag:RESULT}", "TAGS": ["STR"]}),
            _action_success_case("derive_ratio", {"TYPE": "DERIVE", "NAME": "ratio", "EXPR": "{tag:RESULT} / {tag:REF_HIGH}", "TAGS": ["RATIO", "NUM"]}),
            _action_success_case("derive_log", {"TYPE": "DERIVE", "NAME": "log_result", "EXPR": "log10({tag:RESULT} + 1)", "TAGS": ["NUM"]}),
            _action_success_case("date_derive_age", {"TYPE": "DATE_DERIVE", "NAME": "visit_age", "KIND": "age", "FROM": "tag:AGE", "AS_OF": "2026-01-01", "TAGS": ["AGE"]}),
            _action_success_case("date_derive_tat_minutes", {"TYPE": "DATE_DERIVE", "NAME": "tat_min", "KIND": "diff_minutes", "START": "tag:RECEIVED_AT", "END": "tag:RESULT_TIME", "TAGS": ["DURATION", "NUM"]}),
            _action_success_case("date_derive_part", {"TYPE": "DATE_DERIVE", "NAME": "report_date", "KIND": "part", "FROM": "tag:RESULT_TIME", "PART": "date", "TAGS": ["DATE"]}),
            _action_success_case("impute_constant", {"TYPE": "IMPUTE", "TAGS": ["RESULT"], "METHOD": "constant", "VALUE": "0"}),
            _action_success_case("impute_median", {"TYPE": "IMPUTE", "TAGS": ["REF_LOW"], "METHOD": "median"}),
            _action_success_case("outlier_iqr", {"TYPE": "OUTLIER_FILTER", "COLUMN": "tag:RESULT", "METHOD": "iqr"}),
            _action_success_case("recode_new_column", {"TYPE": "RECODE", "COLUMN": "tag:GROUP", "NAME": "dept_label", "MAP": {"IM": "내과", "GS": "외과"}, "DEFAULT": "기타", "TAGS": ["GROUP", "CATEGORY"]}),
            _action_success_case("dedup_all", {"TYPE": "DEDUP", "KEEP": "first"}),
            _action_success_case("dedup_key", {"TYPE": "DEDUP", "TAGS": ["ID(patient)"], "KEEP": "last"}),
            _action_success_case("sort_text", {"TYPE": "SORT", "BY": ["tag:ID(patient)", "tag:RESULT_TIME"]}),
            _action_success_case("sort_numeric", {"TYPE": "SORT", "BY": ["tag:REF_HIGH"], "NUMERIC": True, "ASCENDING": False}),
            _action_success_case("round_result", {"TYPE": "ROUND", "TAGS": ["RESULT"], "DECIMALS": 1}),
            _action_success_case("bin_age_edges", {"TYPE": "BIN", "COLUMN": "tag:AGE", "NAME": "age_group", "BINS": [0, 18, 65, 120], "LABELS": ["child", "adult", "older"], "TAGS": ["AGE_GROUP", "CATEGORY"]}),
            _action_success_case("bin_ref_width", {"TYPE": "BIN", "COLUMN": "tag:REF_HIGH", "NAME": "ref_band", "WIDTH": 20, "TAGS": ["CATEGORY"]}),
            _action_success_case("unit_convert_glu", {"TYPE": "UNIT.CONVERT", "SOURCE": "tag:RESULT", "UNIT_COLUMN": "tag:UNIT", "TEST_COLUMN": "tag:ITEM", "OUTPUT_VALUE": "converted", "OUTPUT_UNIT": "target_unit", "TARGET_UNIT_BY_TEST": {"GLU": "mg/dL"}, "DECIMALS": 1}),
            _action_success_case("row_eq", {"TYPE": "ROW.INCLUDE", "COLUMN": "tag:ITEM", "OP": "eq", "VALUE": "AST"}),
            _action_success_case("row_ne", {"TYPE": "ROW.INCLUDE", "COLUMN": "tag:ITEM", "OP": "ne", "VALUE": "AST"}),
            _action_success_case("row_in", {"TYPE": "ROW.INCLUDE", "COLUMN": "tag:GROUP", "OP": "in", "VALUES": ["IM", "ER"]}),
            _action_success_case("row_contains", {"TYPE": "ROW.INCLUDE", "COLUMN": "tag:ID(patient)", "OP": "contains", "VALUE": "P"}),
            _action_success_case("row_startswith", {"TYPE": "ROW.INCLUDE", "COLUMN": "tag:ID(sample)", "OP": "startswith", "VALUE": "S"}),
            _action_success_case("row_regex", {"TYPE": "ROW.INCLUDE", "COLUMN": "tag:ID(sample)", "OP": "regex", "VALUE": r"^S[12]$"}),
            _action_success_case("row_gt", {"TYPE": "ROW.INCLUDE", "COLUMN": "tag:REF_HIGH", "OP": "gt", "VALUE": 10}),
            _action_success_case("row_between_reversed", {"TYPE": "ROW.INCLUDE", "COLUMN": "tag:REF_HIGH", "OP": "between", "VALUES": [50, 10]}),
            _action_success_case("row_date_compare", {"TYPE": "ROW.INCLUDE", "COLUMN": "tag:RESULT_TIME", "OP": ">=", "VALUE": "2026-01-02"}),
            _action_success_case("column_include_tags", {"TYPE": "COLUMN.INCLUDE", "TAGS": ["ID", "ITEM", "RESULT"]}),
            _action_success_case("column_exclude_ops", {"TYPE": "COLUMN.EXCLUDE", "TAGS": ["INSTRUMENT", "GROUP"]}),
            _action_success_case("column_split", {"TYPE": "COLUMN.SPLIT", "COLUMN": "tag:ID(sample)", "INTO": ["sample_prefix", "sample_rest"], "SEP": "S", "TAGS": ["STR"]}),
            _action_success_case("column_concat", {"TYPE": "COLUMN.CONCAT", "COLUMNS": ["tag:ID(patient)", "tag:ID(sample)"], "OUTPUT": "patient_sample", "SEP": "::", "TAGS": ["ID", "STR"]}),
            _action_success_case("pivot_wider_first", {"TYPE": "PIVOT_WIDER", "ID_COLS": ["tag:ID(sample)"], "NAMES_FROM": "tag:ITEM", "VALUES_FROM": "tag:RESULT", "AGG": "first"}),
            _action_success_case("pivot_longer_refs", {"TYPE": "PIVOT_LONGER", "ID_COLS": ["tag:ID(sample)", "tag:ITEM"], "COLS": ["tag:REF_LOW", "tag:REF_HIGH"], "NAMES_TO": "bound", "VALUES_TO": "limit"}),
        ]
    )

    cases.extend(
        [
            _action_error_case("add_column_missing_name", {"TYPE": "ADD_COLUMN", "VALUE": "x"}),
            _action_error_case("add_column_duplicate", {"TYPE": "ADD_COLUMN", "NAME": "result", "VALUE": "x"}),
            _action_error_case("add_column_unknown_tag_template", {"TYPE": "ADD_COLUMN", "NAME": "x", "TEMPLATE": "{tag:UNKNOWN}"}),
            _action_error_case("derive_missing_expr", {"TYPE": "DERIVE", "NAME": "x"}),
            _action_error_case("derive_unsafe_expr", {"TYPE": "DERIVE", "NAME": "x", "EXPR": "__import__('os')"}),
            _action_error_case("date_derive_bad_part", {"TYPE": "DATE_DERIVE", "NAME": "x", "KIND": "part", "FROM": "tag:RESULT_TIME", "PART": "century"}),
            _action_error_case("impute_bad_method", {"TYPE": "IMPUTE", "TAGS": ["RESULT"], "METHOD": "mystery"}),
            _action_error_case("dedup_bad_keep", {"TYPE": "DEDUP", "KEEP": "middle"}),
            _action_error_case("sort_missing_by", {"TYPE": "SORT"}),
            _action_error_case("round_no_target", {"TYPE": "ROUND"}),
            _action_error_case("bin_missing_bins", {"TYPE": "BIN", "COLUMN": "tag:AGE", "NAME": "x"}),
            _action_error_case("split_missing_sep", {"TYPE": "COLUMN.SPLIT", "COLUMN": "tag:ID(sample)", "INTO": ["a", "b"]}),
            _action_error_case("concat_missing_sources", {"TYPE": "COLUMN.CONCAT", "OUTPUT": "x"}),
            _action_error_case("row_missing_condition", {"TYPE": "ROW.INCLUDE"}),
            _action_error_case("row_bad_numeric_bound", {"TYPE": "ROW.INCLUDE", "COLUMN": "tag:RESULT", "OP": "gt", "VALUE": "not-number"}),
            _action_error_case("row_bad_regex", {"TYPE": "ROW.INCLUDE", "COLUMN": "tag:ID(sample)", "OP": "regex", "VALUE": "["}),
            _action_error_case("column_include_missing", {"TYPE": "COLUMN.INCLUDE"}),
            _action_error_case("pivot_wider_bad_agg", {"TYPE": "PIVOT_WIDER", "ID_COLS": ["tag:ID(sample)"], "NAMES_FROM": "tag:ITEM", "VALUES_FROM": "tag:RESULT", "AGG": "median"}),
            _action_error_case("ambiguous_id_selector", {"TYPE": "COLUMN.SPLIT", "COLUMN": "tag:ID", "INTO": ["a", "b"], "SEP": "S"}),
            _action_error_case("unit_convert_missing_source", {"TYPE": "UNIT.CONVERT", "SOURCE": "tag:UNKNOWN", "OUTPUT_VALUE": "x"}),
        ]
    )

    def run_pipeline(test: unittest.TestCase, tmp_path: Path) -> None:
        dataset = _base_dataset(tmp_path, "pipeline")
        meta = dict(dataset.meta)
        meta["ACTIONS"] = {
            "SORT_TIME": {"TYPE": "SORT", "BY": ["tag:ID(patient)", "tag:RESULT_TIME"]},
            "KEY": {"TYPE": "COLUMN.CONCAT", "COLUMNS": ["tag:ID(patient)", "tag:ID(sample)"], "OUTPUT": "key", "SEP": "::", "TAGS": ["ID", "STR"]},
            "CORE": {"TYPE": "COLUMN.INCLUDE", "TAGS": ["ID", "ITEM", "RESULT"]},
        }
        meta["ACTION_PIPELINES"] = {"DEFAULT": ["SORT_TIME", "KEY", "CORE"]}
        result = execute_action_pipeline(dataset.replace(meta=meta))
        _assert_dataset_usable(test, result.final_dataset)
        test.assertGreaterEqual(len(result.outputs), 3)

    cases.append(EdgeCase("ACTION pipeline", "three_step_tag_only_pipeline", run_pipeline))

    cases.extend(
        [
            _plugin_case("abnormal_flag_flag", "ABNORMAL_FLAG", {"MODE": "FLAG"}),
            _plugin_case("abnormal_flag_rate", "ABNORMAL_FLAG", {"MODE": "RATE"}),
            _plugin_case("autoverification_all", "AUTOVERIFICATION", {"OUTPUT": "ALL"}),
            _plugin_case("reference_interval_sparse", "REFERENCE_INTERVAL", {}),
            _plugin_case("chem_item_counts", "CHEMISTRY_ANALYSIS", {"MODE": "ITEM_COUNTS"}),
            _plugin_case("chem_result_summary", "CHEMISTRY_ANALYSIS", {"MODE": "RESULT_SUMMARY"}),
            _plugin_case("chem_instrument_bias", "CHEMISTRY_ANALYSIS", {"MODE": "INSTRUMENT_BIAS"}),
            _plugin_case("chem_delta_check", "CHEMISTRY_ANALYSIS", {"MODE": "DELTA_CHECK"}),
            _plugin_case("chem_age_sex_result", "CHEMISTRY_ANALYSIS", {"MODE": "AGE_SEX_RESULT"}),
            _plugin_case("chem_missing_quality", "CHEMISTRY_ANALYSIS", {"MODE": "MISSING_QUALITY"}),
            _plugin_case("chem_daily_workload", "CHEMISTRY_ANALYSIS", {"MODE": "DAILY_WORKLOAD"}),
            _plugin_case("chem_outliers_iqr", "CHEMISTRY_ANALYSIS", {"MODE": "OUTLIERS_IQR"}),
            _plugin_case("chem_tat_by_test", "CHEMISTRY_ANALYSIS", {"MODE": "TAT_BY_TEST"}),
            _plugin_case("qc_precision", "QC_ANALYSIS", {"MODE": "PRECISION"}),
            _plugin_case("qc_sigma", "QC_ANALYSIS", {"MODE": "SIGMA"}),
            _plugin_case("qc_westgard", "QC_ANALYSIS", {"MODE": "WESTGARD"}),
            _plugin_case("group_test", "GROUP_TEST", {"GROUP": "tag:GROUP"}),
            _plugin_case("correlation", "CORRELATION", {}),
            _plugin_case("result_trend", "RESULT_TREND", {"PERIOD": "D"}),
        ]
    )

    roc_text = textwrap.dedent(
        """
        <DATA>
        [[RESULT::NUM]]score\t[[LABEL::CATEGORY]]label
        0.1\t0
        0.4\t0
        0.6\t1
        0.9\t1
        </DATA>
        """
    ).strip()
    cases.append(_plugin_case("roc_analysis_small", "ROC_ANALYSIS", {"POSITIVE": "1"}, roc_text))

    wide_pivot_context = textwrap.dedent(
        """
        <META>
        [COLUMN.AST.PIVOT_CONTEXT]
        REF_LOW = "0"
        REF_HIGH = "40"

        [COLUMN.ALT.PIVOT_CONTEXT]
        REF_LOW = "10"
        REF_HIGH = "40"
        </META>
        <DATA>
        [[ID(sample)::STR]]sample\t[[RESULT::NUM]]AST\t[[RESULT::NUM]]ALT
        S1\t10\t5
        S2\t60\t20
        </DATA>
        """
    ).strip()
    cases.append(_plugin_case("abnormal_flag_wide_pivot_context", "ABNORMAL_FLAG", {"MODE": "FLAG"}, wide_pivot_context))
    cases.append(_plugin_case("abnormal_rate_wide_pivot_context", "ABNORMAL_FLAG", {"MODE": "RATE"}, wide_pivot_context))

    missing_result = "<DATA>\n[[ID(patient)::STR]]patient_id\nP1\n</DATA>"
    cases.append(_plugin_case("abnormal_flag_missing_result_warns", "ABNORMAL_FLAG", {"MODE": "FLAG"}, missing_result))
    cases.append(_plugin_case("reference_interval_missing_roles_warns", "REFERENCE_INTERVAL", {}, missing_result))

    return cases


class ClinicalEdgeCaseStabilityTest(unittest.TestCase):
    def test_clinical_edge_case_matrix(self) -> None:
        cases = _build_cases()
        self.assertGreaterEqual(len(cases), 120)
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            for index, case in enumerate(cases, start=1):
                with self.subTest(index=index, category=case.category, name=case.name):
                    case.run(self, tmp_path)

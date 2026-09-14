from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from tametools.actions import ActionError, execute_action, execute_action_pipeline
from tametools.analysis import describe_dataset, validate_dataset
from tametools.exceptions import TameFormatError
from tametools.io import read_tame, write_tame
from tametools.plugin_base import run_plugin


@dataclass(frozen=True)
class Scenario:
    id: str
    category: str
    purpose: str
    operation: str
    tame: str
    params: dict[str, Any] = field(default_factory=dict)
    expect: dict[str, Any] = field(default_factory=dict)


BASE_TAME = """
<META>
[SETTINGS]
VALIDATE_ERROR = "REPORT"
</META>
<DATA>
[[ID(patient)::STR]]pid\t[[ID(sample)::STR]]sample\t[[SEX]]sex\t[[DOB::DATE]]dob\t[[AGE]]age\t[[ITEM::TESTNAME::CATEGORY]]test\t[[RESULT::<NUM>]]result\t[[UNIT::CATEGORY]]unit\t[[REF_LOW::NUM]]ref_low\t[[REF_HIGH::NUM]]ref_high\t[[COLLECTION_AT::DATETIME]]collected\t[[RECEIVED_AT::DATETIME]]received\t[[RESULT_TIME::DATETIME]]reported\t[[INSTRUMENT::CATEGORY]]inst\t[[GROUP::CATEGORY]]dept
P1\tS1\tF\t1980-01-01\t46\tAST\t10\tU/L\t0\t40\t2026-01-01 08:00\t2026-01-01 08:10\t2026-01-01 09:00\tA\tIM
P1\tS2\tF\t1980-01-01\t46\tAST\t70\tU/L\t0\t40\t2026-01-02 08:00\t2026-01-02 08:10\t2026-01-02 09:00\tB\tIM
P2\tS3\tM\t1975-06-15\t50\tALT\t5\tU/L\t10\t40\t2026-01-03 07:30\t2026-01-03 07:45\t2026-01-03 08:20\tA\tGS
P2\tS4\tM\t1975-06-15\t50\tALT\t25\tU/L\t10\t40\t2026-01-04 07:30\t2026-01-04 07:45\t2026-01-04 08:20\tB\tGS
P3\tS5\tunknown\t2025-11-01\t2mo\tCr\t<0.6\tmg/dL\t0.6\t1.2\t2026-01-05 06:20\t2026-01-05 06:45\t2026-01-05 07:15\tA\tER
P4\tS6\tother\t2026-01-01\t0d\tGLU\t7.0\tmmol/L\t3.9\t5.6\t2026-01-06 06:20\t2026-01-06 06:45\t2026-01-06 07:15\tB\tIM
</DATA>
""".strip()


WIDE_RESULT_TAME = """
<META>
[SETTINGS]
VALIDATE_ERROR = "REPORT"

[COLUMN.AST.PIVOT_CONTEXT]
UNIT = "U/L"
REF_LOW = "0"
REF_HIGH = "40"

[COLUMN.ALT.PIVOT_CONTEXT]
UNIT = "U/L"
REF_LOW = "10"
REF_HIGH = "40"
</META>
<DATA>
[[ID(patient)::STR]]pid\t[[ID(sample)::STR]]sample\t[[GROUP::CATEGORY]]group\t[[RESULT_TIME::DATETIME]]reported\t[[RESULT::NUM]]AST\t[[RESULT::NUM]]ALT
P1\tS1\tA\t2026-01-01 08:00\t10\t20
P1\tS2\tA\t2026-01-02 08:00\t70\t25
P2\tS3\tB\t2026-01-01 08:00\t20\t5
P2\tS4\tB\t2026-02-01 08:00\t22\t45
</DATA>
""".strip()


METHOD_TAME = """
<META>
[COLUMN.result]
UNIT = "U/L"
</META>
<DATA>
[[ID(patient)::STR]]pid\t[[ID(sample)::STR]]sample\t[[ITEM::TESTNAME::CATEGORY]]test\t[[INSTRUMENT::CATEGORY]]inst\t[[RESULT::NUM]]result
P1\tS1\tAST\tA\t10
P1\tS1\tAST\tB\t12
P2\tS2\tAST\tA\t20
P2\tS2\tAST\tB\t23
P3\tS3\tAST\tA\t30
P3\tS3\tAST\tB\t34
P4\tS4\tAST\tA\t40
P4\tS4\tAST\tB\t45
</DATA>
""".strip()


ROC_TAME = """
<DATA>
[[RESULT::NUM]]score_a\t[[RESULT::NUM]]score_b\t[[LABEL::CATEGORY]]label
0.10\t0.20\t0
0.20\t0.30\t0
0.80\t0.70\t1
0.90\t0.95\t1
</DATA>
""".strip()


def build_scenarios() -> list[Scenario]:
    scenarios: list[Scenario] = []
    scenarios.extend(_validation_scenarios())
    scenarios.extend(_io_scenarios())
    scenarios.extend(_action_success_scenarios())
    scenarios.extend(_action_error_scenarios())
    scenarios.extend(_pipeline_scenarios())
    scenarios.extend(_plugin_scenarios())
    return scenarios


def run_scenario(scenario: Scenario, tmp_path: Path) -> None:
    path = _write_tame_text(tmp_path, scenario.id, scenario.tame, scenario.expect.get("suffix", ".tame"))
    if scenario.operation == "validate":
        dataset = read_tame(path)
        result = validate_dataset(dataset)
        observed = {issue.tag for issue in result.issues}
        expected = set(scenario.expect.get("issue_tags", []))
        if not expected <= observed:
            raise AssertionError(f"{scenario.id}: expected issue tags {expected}, observed {observed}")
        describe_dataset(dataset)
        return

    if scenario.operation == "io_roundtrip":
        dataset = read_tame(path)
        describe_dataset(dataset)
        output_path = tmp_path / f"{scenario.id}.roundtrip.tame"
        write_tame(output_path, dataset)
        reread = read_tame(output_path)
        if list(reread.df.columns) != [column.name for column in reread.columns]:
            raise AssertionError(f"{scenario.id}: roundtrip column mismatch")
        return

    if scenario.operation == "read_error":
        _expect_error(lambda: read_tame(path), (TameFormatError,))
        return

    if scenario.operation == "action":
        dataset = _with_action(read_tame(path), scenario.id, scenario.params)
        output = execute_action(dataset, scenario.id)
        _check_output_dataset(output.dataset, scenario)
        return

    if scenario.operation == "action_error":
        dataset = _with_action(read_tame(path), scenario.id, scenario.params)
        _expect_error(lambda: execute_action(dataset, scenario.id), (ActionError,))
        return

    if scenario.operation == "pipeline":
        dataset = read_tame(path)
        meta = dict(dataset.meta)
        meta["ACTIONS"] = scenario.params["ACTIONS"]
        meta["ACTION_PIPELINES"] = scenario.params["ACTION_PIPELINES"]
        result = execute_action_pipeline(dataset.replace(meta=meta), scenario.params.get("PIPELINE", "DEFAULT"))
        _check_output_dataset(result.final_dataset, scenario)
        return

    if scenario.operation == "plugin":
        dataset = read_tame(path)
        if scenario.params.get("EXPECT_ERROR"):
            _expect_error(lambda: run_plugin(dataset, scenario.params["PLUGIN"], dataset.meta, scenario.id, scenario.params.get("OPTIONS", {})), (ValueError,))
            return
        output = run_plugin(dataset, scenario.params["PLUGIN"], dataset.meta, scenario.id, scenario.params.get("OPTIONS", {}))
        if output is None:
            raise AssertionError(f"{scenario.id}: plugin not found")
        table = output.table
        if table is not None:
            rows_min = int(scenario.expect.get("rows_min", 0))
            if len(table) < rows_min:
                raise AssertionError(f"{scenario.id}: expected at least {rows_min} rows, got {len(table)}")
            for column in scenario.expect.get("columns", []):
                if column not in table.columns:
                    raise AssertionError(f"{scenario.id}: missing expected table column {column}")
        if output.dataset is not None:
            _check_output_dataset(output.dataset, scenario)
        return

    raise AssertionError(f"{scenario.id}: unsupported operation {scenario.operation}")


def scenario_summary() -> pd.DataFrame:
    rows = [
        {"id": scenario.id, "category": scenario.category, "operation": scenario.operation, "purpose": scenario.purpose}
        for scenario in build_scenarios()
    ]
    return pd.DataFrame(rows)


def _validation_scenarios() -> list[Scenario]:
    cases: list[Scenario] = []
    groups = [
        ("NUM_valid", ("NUM",), ["0", "-1", "+2", "1.25", ".5", "0007", "1e3", "3.14159"], []),
        ("NUM_invalid", ("NUM",), ["1,000", "1e", "<5", "positive", "NaN", "5 mg/dL", "--1", "++1"], ["NUM"]),
        ("CNUM_valid", ("<NUM>",), ["0", "<5", "<=5", "> 7", ">=7", "=8", "-0.5", "<1e3"], []),
        ("CNUM_invalid", ("<NUM>",), ["~5", "1e", "positive", "<<5", "5 mg/dL", ">", "<", "abc"], ["<NUM>"]),
        ("AGE_valid", ("AGE",), ["0", "32", "2mo", "3m", "4d", "70세", "1.5year", "0005"], []),
        ("AGE_invalid", ("AGE",), ["-1", "abc", "1wk", "NaN", "1,000", "2h", "세", "--3"], ["AGE"]),
        ("SEX_valid", ("SEX",), ["F", "M", "female", "male", "여", "남", "unknown", "other", "1", "2"], []),
        ("SEX_invalid", ("SEX",), ["?", "남성?", "3", "not recorded", "중성", "ambiguous", "female?", "-"], ["SEX"]),
        ("DATE_valid", ("DATE",), ["2026-01-01", "2026/01/01", "20260101", "01/02/2026"], []),
        ("DATE_invalid", ("DATE",), ["2026-13-01", "not-a-date", "2026-02-30", "2026/99/99"], ["DATE"]),
        ("DATETIME_valid", ("DATETIME",), ["2026-01-01 08:30", "2026/01/01 08:30:00", "2026-01-01T08:30:00", "20260101083000"], []),
        ("DATETIME_invalid", ("DATETIME",), ["2026-13-01 08:00", "not-a-date", "2026-02-30 01:00", "25:61"], ["DATETIME"]),
        ("CELL_STATE_allowed", ("STR", "NULLABLE", "EMPTY_OK", "WS_OK"), ["<<NULL>>", "<<EMPTY>>", "<<WS:3>>"], []),
        ("CELL_STATE_null_rejected", ("STR",), ["<<NULL>>"], ["NULL"]),
        ("CELL_STATE_empty_rejected", ("STR",), ["<<EMPTY>>"], ["EMPTY"]),
        ("CELL_STATE_ws_rejected", ("STR",), ["<<WS:2>>"], ["WS"]),
    ]
    index = 1
    for group_name, tags, values, issue_tags in groups:
        for value in values:
            cases.append(
                Scenario(
                    id=f"V{index:03d}_{group_name}",
                    category="validation",
                    purpose=f"Validate {tags} with value {value!r}",
                    operation="validate",
                    tame=_single_value_tame(tags, value),
                    expect={"issue_tags": issue_tags},
                )
            )
            index += 1
    return cases


def _io_scenarios() -> list[Scenario]:
    return [
        Scenario("IO001_header_only", "io", "Header-only TAME reads and writes", "io_roundtrip", "<DATA>\n[[ID(patient)::STR]]pid\t[[RESULT::NUM]]result\n</DATA>"),
        Scenario("IO002_duplicate_headers", "io", "Duplicate headers become unique columns", "io_roundtrip", "<DATA>\na\ta\n1\t2\n</DATA>"),
        Scenario("IO003_short_row", "io", "Short data row is padded", "io_roundtrip", "<DATA>\na\tb\tc\n1\t2\n</DATA>"),
        Scenario("IO004_long_row", "io", "Long data row is truncated to header width", "io_roundtrip", "<DATA>\na\tb\n1\t2\t3\n</DATA>"),
        Scenario("IO005_quoted_tab", "io", "Quoted tab cell stays one cell", "io_roundtrip", '<DATA>\na\tb\n"one\\ttwo"\t3\n</DATA>'),
        Scenario("IO006_meta_tags", "io", "COLUMN metadata tags attach to data", "io_roundtrip", '<META>\n[COLUMN.result]\nTAGS = ["RESULT", "NUM"]\n</META>\n<DATA>\nresult\n1\n</DATA>'),
        Scenario("IO007_legacy_tags", "io", "Legacy TAGS section still works", "io_roundtrip", '<META>\n[TAGS]\nresult = ["RESULT", "NUM"]\n</META>\n<DATA>\nresult\n1\n</DATA>'),
        Scenario("IO008_two_header_rows", "io", "Two header rows are joined", "io_roundtrip", '<META>\n[DATA]\nheaders = 2\n</META>\n<DATA>\npatient\tresult\nid\tvalue\nP1\t1\n</DATA>'),
        Scenario("IO009_custom_null", "io", "Custom NULL token round-trips", "io_roundtrip", '<META>\n[FORMAT]\nNULL_TOKEN = "NA_NULL"\n</META>\n<DATA>\n[[NULLABLE]]x\nNA_NULL\n</DATA>'),
        Scenario("IO010_bad_toml", "io_error", "Malformed TOML is a clean TameFormatError", "read_error", "<META>\n[SETTINGS\nx = 1\n</META>\n<DATA>\na\n1\n</DATA>"),
        Scenario("IO011_bad_headers", "io_error", "Non-integer DATA.headers is a clean TameFormatError", "read_error", '<META>\n[DATA]\nheaders = "abc"\n</META>\n<DATA>\na\n1\n</DATA>'),
        Scenario("IO012_meta_only", "io_error", "Meta-only file is rejected as dataset", "read_error", "[SETTINGS]\nVALIDATE_ERROR = \"REPORT\"\n", expect={"suffix": ".meta.tame"}),
    ]


def _action_success_scenarios() -> list[Scenario]:
    configs = [
        ("A001_add_constant", "Add batch label", {"TYPE": "ADD_COLUMN", "NAME": "batch", "VALUE": "2026Q2", "TAGS": ["BATCH", "CATEGORY"]}),
        ("A002_add_from_tag", "Copy test column by tag", {"TYPE": "ADD_COLUMN", "NAME": "test_copy", "FROM": "tag:ITEM", "TAGS": ["ITEM", "CATEGORY"]}),
        ("A003_add_template", "Template renders tag references", {"TYPE": "ADD_COLUMN", "NAME": "label", "TEMPLATE": "{tag:ID(patient)}:{tag:ITEM}:{tag:RESULT}", "TAGS": ["STR"]}),
        ("A004_derive_ratio", "Derive ratio against reference high", {"TYPE": "DERIVE", "NAME": "ratio", "EXPR": "{tag:RESULT} / {tag:REF_HIGH}", "TAGS": ["RATIO", "NUM"]}),
        ("A005_derive_log", "Derive log10 result", {"TYPE": "DERIVE", "NAME": "log_result", "EXPR": "log10({tag:RESULT} + 1)", "TAGS": ["NUM"]}),
        ("A006_derive_round", "Derive rounded result", {"TYPE": "DERIVE", "NAME": "rounded_ratio", "EXPR": "round({tag:RESULT} / {tag:REF_HIGH}, 2)", "TAGS": ["RATIO", "NUM"]}),
        ("A007_date_age", "Calculate age from DOB and collection time", {"TYPE": "DATE_DERIVE", "NAME": "visit_age", "KIND": "age", "FROM": "tag:DOB", "AS_OF": "tag:COLLECTION_AT", "TAGS": ["AGE"]}),
        ("A008_tat_minutes", "Calculate TAT in minutes", {"TYPE": "DATE_DERIVE", "NAME": "tat_min", "KIND": "diff_minutes", "START": "tag:RECEIVED_AT", "END": "tag:RESULT_TIME", "TAGS": ["DURATION", "NUM"]}),
        ("A009_report_date", "Extract report date", {"TYPE": "DATE_DERIVE", "NAME": "report_date", "KIND": "part", "FROM": "tag:RESULT_TIME", "PART": "date", "TAGS": ["DATE"]}),
        ("A010_impute_constant", "Impute result blanks with constant", {"TYPE": "IMPUTE", "TAGS": ["RESULT"], "METHOD": "constant", "VALUE": "0"}),
        ("A011_impute_median", "Impute reference low by median", {"TYPE": "IMPUTE", "TAGS": ["REF_LOW"], "METHOD": "median"}),
        ("A012_impute_mode", "Impute group by mode", {"TYPE": "IMPUTE", "TAGS": ["GROUP"], "METHOD": "mode"}),
        ("A013_outlier_iqr", "IQR outlier filter", {"TYPE": "OUTLIER_FILTER", "COLUMN": "tag:RESULT", "METHOD": "iqr"}),
        ("A014_outlier_sd", "SD outlier filter", {"TYPE": "OUTLIER_FILTER", "COLUMN": "tag:REF_HIGH", "METHOD": "sd"}),
        ("A015_recode_new", "Recode department into new label", {"TYPE": "RECODE", "COLUMN": "tag:GROUP", "NAME": "dept_label", "MAP": {"IM": "내과", "GS": "외과"}, "DEFAULT": "기타", "TAGS": ["GROUP", "CATEGORY"]}),
        ("A016_recode_in_place", "Recode instrument in-place", {"TYPE": "RECODE", "COLUMN": "tag:INSTRUMENT", "MAP": {"A": "AnalyzerA", "B": "AnalyzerB"}}),
        ("A017_dedup_all", "Drop exact duplicates", {"TYPE": "DEDUP", "KEEP": "first"}),
        ("A018_dedup_patient", "Deduplicate by patient", {"TYPE": "DEDUP", "TAGS": ["ID(patient)"], "KEEP": "last"}),
        ("A019_sort_text", "Sort by patient and result time", {"TYPE": "SORT", "BY": ["tag:ID(patient)", "tag:RESULT_TIME"]}),
        ("A020_sort_numeric", "Numeric sort by reference high", {"TYPE": "SORT", "BY": ["tag:REF_HIGH"], "NUMERIC": True, "ASCENDING": False}),
        ("A021_round_result", "Round comparator-aware result", {"TYPE": "ROUND", "TAGS": ["RESULT"], "DECIMALS": 1}),
        ("A022_bin_age_edges", "Bin age with explicit edges", {"TYPE": "BIN", "COLUMN": "tag:AGE", "NAME": "age_group", "BINS": [0, 18, 65, 120], "LABELS": ["child", "adult", "older"], "TAGS": ["AGE_GROUP", "CATEGORY"]}),
        ("A023_bin_ref_width", "Bin reference high by width", {"TYPE": "BIN", "COLUMN": "tag:REF_HIGH", "NAME": "ref_band", "WIDTH": 20, "TAGS": ["CATEGORY"]}),
        ("A024_unit_convert", "Convert GLU mmol/L to mg/dL", {"TYPE": "UNIT.CONVERT", "SOURCE": "tag:RESULT", "UNIT_COLUMN": "tag:UNIT", "TEST_COLUMN": "tag:ITEM", "OUTPUT_VALUE": "converted", "OUTPUT_UNIT": "target_unit", "TARGET_UNIT_BY_TEST": {"GLU": "mg/dL"}, "DECIMALS": 1}),
        ("A025_row_eq", "Include AST rows", {"TYPE": "ROW.INCLUDE", "COLUMN": "tag:ITEM", "OP": "eq", "VALUE": "AST"}),
        ("A026_row_in", "Include selected groups", {"TYPE": "ROW.INCLUDE", "COLUMN": "tag:GROUP", "OP": "in", "VALUES": ["IM", "ER"]}),
        ("A027_row_contains", "Include patient IDs containing P", {"TYPE": "ROW.INCLUDE", "COLUMN": "tag:ID(patient)", "OP": "contains", "VALUE": "P"}),
        ("A028_row_regex", "Include sample IDs by regex", {"TYPE": "ROW.INCLUDE", "COLUMN": "tag:ID(sample)", "OP": "regex", "VALUE": r"^S[12]$"}),
        ("A029_row_between", "Include reference high in reversed bounds", {"TYPE": "ROW.INCLUDE", "COLUMN": "tag:REF_HIGH", "OP": "between", "VALUES": [50, 10]}),
        ("A030_row_date", "Include reports after date", {"TYPE": "ROW.INCLUDE", "COLUMN": "tag:RESULT_TIME", "OP": ">=", "VALUE": "2026-01-03"}),
        ("A031_column_include", "Keep core columns by tags", {"TYPE": "COLUMN.INCLUDE", "TAGS": ["ID", "ITEM", "RESULT"]}),
        ("A032_column_exclude", "Remove operational columns", {"TYPE": "COLUMN.EXCLUDE", "TAGS": ["INSTRUMENT", "GROUP"]}),
        ("A033_column_split", "Split sample identifier", {"TYPE": "COLUMN.SPLIT", "COLUMN": "tag:ID(sample)", "INTO": ["prefix", "sample_num"], "SEP": "S", "TAGS": ["STR"]}),
        ("A034_column_concat", "Make patient-sample key", {"TYPE": "COLUMN.CONCAT", "COLUMNS": ["tag:ID(patient)", "tag:ID(sample)"], "OUTPUT": "patient_sample", "SEP": "::", "TAGS": ["ID", "STR"]}),
        ("A035_pivot_wider", "Long-to-wide by item", {"TYPE": "PIVOT_WIDER", "ID_COLS": ["tag:ID(sample)"], "NAMES_FROM": "tag:ITEM", "VALUES_FROM": "tag:RESULT", "AGG": "first"}),
        ("A036_pivot_longer", "Reference bounds wide-to-long", {"TYPE": "PIVOT_LONGER", "ID_COLS": ["tag:ID(sample)", "tag:ITEM"], "COLS": ["tag:REF_LOW", "tag:REF_HIGH"], "NAMES_TO": "bound", "VALUES_TO": "limit"}),
    ]
    return [
        Scenario(id=sid, category="action_success", purpose=purpose, operation="action", tame=BASE_TAME, params=config)
        for sid, purpose, config in configs
    ]


def _action_error_scenarios() -> list[Scenario]:
    configs = [
        ("E001_add_missing_name", "ADD_COLUMN without NAME", {"TYPE": "ADD_COLUMN", "VALUE": "x"}),
        ("E002_add_duplicate", "ADD_COLUMN duplicate target", {"TYPE": "ADD_COLUMN", "NAME": "result", "VALUE": "x"}),
        ("E003_template_unknown_tag", "Template with unknown tag", {"TYPE": "ADD_COLUMN", "NAME": "x", "TEMPLATE": "{tag:UNKNOWN}"}),
        ("E004_derive_missing_expr", "DERIVE without expression", {"TYPE": "DERIVE", "NAME": "x"}),
        ("E005_derive_unsafe", "Unsafe DERIVE expression rejected", {"TYPE": "DERIVE", "NAME": "x", "EXPR": "__import__('os')"}),
        ("E006_bad_date_part", "Unsupported DATE_DERIVE part", {"TYPE": "DATE_DERIVE", "NAME": "x", "KIND": "part", "FROM": "tag:RESULT_TIME", "PART": "century"}),
        ("E007_bad_impute", "Unsupported imputation method", {"TYPE": "IMPUTE", "TAGS": ["RESULT"], "METHOD": "mystery"}),
        ("E008_bad_dedup_keep", "Unsupported DEDUP keep", {"TYPE": "DEDUP", "KEEP": "middle"}),
        ("E009_sort_no_by", "SORT without BY", {"TYPE": "SORT"}),
        ("E010_round_no_target", "ROUND without target", {"TYPE": "ROUND"}),
        ("E011_bin_no_bins", "BIN without bins or width", {"TYPE": "BIN", "COLUMN": "tag:AGE", "NAME": "x"}),
        ("E012_split_no_sep", "COLUMN.SPLIT without separator", {"TYPE": "COLUMN.SPLIT", "COLUMN": "tag:ID(sample)", "INTO": ["a", "b"]}),
        ("E013_concat_no_sources", "COLUMN.CONCAT without sources", {"TYPE": "COLUMN.CONCAT", "OUTPUT": "x"}),
        ("E014_row_no_condition", "ROW.INCLUDE without condition", {"TYPE": "ROW.INCLUDE"}),
        ("E015_bad_numeric_bound", "Bad numeric bound gives ActionError", {"TYPE": "ROW.INCLUDE", "COLUMN": "tag:RESULT", "OP": "gt", "VALUE": "not-number"}),
        ("E016_bad_regex", "Bad regex gives ActionError", {"TYPE": "ROW.INCLUDE", "COLUMN": "tag:ID(sample)", "OP": "regex", "VALUE": "["}),
        ("E017_column_include_missing", "COLUMN.INCLUDE without target", {"TYPE": "COLUMN.INCLUDE"}),
        ("E018_pivot_bad_agg", "Unsupported pivot aggregation", {"TYPE": "PIVOT_WIDER", "ID_COLS": ["tag:ID(sample)"], "NAMES_FROM": "tag:ITEM", "VALUES_FROM": "tag:RESULT", "AGG": "median"}),
        ("E019_ambiguous_id", "Ambiguous single tag selector is rejected", {"TYPE": "COLUMN.SPLIT", "COLUMN": "tag:ID", "INTO": ["a", "b"], "SEP": "S"}),
        ("E020_unit_missing_source", "UNIT.CONVERT unknown source rejected", {"TYPE": "UNIT.CONVERT", "SOURCE": "tag:UNKNOWN", "OUTPUT_VALUE": "x"}),
    ]
    return [
        Scenario(id=sid, category="action_error", purpose=purpose, operation="action_error", tame=BASE_TAME, params=config)
        for sid, purpose, config in configs
    ]


def _pipeline_scenarios() -> list[Scenario]:
    return [
        Scenario(
            id="P001_tag_only_clean_pipeline",
            category="pipeline",
            purpose="Sort, derive key, and keep core columns using only tags",
            operation="pipeline",
            tame=BASE_TAME,
            params={
                "ACTIONS": {
                    "SORT": {"TYPE": "SORT", "BY": ["tag:ID(patient)", "tag:RESULT_TIME"]},
                    "KEY": {"TYPE": "COLUMN.CONCAT", "COLUMNS": ["tag:ID(patient)", "tag:ID(sample)"], "OUTPUT": "key", "SEP": "::", "TAGS": ["ID", "STR"]},
                    "CORE": {"TYPE": "COLUMN.INCLUDE", "TAGS": ["ID", "ITEM", "RESULT"]},
                },
                "ACTION_PIPELINES": {"DEFAULT": ["SORT", "KEY", "CORE"]},
            },
        )
    ]


def _plugin_scenarios() -> list[Scenario]:
    plugin_specs = [
        ("PL001_reference_interval", BASE_TAME, "REFERENCE_INTERVAL", {}, ["test_name"], 1),
        ("PL002_abnormal_flag", BASE_TAME, "ABNORMAL_FLAG", {"MODE": "FLAG", "ALLOW_UNDECLARED_UNITS": True}, [], 0),
        ("PL003_abnormal_rate", BASE_TAME, "ABNORMAL_FLAG", {"MODE": "RATE", "ALLOW_UNDECLARED_UNITS": True}, ["abnormal_rate"], 1),
        ("PL004_autoverification", BASE_TAME, "AUTOVERIFICATION", {"CRITICAL_HIGH_BY_TEST": {"AST": 60}, "ALLOW_UNDECLARED_UNITS": True}, ["decision"], 1),
        ("PL005_chem_item_counts", BASE_TAME, "CHEMISTRY_ANALYSIS", {"MODE": "ITEM_COUNTS"}, ["검사항목명"], 1),
        ("PL006_chem_result_summary", BASE_TAME, "CHEMISTRY_ANALYSIS", {"MODE": "RESULT_SUMMARY"}, ["중앙값"], 1),
        ("PL007_chem_histogram", BASE_TAME, "CHEMISTRY_ANALYSIS", {"MODE": "RESULT_HISTOGRAM"}, ["구간"], 1),
        ("PL008_chem_instrument_bias", BASE_TAME, "CHEMISTRY_ANALYSIS", {"MODE": "INSTRUMENT_BIAS"}, ["장비명"], 1),
        ("PL009_chem_tat_test", BASE_TAME, "CHEMISTRY_ANALYSIS", {"MODE": "TAT_BY_TEST"}, ["중앙값TAT분"], 1),
        ("PL010_chem_tat_instrument", BASE_TAME, "CHEMISTRY_ANALYSIS", {"MODE": "TAT_BY_INSTRUMENT"}, ["장비명"], 1),
        ("PL011_chem_daily", BASE_TAME, "CHEMISTRY_ANALYSIS", {"MODE": "DAILY_WORKLOAD"}, ["날짜"], 1),
        ("PL012_chem_hourly", BASE_TAME, "CHEMISTRY_ANALYSIS", {"MODE": "HOURLY_WORKLOAD"}, ["시간"], 1),
        ("PL013_chem_age_sex", BASE_TAME, "CHEMISTRY_ANALYSIS", {"MODE": "AGE_SEX_RESULT"}, ["연령그룹"], 1),
        ("PL014_chem_outliers", BASE_TAME, "CHEMISTRY_ANALYSIS", {"MODE": "OUTLIERS_IQR"}, ["이상치수"], 1),
        ("PL015_chem_delta", BASE_TAME, "CHEMISTRY_ANALYSIS", {"MODE": "DELTA_CHECK"}, ["절대변화"], 1),
        ("PL016_chem_missing", BASE_TAME, "CHEMISTRY_ANALYSIS", {"MODE": "MISSING_QUALITY"}, ["비정상셀수"], 1),
        ("PL017_method_comparison", METHOD_TAME, "METHOD_COMPARISON", {"METHOD_A": "A", "METHOD_B": "B"}, ["pb_slope"], 1),
        ("PL018_group_test", BASE_TAME, "GROUP_TEST", {"GROUP": "tag:INSTRUMENT"}, ["method"], 1),
        ("PL019_correlation", BASE_TAME, "CORRELATION", {}, [], 0),
        ("PL020_qc_precision", BASE_TAME, "QC_ANALYSIS", {"MODE": "PRECISION"}, ["cv_percent"], 1),
        ("PL021_qc_westgard", BASE_TAME, "QC_ANALYSIS", {"MODE": "WESTGARD"}, ["violation"], 1),
        ("PL022_roc_summary", ROC_TAME, "ROC_ANALYSIS", {"POSITIVE": "1"}, ["auc"], 1),
        ("PL023_roc_curve", ROC_TAME, "ROC_ANALYSIS", {"MODE": "CURVE", "POSITIVE": "1"}, ["sensitivity"], 1),
        ("PL024_result_trend", BASE_TAME, "RESULT_TREND", {"PERIOD": "D"}, ["moving_avg"], 1),
        ("PL025_wide_chem_summary", WIDE_RESULT_TAME, "CHEMISTRY_ANALYSIS", {"MODE": "RESULT_SUMMARY"}, ["원본결과컬럼"], 2),
        ("PL026_wide_abnormal_flag", WIDE_RESULT_TAME, "ABNORMAL_FLAG", {"MODE": "FLAG"}, [], 0),
        ("PL027_wide_abnormal_rate", WIDE_RESULT_TAME, "ABNORMAL_FLAG", {"MODE": "RATE"}, ["abnormal_rate"], 2),
        ("PL028_wide_autoverification", WIDE_RESULT_TAME, "AUTOVERIFICATION", {"CRITICAL_HIGH_BY_TEST": {"AST": 60}}, ["source_result_column"], 4),
        ("PL029_wide_qc", WIDE_RESULT_TAME, "QC_ANALYSIS", {"MODE": "PRECISION"}, ["source_result_column"], 2),
        ("PL030_wide_trend", WIDE_RESULT_TAME, "RESULT_TREND", {"PERIOD": "M"}, ["source_result_column"], 2),
    ]
    return [
        Scenario(
            id=sid,
            category="plugin",
            purpose=f"Run {plugin} with options {options}",
            operation="plugin",
            tame=tame,
            params={"PLUGIN": plugin, "OPTIONS": options, "EXPECT_ERROR": sid == "PL021_qc_westgard"},
            expect={"columns": columns, "rows_min": rows_min},
        )
        for sid, tame, plugin, options, columns, rows_min in plugin_specs
    ]


def _single_value_tame(tags: tuple[str, ...], value: str) -> str:
    return f"<DATA>\n[[{'::'.join(tags)}]]value\n{value}\n</DATA>"


def _with_action(dataset, name: str, config: dict[str, Any]):
    meta = dict(dataset.meta)
    meta["ACTIONS"] = {name: config}
    return dataset.replace(meta=meta)


def _write_tame_text(tmp_path: Path, scenario_id: str, text: str, suffix: str = ".tame") -> Path:
    safe_id = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in scenario_id)
    path = tmp_path / f"{safe_id}{suffix}"
    path.write_text(text, encoding="utf-8")
    return path


def _check_output_dataset(dataset, scenario: Scenario) -> None:
    if dataset is None:
        raise AssertionError(f"{scenario.id}: expected output dataset")
    if list(dataset.df.columns) != [column.name for column in dataset.columns]:
        raise AssertionError(f"{scenario.id}: output column specs do not match DataFrame")
    validate_dataset(dataset)
    describe_dataset(dataset)


def _expect_error(fn, errors: tuple[type[BaseException], ...]) -> None:
    try:
        fn()
    except errors as exc:
        if not str(exc).strip():
            raise AssertionError("Expected clean error message")
        return
    raise AssertionError(f"Expected one of {[error.__name__ for error in errors]}")

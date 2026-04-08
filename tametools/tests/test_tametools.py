from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile
import textwrap
import unittest

import pandas as pd
import openpyxl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tametools.analysis import describe_dataset, exploratory_data_analysis, reference_interval_plan, validate_dataset
from tametools.cellstate import NULL, cell_state
from tametools.command_pipelines import CommandPipelineError, available_command_pipelines, execute_command_pipeline
from tametools.exporters import export_dataset
from tametools.images import embed_image_columns, extract_image_columns
from tametools.io import import_xlsx_into_tame, read_tame, read_xlsx, write_data_tame, write_meta_tame, write_tame, write_xlsx
from tametools.merge import merge_datasets
from tametools.pipeline import execute_work
from tametools.plugins import list_plugins, run_plugin
from tametools.tags import build_header, parse_header
from tametools.transforms import anonymize_dataset, harmonize_comparator_thresholds, sample_dataset, split_comparator_columns, write_mapping_tables


SAMPLE_TAME = textwrap.dedent(
    """
    <META>
    [INFO]
    DESCRIPTION = "sample"

    [TAGS]
    "진료과" = ["BY"]

    [SETTINGS]
    VALIDATE_ERROR = "DELETE"
    CRR = "VALUE"

    [WORKS]
    DEFAULT = ["VALIDATE", "DESCRIBE"]
    RI = ["VALIDATE", "REFERENCE_INTERVAL"]
    </META>
    <DATA>
    [[ID::STR]]등록번호\t[[GENDER]]성별\t[[AGE]]나이\t[[ITEM]]검사항목명\t[[RESULT::<NUM>]]보고값\t진료과
    A0001\tF\t32\tAST\t25\tIM
    A0002\t남\t2m\tAST\t<3\tGS
    A0003\tX\tbad\tALT\tpositive\tIM
    </DATA>
    """
).strip()

SAMPLE_ANON_TAME = textwrap.dedent(
    """
    <META>
    [SETTINGS]
    VALIDATE_ERROR = "REPORT"
    </META>
    <DATA>
    [[HOSPITAL_ID]]병원ID\t[[NAME]]이름\t[[ID::STR]]등록번호\t[[RESULT::<NUM>]]보고값
    H01\t홍길동\tP001\t<30
    H01\t김영희\tP002\t25
    </DATA>
    """
).strip()

SAMPLE_NUM_TAME = textwrap.dedent(
    """
    <META>
    [SETTINGS]
    VALIDATE_ERROR = "REPORT"
    </META>
    <DATA>
    [[HOSPITAL_ID]]병원ID\t[[ITEM]]검사항목명\t[[RESULT::NUM]]보고값
    A\tAST\t30
    A\tALT\t40
    </DATA>
    """
).strip()

SAMPLE_CNUM_TAME = textwrap.dedent(
    """
    <META>
    [SETTINGS]
    VALIDATE_ERROR = "REPORT"
    CRR = "VALUE"
    </META>
    <DATA>
    [[HOSPITAL_ID]]병원ID\t[[ITEM]]검사항목명\t[[RESULT::<NUM>]]보고값
    B\tAST\t<30
    B\tAST\t<25
    B\tALT\t35
    </DATA>
    """
).strip()

SAMPLE_HARMONIZE_TAME = textwrap.dedent(
    """
    <META>
    [SETTINGS]
    VALIDATE_ERROR = "REPORT"
    CRR = "HARMONIZE"
    </META>
    <DATA>
    [[ITEM]]검사항목명\t[[RESULT::<NUM>]]보고값
    AST\t<30
    AST\t<20
    AST\t22
    AST\t18
    ALT\t35
    </DATA>
    """
).strip()

SAMPLE_RI_TAME = textwrap.dedent(
    """
    <META>
    [SETTINGS]
    VALIDATE_ERROR = "REPORT"
    CRR = "VALUE"

    [WORKS]
    RI = ["REFERENCE_INTERVAL"]
    </META>
    <DATA>
    [[TESTNAME]]검사항목명\t[[GENDER]]성별\t[[AGE]]나이\t[[RESULT::<NUM>]]보고값
    AST\tM\t5\t10
    AST\tF\t15\t12
    AST\tM\t25\t<30
    AST\tF\t72\t20
    ALT\tM\t35\t30
    ALT\tF\t82\t40
    </DATA>
    """
).strip()

SAMPLE_RI_MISSING_TAGS_TAME = textwrap.dedent(
    """
    <META>
    [SETTINGS]
    VALIDATE_ERROR = "REPORT"
    CRR = "VALUE"
    </META>
    <DATA>
    [[TESTNAME]]검사항목명\t[[RESULT::NUM]]보고값
    AST\t10
    AST\t12
    ALT\t20
    </DATA>
    """
).strip()

SAMPLE_VALUE_STATES_TAME = textwrap.dedent(
    """
    <META>
    [SETTINGS]
    VALIDATE_ERROR = "REPORT"
    </META>
    <DATA>
    [[COMMENT::STR::NULLABLE::EMPTY_OK::WS_OK]]비고\t[[CODE::STR::REQUIRED]]코드\t[[NOTE::STR]]메모\t[[RESULT::NUM::NULLABLE]]수치
    <<ABSENT>>\tA01\t정상\t1
    <<NULL>>\tA02\t<<NULL>>\t<<NULL>>
    <<EMPTY>>\tA03\t<<EMPTY>>\t2
    <<WS:3>>\tA04\t<<WS:2>>\t3
    \\<<NULL>>\tA05\t\\<<EMPTY>>\t4
    </DATA>
    """
).strip()

SAMPLE_IMAGE_DATA_URL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7Z6gAAAABJRU5ErkJggg=="

SAMPLE_IMAGE_TAME = textwrap.dedent(
    f"""
    <META>
    [SETTINGS]
    VALIDATE_ERROR = "REPORT"
    </META>
    <DATA>
    [[ID::STR]]등록번호\t[[IMAGE::B64]]썸네일\t[[LABEL::STR]]라벨
    P001\t{SAMPLE_IMAGE_DATA_URL}\t정상
    </DATA>
    """
).strip()

SAMPLE_FUNCTION_TAME = textwrap.dedent(
    """
    <META>
    [WORKS]
    DEFAULT = ["TRIM_RESULT"]

    [TRIM_RESULT]
    FUNCTION = "trim_result"

    [FUNCTIONS.trim_result]
    LANG = "python"
    ENTRY = "run"
    SOURCE = '''
def run(dataset, options, api):
    result_column = dataset.first_column_with_tag("RESULT")
    frame = dataset.df.copy()
    frame[result_column.name] = frame[result_column.name].map(
        lambda value: value.strip() if isinstance(value, str) else value
    )
    return dataset.replace(df=frame)
'''
    </META>
    <DATA>
    [[ID::STR]]등록번호\t[[RESULT::STR]]보고값
    P001\t  alpha  
    P002\tbeta
    </DATA>
    """
).strip()

SAMPLE_R_FUNCTION_TAME = textwrap.dedent(
    """
    <META>
    [WORKS]
    DEFAULT = ["TRIM_RESULT_R"]

    [TRIM_RESULT_R]
    FUNCTION = "trim_result_r"

    [FUNCTIONS.trim_result_r]
    LANG = "r"
    ENTRY = "run"
    SOURCE = '''
run <- function(input_path, output_path) {
  df <- read.delim(input_path, check.names = FALSE, quote = "", comment.char = "", stringsAsFactors = FALSE)
  result_column <- grep("보고값$", names(df), value = TRUE)[1]
  df[[result_column]] <- trimws(df[[result_column]])
  write.table(df, output_path, sep = "\\t", row.names = FALSE, quote = FALSE, fileEncoding = "UTF-8")
  "trimmed by R"
}
'''
    </META>
    <DATA>
    [[ID::STR]]등록번호\t[[RESULT::STR]]보고값
    P001\t  gamma  
    P002\tdelta
    </DATA>
    """
).strip()


class TagParsingTest(unittest.TestCase):
    def test_parse_and_build_header(self) -> None:
        name, tags = parse_header("[[RESULT::<NUM>]]보고값")
        self.assertEqual(name, "보고값")
        self.assertEqual(tags, ("RESULT", "<NUM>"))
        self.assertEqual(build_header(name, tags), "[[RESULT::<NUM>]]보고값")

    def test_parse_legacy_header_and_build_canonical_header(self) -> None:
        header = "_AGE_age\\_years"
        name, tags = parse_header(header)
        self.assertEqual(name, "age_years")
        self.assertEqual(tags, ("AGE",))
        self.assertEqual(build_header(name, tags), "[[AGE]]age_years")

    def test_parse_canonical_header_with_underscore_tag(self) -> None:
        header = "[[HOSPITAL_ID]]병원ID"
        name, tags = parse_header(header)
        self.assertEqual(name, "병원ID")
        self.assertEqual(tags, ("HOSPITAL_ID",))


class TameReadTest(unittest.TestCase):
    def test_read_tame_merges_tags(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sample.tame"
            path.write_text(SAMPLE_TAME, encoding="utf-8")
            dataset = read_tame(path)

        self.assertEqual(dataset.columns[0].name, "등록번호")
        self.assertIn("ID", dataset.columns[0].tags)
        self.assertIn("BY", dataset.first_column_with_tag("BY").tags)
        self.assertEqual(dataset.first_column_with_tag("RESULT").name, "보고값")

    def test_write_tame_canonicalizes_legacy_headers(self) -> None:
        legacy_tame = textwrap.dedent(
            """
            <META>
            </META>
            <DATA>
            _HOSPITAL_ID_병원ID\t_RESULT::<NUM>_보고값
            H01\t<30
            </DATA>
            """
        ).strip()
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "legacy.tame"
            output_path = Path(tmpdir) / "canonical.tame"
            input_path.write_text(legacy_tame, encoding="utf-8")
            dataset = read_tame(input_path)
            write_tame(output_path, dataset)
            written = output_path.read_text(encoding="utf-8")

        self.assertIn("[[HOSPITAL_ID]]병원ID", written)
        self.assertIn("[[RESULT::<NUM>]]보고값", written)
        self.assertNotIn("_HOSPITAL_ID_병원ID", written)

    def test_read_tame_distinguishes_absent_null_empty_and_ws(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "states.tame"
            path.write_text(SAMPLE_VALUE_STATES_TAME, encoding="utf-8")
            dataset = read_tame(path)

        self.assertIsNone(dataset.df.loc[0, "비고"])
        self.assertIs(dataset.df.loc[1, "비고"], NULL)
        self.assertEqual(dataset.df.loc[2, "비고"], "")
        self.assertEqual(dataset.df.loc[3, "비고"], "   ")
        self.assertEqual(dataset.df.loc[4, "비고"], "<<NULL>>")
        self.assertEqual(dataset.df.loc[4, "메모"], "<<EMPTY>>")
        self.assertEqual(cell_state(dataset.df.loc[0, "비고"]), "ABSENT")
        self.assertEqual(cell_state(dataset.df.loc[1, "비고"]), "NULL")
        self.assertEqual(cell_state(dataset.df.loc[2, "비고"]), "EMPTY")
        self.assertEqual(cell_state(dataset.df.loc[3, "비고"]), "WS")


class AnalysisTest(unittest.TestCase):
    def test_validate_and_describe(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sample.tame"
            path.write_text(SAMPLE_TAME, encoding="utf-8")
            dataset = read_tame(path)

        validation = validate_dataset(dataset)
        self.assertEqual(len(validation.issues), 3)
        self.assertEqual(len(validation.cleaned_dataset.df), 2)

        described = describe_dataset(validation.cleaned_dataset)
        result_row = described.loc[described["column"] == "보고값"].iloc[0]
        age_row = described.loc[described["column"] == "나이"].iloc[0]
        self.assertEqual(result_row["kind"], "numeric")
        self.assertEqual(int(result_row["count"]), 2)
        self.assertEqual(age_row["kind"], "numeric")

    def test_reference_interval_plan_finds_tagged_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sample.tame"
            path.write_text(SAMPLE_TAME, encoding="utf-8")
            dataset = read_tame(path)

        plan = reference_interval_plan(dataset)
        self.assertEqual(plan.item_column.name, "검사항목명")
        self.assertEqual(plan.gender_column.name, "성별")
        self.assertEqual(plan.age_column.name, "나이")
        self.assertEqual(plan.by_columns[0].name, "진료과")

    def test_execute_work_uses_meta_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sample.tame"
            path.write_text(SAMPLE_TAME, encoding="utf-8")
            dataset = read_tame(path)

        result = execute_work(dataset, "DEFAULT")
        self.assertEqual(result.work_name, "DEFAULT")
        self.assertEqual(len(result.outputs), 2)
        self.assertEqual(result.outputs[0].name, "VALIDATE")
        self.assertEqual(result.outputs[1].name, "DESCRIBE")
        self.assertEqual(len(result.final_dataset.df), 2)

    def test_eda_reports_comparator_profile_and_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cnum.tame"
            path.write_text(SAMPLE_CNUM_TAME, encoding="utf-8")
            dataset = read_tame(path)

        report = exploratory_data_analysis(dataset, comparator_policy="VALUE")
        self.assertFalse(report.summary.empty)
        self.assertFalse(report.comparator_profile.empty)
        self.assertTrue(any(raw in set(report.comparator_profile["raw_value"]) for raw in ["<30", "<25"]))
        self.assertTrue(any("Multiple comparator thresholds detected" in warning for warning in report.warnings))
        impact = report.comparator_policy_impact.iloc[0]
        self.assertEqual(impact["policy"], "VALUE")
        self.assertGreaterEqual(int(impact["numeric_used_under_policy"]), 3)
        self.assertFalse(report.harmonization_preview.empty)

    def test_eda_harmonize_policy_updates_impact(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "harmonize.tame"
            path.write_text(SAMPLE_HARMONIZE_TAME, encoding="utf-8")
            dataset = read_tame(path)

        report = exploratory_data_analysis(dataset, comparator_policy="HARMONIZE")
        impact = report.comparator_policy_impact.loc[report.comparator_policy_impact["result_column"] == "보고값"].iloc[0]
        self.assertEqual(impact["policy"], "HARMONIZE")
        self.assertGreaterEqual(int(impact["harmonized_rows"]), 3)

    def test_validate_recognizes_cell_state_policy_tags(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "states.tame"
            path.write_text(SAMPLE_VALUE_STATES_TAME, encoding="utf-8")
            dataset = read_tame(path)

        validation = validate_dataset(dataset)
        issues = {(issue.row_number, issue.column, issue.tag) for issue in validation.issues}
        self.assertIn((3, "메모", "NULL"), issues)
        self.assertIn((4, "메모", "EMPTY"), issues)
        self.assertIn((5, "메모", "WS"), issues)
        self.assertNotIn((3, "비고", "NULL"), issues)
        self.assertNotIn((4, "비고", "EMPTY"), issues)
        self.assertNotIn((5, "비고", "WS"), issues)

        described = describe_dataset(dataset)
        row = described.loc[described["column"] == "비고"].iloc[0]
        self.assertEqual(int(row["absent_cells"]), 1)
        self.assertEqual(int(row["null_cells"]), 1)
        self.assertEqual(int(row["empty_cells"]), 1)
        self.assertEqual(int(row["ws_cells"]), 1)


class PluginTest(unittest.TestCase):
    def test_list_plugins_includes_reference_interval(self) -> None:
        plugins = {plugin.name for plugin in list_plugins()}
        self.assertIn("REFERENCE_INTERVAL", plugins)
        self.assertIn("RI", plugins)

    def test_reference_interval_plugin_outputs_chainable_tame_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "ri_input.tame"
            output_path = Path(tmpdir) / "ri_output.tame"
            input_path.write_text(SAMPLE_RI_TAME, encoding="utf-8")
            dataset = read_tame(input_path)

            output = run_plugin(dataset, "REFERENCE_INTERVAL", dataset.meta, "REFERENCE_INTERVAL", {})
            self.assertIsNotNone(output)
            self.assertIsNotNone(output.dataset)
            result_dataset = output.dataset
            write_tame(output_path, result_dataset)
            reloaded = read_tame(output_path)

        self.assertTrue(any(column.has_tag("TESTNAME") for column in reloaded.columns))
        self.assertTrue(any(column.has_tag("GENDER_GROUP") for column in reloaded.columns))
        self.assertTrue(any(column.has_tag("AGE_GROUP") for column in reloaded.columns))
        self.assertEqual(set(reloaded.df["그룹수준"]), {"TESTNAME", "TESTNAME+GENDER", "TESTNAME+AGE", "TESTNAME+GENDER+AGE"})
        self.assertIn("70+", set(reloaded.df["연령그룹"]))
        validation = validate_dataset(reloaded)
        self.assertEqual(validation.issues, [])

    def test_reference_interval_plugin_warns_when_gender_age_tags_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "ri_missing.tame"
            input_path.write_text(SAMPLE_RI_MISSING_TAGS_TAME, encoding="utf-8")
            dataset = read_tame(input_path)

        output = run_plugin(dataset, "REFERENCE_INTERVAL", dataset.meta, "REFERENCE_INTERVAL", {})
        self.assertIsNotNone(output)
        self.assertIsNotNone(output.dataset)
        self.assertTrue(any("Missing GENDER tag" in warning for warning in output.warnings or []))
        self.assertTrue(any("Missing AGE tag" in warning for warning in output.warnings or []))
        self.assertEqual(set(output.dataset.df["그룹수준"]), {"TESTNAME"})

    def test_execute_work_runs_reference_interval_plugin(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "ri_input.tame"
            input_path.write_text(SAMPLE_RI_TAME, encoding="utf-8")
            dataset = read_tame(input_path)

        result = execute_work(dataset, "RI")
        self.assertEqual(result.outputs[0].name, "REFERENCE_INTERVAL")
        self.assertIn("참고치하한", list(result.final_dataset.df.columns))
        self.assertIn("참고치상한", list(result.final_dataset.df.columns))


class TransformTest(unittest.TestCase):
    def test_anonymize_dataset_hashes_ids_and_drops_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "anon.tame"
            path.write_text(SAMPLE_ANON_TAME, encoding="utf-8")
            dataset = read_tame(path)

        transformed, mappings = anonymize_dataset(dataset)
        self.assertIn("병원ID", mappings)
        self.assertIn("등록번호", mappings)
        self.assertNotIn("이름", list(transformed.df.columns))
        self.assertTrue(str(transformed.df.loc[0, "병원ID"]).startswith("anon_"))
        self.assertTrue(str(transformed.df.loc[0, "등록번호"]).startswith("anon_"))

    def test_write_mapping_tables_saves_manifest_and_csv_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "anon.tame"
            mapping_dir = Path(tmpdir) / "mappings"
            path.write_text(SAMPLE_ANON_TAME, encoding="utf-8")
            dataset = read_tame(path)

            _, mappings = anonymize_dataset(dataset)
            manifest = write_mapping_tables(mappings, mapping_dir)
            manifest_frame = pd.read_csv(mapping_dir / "manifest.csv", dtype={"rows": "int64"})
            hospital_mapping = pd.read_csv(mapping_dir / "병원ID.mapping.csv", dtype=str)

        self.assertEqual(set(manifest["column"]), {"병원ID", "등록번호"})
        self.assertEqual(set(manifest_frame["column"]), {"병원ID", "등록번호"})
        self.assertIn("anonymized", list(hospital_mapping.columns))
        self.assertTrue(str(hospital_mapping.loc[0, "anonymized"]).startswith("anon_"))

    def test_split_comparator_columns_creates_cmp_and_num_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "anon.tame"
            path.write_text(SAMPLE_ANON_TAME, encoding="utf-8")
            dataset = read_tame(path)

        transformed = split_comparator_columns(dataset, target_columns=["보고값"])
        self.assertIn("보고값__cmp", list(transformed.df.columns))
        self.assertIn("보고값__num", list(transformed.df.columns))
        self.assertEqual(transformed.df.loc[0, "보고값__cmp"], "LT")
        self.assertEqual(float(transformed.df.loc[0, "보고값__num"]), 30.0)
        self.assertEqual(transformed.df.loc[1, "보고값__cmp"], "EQ")

    def test_harmonize_comparator_thresholds_unifies_and_rewrites_band(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "harmonize.tame"
            path.write_text(SAMPLE_HARMONIZE_TAME, encoding="utf-8")
            dataset = read_tame(path)

        transformed, preview = harmonize_comparator_thresholds(dataset)
        self.assertFalse(preview.empty)
        self.assertEqual(transformed.df.loc[0, "보고값"], "<30")
        self.assertEqual(transformed.df.loc[1, "보고값"], "<30")
        self.assertEqual(transformed.df.loc[2, "보고값"], "<30")
        self.assertEqual(transformed.df.loc[3, "보고값"], "18")

    def test_sample_dataset_is_reproducible_with_seed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sample.tame"
            path.write_text(SAMPLE_RI_TAME, encoding="utf-8")
            dataset = read_tame(path)

        sampled_a = sample_dataset(dataset, rows=2, seed=17)
        sampled_b = sample_dataset(dataset, rows=2, seed=17)
        self.assertEqual(len(sampled_a.df), 2)
        self.assertTrue(sampled_a.df.equals(sampled_b.df))
        self.assertIsNotNone(sampled_a.first_column_with_tag("RESULT"))

    def test_sample_dataset_can_sample_per_group_by_tag(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sample.tame"
            path.write_text(SAMPLE_RI_TAME, encoding="utf-8")
            dataset = read_tame(path)

        sampled = sample_dataset(dataset, rows=1, seed=5, by_tags=["TESTNAME"])
        self.assertEqual(len(sampled.df), 2)
        self.assertEqual(set(sampled.df["검사항목명"]), {"AST", "ALT"})

    def test_execute_work_runs_sample_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sample.tame"
            path.write_text(SAMPLE_RI_TAME, encoding="utf-8")
            dataset = read_tame(path)

        meta = dict(dataset.meta)
        meta["WORKS"] = {"DEFAULT": ["SAMPLE"]}
        meta["SAMPLE"] = {"ROWS": 1, "BY_TAGS": ["TESTNAME"], "SEED": 3}
        result = execute_work(dataset.replace(meta=meta), "DEFAULT")
        self.assertEqual(result.outputs[0].name, "SAMPLE")
        self.assertEqual(len(result.final_dataset.df), 2)
        self.assertEqual(set(result.final_dataset.df["검사항목명"]), {"AST", "ALT"})


class XlsxRoundTripTest(unittest.TestCase):
    def test_write_and_read_xlsx(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tame_path = Path(tmpdir) / "sample.tame"
            xlsx_path = Path(tmpdir) / "sample.xlsx"
            roundtrip_path = Path(tmpdir) / "roundtrip.tame"
            tame_path.write_text(SAMPLE_TAME, encoding="utf-8")

            dataset = read_tame(tame_path)
            write_xlsx(xlsx_path, dataset)
            loaded = read_xlsx(xlsx_path)
            write_tame(roundtrip_path, loaded)

            self.assertEqual(list(dataset.df.columns), list(loaded.df.columns))
            self.assertEqual(len(loaded.df), 3)
            self.assertTrue(roundtrip_path.exists())

    def test_state_tokens_roundtrip_in_tame_xlsx_and_pandas_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tame_path = Path(tmpdir) / "states.tame"
            tame_roundtrip = Path(tmpdir) / "states_roundtrip.tame"
            xlsx_path = Path(tmpdir) / "states.xlsx"
            tame_path.write_text(SAMPLE_VALUE_STATES_TAME, encoding="utf-8")

            dataset = read_tame(tame_path)
            pandas_copy = pd.DataFrame(dataset.df.copy(deep=True), columns=dataset.df.columns, dtype=object)
            pandas_dataset = dataset.replace(df=pandas_copy)

            write_tame(tame_roundtrip, pandas_dataset)
            tame_loaded = read_tame(tame_roundtrip)

            write_xlsx(xlsx_path, pandas_dataset)
            xlsx_loaded = read_xlsx(xlsx_path)

        for loaded in (tame_loaded, xlsx_loaded):
            self.assertIsNone(loaded.df.loc[0, "비고"])
            self.assertIs(loaded.df.loc[1, "비고"], NULL)
            self.assertEqual(loaded.df.loc[2, "비고"], "")
            self.assertEqual(loaded.df.loc[3, "비고"], "   ")
            self.assertEqual(loaded.df.loc[4, "비고"], "<<NULL>>")
            self.assertEqual(loaded.df.loc[4, "메모"], "<<EMPTY>>")
            self.assertEqual(cell_state(loaded.df.loc[0, "비고"]), "ABSENT")
            self.assertEqual(cell_state(loaded.df.loc[1, "비고"]), "NULL")

    def test_read_xlsx_multisheet_flattens_and_write_xlsx_restores_sheets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            xlsx_path = Path(tmpdir) / "multisheet.xlsx"
            roundtrip_path = Path(tmpdir) / "multisheet_roundtrip.xlsx"

            workbook = openpyxl.Workbook()
            ws1 = workbook.active
            ws1.title = "Chemistry"
            ws1.append(["[[GENDER]]성별", "[[AGE]]나이", "[[RESULT::NUM]]보고값"])
            ws1.append(["M", "25", "30"])
            ws2 = workbook.create_sheet("Hematology")
            ws2.append(["[[GENDER]]gender", "[[AGE]]ageYears", "[[RESULT::NUM]]resultValue"])
            ws2.append(["F", "41", "12"])
            workbook.save(xlsx_path)

            dataset = read_xlsx(xlsx_path)
            write_xlsx(roundtrip_path, dataset)
            reloaded = read_xlsx(roundtrip_path)
            roundtrip_workbook = openpyxl.load_workbook(roundtrip_path, data_only=True)

        self.assertTrue(any(column.has_tag("SHEET") for column in dataset.columns))
        self.assertIn("시트명", list(dataset.df.columns))
        self.assertIn("GENDER", list(dataset.df.columns))
        self.assertIn("AGE", list(dataset.df.columns))
        self.assertIn("RESULT", list(dataset.df.columns))
        self.assertEqual(set(dataset.df["시트명"]), {"Chemistry", "Hematology"})
        self.assertIn("Chemistry", roundtrip_workbook.sheetnames)
        self.assertIn("Hematology", roundtrip_workbook.sheetnames)
        self.assertEqual(set(reloaded.df["시트명"]), {"Chemistry", "Hematology"})


class MergeTest(unittest.TestCase):
    def test_merge_warns_on_num_conflict_and_promotes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            num_path = Path(tmpdir) / "num.tame"
            cnum_path = Path(tmpdir) / "cnum.tame"
            num_path.write_text(SAMPLE_NUM_TAME, encoding="utf-8")
            cnum_path.write_text(SAMPLE_CNUM_TAME, encoding="utf-8")
            num_dataset = read_tame(num_path)
            cnum_dataset = read_tame(cnum_path)

        result = merge_datasets([num_dataset, cnum_dataset], numeric_conflict="promote")
        result_column = result.dataset.first_column_with_tag("RESULT")
        self.assertIsNotNone(result_column)
        self.assertIn("<NUM>", result_column.tags)
        self.assertTrue(any("NUM_CONFLICT" in warning for warning in result.warnings))
        self.assertIn("source_dataset", list(result.dataset.df.columns))

    def test_merge_split_adds_comparator_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            num_path = Path(tmpdir) / "num.tame"
            cnum_path = Path(tmpdir) / "cnum.tame"
            num_path.write_text(SAMPLE_NUM_TAME, encoding="utf-8")
            cnum_path.write_text(SAMPLE_CNUM_TAME, encoding="utf-8")
            num_dataset = read_tame(num_path)
            cnum_dataset = read_tame(cnum_path)

        result = merge_datasets([num_dataset, cnum_dataset], numeric_conflict="split")
        self.assertIn("보고값__cmp", list(result.dataset.df.columns))
        self.assertIn("보고값__num", list(result.dataset.df.columns))


class ExportTest(unittest.TestCase):
    def test_export_csv_preserves_serialized_states_and_pandas_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "states.tame"
            output_path = Path(tmpdir) / "states.csv"
            input_path.write_text(SAMPLE_VALUE_STATES_TAME, encoding="utf-8")
            dataset = read_tame(input_path)

            result = export_dataset(dataset, output_path)
            exported = pd.read_csv(output_path, dtype=str, keep_default_na=False)

        self.assertEqual(result.format, "csv")
        self.assertEqual(list(exported.columns), ["비고", "코드", "메모", "수치"])
        self.assertEqual(exported.loc[0, "비고"], "<<ABSENT>>")
        self.assertEqual(exported.loc[1, "비고"], "<<NULL>>")
        self.assertEqual(exported.loc[2, "비고"], "<<EMPTY>>")
        self.assertEqual(exported.loc[3, "비고"], "<<WS:3>>")
        self.assertEqual(exported.loc[4, "비고"], "\\<<NULL>>")
        self.assertEqual(exported.loc[4, "메모"], "\\<<EMPTY>>")

    def test_export_r_bundle_writes_r_loader_and_sidecars(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "sample.tame"
            bundle_path = Path(tmpdir) / "sample_bundle"
            input_path.write_text(SAMPLE_TAME, encoding="utf-8")
            dataset = read_tame(input_path)

            result = export_dataset(dataset, bundle_path, format="r_bundle")
            data_frame = pd.read_csv(bundle_path / "data.csv", dtype=str, keep_default_na=False)
            columns_frame = pd.read_csv(bundle_path / "columns.csv", dtype=str, keep_default_na=False)
            loader_text = (bundle_path / "load_tame_bundle.R").read_text(encoding="utf-8")
            readme_text = (bundle_path / "README_R.md").read_text(encoding="utf-8")
            manifest_text = (bundle_path / "bundle.json").read_text(encoding="utf-8")
            data_exists = (bundle_path / "data.csv").exists()
            columns_exists = (bundle_path / "columns.csv").exists()
            meta_exists = (bundle_path / "meta.toml").exists()

        self.assertEqual(result.format, "r_bundle")
        self.assertTrue(data_exists)
        self.assertTrue(columns_exists)
        self.assertTrue(meta_exists)
        self.assertIn("read_tame_bundle <- function", loader_text)
        self.assertIn("RESULT::<NUM>", "".join(columns_frame["tags"].tolist()))
        self.assertEqual(data_frame.loc[1, "보고값"], "<3")
        self.assertIn("\"data_format\": \"csv\"", manifest_text)
        self.assertIn("split-comparator", readme_text)

    def test_export_sql_writes_data_columns_and_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "sample.tame"
            output_path = Path(tmpdir) / "sample_export.sql"
            input_path.write_text(SAMPLE_TAME, encoding="utf-8")
            dataset = read_tame(input_path)

            result = export_dataset(dataset, output_path, format="sql")
            sql_text = output_path.read_text(encoding="utf-8")

        self.assertEqual(result.format, "sql")
        self.assertIn('CREATE TABLE "sample_export_data"', sql_text)
        self.assertIn('CREATE TABLE "sample_export_columns"', sql_text)
        self.assertIn('CREATE TABLE "sample_export_sections"', sql_text)
        self.assertIn("INSERT INTO \"sample_export_sections\" VALUES ('META'", sql_text)
        self.assertIn("보고값", sql_text)
        self.assertIn("<3", sql_text)

    def test_merge_harmonize_rewrites_threshold_band(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            p1 = Path(tmpdir) / "hospital_a.tame"
            p2 = Path(tmpdir) / "hospital_b.tame"
            p1.write_text(
                textwrap.dedent(
                    """
                    <META>
                    </META>
                    <DATA>
                    [[ITEM]]검사항목명\t[[RESULT::<NUM>]]보고값
                    AST\t<30
                    AST\t29
                    </DATA>
                    """
                ).strip(),
                encoding="utf-8",
            )
            p2.write_text(
                textwrap.dedent(
                    """
                    <META>
                    </META>
                    <DATA>
                    [[ITEM]]검사항목명\t[[RESULT::<NUM>]]보고값
                    AST\t<20
                    AST\t18
                    </DATA>
                    """
                ).strip(),
                encoding="utf-8",
            )
            d1 = read_tame(p1)
            d2 = read_tame(p2)

        result = merge_datasets([d1, d2], numeric_conflict="harmonize")
        ast_values = result.dataset.df.loc[result.dataset.df["검사항목명"] == "AST", "보고값"].tolist()
        self.assertIn("<30", ast_values)
        self.assertEqual(ast_values.count("<30"), 3)
        self.assertTrue(any("HARMONIZE_APPLIED" in warning for warning in result.warnings))

    def test_merge_uses_tag_name_when_column_names_differ(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            p1 = Path(tmpdir) / "a.tame"
            p2 = Path(tmpdir) / "b.tame"
            p1.write_text(
                textwrap.dedent(
                    """
                    <META></META>
                    <DATA>
                    [[GENDER]]성별\t[[AGE]]나이\t[[RESULT::NUM]]보고값
                    M\t20\t10
                    </DATA>
                    """
                ).strip(),
                encoding="utf-8",
            )
            p2.write_text(
                textwrap.dedent(
                    """
                    <META></META>
                    <DATA>
                    [[GENDER]]gender\t[[AGE]]ageYears\t[[RESULT::NUM]]resultValue
                    F\t30\t20
                    </DATA>
                    """
                ).strip(),
                encoding="utf-8",
            )
            d1 = read_tame(p1)
            d2 = read_tame(p2)

        result = merge_datasets([d1, d2], add_source_column=False)
        self.assertIn("GENDER", list(result.dataset.df.columns))
        self.assertIn("AGE", list(result.dataset.df.columns))
        self.assertIn("RESULT", list(result.dataset.df.columns))
        self.assertTrue(any("tag_name 'GENDER'" in warning for warning in result.warnings))


class SidecarTest(unittest.TestCase):
    def test_data_tame_auto_loads_sibling_meta_tame(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "sample.tame"
            meta_path = Path(tmpdir) / "sample.meta.tame"
            data_path = Path(tmpdir) / "sample.data.tame"
            input_path.write_text(SAMPLE_TAME, encoding="utf-8")
            dataset = read_tame(input_path)

            write_meta_tame(meta_path, dataset)
            write_data_tame(data_path, dataset)
            meta_text = meta_path.read_text(encoding="utf-8")
            reloaded = read_tame(data_path)

        self.assertIn("[TAGS]", meta_text)
        self.assertIn('"보고값"', meta_text)
        self.assertEqual(reloaded.settings()["CRR"], "VALUE")
        self.assertIsNotNone(reloaded.first_column_with_tag("RESULT"))
        self.assertEqual(execute_work(reloaded, "DEFAULT").work_name, "DEFAULT")

    def test_xlsx_with_meta_tame_restores_tags_and_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "template.tame"
            meta_path = Path(tmpdir) / "fresh.meta.tame"
            xlsx_path = Path(tmpdir) / "fresh.xlsx"
            input_path.write_text(SAMPLE_TAME, encoding="utf-8")
            dataset = read_tame(input_path)
            write_meta_tame(meta_path, dataset)

            workbook = openpyxl.Workbook()
            sheet = workbook.active
            sheet.title = "DATA"
            sheet.append(["등록번호", "성별", "나이", "검사항목명", "보고값", "진료과"])
            sheet.append(["A1001", "F", "32", "AST", "25", "IM"])
            sheet.append(["A1002", "X", "bad", "ALT", "positive", "GS"])
            workbook.save(xlsx_path)

            loaded = read_xlsx(xlsx_path)
            result = execute_work(loaded, "DEFAULT")

        self.assertIsNotNone(loaded.first_column_with_tag("RESULT"))
        self.assertIsNotNone(loaded.first_column_with_tag("AGE"))
        self.assertEqual(result.outputs[0].name, "VALIDATE")
        self.assertEqual(result.outputs[1].name, "DESCRIBE")
        self.assertEqual(len(result.final_dataset.df), 1)

    def test_import_xlsx_into_tame_preserves_template_tags(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            template_path = Path(tmpdir) / "template.tame"
            output_path = Path(tmpdir) / "updated.tame"
            xlsx_path = Path(tmpdir) / "fresh.xlsx"
            template_path.write_text(SAMPLE_TAME, encoding="utf-8")
            template = read_tame(template_path)

            workbook = openpyxl.Workbook()
            sheet = workbook.active
            sheet.title = "DATA"
            sheet.append(["등록번호", "성별", "나이", "검사항목명", "보고값", "진료과"])
            sheet.append(["B0001", "F", "44", "AST", "18", "IM"])
            sheet.append(["B0002", "M", "55", "ALT", "<12", "GS"])
            workbook.save(xlsx_path)

            imported = import_xlsx_into_tame(template, xlsx_path)
            write_tame(output_path, imported)
            written = output_path.read_text(encoding="utf-8")

        self.assertEqual(imported.columns, template.columns)
        self.assertEqual(imported.df.loc[0, "등록번호"], "B0001")
        self.assertEqual(imported.df.loc[1, "보고값"], "<12")
        self.assertEqual(imported.settings()["CRR"], "VALUE")
        self.assertEqual(imported.tagged_headers()[4], "[[RESULT::<NUM>]]보고값")
        self.assertIn("[[RESULT::<NUM>]]보고값", written)
        self.assertIn("B0002\tM\t55\tALT\t<12\tGS", written)

    def test_import_xlsx_into_tame_rejects_header_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            template_path = Path(tmpdir) / "template.tame"
            xlsx_path = Path(tmpdir) / "fresh.xlsx"
            template_path.write_text(SAMPLE_TAME, encoding="utf-8")
            template = read_tame(template_path)

            workbook = openpyxl.Workbook()
            sheet = workbook.active
            sheet.title = "DATA"
            sheet.append(["등록번호", "성별", "나이", "검사항목명", "결과값", "진료과"])
            sheet.append(["B0001", "F", "44", "AST", "18", "IM"])
            workbook.save(xlsx_path)

            with self.assertRaisesRegex(ValueError, "XLSX import header validation failed"):
                import_xlsx_into_tame(template, xlsx_path)


class EmbeddedFunctionTest(unittest.TestCase):
    def test_execute_work_rejects_embedded_functions_without_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "function.tame"
            path.write_text(SAMPLE_FUNCTION_TAME, encoding="utf-8")
            dataset = read_tame(path)

        with self.assertRaisesRegex(PermissionError, "allow_functions=True"):
            execute_work(dataset, "DEFAULT")

    def test_execute_work_runs_embedded_python_function(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "function.tame"
            path.write_text(SAMPLE_FUNCTION_TAME, encoding="utf-8")
            dataset = read_tame(path)

        result = execute_work(dataset, "DEFAULT", allow_functions=True)

        self.assertEqual(result.outputs[0].name, "TRIM_RESULT")
        self.assertEqual(result.final_dataset.df.loc[0, "보고값"], "alpha")
        self.assertEqual(result.final_dataset.df.loc[1, "보고값"], "beta")
        self.assertIsNotNone(result.final_dataset.first_column_with_tag("RESULT"))

    @unittest.skipIf(shutil.which("Rscript") is None, "Rscript is not available")
    def test_execute_work_runs_embedded_r_function(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "function_r.tame"
            path.write_text(SAMPLE_R_FUNCTION_TAME, encoding="utf-8")
            dataset = read_tame(path)

        result = execute_work(dataset, "DEFAULT", allow_functions=True)

        self.assertEqual(result.outputs[0].name, "TRIM_RESULT_R")
        self.assertEqual(result.outputs[0].message, "trimmed by R")
        self.assertEqual(result.final_dataset.df.loc[0, "보고값"], "gamma")
        self.assertEqual(result.final_dataset.df.loc[1, "보고값"], "delta")
        self.assertIsNotNone(result.final_dataset.first_column_with_tag("RESULT"))


class CommandPipelineTest(unittest.TestCase):
    def test_available_command_pipelines_reads_meta_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "pipeline.tame"
            path.write_text(SAMPLE_RI_TAME, encoding="utf-8")
            dataset = read_tame(path)

        meta = dict(dataset.meta)
        meta["PIPELINES"] = {"DEFAULT": ["sample --rows 1 --by-tag TESTNAME --seed 11", "describe"]}
        names = available_command_pipelines(meta)
        self.assertEqual(names, ["DEFAULT"])

    def test_execute_command_pipeline_chains_sample_and_nested_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "pipeline.tame"
            path.write_text(SAMPLE_RI_TAME, encoding="utf-8")
            dataset = read_tame(path)

        meta = dict(dataset.meta)
        meta["PIPELINES"] = {"DEFAULT": ["sample --rows 1 --by-tag TESTNAME --seed 11", "run RI"]}
        result = execute_command_pipeline(dataset.replace(meta=meta), "DEFAULT")

        self.assertEqual(result.outputs[0].name, "sample --rows 1 --by-tag TESTNAME --seed 11")
        self.assertEqual(result.outputs[1].name, "run RI")
        self.assertEqual(len(result.final_dataset.df), 8)
        self.assertIn("참고치하한", list(result.final_dataset.df.columns))
        self.assertEqual(set(result.outputs[1].dataset.df["검사항목명"]), {"AST", "ALT"})

    def test_execute_command_pipeline_supports_relative_export_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "pipeline.tame"
            export_path = Path(tmpdir) / "exports" / "sample.sql"
            path.write_text(SAMPLE_TAME, encoding="utf-8")
            dataset = read_tame(path)

            meta = dict(dataset.meta)
            meta["PIPELINES"] = {"DEFAULT": [f"export {export_path.relative_to(path.parent)} --format sql"]}
            result = execute_command_pipeline(dataset.replace(meta=meta), "DEFAULT")
            sql_text = export_path.read_text(encoding="utf-8")

        self.assertEqual(result.outputs[0].name, "export exports/sample.sql --format sql")
        self.assertIn("CREATE TABLE", sql_text)

    def test_execute_command_pipeline_rejects_unknown_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "pipeline.tame"
            path.write_text(SAMPLE_TAME, encoding="utf-8")
            dataset = read_tame(path)

        meta = dict(dataset.meta)
        meta["PIPELINES"] = {"DEFAULT": ["merge other.tame --output merged.tame"]}
        with self.assertRaisesRegex(CommandPipelineError, "Unsupported command"):
            execute_command_pipeline(dataset.replace(meta=meta), "DEFAULT")

    def test_execute_command_pipeline_rejects_undefined_pipeline_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "pipeline.tame"
            path.write_text(SAMPLE_TAME, encoding="utf-8")
            dataset = read_tame(path)

        with self.assertRaisesRegex(CommandPipelineError, "Undefined command pipeline"):
            execute_command_pipeline(dataset, "DEFAULT")


class ImageTest(unittest.TestCase):
    def test_extract_and_embed_image_columns_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tame_path = Path(tmpdir) / "images.tame"
            image_dir = Path(tmpdir) / "images"
            tame_path.write_text(SAMPLE_IMAGE_TAME, encoding="utf-8")
            dataset = read_tame(tame_path)

            extracted, manifest = extract_image_columns(dataset, image_dir)
            path_value = extracted.df.loc[0, "썸네일"]
            reembedded, embed_manifest = embed_image_columns(extracted, base_dir=Path.cwd())
            self.assertEqual(manifest.loc[0, "mime"], "image/png")
            self.assertTrue(Path(path_value).exists())
            self.assertTrue(any(column.has_tag("PATH") for column in extracted.columns if column.name == "썸네일"))
            self.assertEqual(reembedded.df.loc[0, "썸네일"], SAMPLE_IMAGE_DATA_URL)
            self.assertEqual(embed_manifest.loc[0, "mime"], "image/png")


if __name__ == "__main__":
    unittest.main()

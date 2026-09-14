"""임상용 안정성·검증 보강 테스트.

기존 test_edge_cases / scenario_matrix는 "에러 없이 실행되는가 / 컬럼이 있는가"를 폭넓게
다룬다. 이 파일은 그 위에 임상검사실 도구에서 가장 중요한 네 축을 검증한다.

1. 수치적 정확성(NumericalCorrectness): 손계산 가능한 입력에 대해 통계·파생값이 정확한지.
2. 퇴화 입력 견고성(DegenerateInput): 단일행·전결측·단일클래스·상수열·빈 데이터에서
   크래시 없이 graceful하게 동작하는지(회귀 가드).
3. 검열값/특수셀(Censored): '<', '>' 비교자와 NULL/EMPTY/WS 셀이 계산·변환·왕복에서
   보존·처리되는지.
4. 안전성/결정성(Safety): DERIVE 식 샌드박스, 검증 정책(DELETE/REPORT), 라운드트립
   충실도, 동일 입력 재실행 결정성.

모든 기대값은 코드 구현이 아니라 수학적으로 필연인 결과(예: 완전분리 ROC의 AUC=1.0,
y=x 두 방법의 회귀 기울기=1.0, 8..12의 CV%=15.811)로 잡아 진짜 정확성을 검증한다.
"""
from __future__ import annotations

import math
from pathlib import Path
import tempfile
import textwrap
import unittest

import numpy as np
import pandas as pd

from tametools.actions import ActionError, execute_action, execute_action_pipeline
from tametools.analysis import describe_dataset, validate_dataset
from tametools.io import read_tame, write_tame
from tametools.models import TameDataset
from tametools.plugin_base import run_plugin


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_TMP = tempfile.TemporaryDirectory()
_TMP_PATH = Path(_TMP.name)
_counter = 0


def _read(text: str) -> TameDataset:
    global _counter
    _counter += 1
    path = _TMP_PATH / f"case_{_counter}.tame"
    path.write_text(textwrap.dedent(text).strip() + "\n", encoding="utf-8")
    return read_tame(path)


def _run_action(dataset: TameDataset, config: dict) -> "object":
    meta = dict(dataset.meta)
    meta["ACTIONS"] = {"X": config}
    return execute_action(dataset.replace(meta=meta), "X")


def tearDownModule() -> None:  # noqa: N802 - unittest hook
    _TMP.cleanup()


# ---------------------------------------------------------------------------
# 1. 수치적 정확성
# ---------------------------------------------------------------------------

class NumericalCorrectnessTest(unittest.TestCase):
    def test_reference_interval_quantiles_are_exact(self) -> None:
        rows = "\n".join(f"RI\t{v}" for v in range(0, 121))  # 0..120, n=121
        ds = _read(f"<DATA>\n[[ITEM::TESTNAME::CATEGORY]]t\t[[RESULT::<NUM>]]r\n{rows}\n</DATA>")
        out = run_plugin(ds, "REFERENCE_INTERVAL", ds.meta, "ri", {})
        row = out.table.iloc[0]
        self.assertEqual(int(row["n"]), 121)
        self.assertAlmostEqual(float(row["ref_low"]), 3.0, places=6)
        self.assertAlmostEqual(float(row["ref_high"]), 117.0, places=6)
        self.assertAlmostEqual(float(row["mean"]), 60.0, places=6)
        self.assertAlmostEqual(float(row["median"]), 60.0, places=6)

    def test_qc_precision_cv_is_exact(self) -> None:
        # 8,9,10,11,12 -> mean 10, sd(ddof1)=sqrt(2.5)=1.5811, CV%=15.811
        ds = _read("<DATA>\n[[ITEM::TESTNAME::CATEGORY]]t\t[[RESULT::NUM]]r\nQC\t8\nQC\t9\nQC\t10\nQC\t11\nQC\t12\n</DATA>")
        out = run_plugin(ds, "QC_ANALYSIS", ds.meta, "qc", {"MODE": "PRECISION"})
        row = out.table.iloc[0]
        self.assertEqual(int(row["n"]), 5)
        self.assertAlmostEqual(float(row["mean"]), 10.0, places=6)
        self.assertAlmostEqual(float(row["sd"]), math.sqrt(2.5), places=3)
        self.assertAlmostEqual(float(row["cv_percent"]), 100 * math.sqrt(2.5) / 10, places=2)

    def test_correlation_is_one_for_perfect_linear(self) -> None:
        ds = _read(
            "<DATA>\n[[ID(sample)::STR]]s\t[[RESULT::NUM]]A\t[[RESULT::NUM]]B\n"
            "S1\t1\t2\nS2\t2\t4\nS3\t3\t6\nS4\t4\t8\n</DATA>"
        )
        out = run_plugin(ds, "CORRELATION", ds.meta, "c", {})
        self.assertAlmostEqual(float(out.table.iloc[0]["r"]), 1.0, places=6)

    def test_method_comparison_identity_and_constant_bias(self) -> None:
        identity = _read(
            "<DATA>\n[[ITEM::TESTNAME::CATEGORY]]t\t[[INSTRUMENT::CATEGORY]]i\t[[ID(sample)::STR]]s\t[[RESULT::NUM]]r\n"
            "AST\tA\tS1\t10\nAST\tB\tS1\t10\nAST\tA\tS2\t20\nAST\tB\tS2\t20\n"
            "AST\tA\tS3\t30\nAST\tB\tS3\t30\nAST\tA\tS4\t40\nAST\tB\tS4\t40\n</DATA>"
        )
        identity.meta["COLUMN"] = {"r": {"UNIT": "U/L"}}
        row = run_plugin(identity, "METHOD_COMPARISON", identity.meta, "m", {"METHOD_A": "A", "METHOD_B": "B"}).table.iloc[0]
        self.assertAlmostEqual(float(row["pb_slope"]), 1.0, places=6)
        self.assertAlmostEqual(float(row["pb_intercept"]), 0.0, places=6)
        self.assertAlmostEqual(float(row["bias"]), 0.0, places=6)
        self.assertAlmostEqual(float(row["r"]), 1.0, places=6)

        offset = _read(
            "<DATA>\n[[ITEM::TESTNAME::CATEGORY]]t\t[[INSTRUMENT::CATEGORY]]i\t[[ID(sample)::STR]]s\t[[RESULT::NUM]]r\n"
            "AST\tA\tS1\t10\nAST\tB\tS1\t15\nAST\tA\tS2\t20\nAST\tB\tS2\t25\n"
            "AST\tA\tS3\t30\nAST\tB\tS3\t35\nAST\tA\tS4\t40\nAST\tB\tS4\t45\n</DATA>"
        )
        offset.meta["COLUMN"] = {"r": {"UNIT": "U/L"}}
        row = run_plugin(offset, "METHOD_COMPARISON", offset.meta, "m", {"METHOD_A": "A", "METHOD_B": "B"}).table.iloc[0]
        self.assertAlmostEqual(float(row["pb_slope"]), 1.0, places=6)
        self.assertAlmostEqual(float(row["pb_intercept"]), 5.0, places=6)
        self.assertAlmostEqual(float(row["bias"]), 5.0, places=6)  # bias = method_b - method_a

    def test_roc_auc_is_one_for_separable_scores(self) -> None:
        ds = _read("<DATA>\n[[RESULT::NUM]]score\t[[LABEL::CATEGORY]]label\n0.1\t0\n0.2\t0\n0.8\t1\n0.9\t1\n</DATA>")
        row = run_plugin(ds, "ROC_ANALYSIS", ds.meta, "r", {"POSITIVE": "1"}).table.iloc[0]
        self.assertAlmostEqual(float(row["auc"]), 1.0, places=6)
        self.assertAlmostEqual(float(row["sensitivity"]), 1.0, places=6)
        self.assertAlmostEqual(float(row["specificity"]), 1.0, places=6)
        self.assertEqual(int(row["n_pos"]), 2)
        self.assertEqual(int(row["n_neg"]), 2)

    def test_delta_check_absolute_and_percent_are_exact(self) -> None:
        ds = _read(
            "<DATA>\n[[ID(patient)::STR]]p\t[[ITEM::TESTNAME::CATEGORY]]t\t[[RESULT_TIME::DATETIME]]ti\t[[RESULT::<NUM>]]r\n"
            "P1\tAST\t2026-01-01 08:00\t10\nP1\tAST\t2026-01-02 08:00\t20\n</DATA>"
        )
        row = run_plugin(ds, "CHEMISTRY_ANALYSIS", ds.meta, "d", {"MODE": "DELTA_CHECK"}).table.iloc[0]
        self.assertAlmostEqual(float(row["절대변화"]), 10.0, places=6)
        self.assertAlmostEqual(float(row["변화율"]), 100.0, places=6)
        self.assertAlmostEqual(float(row["시간간격"]), 24.0, places=6)

    def test_calendar_age_respects_birthday_boundary_and_leap_day(self) -> None:
        ds = _read(
            "<DATA>\n[[ID(patient)::STR]]p\t[[DOB::DATE]]dob\t[[COLLECTION_AT::DATETIME]]c\n"
            "P1\t2000-03-01\t2026-02-28 10:00\n"   # 생일 전날 -> 25
            "P2\t2000-03-01\t2026-03-01 10:00\n"   # 생일 당일 -> 26
            "P3\t2004-02-29\t2026-02-28 10:00\n"   # 윤일생, 비윤년 2/28 -> 21
            "</DATA>"
        )
        out = _run_action(ds, {"TYPE": "DATE_DERIVE", "NAME": "age", "KIND": "age", "FROM": "tag:DOB", "AS_OF": "tag:COLLECTION_AT", "TAGS": ["AGE"]})
        self.assertEqual([int(v) for v in out.dataset.df["age"]], [25, 26, 21])

    def test_tat_minutes_is_exact(self) -> None:
        ds = _read("<DATA>\n[[RECEIVED_AT::DATETIME]]r\t[[RESULT_TIME::DATETIME]]t\n2026-01-01 08:00:00\t2026-01-01 09:30:00\n</DATA>")
        out = _run_action(ds, {"TYPE": "DATE_DERIVE", "NAME": "tat", "KIND": "diff_minutes", "START": "tag:RECEIVED_AT", "END": "tag:RESULT_TIME"})
        self.assertAlmostEqual(float(out.dataset.df["tat"].iloc[0]), 90.0, places=6)

    def test_unit_conversion_factor_is_exact(self) -> None:
        ds = _read("<DATA>\n[[ITEM::TESTNAME::CATEGORY]]t\t[[RESULT::<NUM>]]r\t[[UNIT::CATEGORY]]u\nGLU\t5\tmmol/L\n</DATA>")
        out = _run_action(ds, {
            "TYPE": "UNIT.CONVERT", "SOURCE": "tag:RESULT", "UNIT_COLUMN": "tag:UNIT", "TEST_COLUMN": "tag:ITEM",
            "OUTPUT_VALUE": "conv", "TARGET_UNIT_BY_TEST": {"GLU": "mg/dL"}, "DECIMALS": 4,
        })
        # 5 mmol/L * 18.0182 = 90.091 mg/dL
        self.assertAlmostEqual(float(out.dataset.df["conv"].iloc[0]), 5 * 18.0182, places=3)


# ---------------------------------------------------------------------------
# 2. 퇴화 입력 견고성 (크래시 없이 graceful)
# ---------------------------------------------------------------------------

class DegenerateInputRobustnessTest(unittest.TestCase):
    def _assert_empty_table(self, out) -> None:
        self.assertIsNotNone(out)
        self.assertIsNotNone(out.table)
        self.assertEqual(len(out.table), 0)

    def test_qc_with_single_observation_does_not_crash(self) -> None:
        ds = _read("<DATA>\n[[ITEM::TESTNAME::CATEGORY]]t\t[[RESULT::NUM]]r\nQC\t10\n</DATA>")
        row = run_plugin(ds, "QC_ANALYSIS", ds.meta, "qc", {"MODE": "PRECISION"}).table.iloc[0]
        self.assertEqual(int(row["n"]), 1)
        self.assertAlmostEqual(float(row["mean"]), 10.0, places=6)
        # n=1에서 분산은 정의되지 않으므로 0 또는 NaN 둘 다 허용(크래시 없음만 보장)
        self.assertTrue(pd.isna(row["sd"]) or float(row["sd"]) == 0.0)

    def test_correlation_with_constant_column_is_nan_not_error(self) -> None:
        ds = _read(
            "<DATA>\n[[ID(sample)::STR]]s\t[[RESULT::NUM]]A\t[[RESULT::NUM]]B\n"
            "S1\t5\t1\nS2\t5\t2\nS3\t5\t3\n</DATA>"
        )
        out = run_plugin(ds, "CORRELATION", ds.meta, "c", {})
        self.assertTrue(pd.isna(out.table.iloc[0]["r"]))

    def test_roc_with_single_class_returns_empty(self) -> None:
        ds = _read("<DATA>\n[[RESULT::NUM]]score\t[[LABEL::CATEGORY]]label\n0.1\t0\n0.2\t0\n0.3\t0\n</DATA>")
        self._assert_empty_table(run_plugin(ds, "ROC_ANALYSIS", ds.meta, "r", {"POSITIVE": "1"}))

    def test_reference_interval_all_missing_returns_empty(self) -> None:
        ds = _read("<DATA>\n[[ITEM::TESTNAME::CATEGORY]]t\t[[RESULT::<NUM>]]r\nAST\t\nAST\t\n</DATA>")
        self._assert_empty_table(run_plugin(ds, "REFERENCE_INTERVAL", ds.meta, "ri", {}))

    def test_group_test_single_group_returns_empty(self) -> None:
        ds = _read("<DATA>\n[[GROUP::CATEGORY]]g\t[[RESULT::NUM]]r\nA\t1\nA\t2\nA\t3\n</DATA>")
        self._assert_empty_table(run_plugin(ds, "GROUP_TEST", ds.meta, "g", {"GROUP": "tag:GROUP"}))

    def test_chemistry_summary_on_empty_data_returns_empty(self) -> None:
        ds = _read("<DATA>\n[[ITEM::TESTNAME::CATEGORY]]t\t[[RESULT::<NUM>]]r\n</DATA>")
        self._assert_empty_table(run_plugin(ds, "CHEMISTRY_ANALYSIS", ds.meta, "s", {"MODE": "RESULT_SUMMARY"}))

    def test_delta_check_without_prior_returns_empty(self) -> None:
        ds = _read(
            "<DATA>\n[[ID(patient)::STR]]p\t[[ITEM::TESTNAME::CATEGORY]]t\t[[RESULT_TIME::DATETIME]]ti\t[[RESULT::<NUM>]]r\n"
            "P1\tAST\t2026-01-01 08:00\t10\n</DATA>"
        )
        self._assert_empty_table(run_plugin(ds, "CHEMISTRY_ANALYSIS", ds.meta, "d", {"MODE": "DELTA_CHECK"}))

    def test_row_filter_to_empty_still_yields_usable_dataset(self) -> None:
        ds = _read("<DATA>\n[[ID(patient)::STR]]p\t[[RESULT::NUM]]r\nP1\t10\nP2\t20\n</DATA>")
        out = _run_action(ds, {"TYPE": "ROW.INCLUDE", "COLUMN": "tag:RESULT", "OP": "gt", "VALUE": 1000})
        self.assertEqual(len(out.dataset.df), 0)
        # 빈 데이터셋도 검증/요약이 가능해야 한다
        validate_dataset(out.dataset)
        describe_dataset(out.dataset)
        self.assertEqual(list(out.dataset.df.columns), [c.name for c in out.dataset.columns])

    def test_outlier_filter_with_identical_values_removes_nothing(self) -> None:
        ds = _read("<DATA>\n[[ITEM::TESTNAME::CATEGORY]]t\t[[RESULT::NUM]]r\nAST\t5\nAST\t5\nAST\t5\nAST\t5\n</DATA>")
        out = _run_action(ds, {"TYPE": "OUTLIER_FILTER", "COLUMN": "tag:RESULT", "METHOD": "iqr", "BY": ["tag:ITEM"]})
        self.assertEqual(int(out.table.iloc[0]["removed"]), 0)


# ---------------------------------------------------------------------------
# 3. 검열값 / 특수셀 처리
# ---------------------------------------------------------------------------

class CensoredValueHandlingTest(unittest.TestCase):
    def test_unit_convert_preserves_comparator_operator(self) -> None:
        ds = _read(
            "<DATA>\n[[ITEM::TESTNAME::CATEGORY]]t\t[[RESULT::<NUM>]]r\t[[UNIT::CATEGORY]]u\n"
            "GLU\t<5\tmmol/L\nGLU\t7\t\nAST\t20\tU/L\n</DATA>"
        )
        out = _run_action(ds, {
            "TYPE": "UNIT.CONVERT", "SOURCE": "tag:RESULT", "UNIT_COLUMN": "tag:UNIT", "TEST_COLUMN": "tag:ITEM",
            "OUTPUT_VALUE": "conv", "TARGET_UNIT_BY_TEST": {"GLU": "mg/dL"}, "DECIMALS": 3,
        })
        statuses = list(out.dataset.df["conversion_status"])
        self.assertEqual(statuses[0], "CONVERTED")
        self.assertTrue(str(out.dataset.df["conv"].iloc[0]).startswith("<"))  # 연산자 보존
        self.assertEqual(statuses[1], "UNIT_MISSING")          # 단위 결측
        self.assertEqual(statuses[2], "TARGET_UNIT_MISSING")   # 대상단위 미지정 항목

    def test_round_preserves_non_numeric_comparator_cells(self) -> None:
        ds = _read("<DATA>\n[[RESULT::<NUM>]]r\n10.4\n<8\n12.6\n</DATA>")
        out = _run_action(ds, {"TYPE": "ROUND", "TAGS": ["RESULT"], "DECIMALS": 0})
        values = list(out.dataset.df["r"].astype(str))
        self.assertIn("<8", values)              # 비교자 셀 보존
        self.assertIn("10", values)              # 10.4 -> 10
        self.assertIn("13", values)              # 12.6 -> 13

    def test_comparator_values_validate_as_clean(self) -> None:
        ds = _read("<DATA>\n[[RESULT::<NUM>]]r\n<0.6\n>=7\n=8\n-0.5\n</DATA>")
        result = validate_dataset(ds)
        self.assertEqual([i for i in result.issues if i.tag == "<NUM>"], [])

    def test_special_cells_round_trip_unchanged(self) -> None:
        ds = _read(
            "<DATA>\n[[RESULT::<NUM>::NULLABLE]]보고값\t[[ITEM::CATEGORY]]항목\n"
            "<0.6\t크레아티닌\n<<NULL>>\tγ-GT\n>1000\tβ2-MG\n</DATA>"
        )
        out_path = _TMP_PATH / "roundtrip_special.tame"
        write_tame(out_path, ds)
        reread = read_tame(out_path)
        self.assertEqual(list(ds.df["보고값"]), list(reread.df["보고값"]))
        self.assertEqual(list(ds.df["항목"]), list(reread.df["항목"]))


# ---------------------------------------------------------------------------
# 4. 안전성 / 검증 정책 / 결정성
# ---------------------------------------------------------------------------

class DeriveSandboxSecurityTest(unittest.TestCase):
    MALICIOUS = [
        "__import__('os').system('echo x')",
        "().__class__.__bases__",
        "[x for x in (1, 2)]",
        "(lambda: 1)()",
        "{tag:RESULT}.__class__",
        "open('/etc/passwd')",
        "globals()",
        "eval('1+1')",
    ]

    def _derive(self, expr: str):
        ds = _read("<DATA>\n[[RESULT::NUM]]r\t[[REF_HIGH::NUM]]h\n10\t40\n</DATA>")
        return _run_action(ds, {"TYPE": "DERIVE", "NAME": "x", "EXPR": expr})

    def test_malicious_expressions_are_blocked(self) -> None:
        for expr in self.MALICIOUS:
            with self.subTest(expr=expr):
                with self.assertRaises(ActionError):
                    self._derive(expr)

    def test_allowed_math_functions_work(self) -> None:
        out = self._derive("log10({tag:RESULT}) + sqrt({tag:REF_HIGH})")
        self.assertAlmostEqual(float(out.dataset.df["x"].iloc[0]), math.log10(10) + math.sqrt(40), places=6)

    def test_division_by_zero_yields_inf_not_crash(self) -> None:
        out = self._derive("{tag:RESULT} / 0")
        self.assertTrue(math.isinf(float(out.dataset.df["x"].iloc[0])))


class ValidationPolicyTest(unittest.TestCase):
    DATA = (
        "<META>\n[SETTINGS]\nVALIDATE_ERROR = \"{mode}\"\n</META>\n"
        "<DATA>\n[[ID(patient)::STR]]p\t[[RESULT::NUM]]r\nP1\t10\nP2\tnotnum\nP3\t30\n</DATA>"
    )

    def test_report_mode_keeps_rows_but_reports_issue(self) -> None:
        ds = _read(self.DATA.format(mode="REPORT"))
        result = validate_dataset(ds)
        self.assertEqual(len(result.cleaned_dataset.df), 3)
        self.assertIn("NUM", {issue.tag for issue in result.issues})

    def test_delete_mode_drops_invalid_rows_in_cleaned_dataset(self) -> None:
        ds = _read(self.DATA.format(mode="DELETE"))
        result = validate_dataset(ds)
        self.assertEqual(len(result.cleaned_dataset.df), 2)
        self.assertEqual(sorted(result.cleaned_dataset.df["r"]), ["10", "30"])
        self.assertIn("NUM", {issue.tag for issue in result.issues})


class LisExportIoRobustnessTest(unittest.TestCase):
    """실제 LIS 추출본에서 흔한 인코딩/개행/식별자 변형을 견고하게 읽는지."""

    BASE = "<DATA>\n[[ID(patient)::STR]]p\t[[RESULT::NUM]]r\n0042\t10\n0007\t20\n</DATA>\n"

    def _read_bytes(self, name: str, payload: bytes) -> TameDataset:
        path = _TMP_PATH / name
        path.write_bytes(payload)
        return read_tame(path)

    def test_crlf_line_endings_are_handled(self) -> None:
        ds = self._read_bytes("crlf.tame", self.BASE.replace("\n", "\r\n").encode("utf-8"))
        self.assertEqual(len(ds.df), 2)
        self.assertEqual(list(ds.df["p"]), ["0042", "0007"])

    def test_utf8_bom_is_stripped(self) -> None:
        ds = self._read_bytes("bom.tame", b"\xef\xbb\xbf" + self.BASE.encode("utf-8"))
        self.assertEqual(list(ds.df.columns), ["p", "r"])  # BOM이 첫 컬럼명에 섞이지 않음
        self.assertEqual(len(ds.df), 2)

    def test_trailing_blank_lines_do_not_add_rows(self) -> None:
        ds = self._read_bytes("trailing.tame", (self.BASE + "\n\n\n").encode("utf-8"))
        self.assertEqual(len(ds.df), 2)

    def test_leading_zero_identifiers_are_preserved_as_strings(self) -> None:
        ds = self._read_bytes("leadzero.tame", self.BASE.encode("utf-8"))
        # 환자번호의 선행 0이 숫자화로 사라지면 매칭/조인이 깨진다 → 문자열로 보존돼야 함
        self.assertEqual(list(ds.df["p"]), ["0042", "0007"])

    def test_many_columns_round_trip(self) -> None:
        headers = "\t".join(f"[[RESULT::NUM]]c{i}" for i in range(60))
        values = "\t".join(str(i) for i in range(60))
        ds = self._read_bytes("wide.tame", f"<DATA>\n{headers}\n{values}\n</DATA>\n".encode("utf-8"))
        self.assertEqual(len(ds.columns), 60)
        out = _TMP_PATH / "wide.roundtrip.tame"
        write_tame(out, ds)
        self.assertEqual(len(read_tame(out).columns), 60)


class DeterminismTest(unittest.TestCase):
    PIPE = {
        "ACTIONS": {
            "SORT": {"TYPE": "SORT", "BY": ["tag:ID(patient)", "tag:RESULT_TIME"]},
            "KEY": {"TYPE": "COLUMN.CONCAT", "COLUMNS": ["tag:ID(patient)", "tag:ID(sample)"], "OUTPUT": "key", "SEP": "::", "TAGS": ["ID", "STR"]},
            "CORE": {"TYPE": "COLUMN.INCLUDE", "TAGS": ["ID", "ITEM", "RESULT"]},
        },
        "ACTION_PIPELINES": {"DEFAULT": ["SORT", "KEY", "CORE"]},
    }
    DATA = (
        "<DATA>\n[[ID(patient)::STR]]p\t[[ID(sample)::STR]]s\t[[ITEM::TESTNAME::CATEGORY]]t\t"
        "[[RESULT::NUM]]r\t[[RESULT_TIME::DATETIME]]ti\n"
        "P2\tS3\tALT\t5\t2026-01-03 08:00\nP1\tS1\tAST\t10\t2026-01-01 08:00\n"
        "P1\tS2\tAST\t70\t2026-01-02 08:00\nP3\tS4\tCr\t1\t2026-01-04 08:00\n</DATA>"
    )

    def _pipeline_df(self) -> pd.DataFrame:
        ds = _read(self.DATA)
        meta = dict(ds.meta)
        meta["ACTIONS"] = self.PIPE["ACTIONS"]
        meta["ACTION_PIPELINES"] = self.PIPE["ACTION_PIPELINES"]
        return execute_action_pipeline(ds.replace(meta=meta)).final_dataset.df

    def test_pipeline_is_deterministic(self) -> None:
        first = self._pipeline_df()
        second = self._pipeline_df()
        pd.testing.assert_frame_equal(first.reset_index(drop=True), second.reset_index(drop=True))

    def test_sort_is_stable_and_correct(self) -> None:
        df = self._pipeline_df()
        # CORE가 ID 태그만 유지하므로 환자ID 컬럼명은 'p'로 보존된다
        self.assertIn("p", df.columns)
        patients = list(df["p"])
        self.assertEqual(patients, sorted(patients))


if __name__ == "__main__":
    unittest.main()

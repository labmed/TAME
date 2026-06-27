"""감사·계측 로그, 재현성, 엑셀 무결성 검사 회귀 테스트."""
from __future__ import annotations

import unittest

import pandas as pd

from tametools.audit import (
    dataset_content_hash,
    RunRecorder,
    run_twice,
    verify_reproducible,
    stamp_integrity,
    check_integrity,
)
from tametools.excel_integrity import excel_general_coercion, check_excel_roundtrip
from tametools.models import ColumnSpec, TameDataset


def _dataset(result_values):
    cols = [
        ColumnSpec(name="등록번호", original_header="[[ID::STR]]등록번호", tags=("ID", "STR")),
        ColumnSpec(name="결과", original_header="[[RESULT::<NUM>]]결과", tags=("RESULT", "<NUM>")),
    ]
    df = pd.DataFrame({"등록번호": ["00123456", "000777"], "결과": result_values})
    return TameDataset(df=df, columns=cols)


class ExcelIntegrityTest(unittest.TestCase):
    def test_leading_zero_and_precision_and_date_detected(self):
        self.assertEqual(excel_general_coercion("00123456")[0], "leading_zero_loss")
        self.assertEqual(excel_general_coercion("1234567890123456789")[0], "precision_loss")
        for code in ("3-4", "MAR1", "OCT-4", "1-DEC"):
            self.assertEqual(excel_general_coercion(code)[0], "date_coercion", code)

    def test_comparator_tokens_and_plain_numbers_not_flagged(self):
        for safe in ("<3", ">5000", "<<NULL>>", "<<WS:2>>", "12.5", "1500", "abc", ""):
            self.assertIsNone(excel_general_coercion(safe)[0], safe)

    def test_roundtrip_report_counts(self):
        ds = _dataset(["<3", "120"])
        report = check_excel_roundtrip(ds)
        # 등록번호 2셀이 leading-zero 손상, 결과 열은 손상 없음
        self.assertEqual(report.by_category.get("leading_zero_loss"), 2)
        self.assertEqual(report.altered_cells, 2)
        self.assertNotIn("결과", report.by_column)


class AuditReproducibilityTest(unittest.TestCase):
    def test_content_hash_is_deterministic_and_sensitive(self):
        ds_a = _dataset(["<3", "120"])
        ds_b = _dataset(["<3", "120"])
        ds_c = _dataset(["<3", "999"])
        self.assertEqual(dataset_content_hash(ds_a), dataset_content_hash(ds_b))
        self.assertNotEqual(dataset_content_hash(ds_a), dataset_content_hash(ds_c))

    def test_run_twice_and_verify_reproducible(self):
        ds = _dataset(["<3", "120"])
        result = run_twice(lambda: ds)
        self.assertTrue(result["reproducible"])
        self.assertEqual(result["hash_first"], result["hash_second"])
        self.assertTrue(verify_reproducible(lambda: ds, runs=3)["reproducible"])

    def test_run_recorder_summary(self):
        ds = _dataset(["<3", "120"])
        rec = RunRecorder("unit")
        with rec.step("load", input_ds=ds) as s:
            s["output"] = ds
            s["issues"] = 0
        with rec.step("transform", input_ds=ds) as s:
            s["output"] = _dataset(["3", "120"])
            s["issues"] = 1
        summary = rec.summary()
        self.assertEqual(summary["steps"], 2)
        self.assertEqual(summary["issues"], 1)
        self.assertTrue(summary["final_hash"])
        self.assertGreaterEqual(summary["total_seconds"], 0.0)


class IntegrityStampTest(unittest.TestCase):
    def test_stamp_intact_then_tamper_detected(self):
        ds = _dataset(["<3", "120"])
        stamped = stamp_integrity(ds)
        intact = check_integrity(stamped)
        self.assertTrue(intact["present"])
        self.assertTrue(intact["intact"])
        # 셀 변조 → 적발
        df2 = stamped.df.copy()
        df2.iloc[0, df2.columns.get_loc("결과")] = "999"
        tampered = stamped.replace(df=df2)
        report = check_integrity(tampered)
        self.assertTrue(report["present"])
        self.assertFalse(report["intact"])

    def test_check_without_stamp_reports_absent(self):
        self.assertFalse(check_integrity(_dataset(["1", "2"]))["present"])


if __name__ == "__main__":
    unittest.main()

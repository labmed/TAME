"""init(bootstrap) — xlsx/csv → 시작용 .tame 태그·데이터타입 추정 테스트."""
from __future__ import annotations

import os
import tempfile
import unittest

import pandas as pd

from tametools.bootstrap import infer_narrow_type, infer_column_tags, init_from_table, list_sheets
from tametools.io import read_tame


class NarrowTypeTest(unittest.TestCase):
    def test_num_vs_comparator_num_strictly_distinguished(self):
        self.assertEqual(infer_narrow_type(pd.Series(["101", "99", "120"])), "NUM")
        self.assertEqual(infer_narrow_type(pd.Series(["<0.1", "2.0", "<0.2"])), "<NUM>")

    def test_leading_zero_numbers_are_str(self):
        # 앞자리 0(등록번호/검체번호)은 숫자로 보면 손상되므로 STR
        self.assertEqual(infer_narrow_type(pd.Series(["08123456", "00099", "07654321"])), "STR")

    def test_datetime_and_date_and_category(self):
        self.assertEqual(infer_narrow_type(pd.Series(["2026-06-01 09:30", "2026-06-02 10:00"])), "DATETIME")
        self.assertEqual(infer_narrow_type(pd.Series(["2026-06-01", "2026-06-02"])), "DATE")
        self.assertEqual(infer_narrow_type(pd.Series(["Positive", "Negative", "Positive", "Negative"])), "CATEGORY")


class TagInferenceTest(unittest.TestCase):
    def test_semantic_and_type_tags(self):
        self.assertEqual(infer_column_tags("등록번호", pd.Series(["08123", "07654"])), ("ID", "STR"))
        self.assertEqual(infer_column_tags("성별", pd.Series(["M", "F"])), ("SEX",))
        self.assertEqual(infer_column_tags("나이", pd.Series(["45", "52"])), ("AGE",))
        self.assertEqual(infer_column_tags("검사항목", pd.Series(["Glucose", "ALT"])), ("TESTNAME",))
        self.assertEqual(infer_column_tags("순수수치", pd.Series(["101", "99", "120"])), ("RESULT", "NUM"))
        self.assertEqual(infer_column_tags("hsCRP", pd.Series(["<0.1", "2.0", "3.0"])), ("RESULT", "<NUM>"))


class InitFromTableTest(unittest.TestCase):
    def test_init_records_source_sheet_types_and_log(self):
        with tempfile.TemporaryDirectory() as d:
            xlsx = os.path.join(d, "multi.xlsx")
            with pd.ExcelWriter(xlsx) as xw:
                pd.DataFrame({"등록번호": ["08123456", "00099"], "hsCRP": ["<0.1", "2.0"]}).to_excel(
                    xw, sheet_name="Chem", index=False)
                pd.DataFrame({"x": ["1", "2"]}).to_excel(xw, sheet_name="Other", index=False)
            self.assertEqual(list_sheets(xlsx), ["Chem", "Other"])

            out = os.path.join(d, "o.tame")
            result = init_from_table(xlsx, out, sheet="Chem")
            self.assertEqual(result["sheet"], "Chem")
            names = [c[0] for c in result["columns"]]
            self.assertEqual(names, ["등록번호", "hsCRP"])

            ds = read_tame(out)
            # META.INFO 에 출처 파일/시트 기록
            info = {k.upper(): v for k, v in ds.meta.get("INFO", {}).items()}
            self.assertEqual(info.get("SOURCE_SHEET"), "Chem")
            self.assertEqual(info.get("SOURCE_FILE"), "multi.xlsx")
            # 컬럼 데이터타입 META 기록(앞자리0→STR, comparator→<NUM>)
            types = {k: v for k, v in ds.meta.get("INIT", {}).get("TYPES", {}).items()}
            self.assertEqual(types.get("등록번호"), "STR")
            self.assertEqual(types.get("hsCRP"), "<NUM>")
            # LOG 에 INIT + 시트 기록
            log = ds.meta.get("LOG")
            self.assertTrue(log and any(e.get("OPERATION") == "INIT" for e in log))


if __name__ == "__main__":
    unittest.main()

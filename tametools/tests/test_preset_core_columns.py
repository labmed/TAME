"""Clinical Chemistry preset 회귀 테스트.

CORE_COLUMNS 전처리가 컬럼명이 아니라 태그로 컬럼을 선택하도록 고정한다(컬럼명 독립성).
"""
from __future__ import annotations

from pathlib import Path
import tempfile
import textwrap
import unittest

from tametools.actions import execute_action
from tametools.io import read_tame
from tametools.presets import apply_preset


def _read(text: str):
    path = Path(tempfile.mkdtemp()) / "x.tame"
    path.write_text(textwrap.dedent(text).strip() + "\n", encoding="utf-8")
    return read_tame(path)


UNTAGGED = (
    "<DATA>\n검사시간\t바코드번호\t등록번호\t검사항목명\t보고값\t장비명\t접수일\tsex\tage\n"
    "2026-01-01 09:00\tB1\tP1\tAST\t30\tCobas\t2026-01-01 08:30\tM\t40\n"
    "2026-01-02 09:00\tB2\tP2\tALT\t25\tAU\t2026-01-02 08:30\tF\t55\n</DATA>"
)


class PresetCoreColumnsTest(unittest.TestCase):
    def test_core_columns_uses_tags_not_column_names(self) -> None:
        out = apply_preset(_read(UNTAGGED), "CLINICAL_CHEMISTRY")
        core = out.dataset.meta["ACTIONS"]["CORE_COLUMNS"]
        self.assertNotIn("COLUMNS", core)                 # 컬럼명 선택 아님
        self.assertIn("TAGS", core)
        # 역할 태그로 구성(컬럼명/예시전용 태그 아님)
        self.assertIn("RESULT", core["TAGS"])
        self.assertIn("TESTNAME", core["TAGS"])
        self.assertIn("ID(patient)", core["TAGS"])

    def test_core_columns_action_keeps_core_columns(self) -> None:
        out = apply_preset(_read(UNTAGGED), "CLINICAL_CHEMISTRY")
        kept = execute_action(out.dataset, "CORE_COLUMNS")
        names = {c.name for c in kept.dataset.columns}
        # 태그 기반 선택으로 핵심 컬럼이 유지된다
        for col in ["검사항목명", "보고값", "등록번호", "바코드번호", "장비명", "sex", "age"]:
            self.assertIn(col, names)


if __name__ == "__main__":
    unittest.main()

"""사용자 정의 범주형 어휘 정규화·검증 테스트 (reviewer C-2)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tametools.categories import category_validation_issues, normalize_categories
from tametools.io import read_tame

SAMPLE = """
<META>
[INFO]
DESCRIPTION = "category vocab"
[CATEGORIES.RESULT_QUAL]
VALUES = ["POSITIVE", "NEGATIVE", "EQUIVOCAL"]
MAP = { "양성"="POSITIVE", "+"="POSITIVE", "pos"="POSITIVE", "음성"="NEGATIVE", "-"="NEGATIVE", "+/-"="EQUIVOCAL" }
STRICT = true
</META>
<DATA>
[[ID::STR]]등록번호\t[[CATEGORY::RESULT_QUAL]]판정
P1\t양성
P2\t-
P3\tpos
P4\tNegative
P5\t모름
</DATA>
"""


def _dataset():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "c.tame"
        p.write_text(SAMPLE.strip() + "\n", encoding="utf-8")
        return read_tame(p)


class CategoryNormalizationTest(unittest.TestCase):
    def test_synonyms_map_to_canonical(self):
        ds, report = normalize_categories(_dataset())
        self.assertEqual(ds.df["판정"].tolist(), ["POSITIVE", "NEGATIVE", "POSITIVE", "NEGATIVE", "모름"])
        row = report.iloc[0]
        self.assertEqual(int(row["changed_cells"]), 4)
        self.assertEqual(int(row["unmapped_cells"]), 1)

    def test_strict_validation_flags_only_unmappable(self):
        issues = category_validation_issues(_dataset())
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].value, "모름")
        self.assertEqual(issues[0].column, "판정")

    def test_no_vocabularies_is_noop(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "p.tame"
            p.write_text("<DATA>\n[[ID::STR]]등록번호\t[[CATEGORY]]판정\nP1\tx\n</DATA>\n", encoding="utf-8")
            ds = read_tame(p)
            out, report = normalize_categories(ds)
            self.assertTrue(report.empty)
            self.assertEqual(out.df["판정"].tolist(), ["x"])


if __name__ == "__main__":
    unittest.main()

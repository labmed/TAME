"""시간형(DATE/DATETIME/TIME) ISO 8601 정규화 테스트 (reviewer C-2)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tametools.datetimes import datetime_validation_issues, normalize_datetimes
from tametools.io import read_tame

SAMPLE = (
    "<DATA>\n"
    "[[ID::STR]]등록번호\t[[DATETIME]]채취일시\t[[DATE]]보고일\t[[TIME]]보고시각\n"
    "P1\t2026/06/01 09:30\t2026-06-01\t08:30\n"
    "P2\t2026-06-03T08:15:00\t06-Jun-2026\t083000\n"
    "P3\t2026-06-05 00:00:00\tabc\t2026-01-01\n"
    "</DATA>\n"
)


def _dataset():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "d.tame"
        p.write_text(SAMPLE, encoding="utf-8")
        return read_tame(p)


class DatetimeNormalizationTest(unittest.TestCase):
    def test_mixed_spellings_to_iso(self):
        ds, report = normalize_datetimes(_dataset())
        self.assertEqual(ds.df["채취일시"].tolist(),
                         ["2026-06-01T09:30:00", "2026-06-03T08:15:00", "2026-06-05T00:00:00"])
        self.assertEqual(ds.df["보고일"].tolist(), ["2026-06-01", "2026-06-06", "abc"])
        self.assertEqual(ds.df["보고시각"].tolist(), ["08:30:00", "08:30:00", "2026-01-01"])

    def test_unparsed_reported_not_destroyed(self):
        report = normalize_datetimes(_dataset())[1]
        date_row = report.loc[report["column"] == "보고일"].iloc[0]
        self.assertEqual(int(date_row["unparsed_cells"]), 1)
        issues = datetime_validation_issues(_dataset())
        self.assertTrue(any(i.value == "abc" and i.column == "보고일" for i in issues))
        self.assertTrue(any(i.value == "2026-01-01" and i.column == "보고시각" for i in issues))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tametools" / "src"))

from tametools.analysis import validate_dataset
from tametools.io import read_tame
from tametools.plugin_base import list_plugins, run_plugin


HAS_REPORT_DEPS = importlib.util.find_spec("docx") is not None and importlib.util.find_spec("matplotlib") is not None


def _read_tame_text(text: str):
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "sample.tame"
        path.write_text(textwrap.dedent(text).strip(), encoding="utf-8")
        return read_tame(path)


SAMPLE = """
<DATA>
[[ORDER_ID::ID(order)]]order_id\t[[SAMPLE_ID::ID(sample)]]sample_id\t[[TESTNAME::CATEGORY]]test\t[[ORDER_PLACED_AT::DATETIME]]order_at\t[[SPECIMEN_COLLECTED_AT::DATETIME]]collection_at\t[[LAB_RECEIVED_AT::DATETIME]]lab_at\t[[LAB_SECTION_RECEIVED_AT::DATETIME]]section_at\t[[TEST_STARTED_AT::DATETIME]]test_at\t[[RESULT_CREATED_AT::DATETIME]]result_at\t[[PRELIMINARY_REPORTED_AT::DATETIME]]prelim_at\t[[FINAL_REPORTED_AT::DATETIME]]final_at
O001\tS001\tCBC\t2026-06-25 08:00\t2026-06-25 08:15\t2026-06-25 08:35\t2026-06-25 08:45\t2026-06-25 08:55\t2026-06-25 09:10\t2026-06-25 09:20\t2026-06-25 09:35
O002\tS002\tCBC\t2026-06-25 09:00\t2026-06-25 09:20\t2026-06-25 09:50\t2026-06-25 10:05\t2026-06-25 10:15\t2026-06-25 10:35\t2026-06-25 10:45\t2026-06-25 11:10
O003\tS003\tChemistry\t2026-06-25 08:10\t2026-06-25 08:40\t2026-06-25 09:05\t2026-06-25 09:25\t2026-06-25 09:40\t2026-06-25 10:05\t2026-06-25 10:15\t2026-06-25 10:50
O004\tS004\tChemistry\t2026-06-25 10:00\t2026-06-25 10:30\t2026-06-25 11:10\t2026-06-25 11:35\t2026-06-25 11:55\t2026-06-25 12:30\t2026-06-25 12:40\t2026-06-25 13:20
O005\tS005\tCBC\t2026-06-25 12:00\t2026-06-25 12:15\t2026-06-25 12:50\t2026-06-25 12:45\t2026-06-25 13:05\t2026-06-25 13:20\t2026-06-25 13:25\t2026-06-25 13:40
</DATA>
"""


class LabTatPluginTests(unittest.TestCase):
    def test_plugin_is_registered(self) -> None:
        names = {plugin.name for plugin in list_plugins()}
        self.assertIn("LAB_TAT_ANALYSIS", names)
        self.assertIn("TAT_ANALYSIS", names)

    def test_lab_tat_analysis_calculates_stage_summary_and_chainable_dataset(self) -> None:
        dataset = _read_tame_text(SAMPLE)
        output = run_plugin(
            dataset,
            "LAB_TAT_ANALYSIS",
            dataset.meta,
            "LAB_TAT_ANALYSIS",
            {"TARGET_COLLECTION_TO_LAB_MINUTES": 25, "TARGET_ORDER_TO_FINAL_MINUTES": 180},
        )

        self.assertIsNotNone(output)
        self.assertIsNotNone(output.dataset)
        self.assertIn("stage_summary", output.tables)
        self.assertIn("row_stage_times", output.tables)
        self.assertIn("invalid_intervals", output.tables)
        self.assertEqual({chart["name"] for chart in output.charts}, {"LAB_TAT_MEDIAN", "LAB_TAT_P95", "LAB_TAT_BREACH_RATE"})
        self.assertIn("LAB_TAT_MEDIAN", output.dataset.meta["VISUALIZATIONS"])
        self.assertEqual(validate_dataset(output.dataset).issues, [])

        cbc_collection = output.table.loc[
            (output.table["group"] == "CBC") & (output.table["stage_key"] == "collection_to_lab")
        ].iloc[0]
        self.assertEqual(int(cbc_collection["n_total"]), 3)
        self.assertEqual(int(cbc_collection["n_valid"]), 3)
        self.assertAlmostEqual(float(cbc_collection["median_minutes"]), 30.0, places=3)
        self.assertEqual(int(cbc_collection["breached_count"]), 2)

        invalid = output.tables["invalid_intervals"]
        self.assertEqual(set(invalid["stage_key"]), {"lab_to_section"})
        self.assertEqual(set(invalid["status"]), {"negative"})

    def test_lab_tat_analysis_can_group_by_all(self) -> None:
        dataset = _read_tame_text(SAMPLE)
        output = run_plugin(dataset, "TAT_ANALYSIS", dataset.meta, "TAT_ANALYSIS", {"GROUP_BY": "NONE"})

        self.assertEqual(set(output.table["group"]), {"ALL"})
        total = output.table.loc[output.table["stage_key"] == "order_to_final"].iloc[0]
        self.assertEqual(int(total["n_valid"]), 5)
        self.assertGreater(float(total["p95_minutes"]), 100.0)

    @unittest.skipUnless(HAS_REPORT_DEPS, "report extras are not installed")
    def test_lab_tat_analysis_writes_docx_report_with_charts(self) -> None:
        dataset = _read_tame_text(SAMPLE)
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "lab_tat_report.docx"
            output = run_plugin(
                dataset,
                "LAB_TAT_ANALYSIS",
                dataset.meta,
                "LAB_TAT_ANALYSIS",
                {"REPORT_PATH": str(report_path), "TARGET_COLLECTION_TO_LAB_MINUTES": 25},
            )

            self.assertIn(str(report_path), output.files)
            self.assertTrue(report_path.exists())
            with zipfile.ZipFile(report_path) as archive:
                images = [name for name in archive.namelist() if name.startswith("word/media/") and name.endswith(".png")]
            self.assertTrue(images)

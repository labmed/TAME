from __future__ import annotations

import contextlib
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tametools" / "src"))

from tametools.cli import main as cli_main
from tametools.io import read_tame
from tametools.plugin_base import list_plugins, run_plugin
from tametools.reporting import chart_spec, render_chart_png, with_visualizations


HAS_REPORT_DEPS = importlib.util.find_spec("docx") is not None and importlib.util.find_spec("matplotlib") is not None


class PluginExtensibilityTests(unittest.TestCase):
    def sample_path(self) -> Path:
        return ROOT / "tutorial" / "05_plugins" / "sample_report_plugin.tame"

    @unittest.skipUnless(HAS_REPORT_DEPS, "report extras are not installed")
    def test_report_plugin_reuses_existing_plugins_and_writes_docx_with_chart(self) -> None:
        dataset = read_tame(self.sample_path())
        plugin_names = {plugin.name for plugin in list_plugins(dataset.meta, allow_external=True)}
        self.assertIn("COMBINED_CHEMISTRY_REPORT", plugin_names)

        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "combined_report.docx"
            output = run_plugin(
                dataset,
                "COMBINED_CHEMISTRY_REPORT",
                dataset.meta,
                "COMBINED_CHEMISTRY_REPORT",
                {"REPORT_PATH": str(report_path)},
                allow_external=True,
            )

            self.assertIsNotNone(output)
            self.assertIn("reference_interval", output.tables)
            self.assertIn("summary", output.tables)
            self.assertEqual(output.charts[0]["name"], "SUMMARY_MEDIAN")
            self.assertEqual(output.charts[0]["TABLE"], "summary")
            self.assertIn(str(report_path), output.files)
            self.assertTrue(report_path.exists())
            self.assertIn("SUMMARY_MEDIAN", output.dataset.meta["VISUALIZATIONS"])
            self.assertFalse([warning for warning in output.warnings or [] if "Missing required role result" in warning])

            with zipfile.ZipFile(report_path) as archive:
                names = archive.namelist()
            self.assertTrue(any(name.startswith("word/media/") and name.endswith(".png") for name in names))

    @unittest.skipUnless(HAS_REPORT_DEPS, "report extras are not installed")
    def test_cli_prints_report_plugin_charts_and_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "cli_report.docx"
            stdout = io.StringIO()
            stderr = io.StringIO()
            argv = [
                "tametools",
                "run-plugin",
                str(self.sample_path()),
                "COMBINED_CHEMISTRY_REPORT",
                "--allow-plugins",
                "--option",
                f"REPORT_PATH={report_path}",
            ]
            with patch.object(sys, "argv", argv), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = cli_main()

            self.assertEqual(exit_code, 0, stderr.getvalue())
            text = stdout.getvalue()
            self.assertIn("[reference_interval]", text)
            self.assertIn("[summary]", text)
            self.assertIn("[charts]", text)
            self.assertIn("chart: SUMMARY_MEDIAN", text)
            self.assertIn("[files]", text)
            self.assertIn(f"file: {report_path}", text)
            self.assertTrue(report_path.exists())

    @unittest.skipUnless(HAS_REPORT_DEPS, "report extras are not installed")
    def test_chart_helper_can_attach_meta_and_render_png(self) -> None:
        dataset = read_tame(self.sample_path())
        summary = run_plugin(dataset, "CHEMISTRY_ANALYSIS", dataset.meta, "SUMMARY", {"MODE": "RESULT_SUMMARY"})
        chart = chart_spec("SUMMARY_MEDIAN", type="bar", title="검사항목별 중앙값", x="검사항목명", y="중앙값")
        updated = with_visualizations(summary.dataset, [chart])

        self.assertIn("SUMMARY_MEDIAN", updated.meta["VISUALIZATIONS"])
        with tempfile.TemporaryDirectory() as tmpdir:
            png_path = render_chart_png(chart, {"main": summary.table}, Path(tmpdir) / "summary.png")
            self.assertTrue(png_path.exists())
            self.assertGreater(png_path.stat().st_size, 0)

    @unittest.skipUnless(HAS_REPORT_DEPS, "report extras are not installed")
    def test_chart_render_uses_portable_temp_mplconfigdir(self) -> None:
        previous = os.environ.pop("MPLCONFIGDIR", None)
        try:
            chart = chart_spec(
                "PORTABLE_TEMP",
                type="bar",
                title="Portable temp",
                x="label",
                y="value",
                rows=[{"label": "A", "value": 1}],
            )
            with tempfile.TemporaryDirectory() as tmpdir:
                png_path = render_chart_png(chart, {}, Path(tmpdir) / "portable.png")
                self.assertTrue(png_path.exists())
                self.assertEqual(Path(os.environ["MPLCONFIGDIR"]), Path(tempfile.gettempdir()) / "tametools-matplotlib")
        finally:
            if previous is None:
                os.environ.pop("MPLCONFIGDIR", None)
            else:
                os.environ["MPLCONFIGDIR"] = previous

    def test_clinical_stats_outputs_first_class_chart_specs(self) -> None:
        dataset = read_tame(self.sample_path())
        output = run_plugin(dataset, "QC_ANALYSIS", dataset.meta, "QC_ANALYSIS", {"MODE": "PRECISION"})

        self.assertIsNotNone(output)
        self.assertTrue(output.charts)
        self.assertEqual(output.charts[0]["name"], "QC_CV_PERCENT")
        self.assertIn("QC_CV_PERCENT", output.dataset.meta["VISUALIZATIONS"])


class IndependentReportPluginTests(unittest.TestCase):
    """제3자(발주사) 독립 검증 플러그인 회귀 테스트.

    수행사 예제와 다른 플러그인 조합(QC + 이상률 + 추세), 다른 차트 타입(BAR+LINE),
    컬럼명이 전부 비표준인 입력에서 차트 포함 docx 보고서가 만들어지는지 고정한다.
    """

    def sample_path(self) -> Path:
        return ROOT / "tutorial" / "05_plugins" / "sample_indep_lab_report.tame"

    def test_input_uses_non_standard_column_names(self) -> None:
        # 컬럼명 독립성 검증의 전제: 입력 컬럼명이 비표준(태그만 표준)이어야 한다.
        dataset = read_tame(self.sample_path())
        names = [column.name for column in dataset.columns]
        self.assertIn("측정치", names)   # RESULT 컬럼이 표준명("보고값")이 아님
        self.assertIn("검사명", names)   # ITEM 컬럼이 표준명("검사항목명")이 아님
        self.assertEqual(dataset.first_column_with_tag("RESULT").name, "측정치")

    @unittest.skipUnless(HAS_REPORT_DEPS, "report extras are not installed")
    def test_report_plugin_reuses_three_plugins_and_renders_mixed_charts(self) -> None:
        dataset = read_tame(self.sample_path())
        self.assertIn("INDEP_LAB_REPORT", {plugin.name for plugin in list_plugins(dataset.meta, allow_external=True)})

        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "indep_report.docx"
            output = run_plugin(
                dataset,
                "INDEP_LAB_REPORT",
                dataset.meta,
                "INDEP_LAB_REPORT",
                {"REPORT_PATH": str(report_path)},
                allow_external=True,
            )

            self.assertIsNotNone(output)
            # 기존 플러그인 3종이 재사용되어 각각 표를 만든다.
            self.assertEqual(set(output.tables), {"qc_precision", "abnormal_rate", "result_trend"})
            self.assertFalse(output.tables["qc_precision"].empty)

            # 차트는 BAR 2개 + LINE 1개 (BAR 일변도가 아님).
            chart_types = [chart["TYPE"] for chart in output.charts]
            self.assertEqual(chart_types, ["BAR", "BAR", "LINE"])
            self.assertEqual([chart["name"] for chart in output.charts], ["QC_CV", "ABNORMAL_RATE", "TREND_MA"])
            for chart in output.charts:
                self.assertIn(chart["name"], output.dataset.meta["VISUALIZATIONS"])

            # 차트가 role 없음 경고로 비지 않았는지(컬럼명 비표준에도 태그로 결과 산출).
            self.assertFalse([w for w in output.warnings or [] if "Missing required role result" in w])

            # docx에 차트 이미지 3개가 실제로 내장된다.
            self.assertIn(str(report_path), output.files)
            self.assertTrue(report_path.exists())
            with zipfile.ZipFile(report_path) as archive:
                images = [n for n in archive.namelist() if n.startswith("word/media/") and n.endswith(".png")]
            self.assertEqual(len(images), 3)

    @unittest.skipUnless(HAS_REPORT_DEPS, "report extras are not installed")
    def test_cli_runs_independent_report_plugin(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "indep_cli_report.docx"
            stdout, stderr = io.StringIO(), io.StringIO()
            argv = [
                "tametools",
                "run-plugin",
                str(self.sample_path()),
                "INDEP_LAB_REPORT",
                "--allow-plugins",
                "--option",
                f"REPORT_PATH={report_path}",
            ]
            with patch.object(sys, "argv", argv), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = cli_main()

            self.assertEqual(exit_code, 0, stderr.getvalue())
            text = stdout.getvalue()
            self.assertIn("[qc_precision]", text)
            self.assertIn("[abnormal_rate]", text)
            self.assertIn("[result_trend]", text)
            self.assertIn("chart: TREND_MA type=LINE", text)
            self.assertIn(f"file: {report_path}", text)
            self.assertTrue(report_path.exists())

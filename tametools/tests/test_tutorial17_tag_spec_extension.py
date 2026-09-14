from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tametools" / "src"))

from tametools.age import age_value_profile
from tametools.cli import main as cli_main
from tametools.io import read_tame
from tametools.plugin_base import run_plugin


TUTORIAL17 = ROOT / "tutorial" / "17_tag_spec_extension"
CUSTOM_AGE5 = TUTORIAL17 / "custom_tags_age5.tame"
WIDE_PIVOT = TUTORIAL17 / "wide_pivot_context.tame"
RUNNER = TUTORIAL17 / "run_tag_spec_examples.py"


class Tutorial17TagSpecExtensionTest(unittest.TestCase):
    def _run_cli(self, *args: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["tametools", *args]), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = cli_main()
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def test_age5_review_and_chemistry_analysis_use_five_year_bins(self) -> None:
        dataset = read_tame(CUSTOM_AGE5)

        age_profile = age_value_profile(dataset)
        self.assertEqual(set(age_profile["preferred_band_width"]), {5})
        self.assertEqual(set(age_profile["preferred_band"]), {"1-4", "5-9", "10-14"})

        code, output, stderr = self._run_cli("review", str(CUSTOM_AGE5))
        self.assertEqual(code, 0, stderr)
        self.assertIn("[review:AGE]", output)
        self.assertIn("distribution_5y: 1-4: 2, 5-9: 2, 10-14: 2", output)
        self.assertNotIn("distribution_10y: 1-9: 4, 10-19: 2", output)

        chemistry = run_plugin(dataset, "CHEMISTRY_ANALYSIS", dataset.meta, "CHEMISTRY_ANALYSIS", {"MODE": "AGE_SEX_RESULT"})
        self.assertEqual(set(chemistry.dataset.df["연령그룹"]), {"1-4", "5-9", "10-14"})
        self.assertEqual(set(chemistry.dataset.df["검사항목명"]), {"AST", "ALT"})

    def test_wide_pivot_context_flag_uses_per_column_reference_limits(self) -> None:
        dataset = read_tame(WIDE_PIVOT)
        output = run_plugin(dataset, "ABNORMAL_FLAG", dataset.meta, "ABNORMAL_FLAG", {"MODE": "FLAG"})

        self.assertIn("source=PIVOT_CONTEXT", output.message)
        self.assertEqual(set(output.table["test"]), {"AST", "ALT"})
        counts = {
            (str(row["test"]), str(row["flag"])): int(row["count"])
            for _, row in output.table.iterrows()
        }
        self.assertEqual(counts[("AST", "N")], 2)
        self.assertEqual(counts[("AST", "H")], 1)
        self.assertEqual(counts[("ALT", "N")], 2)
        self.assertEqual(counts[("ALT", "H")], 1)

        self.assertIn("AST_이상플래그", output.dataset.df.columns)
        self.assertIn("ALT_이상플래그", output.dataset.df.columns)
        self.assertEqual(output.dataset.column_metadata("AST_이상플래그")["REFERENCE_SOURCE"], "PIVOT_CONTEXT")
        self.assertEqual(output.dataset.column_metadata("ALT_이상플래그")["REFERENCE_SOURCE"], "PIVOT_CONTEXT")

    def test_runner_captures_outputs_and_self_validates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env = os.environ.copy()
            env["PYTHONPATH"] = f"{ROOT / 'tametools' / 'src'}:{ROOT}:" + env.get("PYTHONPATH", "")
            completed = subprocess.run(
                [sys.executable, str(RUNNER), "--output-dir", tmpdir],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stdout)
            output_dir = Path(tmpdir)
            self.assertTrue((output_dir / "execution_log.md").exists())
            self.assertTrue((output_dir / "01_review_age5.stdout.txt").exists())
            self.assertTrue((output_dir / "03_age_sex_result.stdout.txt").exists())
            self.assertTrue((output_dir / "04_abnormal_flag.stdout.txt").exists())

            log = (output_dir / "execution_log.md").read_text(encoding="utf-8")
            self.assertIn("PASS: review AGE profile uses AGE_BIN_WIDTH=5", log)
            self.assertIn("PASS: ABNORMAL_FLAG uses per-column PIVOT_CONTEXT references", log)

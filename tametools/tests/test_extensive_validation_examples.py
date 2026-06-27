from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCENARIO_PATH = ROOT / "tutorial" / "15_extensive_validation" / "scenario_matrix.py"
spec = importlib.util.spec_from_file_location("extensive_validation_scenario_matrix", SCENARIO_PATH)
scenario_matrix = importlib.util.module_from_spec(spec)
assert spec is not None and spec.loader is not None
sys.modules[spec.name] = scenario_matrix
spec.loader.exec_module(scenario_matrix)

SCENARIOS = scenario_matrix.build_scenarios()


class ExtensiveValidationExamplesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls.tmp_path = Path(cls._tmpdir.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmpdir.cleanup()

    def test_000_extensive_validation_has_100_plus_scenarios(self) -> None:
        self.assertGreaterEqual(len(SCENARIOS), 100)


def _make_scenario_test(scenario):
    def test(self) -> None:
        scenario_matrix.run_scenario(scenario, self.tmp_path)

    test.__name__ = f"test_{scenario.id.lower()}"
    test.__doc__ = scenario.purpose
    return test


for scenario in SCENARIOS:
    setattr(ExtensiveValidationExamplesTest, f"test_{scenario.id.lower()}", _make_scenario_test(scenario))

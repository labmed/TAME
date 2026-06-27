from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "tametools" / "src"))
sys.path.insert(0, str(HERE))
os.chdir(REPO_ROOT)

from scenario_matrix import build_scenarios, run_scenario, scenario_summary  # noqa: E402


def main() -> int:
    scenarios = build_scenarios()
    failures: list[tuple[str, str]] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        for scenario in scenarios:
            try:
                run_scenario(scenario, tmp_path)
            except Exception as exc:  # pragma: no cover - command-line reporting
                failures.append((scenario.id, str(exc)))

    summary = scenario_summary()
    print(f"총 {len(scenarios)}개 extensive validation scenario")
    for category, count in summary.groupby("category", observed=False).size().sort_index().items():
        print(f"  {category}: {int(count)}")
    if failures:
        print("\nFAIL")
        for scenario_id, message in failures:
            print(f"  {scenario_id}: {message}")
        return 1
    print("\nPASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

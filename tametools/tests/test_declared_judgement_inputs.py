"""A verdict needs its inputs: sigma needs a declared bias, and an outside-rate verdict needs enough values."""
import pytest

from tametools.io import read_tame
from tametools.plugin_base import run_plugin

QC = """<META>
[COLUMN.level]
ID = "level"
TAGS = ["QC_LEVEL", "CATEGORY"]

[COLUMN.glucose]
ID = "glucose"
UNIT = "mg/dL"
TAGS = ["RESULT", "NUM"]

[COLUMN.sodium]
ID = "sodium"
UNIT = "mmol/L"
TAGS = ["RESULT", "NUM"]
</META>
<DATA>
level\tglucose\tsodium
L\t99\t139
L\t101\t141
H\t198\t149
H\t202\t151
</DATA>
"""


@pytest.fixture
def qc(tmp_path):
    path = tmp_path / "qc.tame"
    path.write_text(QC, encoding="utf-8")
    return read_tame(path)


def sigma(data, **options):
    output = run_plugin(data, "QC_ANALYSIS", data.meta, "QC_ANALYSIS", {"MODE": "SIGMA", "RESULT_IDS": ["glucose", "sodium"], **options})
    table = output.table.set_index(["result_id", "level"])
    return table, output.warnings


def test_sigma_needs_a_declared_bias(qc):
    table, warnings = sigma(qc, TEA=10)
    assert table.sigma.isna().all() and set(table.sigma_status) == {"bias_missing"}
    assert sum("no BIAS is declared" in warning for warning in warnings) == 4

    table, warnings = sigma(qc, TEA=10, BIAS=0)                 # a measured bias of zero is said, not assumed
    assert table.loc[("glucose", "L"), "sigma"] == 7.07 and set(table.sigma_status) == {"evaluated"} and not warnings
    assert table.loc[("glucose", "L"), ["tea_percent", "bias_percent"]].tolist() == [10.0, 0.0]

    table, _ = sigma(qc, TEA=10, BIAS=-3)                       # the size of the bias counts, not its direction
    assert table.loc[("glucose", "L"), "sigma"] == 4.95


def test_tea_and_bias_can_differ_by_analyte_and_level(qc):
    table, warnings = sigma(qc, TEA={"glucose": {"L": 10, "H": 8}, "sodium": 4}, BIAS={"glucose": 1, "sodium": {"L": 0.5}})
    assert table.loc[("glucose", "L"), ["tea_percent", "bias_percent"]].tolist() == [10.0, 1.0]
    assert table.loc[("glucose", "H"), ["tea_percent", "bias_percent"]].tolist() == [8.0, 1.0]
    assert table.loc[("sodium", "L"), ["tea_percent", "bias_percent", "sigma_status"]].tolist() == [4.0, 0.5, "evaluated"]
    assert table.loc[("sodium", "H"), "sigma_status"] == "bias_missing"      # nothing declared for that level
    assert len(warnings) == 1 and "sodium" in warnings[0]

    by_level, _ = sigma(qc, TEA={"L": 10, "H": 8}, BIAS={"L": 0, "H": 0})
    assert by_level.tea_percent.tolist() == [8.0, 10.0, 8.0, 10.0] or sorted(by_level.tea_percent.tolist()) == [8.0, 8.0, 10.0, 10.0]


def test_a_missing_tea_is_said_and_a_wrong_one_is_an_error(qc):
    table, warnings = sigma(qc, TEA={"L": 10}, BIAS=0)
    assert table.loc[("glucose", "H"), "sigma_status"] == "tea_missing" and any("no TEA is declared" in warning for warning in warnings)
    for options, message in (({"TEA": 10, "BIAS": {"L": "three"}}, "BIAS.L must be a number"),
                             ({"TEA": {"L": 0}, "BIAS": 0}, "TEA.L must be a finite number in percent above 0"),
                             ({"TEA": [10], "BIAS": 0}, "TEA must be a number")):
        with pytest.raises(ValueError, match=message):
            sigma(qc, **options)
    precision = run_plugin(qc, "QC_ANALYSIS", qc.meta, "QC_ANALYSIS", {"MODE": "PRECISION", "RESULT_IDS": ["glucose"]}).table
    assert "sigma" not in precision.columns                     # precision alone asks for neither


RI = "<META>\n[COLUMN.alt]\nID = \"alt\"\nUNIT = \"U/L\"\nTAGS = [\"RESULT\", \"NUM\"]\n</META>\n<DATA>\nalt\n{values}\n</DATA>\n"


@pytest.mark.parametrize("count, verdict", [(5, "insufficient_n"), (19, "insufficient_n"), (20, "pass"), (40, "pass")])
def test_the_older_reference_interval_plugin_gives_no_verdict_on_a_handful_of_values(tmp_path, count, verdict):
    path = tmp_path / "ri.tame"
    path.write_text(RI.format(values="\n".join(str(1 + index % 9) for index in range(count))), encoding="utf-8")
    data = read_tame(path)
    output = run_plugin(data, "REFERENCE_INTERVAL", data.meta, "REFERENCE_INTERVAL", {"VERIFY_LOW": 0, "VERIFY_HIGH": 10})
    assert output.table.verify_pass.tolist() == [verdict] and output.table.verify_outside_rate.tolist() == [0.0]
    assert any("No verification verdict" in warning for warning in output.warnings) == (verdict == "insufficient_n")


def test_the_verdict_still_fails_when_too_many_values_are_outside(tmp_path):
    path = tmp_path / "ri.tame"
    path.write_text(RI.format(values="\n".join(str(value) for value in [*range(1, 18), 50, 60, 70])), encoding="utf-8")
    data = read_tame(path)
    output = run_plugin(data, "REFERENCE_INTERVAL", data.meta, "REFERENCE_INTERVAL", {"VERIFY_LOW": 0, "VERIFY_HIGH": 20})
    assert output.table.verify_pass.tolist() == ["fail"] and output.table.verify_n_above.tolist() == [3]

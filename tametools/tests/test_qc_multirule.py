"""Westgard multirule engine and the WESTGARD_QC plugin."""
import importlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tametools.io import read_tame
from tametools.plugin_base import run_plugin
from tametools.plugin_base import list_plugins

list_plugins()      # WESTGARD_QC is a plugin package: use its modules as tametools loads them
_qc_multirule = importlib.import_module("tametools_dropin_westgard_qc.multirule")
evaluate_multirule = _qc_multirule.evaluate_multirule
parse_rule = _qc_multirule.parse_rule
rule_plan = _qc_multirule.rule_plan

ROOT = Path(__file__).resolve().parents[2]
NAN = float("nan")


def points(**levels):
    """One analyte on one instrument; ``levels`` maps a level name to its z-score per run."""
    rows = []
    for level, values in levels.items():
        for run, z in enumerate(values, 1):
            if z is not None:  # None: the level was not measured in that run
                rows.append({"group": "glucose", "run": f"run-{run:02d}", "order": run, "material": level, "z": z})
    return pd.DataFrame(rows)


def judged(frame, **options):
    return pd.concat([frame, evaluate_multirule(frame, **options)], axis=1)


def row(result, level, run):
    return result.loc[(result.material == level) & (result.run == f"run-{run:02d}")].iloc[0]


@pytest.mark.parametrize("text, expected", [
    ("1_2s", ("1_2s", "single", 1, 2.0, "random")),
    ("1_3S", ("1_3s", "single", 1, 3.0, "random")),
    ("1_2.5s", ("1_2.5s", "single", 1, 2.5, "random")),
    ("2_2s", ("2_2s", "consecutive", 2, 2.0, "systematic")),
    ("4_1s", ("4_1s", "consecutive", 4, 1.0, "systematic")),
    ("10x", ("10x", "consecutive", 10, 0.0, "systematic")),
    ("r_4s", ("R_4s", "range", 2, 2.0, "random")),
    ("2of3_2s", ("2of3_2s", "count", 3, 2.0, "systematic")),
])
def test_rule_names_are_parsed(text, expected):
    rule = parse_rule(text)
    assert (rule.name, rule.kind, rule.n, rule.limit, rule.error) == expected


@pytest.mark.parametrize("text", ["", "2s", "0_2s", "1x", "3of3_2s", "R_0s", "westgard"])
def test_invalid_rule_names_are_rejected(text):
    with pytest.raises(ValueError):
        parse_rule(text)


def test_one_measurement_beyond_2s_warns_and_beyond_3s_rejects():
    result = judged(points(L1=[0.1, 2.4, -0.3, -3.1], L2=[0.0, 0.2, 0.1, 0.3]))
    assert row(result, "L1", 1).decision == "IN_CONTROL"
    warned = row(result, "L1", 2)
    assert (warned.warning, warned.violation, warned.decision) == ("1_2s", "NONE", "WARNING")
    rejected = row(result, "L1", 4)
    assert (rejected.warning, rejected.violation, rejected.error_type, rejected.decision) == ("1_2s", "1_3s", "random", "REJECT")
    # The other level of a rejected run is not itself flagged, but the run is.
    partner = row(result, "L2", 4)
    assert (partner.decision, partner.run_decision) == ("IN_CONTROL", "REJECT")
    assert row(result, "L2", 2).run_decision == "WARNING"


def test_2_2s_across_runs_flags_the_run_that_completes_it():
    result = judged(points(L1=[2.2, 2.3, 0.0, -2.1, -2.6], L2=[0.0, 0.1, 0.0, 0.2, 0.1]))
    assert row(result, "L1", 1).violation == "NONE"          # first excursion: warning only
    second = row(result, "L1", 2)
    assert second.violation == "2_2s" and second.rule_scope == "1_2s:point; 2_2s:across_run"
    assert second.error_type == "systematic"
    assert row(result, "L1", 4).violation == "NONE"          # opposite side starts a new sequence
    assert row(result, "L1", 5).violation == "2_2s"


def test_2_2s_within_run_needs_the_same_side_and_r_4s_opposite_sides():
    result = judged(points(L1=[2.1, 2.3, 0.5], L2=[2.4, -2.2, 0.4]))
    same = result.loc[result.run == "run-01"]
    assert set(same.violation) == {"2_2s"} and set(same.rule_scope) == {"1_2s:point; 2_2s:within_run"}
    opposite = result.loc[result.run == "run-02"]
    assert list(opposite.sort_values("material").violation) == ["R_4s", "R_4s"]        # run 1 was rejected, so 2_2s across runs starts again
    assert row(result, "L2", 2).error_type == "random"
    counted = judged(points(L1=[2.1, 2.3, 0.5], L2=[2.4, -2.2, 0.4]), after_reject="CONTINUE")
    assert row(counted, "L1", 2).violation == "2_2s, R_4s" and row(counted, "L1", 2).error_type == "random+systematic"


def test_r_4s_is_not_applied_between_runs():
    result = judged(points(L1=[2.5, -2.5, 2.5], L2=[0.0, 0.0, 0.0]))
    assert set(result.violation) == {"NONE"}
    assert list(result.loc[result.material == "L1", "decision"]) == ["WARNING"] * 3


def test_4_1s_within_one_material_and_across_materials():
    within = judged(points(L1=[1.2, 1.5, 1.1, 1.3, 1.4, 0.2], L2=[0.0, -0.2, 0.1, 0.0, -0.1, 0.0]))
    assert [row(within, "L1", run).violation for run in range(1, 7)] == ["NONE"] * 3 + ["4_1s", "NONE", "NONE"]   # counting restarts after the rejected run
    counted = judged(points(L1=[1.2, 1.5, 1.1, 1.3, 1.4, 0.2], L2=[0.0, -0.2, 0.1, 0.0, -0.1, 0.0]), after_reject="CONTINUE")
    assert [row(counted, "L1", run).violation for run in range(1, 7)] == ["NONE"] * 3 + ["4_1s", "4_1s", "NONE"]
    assert row(within, "L1", 4).rule_scope == "4_1s:across_run"
    assert row(within, "L1", 4).warning == "NONE"            # a shift is rejected without any 2 SD excursion
    across = judged(points(L1=[0.1, -1.2, -1.5], L2=[0.0, -1.1, -1.3]))
    assert set(across.loc[across.run == "run-03", "rule_scope"]) == {"4_1s:across_run_material"}
    assert set(across.loc[across.run == "run-02", "violation"]) == {"NONE"}


def test_10x_counts_strictly_one_side_of_the_mean():
    above = [0.3, 0.5, 0.2, 0.6, 0.4, 0.1, 0.7, 0.3, 0.2]
    quiet = [0.1, -0.1] * 5
    result = judged(points(L1=[*above, 0.4], L2=quiet))
    assert row(result, "L1", 10).violation == "10x" and row(result, "L1", 9).violation == "NONE"
    assert set(judged(points(L1=[*above[:4], 0.0, *above[4:], 0.4], L2=[*quiet, 0.1])).violation) == {"NONE"}  # exactly on the mean breaks it
    both = judged(points(L1=[0.2, 0.4, 0.1, 0.3, 0.5], L2=[0.3, 0.1, 0.6, 0.2, 0.4]))
    assert set(both.loc[both.run == "run-05", "rule_scope"]) == {"10x:across_run_material"}
    assert set(both.loc[both.run == "run-04", "violation"]) == {"NONE"}


def test_missing_result_breaks_a_sequence_but_an_unmeasured_level_does_not():
    missing = judged(points(L1=[2.2, NAN, 2.3], L2=[0.0, 0.0, 0.0]))
    assert row(missing, "L1", 3).violation == "NONE"
    assert row(missing, "L1", 2).decision == "NOT_EVALUATED"
    assert row(missing, "L2", 2).run_decision == "NOT_EVALUATED"   # a run with a missing level is not called in control
    skipped = judged(points(L1=[2.2, None, 2.3], L2=[0.0, 0.0, 0.0]))
    assert row(skipped, "L1", 3).violation == "2_2s"


def test_classic_trigger_inspects_rejection_rules_only_after_a_warning():
    frame = points(L1=[1.2, 1.5, 1.1, 1.3, 2.1], L2=[0.0, -0.2, 0.1, 0.0, -0.1])
    always = judged(frame)
    assert row(always, "L1", 4).violation == "4_1s"
    classic = judged(frame, trigger="1_2S")
    assert row(classic, "L1", 4).violation == "NONE"          # no 2 SD excursion in that run
    assert row(classic, "L1", 5).violation == "4_1s"          # the 1_2s warning triggers inspection


@pytest.mark.parametrize("options", [
    {"rules": ["1_3s"], "warning_rules": []},                    # 1_2s not in use at all
    {"rules": ["1_3s"], "warning_rules": ["10x"]},               # another rule is the only warning
    {"rules": ["1_2s", "1_3s"], "warning_rules": []},            # 1_2s itself rejects
])
def test_the_classic_trigger_is_the_2_sd_excursion_not_the_use_made_of_1_2s(options):
    beyond = judged(points(L1=[4.0]), trigger="1_2S", **options).iloc[0]
    assert "1_3s" in beyond.violation and beyond.run_decision == "REJECT"
    inside = judged(points(L1=[1.2, 1.5, 1.1, 1.3]), rules=["4_1s"], warning_rules=[], trigger="1_2S")
    assert set(inside.violation) == {"NONE"}                     # no control beyond 2 SD: nothing is inspected
    pair = judged(points(L1=[2.2, 2.2]), rules=["2_2s"], warning_rules=["1_2.5s"], trigger="1_2S")
    assert row(pair, "L1", 2).violation == "2_2s"                # 2.2 is beyond 2 SD although no warning rule fired


@pytest.mark.parametrize("values, expected", [
    ([2.2, 2.3, 0.0], ["NONE", "2of3_2s", "NONE"]),              # decided by the second value; the third cannot undo it
    ([2.2, 0.0, 2.3], ["NONE", "NONE", "2of3_2s"]),
    ([0.0, 2.2, 2.3], ["NONE", "NONE", "2of3_2s"]),
    ([-2.2, -2.3, 0.0], ["NONE", "2of3_2s", "NONE"]),
    ([2.2, -2.3, 0.0], ["NONE", "NONE", "NONE"]),                # opposite sides do not add up
    ([2.2, 0.0, 0.0, 2.3], ["NONE", "NONE", "NONE", "NONE"]),    # three runs apart: not within three
])
def test_m_of_n_is_flagged_on_the_run_that_completes_it_wherever_it_starts(values, expected):
    result = judged(points(L1=values), rules=["2of3_2s"], warning_rules=[], scope="WITHIN_MATERIAL", after_reject="CONTINUE")
    assert list(result.violation) == expected
    two_levels = judged(points(L1=[2.2], L2=[2.4]), rules=["2of3_2s"], warning_rules=[])
    assert list(two_levels.violation) == ["2of3_2s", "2of3_2s"]  # two controls of one run, both beyond the same limit


def test_scope_and_rule_selection():
    frame = points(L1=[2.1, 2.3], L2=[2.4, 0.0])
    assert row(judged(frame, scope="WITHIN_MATERIAL"), "L2", 1).violation == "NONE"
    assert row(judged(frame, scope="ACROSS_MATERIAL"), "L1", 2).violation == "NONE"
    assert set(judged(frame, rules=["1_3s"]).violation) == {"NONE"}
    assert row(judged(frame, rules=["1_2s"], warning_rules=[]), "L1", 1).decision == "REJECT"
    with pytest.raises(ValueError, match="both a warning and a rejection"):
        judged(frame, rules=["1_2s"])
    three = judged(points(L1=[2.2], L2=[2.4], L3=[0.1]), rules=["2of3_2s"])
    assert list(three.sort_values("material").violation) == ["2of3_2s", "2of3_2s", "NONE"]


def test_appending_runs_never_changes_earlier_decisions():
    rng = np.random.default_rng(20261111)
    z = rng.normal(0.0, 1.0, size=(60, 2)).round(2)
    z[10:22, 0] = np.abs(z[10:22, 0]) + 1.05                 # a sustained shift: 4_1s, again after every restart
    z[48:58] = np.column_stack([[0.3, -0.2] * 5, np.linspace(0.2, 0.6, 10)])   # ten quiet runs with level 2 just above the mean: 10x
    z[25] = [2.5, -2.4]                                      # R_4s
    z[30:32, 1] = [2.3, 2.6]                                 # 2_2s
    z[40, 0] = 3.4                                           # 1_3s
    z[45, 0] = np.nan                                        # a missing result
    complete = points(L1=list(z[:, 0]), L2=list(z[:, 1]))
    final = judged(complete)
    assert {"1_3s", "2_2s", "R_4s", "4_1s", "10x"} <= {name for cell in final.violation for name in cell.split(", ")}
    for runs in (1, 5, 18, 37, 59):
        partial = judged(points(L1=list(z[:runs, 0]), L2=list(z[:runs, 1])))
        expected = final.loc[final.order <= runs].reset_index(drop=True)
        pd.testing.assert_frame_equal(partial.reset_index(drop=True), expected)


def test_duplicate_measurement_in_a_run_is_an_error():
    frame = pd.concat([points(L1=[0.1]), points(L1=[0.2])], ignore_index=True)
    with pytest.raises(ValueError, match="Duplicate QC measurement"):
        evaluate_multirule(frame)


def test_a_rejected_run_restarts_skips_or_keeps_the_consecutive_counts():
    # Level 1 stays just above the mean; run 5 is rejected by 1_3s.
    above = [0.3, 0.4, 0.2, 0.5, 3.4, 0.3, 0.4, 0.2, 0.5, 0.3, 0.4, 0.3, 0.2, 0.4, 0.3]
    frame = points(L1=above, L2=[0.1, -0.1] * 7 + [0.1])

    def first_10x(policy):
        result = judged(frame, after_reject=policy)
        return next((run for run in range(1, 16) if "10x" in row(result, "L1", run).violation), None)

    assert first_10x("CONTINUE") == 10      # every value counts, the rejected one included
    assert first_10x("SKIP") == 11          # the rejected value is left out: runs 1-4 and 6-11 make ten
    assert first_10x("RESTART") == 15       # counting starts again: runs 6-15 (the default, as Westgard describes)
    assert first_10x("RESTART") == next(run for run in range(1, 16) if "10x" in row(judged(frame), "L1", run).violation)

    # The restart covers every control level of the analyte, not only the level that failed.
    shift = points(L1=[0.0, 0.1, 0.0, 3.5, 0.0, 0.1], L2=[0.0, 1.2, 1.3, 1.4, 1.5, 1.6])   # run 4 is rejected through level 1
    level_2 = {policy: [row(judged(shift, after_reject=policy), "L2", run).violation for run in (5, 6)] for policy in ("CONTINUE", "SKIP", "RESTART")}
    assert level_2 == {"CONTINUE": ["4_1s", "4_1s"], "SKIP": ["NONE", "4_1s"], "RESTART": ["NONE", "NONE"]}
    with pytest.raises(ValueError, match="AFTER_REJECT"):
        judged(shift, after_reject="FORGET")


def test_r_4s_can_be_read_as_a_count_or_as_a_range():
    # Run 1: +2.6 and -1.8 SD (4.4 SD apart, but only one value beyond 2 SD). Run 2: +2.2 and -2.1 SD.
    frame = points(L1=[2.6, 2.2], L2=[-1.8, -2.1])
    count = judged(frame, rules=["R_4s"])
    assert [set(count.loc[count.run == run, "violation"]) for run in ("run-01", "run-02")] == [{"NONE"}, {"R_4s"}]
    ranged = judged(frame, rules=["R_4s"], rule_forms={"R_4s": "RANGE"})
    assert [set(ranged.loc[ranged.run == run, "violation"]) for run in ("run-01", "run-02")] == [{"R_4s"}, {"R_4s"}]
    assert rule_plan({"RULE_SETTINGS": {"r_4s": {"FORM": "range"}}}).rule_forms == {"R_4s": "RANGE"}
    with pytest.raises(ValueError, match="not a range rule in use"):
        judged(frame, rule_forms={"2_2s": "RANGE"})


# ---------------------------------------------------------------------------- per-rule configuration

def planned(frame, settings, result_id=None):
    plan = rule_plan(settings, result_id=result_id)
    return judged(frame, rules=plan.rules, warning_rules=plan.warning_rules, scope=plan.scope, trigger=plan.trigger,
                  rule_scopes=plan.rule_scopes, after_reject=plan.after_reject, rule_forms=plan.rule_forms)


# For every rule of the classic multirule: data whose last level-1 measurement triggers that rule (and no other rejection rule).
ONE_RULE = {
    "1_2s": points(L1=[2.4], L2=[0.1]),
    "1_3s": points(L1=[3.2], L2=[0.1]),
    "2_2s": points(L1=[2.2, 2.3], L2=[0.0, 0.1]),
    "R_4s": points(L1=[2.2], L2=[-2.3]),
    "4_1s": points(L1=[1.2, 1.3, 1.4, 1.5], L2=[0.0, 0.1, -0.1, 0.0]),
    "10x": points(L1=[0.3] * 10, L2=[0.1, -0.1] * 5),
}


@pytest.mark.parametrize("name", list(ONE_RULE))
def test_each_rule_can_be_rejection_warning_or_off_on_its_own(name):
    frame = ONE_RULE[name]
    last_run = int(frame.order.max())
    only = lambda action: {"RULES": [], "WARNING_RULES": [], "RULE_SETTINGS": {name: {"ACTION": action}}}

    rejected = row(planned(frame, only("REJECT")), "L1", last_run)
    assert (rejected.violation, rejected.warning, rejected.decision) == (name, "NONE", "REJECT")
    warned = row(planned(frame, only("WARNING")), "L1", last_run)
    assert (warned.violation, warned.warning, warned.decision) == ("NONE", name, "WARNING")
    off = planned(frame, only("OFF"))
    assert set(off.decision) == {"IN_CONTROL"}

    # Switching one rule off in the default set leaves the others in force, and nothing else fires here.
    without = planned(frame, {"RULE_SETTINGS": {name: {"ACTION": "OFF"}}})
    assert name not in {rule for cell in [*without.violation, *without.warning] for rule in cell.split(", ")}
    default = row(planned(frame, {}), "L1", last_run)
    assert name in {*default.violation.split(", "), *default.warning.split(", ")}


def test_rule_plan_resolves_lists_single_rules_and_analytes():
    default = rule_plan()
    assert (default.warning_rules, default.rules) == (("1_2s",), ("1_3s", "2_2s", "R_4s", "4_1s", "10x"))
    assert (default.scope, default.trigger, default.rule_scopes) == ("BOTH", "ALWAYS", {})
    assert (default.after_reject, default.rule_forms) == ("RESTART", {})
    assert default.scope_for("2_2s") == "BOTH" and default.scope_for("1_3s") == "" and default.scope_for("R_4s") == ""

    strict = rule_plan({"RULES": ["1_2s", "1_3S"]})                       # listing 1_2s as a rejection rule moves it out of the warnings
    assert (strict.warning_rules, strict.rules) == ((), ("1_2s", "1_3s"))

    settings = {
        "RULE_SETTINGS": {"4_1s": {"ACTION": "WARNING", "SCOPE": "WITHIN_MATERIAL"}, "10X": {"ACTION": "OFF"},
                          "8x": {"ACTION": "REJECT", "SCOPE": "ACROSS_MATERIAL"}, "2_2s": {"SCOPE": "ACROSS_MATERIAL"}},
        "BY_RESULT": {"glucose": {"RULES": ["1_3s"], "WARNING_RULES": [], "TRIGGER": "1_2S"},
                      "alt": {"RULE_SETTINGS": {"4_1s": {"ACTION": "REJECT"}, "8x": {"ACTION": "OFF"}}}},
    }
    shared = rule_plan(settings, result_id="creatinine")
    assert shared.warning_rules == ("1_2s", "4_1s") and shared.rules == ("1_3s", "2_2s", "R_4s", "8x")
    assert shared.rule_scopes == {"4_1s": "WITHIN_MATERIAL", "8x": "ACROSS_MATERIAL", "2_2s": "ACROSS_MATERIAL"}
    glucose = rule_plan(settings, result_id="glucose")
    assert (glucose.warning_rules, glucose.rules, glucose.trigger, glucose.rule_scopes) == ((), ("1_3s",), "1_2S", {})
    alt = rule_plan(settings, result_id="alt")
    assert alt.warning_rules == ("1_2s",) and alt.rules == ("1_3s", "2_2s", "R_4s", "4_1s")
    assert alt.rule_scopes == {"4_1s": "WITHIN_MATERIAL", "2_2s": "ACROSS_MATERIAL"}   # the scope set for every analyte still applies
    assert alt.as_dict()["RULES"] == ["1_3s", "2_2s", "R_4s", "4_1s"]


@pytest.mark.parametrize("settings, message", [
    ({"RULES": ["1_3s"], "WARNING_RULES": ["1_3s"]}, "both a warning and a rejection"),
    ({"RULE_SETTINGS": {"1_3s": {"ACTION": "MAYBE"}}}, "ACTION must be one of"),
    ({"RULE_SETTINGS": {"1_3s": {"SCOPE": "WITHIN_MATERIAL"}}}, "has no SCOPE"),
    ({"RULE_SETTINGS": {"R_4s": {"SCOPE": "ACROSS_MATERIAL"}}}, "has no SCOPE"),
    ({"RULE_SETTINGS": {"2_2s": {"SCOPE": "EVERYWHERE"}}}, "SCOPE must be one of"),
    ({"RULE_SETTINGS": {"2_2s": {"LIMIT": 2}}}, "Unknown RULE_SETTINGS"),
    ({"RULE_SETTINGS": {"2s": {"ACTION": "OFF"}}}, "Unknown QC rule"),
    ({"RULES": ["1_3s", "1_3s"]}, "distinct"),
    ({"SCOPE": "ALL"}, "SCOPE must be one of"),
    ({"LIMIT": 3}, "Unknown QC rule option"),
    ({"AFTER_REJECT": "FORGET"}, "AFTER_REJECT must be one of"),
    ({"RULE_SETTINGS": {"2_2s": {"FORM": "RANGE"}}}, "has no FORM"),
    ({"RULE_SETTINGS": {"R_4s": {"FORM": "WIDE"}}}, "FORM must be one of"),
])
def test_invalid_rule_configuration_is_rejected(settings, message):
    with pytest.raises(ValueError, match=message):
        rule_plan(settings)


def test_scope_can_differ_per_rule():
    # Run 1: both levels beyond +2 SD (within one run). Runs 1-2: level 1 beyond +2 SD twice (same material, consecutive runs).
    # (AFTER_REJECT = CONTINUE so that the rejected run 1 still counts toward run 2 in this small example.)
    frame = points(L1=[2.2, 2.3], L2=[2.4, 0.1])
    both = planned(frame, {"AFTER_REJECT": "CONTINUE"})
    assert row(both, "L2", 1).rule_scope == "1_2s:point; 2_2s:within_run" and row(both, "L1", 2).rule_scope == "1_2s:point; 2_2s:across_run"
    within = planned(frame, {"AFTER_REJECT": "CONTINUE", "RULE_SETTINGS": {"2_2s": {"SCOPE": "WITHIN_MATERIAL"}}})
    assert row(within, "L2", 1).violation == "NONE" and row(within, "L1", 2).violation == "2_2s"
    across = planned(frame, {"AFTER_REJECT": "CONTINUE", "RULE_SETTINGS": {"2_2s": {"SCOPE": "ACROSS_MATERIAL"}}})
    assert row(across, "L2", 1).violation == "2_2s" and row(across, "L1", 2).violation == "NONE"
    # A per-rule scope leaves the other rules on the default: 4_1s still sees four consecutive level-1 runs.
    shift = points(L1=[1.2, 1.3, 1.4, 1.5], L2=[0.0, 0.1, -0.1, 0.0])
    assert row(planned(shift, {"RULE_SETTINGS": {"2_2s": {"SCOPE": "ACROSS_MATERIAL"}}}), "L1", 4).violation == "4_1s"
    assert row(planned(shift, {"SCOPE": "ACROSS_MATERIAL"}), "L1", 4).violation == "NONE"
    assert row(planned(shift, {"SCOPE": "ACROSS_MATERIAL", "RULE_SETTINGS": {"4_1s": {"SCOPE": "BOTH"}}}), "L1", 4).violation == "4_1s"


# ---------------------------------------------------------------------------- plugin

@pytest.fixture
def example():
    source = read_tame(ROOT / "web/backend/examples/qc_workflow.tame")
    source.meta.pop("PLUGINS")
    return source


def test_plugin_shares_the_existing_qc_declaration_and_agrees_with_the_basic_rules(example):
    output = run_plugin(example, "WESTGARD_QC", example.meta, "WESTGARD_QC", {})
    basic = run_plugin(example, "QC_ANALYSIS", example.meta, "QC_ANALYSIS", {**example.meta["QC_ANALYSIS"], "MODE": "WESTGARD"}).table
    table = output.table
    assert len(table) == len(basic) == 48
    keys = ["result_id", "level", "lot", "run"]
    merged = table.merge(basic[[*keys, "z", "violation"]], on=keys, suffixes=("", "_basic"))
    assert len(merged) == 48
    np.testing.assert_allclose(merged.z, merged.z_basic, equal_nan=True)
    shared = {"1_3s", "2_2s", "R_4s"}
    for _, item in merged.iterrows():
        assert set(item.violation.split(", ")) & shared == set(item.violation_basic.split(", ")) & shared
    glucose = table.loc[table.result_id.eq("glucose") & table.level.eq("L")].set_index("run")
    assert glucose.loc["run-03", "decision"] == "WARNING"
    assert glucose.loc["run-04", "violation"] == "2_2s"
    assert glucose.loc["run-06", "violation"] == "1_3s"
    assert glucose.loc["run-09", "violation"] == "R_4s"
    assert glucose.loc["run-12", "decision"] == "NOT_EVALUATED"
    runs = output.tables["runs"].set_index(["result_id", "run"])
    assert runs.loc[("glucose", "run-04"), "decision"] == "REJECT"
    assert runs.loc[("alt", "run-04"), "decision"] == "IN_CONTROL"
    assert output.dataset.meta["ANALYSIS_RESULT"]["ROW_KEY"] == ["result_id", "instrument", "lot", "level", "run"]
    assert not table.duplicated(output.dataset.meta["ANALYSIS_RESULT"]["ROW_KEY"]).any()
    assert len(output.charts) == 2 and any("manual review" in warning for warning in output.warnings)


def test_plugin_options(example):
    def run(**options):
        return run_plugin(example, "WESTGARD_QC", example.meta, "WESTGARD_QC", options).table

    only = run(RULES="1_3s", WARNING_RULES=[])
    assert set(only.violation) == {"NONE", "1_3s", "NE"} and set(only.warning) == {"NONE", "NE"}
    classic = run(TRIGGER="1_2S")
    pd.testing.assert_series_equal(classic.decision, run().decision)   # no rule fires without a 2 SD excursion in this example
    with pytest.raises(ValueError, match="Unknown WESTGARD_QC option"):
        run(MODE="WESTGARD")
    with pytest.raises(ValueError, match="Unknown QC rule"):
        run(RULES=["3s"])
    with pytest.raises(ValueError, match="TRIGGER"):
        run(TRIGGER="SOMETIMES")


def test_plugin_applies_a_different_rule_set_to_each_analyte(example):
    settings = {"RULE_SETTINGS": {"R_4s": {"ACTION": "WARNING"}},
                "BY_RESULT": {"glucose": {"RULES": ["1_3s"], "RULE_SETTINGS": {"2_2s": {"ACTION": "WARNING", "SCOPE": "WITHIN_MATERIAL"}}}}}
    output = run_plugin(example, "WESTGARD_QC", example.meta, "WESTGARD_QC", settings)
    glucose = output.table.loc[output.table.result_id.eq("glucose") & output.table.level.eq("L")].set_index("run")
    # glucose: only 1_3s rejects; 2_2s and R_4s are reported as warnings.
    assert (glucose.loc["run-04", "violation"], glucose.loc["run-04", "warning"], glucose.loc["run-04", "decision"]) == ("NONE", "1_2s, 2_2s", "WARNING")
    assert (glucose.loc["run-06", "violation"], glucose.loc["run-06", "decision"]) == ("1_3s", "REJECT")
    assert (glucose.loc["run-09", "violation"], glucose.loc["run-09", "warning"]) == ("NONE", "1_2s, R_4s")
    rulesets = output.dataset.meta["PLUGIN"]["RULESETS"]
    assert rulesets["glucose"] == {"RULES": ["1_3s"], "WARNING_RULES": ["1_2s", "R_4s", "2_2s"], "SCOPE": "BOTH", "TRIGGER": "ALWAYS",
                                   "AFTER_REJECT": "RESTART", "RULE_SCOPES": {"2_2s": "WITHIN_MATERIAL"}, "RULE_FORMS": {}}
    assert rulesets["alt"]["RULES"] == ["1_3s", "2_2s", "4_1s", "10x"] and rulesets["alt"]["WARNING_RULES"] == ["1_2s", "R_4s"]
    assert output.dataset.meta["PLUGIN"]["RULES"] == ["1_3s", "2_2s", "4_1s", "10x"]          # the set used when an analyte has no entry
    with pytest.raises(ValueError, match="BY_RESULT names a result that is not analysed: sodium"):
        run_plugin(example, "WESTGARD_QC", example.meta, "WESTGARD_QC", {"BY_RESULT": {"sodium": {"RULES": ["1_3s"]}}})


def test_local_times_need_a_declared_timezone_and_define_the_runs(example):
    from copy import deepcopy
    local = example.replace(df=example.df.copy(), meta=deepcopy(example.meta))
    local.df["측정시각"] = local.df["측정시각"].str.replace("+00:00", "", regex=False)
    options = {key: value for key, value in example.meta["QC_ANALYSIS"].items() if key not in {"MODE", "RUN_ID"}}
    local.meta["QC_ANALYSIS"] = {}
    with pytest.raises(ValueError, match="TIMEZONE"):
        run_plugin(local, "WESTGARD_QC", local.meta, "WESTGARD_QC", options)
    baselines = [{**baseline, "VALID_FROM": baseline["VALID_FROM"][:10], "VALID_TO": baseline["VALID_TO"][:10]}
                 for baseline in options["BASELINES"]]
    table = run_plugin(local, "WESTGARD_QC", local.meta, "WESTGARD_QC",
                       {**options, "BASELINES": baselines, "TIMEZONE": "Asia/Seoul"}).table
    assert table.run.nunique() == 12 and table.run.iloc[0] == "2026-01-01T08:00:00"   # no run column: one run per time
    assert table.time.iloc[0] == "2026-01-01T08:00:00+09:00"
    reference = run_plugin(example, "WESTGARD_QC", example.meta, "WESTGARD_QC", {}).table
    assert list(table.violation) == list(reference.violation)
    assert "__westgard_run__" not in [column.name for column in local.columns]            # the input is not altered


# ---------------------------------------------------------------------------- what a run is

_QC_HEAD = """<META>
[COLUMN.run]
ID = "run"
TAGS = ["STR"]

[COLUMN.time]
ID = "time"
TAGS = ["DATETIME"]

[COLUMN.level]
ID = "level"
TAGS = ["CATEGORY"]

[COLUMN.lot]
ID = "lot"
TAGS = ["CATEGORY"]

[COLUMN.instrument]
ID = "instrument"
TAGS = ["INSTRUMENT", "CATEGORY"]

[COLUMN.glucose]
ID = "glucose"
UNIT = "mg/dL"
TAGS = ["RESULT", "NUM", "NULLABLE"]

[QC_ANALYSIS]
RESULT_IDS = ["glucose"]
RUN_ID = "run"
TIME_ID = "time"
LEVEL_ID = "level"
LOT_ID = "lot"
INSTRUMENT_ID = "instrument"
MODE = "WESTGARD"
"""

_QC_BASELINE = """
[[QC_ANALYSIS.BASELINES]]
RESULT_ID = "glucose"
LEVEL = "{level}"
LOT = "{lot}"
INSTRUMENT = "Analyzer-1"
MEAN = 100
SD = 2
UNIT = "mg/dL"
REFERENCE = "Declared example baseline"
VALID_FROM = "2026-01-01T00:00:00+00:00"
VALID_TO = "2027-01-01T00:00:00+00:00"
"""


def qc_file(folder, rows):
    """Glucose on one instrument; ``rows`` are (run, day, level, lot, value)."""
    materials = dict.fromkeys((level, lot) for _, _, level, lot, _ in rows)
    text = _QC_HEAD + "".join(_QC_BASELINE.format(level=level, lot=lot) for level, lot in materials)
    text += "</META>\n<DATA>\nrun\ttime\tlevel\tlot\tinstrument\tglucose\n"
    text += "".join(f"{run}\t2026-01-{day:02d}T08:00:00+00:00\t{level}\t{lot}\tAnalyzer-1\t{value}\n" for run, day, level, lot, value in rows)
    path = folder / "qc.tame"
    path.write_text(text + "</DATA>\n", encoding="utf-8")
    return read_tame(path)


def test_levels_with_lots_of_their_own_are_one_run(tmp_path):
    # The usual case: each level of a control has its own lot number. Opposite 2 SD excursions in one run are R_4s.
    data = qc_file(tmp_path, [("r1", 2, "L", "A", 105), ("r1", 2, "H", "B", 95)])
    multirule = run_plugin(data, "WESTGARD_QC", data.meta, "WESTGARD_QC", {}).table
    basic = run_plugin(data, "QC_ANALYSIS", data.meta, "QC_ANALYSIS", dict(data.meta["QC_ANALYSIS"])).table
    assert list(multirule.violation) == ["R_4s", "R_4s"] and set(multirule.run_decision) == {"REJECT"}
    assert list(basic.violation) == ["R_4s", "R_4s"]             # the basic rules see the same run


@pytest.mark.parametrize("rows", [
    [("r1", 2, "L", "A", 105), ("r1", 3, "L", "B", 95)],                                   # a new lot on another day
    [("r1", 2, "L", "A", 105), ("r1", 2, "H", "A", 100), ("r1", 10, "L", "B", 95), ("r1", 10, "H", "B", 100)],
    [("r1", 2, "L", "A", 105), ("r1", 3, "H", "A", 95)],                                   # the same lot
])
def test_a_run_name_used_again_on_another_day_is_refused_not_merged(tmp_path, rows):
    data = qc_file(tmp_path, rows)
    for plugin, options in (("WESTGARD_QC", {}), ("QC_ANALYSIS", dict(data.meta["QC_ANALYSIS"]))):
        with pytest.raises(ValueError, match="run 'r1' on Analyzer-1 has 2026-01-02.*identifier that includes the date"):
            run_plugin(data, plugin, data.meta, plugin, options)

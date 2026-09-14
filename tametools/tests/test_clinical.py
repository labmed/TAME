from copy import deepcopy
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from tametools.analysis import validate_dataset
from tametools.cellstate import NULL, serialize_cell
from tametools.planned_analysis import analyze_dataset, write_analysis_report
from tametools.io import read_tame, read_xlsx, write_tame, write_xlsx
from tametools.models import ColumnSpec, TameDataset
from tametools.pipeline import execute_work
from tametools.reporting import chart_spec, render_chart_png, visualization_meta
from tametools.toml_compat import dumps as dumps_toml


def fixture(survey=False):
    df = pd.DataFrame({"record": [str(i) for i in range(8)], "age": ["12", "19", "20", "39", "40", "59", "60", "80"],
        "sex": ["1", "2"]*4, "x": list("12345678"), "y": ["8", "2", "6", "4", "3", "1", "5", "7"],
        "weight": ["1", "2", "1", "3", "2", "1", "4", "2"], "stratum": ["1"]*4+["2"]*4, "psu": ["1", "1", "2", "2"]*2})
    cols = [ColumnSpec(c, c, ["NUM", "NULLABLE", *( ["RESULT"] if c in {"x", "y"} else [])]) for c in df]
    meta = {"ANALYSIS_CONTRACT": {"VERSION": 1}, "COLUMN": {c: {"ID": c} for c in df},
        "ANALYSIS_PLAN": {"VERSION": 1, "MODE": "SURVEY" if survey else "SAMPLE", "RESULT_IDS": ["x", "y"],
            "POLICIES": ["RELEASED", "VALUE", "DELETE"], "PRIMARY_POLICY": "RELEASED", "HISTOGRAM_BINS": 5,
            "GROUPS": {"adults": {"ALL_OF": [{"COLUMN_ID": "age", "MIN": 20}]},
                       "male": {"ALL_OF": [{"COLUMN_ID": "age", "MIN": 20}, {"COLUMN_ID": "sex", "IN_NUMBERS": [1]}]},
                       "female": {"ALL_OF": [{"COLUMN_ID": "age", "MIN": 20}, {"COLUMN_ID": "sex", "IN_NUMBERS": [2]}]},
                       "age80plus": {"ALL_OF": [{"COLUMN_ID": "age", "MIN": 80}]},
                       "empty": {"ALL_OF": [{"COLUMN_ID": "sex", "IN_NUMBERS": [3]}]}},
            "PAIRS": [{"ID": "xy", "X_ID": "x", "Y_ID": "y"}], "PLOT_GROUP": "adults"},
        "WORKS": {"DEFAULT": ["VALIDATE", "ANALYZE"]}}
    meta["COLUMN"]["age"]["TOP_CODE_VALUE"] = 80
    for name in ["x", "y"]:
        meta["COLUMN"][name].update(UNIT="mg/L", ELIGIBILITY={"ALL_OF": [{"COLUMN_ID": "age", "MIN": 20}]})
    if survey:
        meta["SURVEY"] = {"DESIGN": "STRATIFIED_CLUSTER_WR", "WEIGHT_ID": "weight", "STRATUM_ID": "stratum", "PSU_ID": "psu", "EXPECTED_ROWS": 8}
        meta["ANALYSIS_PLAN"]["CONTRASTS"] = [{"ID": "sex_difference", "GROUP_A": "male", "GROUP_B": "female"},
            {"ID": "overlap", "GROUP_A": "adults", "GROUP_B": "female"}, {"ID": "empty_comparison", "GROUP_A": "empty", "GROUP_B": "female"}]
    return TameDataset(df, cols, meta=meta)


def cells(ds):
    return [[str(serialize_cell(v)) for v in row] for row in ds.df.itertuples(index=False, name=None)]


def row(output, table="sample_summary", result_id="x", group="ALL", policy="RELEASED"):
    data = output.tables[table]
    return data.loc[data.result_id.eq(result_id) & data.group.eq(group) & data.policy.eq(policy)].iloc[0]


class ClinicalTests(unittest.TestCase):
    def test_sample_statistics_and_input_preservation(self):
        ds = fixture()
        original, meta = cells(ds), deepcopy(ds.meta)
        output = analyze_dataset(ds)
        r = row(output)
        self.assertEqual(r.analysis_n, 6)
        self.assertEqual(r["mean"], 5.5)
        self.assertEqual(r["median"], 5.5)
        self.assertAlmostEqual(r.sd, math.sqrt(3.5))
        self.assertEqual((r.q25, r.q75), (4.25, 6.75))
        self.assertEqual((cells(ds), ds.meta), (original, meta))
        self.assertEqual(len(output.dataset.df), 8)
        self.assertNotIn("survey_means", output.tables)

    def test_boundaries_missing_conditions_and_top_code(self):
        ds = fixture()
        ds.meta["ANALYSIS_PLAN"]["GROUPS"]["twenties_thirties"] = {"ALL_OF": [{"COLUMN_ID": "age", "MIN": 20, "MAX_EXCLUSIVE": 40}]}
        output = analyze_dataset(ds)
        self.assertEqual(row(output, group="twenties_thirties").analysis_n, 2)
        self.assertEqual(row(output, group="age80plus").analysis_n, 1)
        ds.df.loc[2, "age"] = NULL
        output = analyze_dataset(ds)
        self.assertEqual(row(output).eligibility_missing_n, 1)
        self.assertEqual(row(output).analysis_n, 5)
        for condition in [{"MIN": 85}, {"MIN": 80, "MAX_EXCLUSIVE": 90}]:
            ds.meta["ANALYSIS_PLAN"]["GROUPS"]["invalid"] = {"ALL_OF": [{"COLUMN_ID": "age", **condition}]}
            with self.assertRaisesRegex(ValueError, "top code"):
                analyze_dataset(ds)

    def test_text_groups_do_not_guess_numeric_or_synonym_codes(self):
        ds = fixture()
        ds.meta["ANALYSIS_PLAN"]["GROUPS"]["exact"] = {"ALL_OF": [{"COLUMN_ID": "sex", "IN_TEXT": ["1"]}]}
        self.assertEqual(row(analyze_dataset(ds), group="exact").analysis_n, 3)
        ds.df.loc[2, "sex"] = "1.0"
        output = analyze_dataset(ds)
        self.assertEqual(row(output, group="exact").analysis_n, 2)
        self.assertEqual(row(output, group="male").analysis_n, 3)

    def test_comparator_policies_and_denominators(self):
        ds = fixture()
        ds.columns[3] = ColumnSpec("x", "x", ["RESULT", "<NUM>", "NULLABLE", "EMPTY_OK", "WS_OK"])
        ds.df.loc[2:6, "x"] = ["<3", ">4", NULL, "", "  "]
        ds.meta["ANALYSIS_PLAN"].update(POLICIES=["DELETE", "VALUE"], PRIMARY_POLICY="DELETE")
        output = analyze_dataset(ds)
        deleted, value = row(output, policy="DELETE"), row(output, policy="VALUE")
        self.assertEqual((deleted.domain_n, deleted.analysis_n, deleted.missing_n, deleted.censored_n, deleted.policy_excluded_n), (6, 1, 3, 2, 2))
        self.assertEqual((value.analysis_n, value.policy_excluded_n), (3, 0))
        self.assertEqual(deleted.censoring_status, "explicit_comparator")
        ds.meta["ANALYSIS_PLAN"].update(POLICIES=["RELEASED"], PRIMARY_POLICY="RELEASED")
        with self.assertRaisesRegex(ValueError, "RELEASED needs"):
            analyze_dataset(ds)

    def test_existing_censoring_binding_is_verified(self):
        from test_analysis_contract import fixture as bound_fixture
        from tametools.analysis_contract import derive_censored_results
        ds = derive_censored_results(bound_fixture())
        ds.meta["ANALYSIS_PLAN"] = dict(VERSION=1, MODE="SAMPLE", RESULT_IDS=["analysis"], POLICIES=["RELEASED", "VALUE", "DELETE"], PRIMARY_POLICY="RELEASED")
        output = analyze_dataset(ds)
        self.assertEqual(row(output, result_id="analysis").censored_n, 1)
        ds.df.loc[0, "result"] = "0.11"
        with self.assertRaisesRegex(ValueError, "contradicts"):
            analyze_dataset(ds)

    def test_pairs_use_joint_eligibility_and_pairwise_missingness(self):
        ds = fixture()
        ds.df.loc[2, "y"] = NULL
        out = analyze_dataset(ds)
        corr = out.tables["correlations"].query("group == 'ALL' and policy == 'RELEASED'").iloc[0]
        self.assertEqual((corr.eligible_pair_n, corr.paired_n, corr.excluded_n), (6, 5, 1))
        self.assertNotIn("p_value", corr.index)
        xs, ys = np.array([4,5,6,7,8]), np.array([4,3,1,5,7])
        self.assertAlmostEqual(corr.pearson_r, np.corrcoef(xs, ys)[0, 1])
        self.assertEqual(row(out).analysis_n, 6)

    def test_empty_constant_and_nonpositive_states(self):
        ds = fixture()
        ds.df["y"] = "0"
        out = analyze_dataset(ds)
        self.assertEqual(row(out, group="empty").status, "empty_domain")
        self.assertEqual(row(out, result_id="y").geometric_status, "nonpositive_values")
        self.assertTrue(math.isnan(row(out, result_id="y").geometric_mean))
        self.assertTrue(out.tables["correlations"].status.isin(["constant_variable", "insufficient_pairs"]).all())

    def test_histograms_and_scatter_keep_all_complete_observations(self):
        out = analyze_dataset(fixture())
        self.assertEqual(out.tables["histograms"].groupby("result_id")["count"].sum().to_dict(), {"x": 6, "y": 6})
        scatter = [c for c in out.charts if c["TYPE"] == "SCATTER"][0]
        self.assertEqual(len(scatter["ROWS"]), 6)
        self.assertEqual(scatter["MAX_POINTS"], 6)

    def test_log_histogram_is_explicit_and_does_not_drop_values(self):
        ds = fixture()
        plan = ds.meta["ANALYSIS_PLAN"]
        plan.update(PLOT_RESULT_IDS=["x"], HISTOGRAM_SCALES={"x": "log10"})
        out = analyze_dataset(ds)
        self.assertEqual(out.tables["histograms"]["count"].sum(), 6)
        self.assertTrue(out.tables["histograms"].scale.eq("log10").all())
        self.assertAlmostEqual(row(out)["mean"], 5.5)
        ds.df.loc[2, "x"] = "0"
        with self.assertRaisesRegex(ValueError, "positive values"):
            analyze_dataset(ds)
        for patch_ in [{"PLOT_RESULT_IDS": ["unknown"]}, {"HISTOGRAM_SCALES": {"x": "auto"}}]:
            broken = fixture()
            broken.meta["ANALYSIS_PLAN"].update(patch_)
            with self.assertRaises(ValueError):
                analyze_dataset(broken)

    @unittest.skipUnless(importlib.util.find_spec("pint"), "optional integration dependency unavailable")
    def test_integrated_results_can_be_analysed_without_reinterpreting_raw_cells(self):
        from test_integration import source, profile
        from tametools.integration import integrate_datasets
        ds = integrate_datasets([source()], profile()).dataset
        ds.meta["ANALYSIS_PLAN"] = dict(VERSION=1, MODE="SAMPLE", RESULT_IDS=["creatinine"],
            POLICIES=["DELETE", "VALUE"], PRIMARY_POLICY="DELETE")
        out = analyze_dataset(ds)
        deleted = row(out, result_id="creatinine", policy="DELETE")
        self.assertEqual((deleted.analysis_n, deleted.censored_n, deleted.missing_n), (1,1,1))
        self.assertEqual(deleted["mean"], 12)
        self.assertFalse(validate_dataset(out.dataset).issues)

    def test_compact_report_views_leave_full_precision_tables_unchanged(self):
        from tametools.planned_analysis import _report_tables
        out = analyze_dataset(fixture())
        before = {name: frame.copy(deep=True) for name,frame in out.tables.items()}
        report = _report_tables(out)
        self.assertTrue(all(len(frame.columns) <= 6 for frame in report.values()))
        for name in before:
            pd.testing.assert_frame_equal(before[name], out.tables[name])

    def test_eligibility_indicator_and_rule_are_exclusive(self):
        ds = fixture()
        ds.meta["COLUMN"]["x"]["ELIGIBLE_ID"] = "sex"
        with self.assertRaisesRegex(ValueError, "not both"):
            analyze_dataset(ds)
        del ds.meta["COLUMN"]["x"]["ELIGIBILITY"]
        with self.assertRaisesRegex(ValueError, "nonmissing 0/1"):
            analyze_dataset(ds)

    def test_invalid_plan_fields_fail(self):
        for key, value in [("VERSION", True), ("VERSION", 2), ("MODE", "AUTO"), ("RESULT_IDS", []), ("POLICIES", []),
                           ("POLICIES", ["KEEP"]), ("PRIMARY_POLICY", "unknown"), ("PLOT_GROUP", "unknown"),
                           ("HISTOGRAM_BINS", 0), ("HISTOGRAM_BINS", True), ("GROUPS", []), ("PAIRS", {}), ("UNKNOWN", 1)]:
            ds = fixture()
            ds.meta["ANALYSIS_PLAN"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                analyze_dataset(ds)
            self.assertTrue(validate_dataset(ds).issues)

    def test_invalid_result_unit_and_reserved_bindings_fail(self):
        for kind in ["unit", "id", "tags", "reserved"]:
            ds = fixture()
            if kind == "unit": del ds.meta["COLUMN"]["x"]["UNIT"]
            if kind == "id": ds.meta["ANALYSIS_PLAN"]["RESULT_IDS"] = ["undefined"]
            if kind == "tags": ds.columns[3] = ColumnSpec("x", "x", ["NUM"])
            if kind == "reserved": ds.meta["COLUMN"]["record"]["ID"] = "__analysis_plan_bad"
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                analyze_dataset(ds)

    def test_invalid_groups_and_pairs_fail(self):
        cases = [{"GROUPS": {"ALL": {"ALL_OF": []}}}, {"GROUPS": {"bad": {"ALL_OF": [{"COLUMN_ID": "age", "MIN": 40, "MAX_EXCLUSIVE": 20}]}}},
            {"GROUPS": {"bad": {"ALL_OF": [{"COLUMN_ID": "age", "MIN": True}]}}},
            {"GROUPS": {"bad": {"ALL_OF": [{"COLUMN_ID": "age", "IN_NUMBERS": [1], "MIN": 2}]}}},
            {"PAIRS": [{"ID": "xy", "X_ID": "x", "Y_ID": "x"}]}, {"PAIRS": [{"ID": "bad/path", "X_ID": "x", "Y_ID": "y"}]},
            {"CONTRASTS": [{"ID": "xy", "GROUP_A": "male", "GROUP_B": "female"}]}]
        for patch_ in cases:
            ds = fixture()
            ds.meta["ANALYSIS_PLAN"].update(patch_)
            with self.subTest(patch=patch_), self.assertRaises(ValueError):
                analyze_dataset(ds)

    def test_mode_cannot_erase_or_invent_survey_design(self):
        for has_design in [False, True]:
            ds = fixture(has_design)
            ds.meta["ANALYSIS_PLAN"]["MODE"] = "SAMPLE" if has_design else "SURVEY"
            with self.assertRaisesRegex(ValueError, "MODE must match"):
                analyze_dataset(ds)

    def test_header_rename_and_roundtrip_invariance(self):
        ds = fixture()
        original = analyze_dataset(ds)
        meta = deepcopy(ds.meta)
        mapping = {c.name: "display_" + str(i) for i,c in enumerate(ds.columns)}
        meta["COLUMN"] = {mapping[k]: v for k,v in meta["COLUMN"].items()}
        renamed = ds.replace(df=ds.df.rename(columns=mapping), columns=[ColumnSpec(mapping[c.name], mapping[c.name], c.tags) for c in ds.columns], meta=meta)
        altered = analyze_dataset(renamed)
        pd.testing.assert_frame_equal(original.tables["sample_summary"].drop(columns="column"), altered.tables["sample_summary"].drop(columns="column"))
        with tempfile.TemporaryDirectory() as tmp:
            for ext, write, read in [("tame", write_tame, read_tame), ("xlsx", write_xlsx, read_xlsx)]:
                path = Path(tmp) / ("test." + ext)
                write(path, ds)
                out = analyze_dataset(read(path))
                for name in original.tables:
                    pd.testing.assert_frame_equal(original.tables[name], out.tables[name])

    def test_workflow_and_explicit_options_record_actual_plan(self):
        ds = fixture()
        out = execute_work(ds).outputs[-1]
        self.assertEqual(out.name, "ANALYZE")
        self.assertEqual(out.dataset.meta["LOG"][-1]["PARAMS"]["options"], ds.meta["ANALYSIS_PLAN"])
        options = deepcopy(ds.meta["ANALYSIS_PLAN"])
        options["PRIMARY_POLICY"] = "DELETE"
        self.assertEqual(analyze_dataset(ds, options).dataset.meta["ANALYSIS_PLAN"], options)
        self.assertEqual(ds.meta["ANALYSIS_PLAN"]["PRIMARY_POLICY"], "RELEASED")

    def test_report_and_cli_do_not_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input.tame"
            write_tame(source, fixture())
            env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(Path(__file__).resolve().parents[1] / "src"), os.environ.get("PYTHONPATH", "")]))
            command = [sys.executable, "-m", "tametools", "analyze", str(source), "--output-dir", str(root / "report"), "--docx"]
            run = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
            self.assertTrue((root / "report/analysis_report.docx").exists())
            manifest = json.loads((root / "report/manifest.json").read_text())
            self.assertEqual(manifest["input_rows"], 8)
            self.assertIn("planned_analysis.py", manifest["source_modules_sha256"])
            self.assertFalse((root / "report/dataset.csv").exists())
            run = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertNotEqual(run.returncode, 0)
            options = deepcopy(fixture().meta["ANALYSIS_PLAN"])
            options["PRIMARY_POLICY"] = "DELETE"
            plan = root / "plan.toml"
            plan.write_text(dumps_toml(options), encoding="utf-8")
            command = [sys.executable, "-m", "tametools", "analyze", str(source), "--plan", str(plan),
                       "--output-dir", str(root / "reviewed")]
            run = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
            manifest = json.loads((root / "reviewed/manifest.json").read_text())
            self.assertEqual(manifest["plan"], options)
            self.assertEqual(read_tame(source).meta["ANALYSIS_PLAN"]["PRIMARY_POLICY"], "RELEASED")
            plan.write_text('VERSION = "invalid"\n', encoding="utf-8")
            command[-1] = str(root / "invalid")
            run = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertNotEqual(run.returncode, 0)
            self.assertFalse((root / "invalid").exists())

    def test_scatter_uses_numeric_x_and_interval_metadata_survives(self):
        import matplotlib.pyplot
        chart = chart_spec("xy", type="scatter", x="x", y="y", rows=[{"x":10,"y":1}, {"x":2,"y":3}])
        with tempfile.TemporaryDirectory() as tmp, patch("matplotlib.axes.Axes.scatter") as scatter:
            render_chart_png(chart, {}, Path(tmp)/"x.png")
            self.assertEqual(list(scatter.call_args.args[0]), [10,2])
        chart.update(TYPE="INTERVAL", Y_LOW="low", Y_HIGH="high")
        self.assertEqual(visualization_meta([chart])["xy"]["Y_LOW"], "low")


@unittest.skipUnless(importlib.util.find_spec("samplics"), "optional survey dependency unavailable")
class ClinicalSurveyTests(unittest.TestCase):
    def test_weighted_means_and_contrast_oracle(self):
        ds = fixture(True)
        out = analyze_dataset(ds)
        w = ds.df.weight.astype(float).to_numpy()
        x = ds.df.x.astype(float).to_numpy()
        m = (ds.df.age.astype(float).ge(20) & ds.df.sex.eq("1")).to_numpy()
        f = (ds.df.age.astype(float).ge(20) & ds.df.sex.eq("2")).to_numpy()
        a, b = np.sum(w[m]*x[m])/w[m].sum(), np.sum(w[f]*x[f])/w[f].sum()
        self.assertAlmostEqual(row(out, table="survey_means", group="male")["mean"], a)
        contrast = out.tables["survey_contrasts"].query("contrast == 'sex_difference' and result_id == 'x'").iloc[0]
        scores = np.where(m, w*(x-a)/w[m].sum(), 0) - np.where(f, w*(x-b)/w[f].sum(), 0)
        psu_scores = scores.reshape(4,2).sum(axis=1).reshape(2,2)
        variance = sum(2*sum((block-block.mean())**2) for block in psu_scores)
        self.assertAlmostEqual(contrast.difference, a-b)
        self.assertAlmostEqual(contrast.se, math.sqrt(variance))
        self.assertEqual(len(out.dataset.df), 8)
        self.assertFalse(any("__analysis_plan_" in c.name for c in out.dataset.columns))

    def test_overlap_covariance_and_empty_groups(self):
        ds = fixture(True)
        out = analyze_dataset(ds)
        table = out.tables["survey_contrasts"]
        self.assertTrue(table.query("contrast == 'empty_comparison'").status.eq("empty_group").all())
        item = table.query("contrast == 'overlap' and result_id == 'x'").iloc[0]
        w,x = ds.df.weight.astype(float).to_numpy(), ds.df.x.astype(float).to_numpy()
        a = ds.df.age.astype(float).ge(20).to_numpy()
        b = a & ds.df.sex.eq("2").to_numpy()
        ma,mb = np.average(x[a], weights=w[a]), np.average(x[b], weights=w[b])
        score = np.where(a,w*(x-ma)/w[a].sum(),0)-np.where(b,w*(x-mb)/w[b].sum(),0)
        blocks = score.reshape(4,2).sum(axis=1).reshape(2,2)
        self.assertAlmostEqual(item.se, math.sqrt(sum(2*sum((v-v.mean())**2) for v in blocks)))

    def test_zero_weights_excluded_but_full_design_protected(self):
        ds = fixture(True)
        ds.df.loc[2,"weight"] = "0"
        out = analyze_dataset(ds)
        self.assertEqual(row(out).zero_weight_eligible_n, 1)
        self.assertEqual(row(out).analysis_n, 5)
        self.assertEqual(row(out, table="survey_means").design_n, 7)
        ds = ds.with_df(ds.df.iloc[1:].copy())
        with self.assertRaisesRegex(ValueError, "EXPECTED_ROWS"):
            analyze_dataset(ds)

    def test_invalid_design_fails_before_returning_any_report(self):
        for kind in ["negative", "missing", "singleton"]:
            ds = fixture(True)
            if kind == "negative": ds.df.loc[0,"weight"] = "-1"
            if kind == "missing": ds.df.loc[0,"weight"] = NULL
            if kind == "singleton": ds.df["psu"] = "1"
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                analyze_dataset(ds)


if __name__ == "__main__":
    unittest.main()

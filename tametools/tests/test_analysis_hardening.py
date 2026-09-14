"""Synthetic software counterexamples, never clinical performance evidence."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from tametools.analysis import numeric_series_for, validate_dataset
from tametools.analysis_contract import measurement_values
from tametools.cellstate import NULL
from tametools.integration import integrate_datasets
from tametools.models import ColumnSpec, TameDataset
from tametools.observation_contract import observation_info
from tametools.planned_analysis import analyze_dataset, write_analysis_report
from tametools.plugin_base import run_plugin
from tametools.plugin_base.roles import result_binding
from tametools.reference_flags import reference_flags
from tametools.units import unit_factor, convert_cell


def dataset(data, results=("y",)):
    frame = pd.DataFrame(data, dtype=object)
    columns = [ColumnSpec(name, name, ["RESULT", "<NUM>", "NULLABLE"] if name in results else ["STR", "NULLABLE"]) for name in frame]
    return TameDataset(frame, columns, meta={"ANALYSIS_CONTRACT": {"VERSION": 1},
        "COLUMN": {name: {"ID": name, **({"UNIT": "mg/L"} if name in results else {})} for name in frame}})


def numeric(ds, name, unit=None):
    ds.columns = [ColumnSpec(c.original_header, c.name, ["NUM", "NULLABLE"] if c.name == name else c.tags) for c in ds.columns]
    if unit:
        ds.meta["COLUMN"][name]["UNIT"] = unit


def plan(ds, **extra):
    ds.meta["ANALYSIS_PLAN"] = {"VERSION": 1, "MODE": "SAMPLE", "RESULT_IDS": ["y"],
        "POLICIES": ["DELETE"], "PRIMARY_POLICY": "DELETE", "PLOT_RESULT_IDS": [], **extra}
    return ds


def roc(ds, **options):
    return run_plugin(ds, "ROC_ANALYSIS", ds.meta, "roc", {"LABEL": "label", "POSITIVE": "1", "NEGATIVE": "0", **options})


def method_data():
    return dataset({"y": [1, 2, 2, 4, 3, 6, 4, 8], "method": ["A", "B"] * 4,
        "sample": ["s1", "s1", "s2", "s2", "s3", "s3", "s4", "s4"]})


def compare(ds, **options):
    return run_plugin(ds, "METHOD_COMPARISON", ds.meta, "method", {"METHOD_COLUMN": "method",
        "METHOD_A": "A", "METHOD_B": "B", "KEY_IDS": ["sample"], **options})


class HardeningTests(unittest.TestCase):
    def test_tied_roc_is_half(self):
        self.assertEqual(roc(dataset({"y": [1]*4, "label": ["1", "1", "0", "0"]})).table.iloc[0].auc, .5)

    def test_roc_maximum_ties_match_library(self):
        from sklearn.metrics import roc_auc_score
        ds = dataset({"y": [1, 2, 2, 3, 3], "label": ["0", "1", "0", "1", "0"]})
        self.assertAlmostEqual(roc(ds).table.iloc[0].auc, roc_auc_score([0, 1, 0, 1, 0], [1, 2, 2, 3, 3]), places=4)

    def test_roc_missing_not_negative_and_unknown_rejected(self):
        ds = dataset({"y": [4, 3, 2, NULL], "label": ["1", "0", NULL, "1"]})
        row = roc(ds).table.iloc[0]
        self.assertEqual((row.n_neg, row.n_pos, row.label_missing_n, row.excluded_n), (1, 1, 1, 2))
        ds.df.loc[2, "label"] = "indeterminate"
        with self.assertRaisesRegex(ValueError, "Unknown binary"):
            roc(ds)

    def test_roc_direction_and_origin(self):
        ds = dataset({"y": [1, 2, 3, 4], "label": ["1", "1", "0", "0"]})
        self.assertEqual(roc(ds, DIRECTION="lower").table.iloc[0].auc, 1)
        curve = roc(ds, MODE="CURVE").table
        self.assertEqual((curve.iloc[0].sensitivity, curve.iloc[0].specificity), (0, 1))
        self.assertEqual(curve.iloc[0].threshold_kind, "all_negative")

    def test_explicit_selection_never_falls_back(self):
        ds = dataset({"y": [1], "other": [2]}, results=("y", "other"))
        for options in ({"RESULT": "missing"}, {"RESULT": "tag:RESULT"}, {"RESULT": ""}, {"RESULT_IDS": []}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                result_binding(ds, options)
        self.assertEqual([c.name for c in result_binding(ds, {"RESULT_IDS": ["other"]}).columns], ["other"])

    def test_reference_intervals_not_substitutions(self):
        ds = dataset({"y": ["<5", "<=5", ">10", ">=10", 7, NULL]})
        ds.meta["COLUMN"]["y"]["REFERENCE_INTERVAL"] = {"LOW": 5, "HIGH": 10, "UNIT": "mg/L"}
        self.assertEqual(reference_flags(ds, ds.columns[0])[0].tolist(), ["L", "IND", "H", "IND", "N", "NE"])
        row = run_plugin(ds, "ABNORMAL_FLAG", ds.meta, "flag", {"MODE": "RATE"}).table.iloc[0]
        self.assertEqual((row.total_n, row.n, row.not_evaluated_n, row.indeterminate_n), (6, 3, 1, 2))

    def test_missing_references_are_not_normal(self):
        ds = dataset({"y": [7, NULL]})
        flags, reasons, _ = reference_flags(ds, ds.columns[0])
        self.assertEqual(flags.tolist(), ["NE", "NE"])
        self.assertEqual(reasons.iloc[0], "REFERENCE_MISSING")

    def test_reference_unit_conflict_and_applicability(self):
        ds = dataset({"y": [7, 7], "age": [15, 30], "time": ["2026-01-01T00:00:00+00:00"]*2})
        numeric(ds, "age")
        config = {"LOW": 5, "HIGH": 10, "UNIT": "mg/L", "APPLIES_WHEN": {"ALL_OF": [{"COLUMN_ID": "age", "MIN": 18}]},
            "TIME_ID": "time", "VALID_FROM": "2025-01-01T00:00:00+00:00", "VALID_TO": "2027-01-01T00:00:00+00:00"}
        ds.meta["COLUMN"]["y"]["REFERENCE_INTERVAL"] = config
        self.assertEqual(reference_flags(ds, ds.columns[0])[0].tolist(), ["NE", "N"])
        config["UNIT"] = "mg/dL"
        with self.assertRaisesRegex(ValueError, "UNIT"):
            reference_flags(ds, ds.columns[0])

    def test_method_direction_is_order_invariant(self):
        ds = method_data()
        forward = compare(ds).table
        reverse = compare(ds.with_df(ds.df.iloc[::-1].reset_index(drop=True))).table
        pd.testing.assert_frame_equal(forward, reverse)
        self.assertEqual(forward.iloc[0].deming_slope, 2)
        self.assertLess(forward.iloc[0].bias_ci95_low, forward.iloc[0].bias_ci95_high)

    def test_method_pair_and_duplicate_policies(self):
        ds = method_data()
        ds.df.loc[0, "method"] = "C"
        with self.assertRaisesRegex(ValueError, "Additional methods"):
            compare(ds)
        ds = method_data()
        ds.df = pd.concat([ds.df, ds.df.iloc[:1]], ignore_index=True)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            compare(ds)
        self.assertEqual(compare(ds, DUPLICATES="MEAN").table.iloc[0].n, 4)
        with self.assertRaisesRegex(ValueError, "DEMING_LAMBDA"):
            compare(ds, DEMING_LAMBDA=0)

    def test_nominal_guard_covers_integration_and_survey(self):
        from test_integration import source, profile
        from test_survey import fixture
        from tametools.survey import survey_describe
        ds = source(values=["0", "1"])
        ds.columns[1] = ColumnSpec("local_result", "local_result", ["RESULT", "NUM", "NOMINAL"])
        with self.assertRaisesRegex(ValueError, "NOMINAL"):
            integrate_datasets([ds], profile())
        ds = fixture()
        ds.columns[0] = ColumnSpec("y", "y", ["RESULT", "NUM", "NOMINAL"])
        with self.assertRaisesRegex(ValueError, "NOMINAL"):
            survey_describe(ds)

    def test_semantic_tag_conflict_rejected_before_integration(self):
        from test_integration import source, profile
        ds = source()
        ds.columns[1] = ColumnSpec("local_result", "local_result", ["RESULT", "<NUM>", "ANALYTE(glucose)"])
        with self.assertRaisesRegex(ValueError, "contradicts"):
            integrate_datasets([ds], profile())

    def test_reviewed_integration_tags_survive_validation(self):
        from test_integration import source, profile
        p = profile()
        p["TARGETS"]["creatinine"]["TAGS"] = ["ANALYTE(creatinine)", "SPECIMEN(serum)", "PANEL(renal)"]
        ds = integrate_datasets([source()], p).dataset
        self.assertEqual(len(ds.columns_with_tag("PANEL(renal)")), 1)
        self.assertFalse(validate_dataset(ds).issues)

    def test_units_require_charge_and_reviewed_affine_bridge(self):
        from test_integration import source, profile
        for unit in ("mEq/L", "%", "mmol/mol"):
            self.assertEqual(float(unit_factor(unit, unit)), 1)
        with self.assertRaises(ValueError):
            unit_factor("mEq/L", "mmol/L")
        ds, p = source(unit="mEq/L", values=["4"]), profile("mmol/L")
        ds.meta["COLUMN"]["local_result"]["MEASUREMENT"]["PROPERTY"] = "equivalent_concentration"
        p["BRIDGES"] = {"ion": {"COMPONENT": "creatinine", "FROM_UNIT": "mEq/L", "TO_UNIT": "mmol/L", "FACTOR": .5,
            "ION_CHARGE": 2, "REFERENCE": "Synthetic charge test, not a creatinine chemistry claim"}}
        p["SOURCES"]["A"]["MAPPINGS"][0]["BRIDGE"] = "ion"
        self.assertEqual(integrate_datasets([ds], p).dataset.df.creatinine.iloc[0], "2")
        p["BRIDGES"]["ion"]["FACTOR"] = 1
        with self.assertRaisesRegex(ValueError, "ION_CHARGE"):
            integrate_datasets([ds], p)
        self.assertEqual(convert_cell("<5", 2, offset=-1), "<9")

    def test_unknown_capability_fails_numeric_and_validate(self):
        ds = dataset({"y": [1]})
        ds.meta["REQUIREMENTS"] = {"SEMANTICS_VERSION": 1, "CAPABILITIES": {"future": 1}}
        with self.assertRaisesRegex(ValueError, "capability"):
            numeric_series_for(ds, "y")
        self.assertTrue(validate_dataset(ds).issues)

    def test_observation_keys_repeats_and_time(self):
        ds = dataset({"y": [1, 2], "person": ["p1", "p1"], "visit": ["v1", "v2"]})
        ds.meta["OBSERVATION"] = {"VERSION": 1, "ROW_UNIT": "measurement", "KEY_IDS": ["person", "visit"], "SUBJECT_ID": "person", "REPEAT_POLICY": "ERROR"}
        with self.assertRaisesRegex(ValueError, "Repeated"):
            observation_info(ds)
        ds.meta["OBSERVATION"]["REPEAT_POLICY"] = "CLUSTER"
        self.assertEqual(observation_info(ds, inference=True, cluster_id="person")["subjects"], 1)
        ds.df.loc[1, "visit"] = "v1"
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            observation_info(ds)

    def test_status_applies_to_direct_numeric_and_plan(self):
        ds = plan(dataset({"y": [1, 100, 3], "status": ["final", "cancelled", NULL]}))
        ds.meta["COLUMN"]["y"]["RESULT_CONTEXT"] = {"STATUS_ID": "status", "ACCEPTED_STATUSES": ["final"]}
        self.assertEqual(numeric_series_for(ds, "y").dropna().tolist(), [1])
        out = analyze_dataset(ds)
        self.assertEqual(out.tables["sample_summary"].iloc[0]["mean"], 1)
        self.assertEqual(out.tables["result_status"]["count"].sum(), 3)

    def test_interval_censoring_all_directions_and_policies(self):
        ds = interval_data()
        values, censored = measurement_values(ds, ds.columns[0], "MIDPOINT")
        self.assertEqual(values.tolist(), [3, 5, 10, 7])
        self.assertEqual(censored.tolist(), [False, True, True, True])
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            measurement_values(ds, ds.columns[0], "VALUE")
        ds.meta["COLUMN"]["y"]["REFERENCE_INTERVAL"] = {"LOW": 5, "HIGH": 10, "UNIT": "mg/L"}
        self.assertEqual(reference_flags(ds, ds.columns[0])[0].tolist(), ["L", "L", "H", "N"])
        self.assertFalse(validate_dataset(ds).issues)

    def test_effective_metadata_audit_changes_without_cell_changes(self):
        ds = plan(dataset({"y": [1, 3, 5], "age": [10, 20, 30]}))
        with tempfile.TemporaryDirectory() as directory:
            write_analysis_report(analyze_dataset(ds), Path(directory) / "a")
            ds.meta["COLUMN"]["y"]["ELIGIBILITY"] = {"ALL_OF": [{"COLUMN_ID": "age", "MIN": 18}]}
            write_analysis_report(analyze_dataset(ds), Path(directory) / "b")
            a, b = [json.loads((Path(directory) / name / "manifest.json").read_text()) for name in ("a", "b")]
            self.assertEqual(a["input_cell_sha256"], b["input_cell_sha256"])
            self.assertNotEqual(a["effective_input_sha256"], b["effective_input_sha256"])

    def test_linear_model_hc3_and_cluster_match_library(self):
        import statsmodels.api as sm
        ds = model_data()
        model = {"ID": "adjusted", "Y_ID": "y", "NUMERIC_IDS": ["x"], "COVARIANCE": "HC3", "POLICY": "DELETE"}
        plan(ds, MODELS=[model])
        table = analyze_dataset(ds).tables["models"]
        fitted = sm.OLS(ds.df.y.astype(float), sm.add_constant(ds.df.x.astype(float))).fit(cov_type="HC3", use_t=True)
        np.testing.assert_allclose(table.coefficient, fitted.params)
        np.testing.assert_allclose(table.se, fitted.bse)
        model.update(COVARIANCE="CLUSTER", CLUSTER_ID="person")
        table = analyze_dataset(ds).tables["models"]
        fitted = sm.OLS(ds.df.y.astype(float), sm.add_constant(ds.df.x.astype(float))).fit(cov_type="cluster", cov_kwds={"groups": ds.df.person, "use_correction": True}, use_t=True)
        np.testing.assert_allclose(table.se, fitted.bse)
        self.assertEqual(table.df.tolist(), [5, 5])

    def test_missingness_bounds_are_explicit_not_ci(self):
        ds = plan(dataset({"y": [2, 4, NULL]}), MISSINGNESS=[{"ID": "range", "RESULT_ID": "y", "POLICY": "DELETE", "LOWER": 0, "UPPER": 12, "ASSUMPTION": "Software-test range only"}])
        row = analyze_dataset(ds).tables["missingness_bounds"].iloc[0]
        self.assertEqual((row.mean_lower, row.mean_upper, row.missing_n), (2, 6, 1))

    def test_operational_qc_r4s_is_within_run(self):
        from tametools.quality_control import evaluate_qc
        ds, options = qc_data()
        out = evaluate_qc(ds, [ds.columns[0]], options)
        self.assertFalse(out.violation.str.contains("R_4s").any())
        ds.df["run"] = ["r1", "r1"]
        ds.df["level"] = ["L", "H"]
        ds.df["time"] = ["2026-01-01T00:00:00+00:00"]*2
        options["BASELINES"].append({**options["BASELINES"][0], "LEVEL": "H"})
        out = evaluate_qc(ds, [ds.columns[0]], options)
        self.assertTrue(out.violation.str.contains("R_4s").all())
        options.pop("BASELINES")
        with self.assertRaisesRegex(ValueError, "BASELINES"):
            evaluate_qc(ds, [ds.columns[0]], options)

    def test_reference_rate_no_evaluable_results_is_null(self):
        ds = dataset({"y": [7, NULL]})
        out = run_plugin(ds, "ABNORMAL_FLAG", ds.meta, "rate", {"MODE": "RATE"})
        self.assertEqual(out.table.iloc[0].n, 0)
        self.assertIs(out.table.iloc[0].abnormal_rate, NULL)
        self.assertFalse(validate_dataset(out.dataset).issues)

    def test_measurement_validity_and_device_mismatch_exclude(self):
        ds = dataset({"y": [1, 2, 3], "device": ["d1", "d2", "d1"],
            "time": ["2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00", "2028-01-01T00:00:00+00:00"]})
        ds.meta["COLUMN"]["y"].update(MEASUREMENT={"DEVICE": "d1"}, MEASUREMENT_CONTEXT={"DEVICE_ID": "device",
            "TIME_ID": "time", "VALID_FROM": "2025-01-01T00:00:00+00:00", "VALID_TO": "2027-01-01T00:00:00+00:00", "REFERENCE": "Synthetic context"})
        self.assertEqual(numeric_series_for(ds, "y").dropna().tolist(), [1])
        self.assertFalse(validate_dataset(ds).issues)
        ds.df.loc[0, "time"] = "2026-01-01"
        with self.assertRaisesRegex(ValueError, "timezone"):
            numeric_series_for(ds, "y")

    def test_unknown_or_null_result_selector_is_error(self):
        ds = dataset({"y": [1]})
        for options in ({"RESULT_IDS": None}, {"RESULT": "y", "SCORE": "y"}, {"RESULT_TAGS": ["PANEL(absent)"]}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                result_binding(ds, options)

    def test_row_unit_mismatch_is_error(self):
        ds = plan(dataset({"y": [1, 2], "unit": ["mg/L", "mg/dL"]}))
        ds.meta["COLUMN"]["y"]["UNIT_ID"] = "unit"
        with self.assertRaisesRegex(ValueError, "UNIT"):
            analyze_dataset(ds)

    def test_censored_autoverification_never_passes(self):
        ds = dataset({"y": ["<5", ">10"]})
        ds.meta["COLUMN"]["y"]["REFERENCE_INTERVAL"] = {"LOW": 5, "HIGH": 10, "UNIT": "mg/L"}
        out = run_plugin(ds, "AUTOVERIFICATION", ds.meta, "rules", {})
        self.assertEqual(out.table.decision.tolist(), ["REVIEW", "REVIEW"])

    def test_qc_duplicate_and_missing_baseline_are_errors(self):
        from tametools.quality_control import evaluate_qc
        ds, options = qc_data()
        ds.df["run"] = "r1"
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            evaluate_qc(ds, [ds.columns[0]], options)
        ds, options = qc_data()
        options["BASELINES"][0]["SD"] = 0
        with self.assertRaisesRegex(ValueError, "positive SD"):
            evaluate_qc(ds, [ds.columns[0]], options)
        ds, options = qc_data()
        options["BASELINES"][0]["VALID_TO"] = "2026-01-02T00:00:00+00:00"
        with self.assertRaisesRegex(ValueError, "exactly one"):
            evaluate_qc(ds, [ds.columns[0]], options)

    def test_qc_sorting_and_same_side_consecutive_rule(self):
        from tametools.quality_control import evaluate_qc
        ds, options = qc_data()
        ds.df["y"] = [12.5, 12.6]
        forward = evaluate_qc(ds, [ds.columns[0]], options)
        backward = evaluate_qc(ds.with_df(ds.df.iloc[::-1].reset_index(drop=True)), [ds.columns[0]], options)
        pd.testing.assert_frame_equal(forward, backward)
        self.assertEqual(forward.violation.tolist(), ["NONE", "2_2s"])

    def test_affine_hba1c_profile_and_original_unit_spelling(self):
        from test_integration import source, profile
        ds, p = source(unit="percent", values=["5", "6"]), profile("mmol/mol")
        ds.meta["COLUMN"]["local_result"]["MEASUREMENT"].update(COMPONENT="HbA1c", PROPERTY="reported_percent", CALIBRATION="NGSP")
        p["TARGETS"]["creatinine"]["MEASUREMENT"].update(COMPONENT="HbA1c", PROPERTY="substance_ratio", CALIBRATION="IFCC")
        p["UNIT_ALIASES"] = {"percent": "%"}
        p["BRIDGES"] = {"hba1c": {"COMPONENT": "HbA1c", "FROM_UNIT": "%", "TO_UNIT": "mmol/mol", "FACTOR": "10.93", "OFFSET": "-23.50",
            "FROM_CALIBRATION": "NGSP", "TO_CALIBRATION": "IFCC", "REFERENCE": "https://ngsp.org/ifccngsp.asp (rounded inverse equation; synthetic inputs)"}}
        p["SOURCES"]["A"]["MAPPINGS"][0]["BRIDGE"] = "hba1c"
        result = integrate_datasets([ds], p)
        self.assertEqual(result.dataset.df.creatinine.tolist(), ["31.15", "42.08"])
        self.assertEqual(result.dataset.df.creatinine__raw_unit.tolist(), ["percent", "percent"])
        self.assertEqual(result.table.iloc[0].offset, "-23.5")
        self.assertFalse(validate_dataset(result.dataset).issues)

    def test_interval_and_status_survive_integration(self):
        from test_integration import context
        ds = interval_data()
        ds.df["record"] = ["r1", "r2", "r3", "r4"]
        ds.columns.append(ColumnSpec("record", "record", ["ID", "STR"]))
        ds.meta["COLUMN"]["record"] = {"ID": "record"}
        ds.meta["COLUMN"]["y"]["MEASUREMENT"] = context()
        ds.meta["SOURCE"] = {"ID": "A", "SITE_ID": "site", "RECORD_ID": "record"}
        p = {"VERSION": 1, "ID": "interval-test", "REFERENCE": "Synthetic interval test", "TARGETS": {"y": {"UNIT": "mg/dL", "MEASUREMENT": context()}},
            "SOURCES": {"A": {"MAPPINGS": [{"COLUMN_ID": "y", "TARGET_ID": "y"}]}}}
        result = integrate_datasets([ds], p).dataset
        values, mask = measurement_values(result, result.columns_with_tag("RESULT")[0], "MIDPOINT")
        np.testing.assert_allclose(values, [.3, .5, 1, .7])
        self.assertEqual(mask.sum(), 3)
        self.assertFalse(validate_dataset(result).issues)

    def test_legacy_integration_output_still_validates(self):
        from test_integration import source, profile
        from tametools.integration import PREFIX, LEGACY_SUFFIXES
        ds = integrate_datasets([source()], profile()).dataset
        ds.meta["INTEGRATION_RESULT"]["VERSION"] = 1
        names = PREFIX + ["creatinine" + suffix for suffix in LEGACY_SUFFIXES]
        ds.meta["COLUMN"] = {name: {key: value for key, value in ds.meta["COLUMN"][name].items()
            if key in {"ID", "UNIT", "MEASUREMENT", "INTEGRATION"}} for name in names}
        ds = ds.replace(df=ds.df[names], columns=[c for c in ds.columns if c.name in names])
        self.assertFalse(validate_dataset(ds).issues)

    def test_survey_model_retains_zero_contribution_psu(self):
        ds = model_data()
        for name, values in {"w": [1]*12, "s": [1]*6+[2]*6, "p": [1]*3+[2]*3+[1]*3+[2]*3}.items():
            ds.df[name] = values
            ds.columns.append(ColumnSpec(name, name, ["NUM"]))
            ds.meta["COLUMN"][name] = {"ID": name}
        ds.meta["SURVEY"] = {"DESIGN": "STRATIFIED_CLUSTER_WR", "WEIGHT_ID": "w", "STRATUM_ID": "s", "PSU_ID": "p", "EXPECTED_ROWS": 12}
        plan(ds, MODE="SURVEY", GROUPS={"domain": {"ALL_OF": [{"COLUMN_ID": "x", "MIN": 3}]}},
            MODELS=[{"ID": "adjusted", "Y_ID": "y", "NUMERIC_IDS": ["x"], "COVARIANCE": "SURVEY", "POLICY": "DELETE", "GROUP": "domain"}])
        table = analyze_dataset(ds).tables["models"]
        matrix = np.column_stack([np.ones(12), np.arange(12)])
        y = ds.df.y.to_numpy(float)
        use = np.arange(12) >= 3
        bread = np.linalg.inv(matrix[use].T @ matrix[use])
        beta = bread @ matrix[use].T @ y[use]
        scores = np.zeros_like(matrix)
        scores[use] = matrix[use] * (y[use] - matrix[use] @ beta)[:, None]
        psu_scores = scores.reshape(4, 3, 2).sum(axis=1)
        meat = np.zeros((2, 2))
        for indexes in ([0, 1], [2, 3]):
            centered = psu_scores[indexes] - psu_scores[indexes].mean(axis=0)
            meat += 2 * centered.T @ centered
        np.testing.assert_allclose(table.coefficient, beta)
        np.testing.assert_allclose(table.se, np.sqrt(np.diag(bread @ meat @ bread)))
        self.assertEqual(table.df.tolist(), [1, 1])

    def test_unknown_covariate_and_rank_deficient_models_fail(self):
        ds = model_data()
        plan(ds, MODELS=[{"ID": "m", "Y_ID": "y", "NUMERIC_IDS": ["x"], "COVARIANCE": "HC3", "POLICY": "DELETE"}])
        ds.df["x"] = 1
        with self.assertRaisesRegex(ValueError, "rank-deficient"):
            analyze_dataset(ds)


def interval_data():
    ds = dataset({"y": [3, 3.5, 11, 7], "released": [3, 3.5, 11, 7], "kind": ["EXACT", "LEFT", "RIGHT", "INTERVAL"],
        "lo": [NULL, NULL, 10, 6], "hi": [NULL, 5, NULL, 8]})
    for name in ("released", "lo", "hi"):
        numeric(ds, name, "mg/L")
    ds.meta["COLUMN"]["y"]["CENSORING_INTERVAL"] = {"VERSION": 1, "RELEASED_ID": "released", "KIND_ID": "kind", "LOWER_ID": "lo", "UPPER_ID": "hi", "REFERENCE": "Synthetic test"}
    return ds


def model_data():
    ds = dataset({"y": [2, 4, 4, 8, 9, 10, 13, 12, 17, 17, 20, 21], "x": list(range(12)), "person": [str(i//2) for i in range(12)]})
    numeric(ds, "x")
    return ds


def qc_data():
    ds = dataset({"y": [7.5, 12.5], "run": ["r1", "r2"], "level": ["L", "L"], "lot": ["lot1"]*2,
        "instrument": ["i1"]*2, "time": ["2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00"]})
    baseline = {"RESULT_ID": "y", "LEVEL": "L", "LOT": "lot1", "INSTRUMENT": "i1", "MEAN": 10, "SD": 1, "UNIT": "mg/L",
        "REFERENCE": "Synthetic fixed baseline", "VALID_FROM": "2025-01-01T00:00:00+00:00", "VALID_TO": "2027-01-01T00:00:00+00:00"}
    options = {"RUN_ID": "run", "TIME_ID": "time", "LEVEL_ID": "level", "LOT_ID": "lot", "INSTRUMENT_ID": "instrument", "BASELINES": [baseline]}
    return ds, options

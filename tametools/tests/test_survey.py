from copy import deepcopy
from importlib.util import find_spec
import unittest

import numpy as np
import pandas as pd

from tametools.models import ColumnSpec, TameDataset
from tametools.pipeline import execute_work
from tametools.survey import survey_describe


def fixture():
    frame = pd.DataFrame({"y": [1, 3, 2, 6, 4, 8, 5, 9], "w": [1, 2, 1, 2, 1, 2, 1, 2],
                          "s": [1, 1, 1, 1, 2, 2, 2, 2], "p": [1, 1, 2, 2, 1, 1, 2, 2],
                          "d": [1, 1, 0, 0, 1, 1, 1, 1]})
    columns = [ColumnSpec(c, c, ["RESULT", "NUM"] if c == "y" else ["NUM"]) for c in frame]
    return TameDataset(frame, columns, meta={"ANALYSIS_CONTRACT": {"VERSION": 1},
        "COLUMN": {c: {"ID": c, **({"UNIT": "mg/L"} if c == "y" else {})} for c in frame},
        "SURVEY": {"DESIGN": "STRATIFIED_CLUSTER_WR", "WEIGHT_ID": "w", "STRATUM_ID": "s", "PSU_ID": "p", "EXPECTED_ROWS": 8},
        "SURVEY_DESCRIBE": {"RESULT_IDS": ["y"], "DOMAIN_IDS": ["d"]},
        "WORKS": {"DEFAULT": ["SURVEY_DESCRIBE"]}})


def direct_variance(frame, domain):
    take = domain & frame.y.notna()
    mean = np.average(frame.y[take], weights=frame.w[take])
    scores = frame.w * (frame.y.fillna(0) - mean) * take / frame.w[take].sum()
    psu = scores.groupby([frame.s, frame.p]).sum()
    variance = sum(len(group) / (len(group) - 1) * ((group - group.mean()) ** 2).sum() for _, group in psu.groupby(level=0))
    return mean, np.sqrt(variance)


@unittest.skipUnless(find_spec("samplics"), "optional samplics survey engine is not installed")
class SurveyTests(unittest.TestCase):
    def test_mean_se_match_independent_psu_scores(self):
        ds = fixture()
        rows = survey_describe(ds).table
        for domain in ["ALL", "d"]:
            take = pd.Series(True, index=ds.df.index) if domain == "ALL" else ds.df.d.eq(1)
            mean, se = direct_variance(ds.df, take)
            row = rows.set_index("domain").loc[domain]
            self.assertAlmostEqual(row["mean"], mean, places=12)
            self.assertAlmostEqual(row.se, se, places=12)
        self.assertEqual(rows.design_psus.tolist(), [4, 4])
        self.assertEqual(rows.df.tolist(), [2, 1])

    def test_missing_outcomes_retain_design(self):
        ds = fixture()
        ds.df.loc[[2, 3], "y"] = np.nan
        row = survey_describe(ds).table.iloc[0]
        mean, se = direct_variance(ds.df, pd.Series(True, index=ds.df.index))
        self.assertAlmostEqual(row["mean"], mean)
        self.assertAlmostEqual(row.se, se)
        self.assertEqual(row.design_n, 8)
        self.assertEqual(row.analysis_n, 6)
        self.assertEqual(row.missing_n, 2)

    def test_zero_weight_excluded_explicitly(self):
        ds = fixture()
        ds.df.loc[0, "w"] = 0
        row = survey_describe(ds).table.iloc[0]
        self.assertEqual(row.zero_weight_n, 1)
        self.assertEqual(row.design_n, 7)

    def test_invalid_weights_fail(self):
        for bad in [-1, np.nan, np.inf, "invalid"]:
            ds = fixture()
            ds.df = ds.df.astype(object)
            ds.df.loc[0, "w"] = bad
            with self.assertRaises(ValueError):
                survey_describe(ds)

    def test_bad_domains_fail(self):
        for bad in [2, np.nan]:
            ds = fixture()
            ds.df.loc[0, "d"] = bad
            with self.assertRaisesRegex(ValueError, "Domain"):
                survey_describe(ds)

    def test_accidental_prefilter_fails(self):
        ds = fixture()
        with self.assertRaisesRegex(ValueError, "full design"):
            survey_describe(ds.with_df(ds.df.iloc[:4]))

    def test_singleton_design_fails(self):
        ds = fixture()
        ds.df["p"] = 1
        with self.assertRaisesRegex(ValueError, "Singleton"):
            survey_describe(ds)

    def test_unknown_policy_and_unsupported_design_fail(self):
        ds = fixture()
        ds.meta["SURVEY_DESCRIBE"]["POLICIES"] = ["VAULE"]
        with self.assertRaises(ValueError):
            survey_describe(ds)
        ds = fixture()
        ds.meta["SURVEY"]["FPC"] = .1
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            survey_describe(ds)

    def test_empty_domain_is_not_zero_mean(self):
        ds = fixture()
        ds.df["d"] = 0
        row = survey_describe(ds).table.iloc[1]
        self.assertEqual(row.status, "empty_domain")
        self.assertTrue(np.isnan(row["mean"]))

    def test_eligibility_does_not_drop_design(self):
        ds = fixture()
        ds.meta["COLUMN"]["y"]["ELIGIBLE_ID"] = "d"
        row = survey_describe(ds).table.iloc[0]
        self.assertEqual(row.domain_n, 6)
        self.assertEqual(row.design_n, 8)
        self.assertFalse(row.censoring_flag_available)
        ds.df.loc[0, "d"] = 2
        with self.assertRaisesRegex(ValueError, "Domain|Eligibility"):
            survey_describe(ds)

    def test_domain_with_no_df_has_no_ci(self):
        ds = fixture()
        ds.df["d"] = [1, 1, 0, 0, 0, 0, 0, 0]
        row = survey_describe(ds).table.iloc[1]
        self.assertEqual(row.status, "insufficient_domain_df")
        self.assertTrue(np.isnan(row.ci95_low))

    def test_workflow_logs_explicit_options(self):
        output = execute_work(fixture())
        self.assertEqual(len(output.outputs[0].table), 2)
        self.assertIn("LOG", output.final_dataset.meta)

    def test_header_and_row_order_invariant(self):
        ds = fixture()
        expected = survey_describe(ds).table
        meta = deepcopy(ds.meta)
        meta["COLUMN"] = {"renamed_" + k: v for k, v in meta["COLUMN"].items()}
        renamed = ds.replace(df=ds.df.rename(columns=lambda c: "renamed_" + c).iloc[::-1],
            columns=[ColumnSpec("renamed_" + c.name, "renamed_" + c.name, c.tags) for c in ds.columns], meta=meta)
        actual = survey_describe(renamed).table
        np.testing.assert_allclose(expected[["mean", "se", "ci95_low", "ci95_high"]], actual[["mean", "se", "ci95_low", "ci95_high"]], rtol=1e-12)


if __name__ == "__main__":
    unittest.main()

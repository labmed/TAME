from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from tametools.analysis import validate_dataset
from tametools.analysis_contract import derive_censored_results, measurement_values, resolve_id
from tametools.cellstate import NULL
from tametools.io import read_tame, write_tame, read_xlsx, write_xlsx
from tametools.merge import merge_datasets
from tametools.models import ColumnSpec, TameDataset
from tametools.pipeline import execute_work


def fixture():
    frame = pd.DataFrame({"raw": ["0.11", "2", NULL], "code": ["1", "0", NULL], "result": [None] * 3})
    columns = [ColumnSpec("raw", "raw", ["NUM", "NULLABLE"]),
               ColumnSpec("code", "code", ["CATEGORY", "NULLABLE"]),
               ColumnSpec("result", "result", ["RESULT", "<NUM>", "NULLABLE"])]
    return TameDataset(frame, columns, meta={"ANALYSIS_CONTRACT": {"VERSION": 1},
        "COLUMN": {"raw": {"ID": "released", "UNIT": "mg/L"}, "code": {"ID": "flag"},
                   "result": {"ID": "analysis", "UNIT": "mg/L", "CENSORING": {
                       "SOURCE_ID": "released", "FLAG_ID": "flag", "LIMIT": .15,
                       "BELOW_CODE": 1, "OBSERVED_CODE": 0}}},
        "WORKS": {"DEFAULT": ["CENSOR", "VALIDATE"]}})


class AnalysisContractTests(unittest.TestCase):
    def test_derivation_is_non_destructive_and_preserves_null(self):
        ds = fixture()
        output = derive_censored_results(ds)
        self.assertIsNone(ds.df.result.iloc[0])
        self.assertEqual(output.df.raw.iloc[0], "0.11")
        self.assertEqual(output.df.result.iloc[0], "<0.15")
        self.assertEqual(output.df.result.iloc[1], "2")
        self.assertIs(output.df.result.iloc[2], NULL)
        self.assertFalse(validate_dataset(output).issues)

    def test_policy_values(self):
        ds = derive_censored_results(fixture())
        for policy, first in [("RELEASED", .11), ("VALUE", .15), ("DELETE", np.nan)]:
            values, below = measurement_values(ds, resolve_id(ds, "analysis"), policy)
            np.testing.assert_equal(values.to_numpy(), [first, 2., np.nan])
            self.assertEqual(below.tolist(), [True, False, False])

    def test_unknown_policy_fails(self):
        ds = fixture()
        with self.assertRaisesRegex(ValueError, "Policy"):
            measurement_values(ds, ds.columns[-1], "VAULE")

    def test_duplicate_and_unknown_ids_fail(self):
        ds = fixture()
        with self.assertRaisesRegex(ValueError, "Unknown"):
            resolve_id(ds, "missing")
        ds.meta["COLUMN"]["code"]["ID"] = "released"
        self.assertTrue(validate_dataset(ds).issues)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            derive_censored_results(ds)

    def test_invalid_binding_parameters_fail(self):
        changes = [("LIMIT", 0), ("LIMIT", float("inf")), ("BELOW_CODE", 0),
                   ("SOURCE_ID", "analysis"), ("FLAG_ID", "unknown"), ("EXTRA", 1)]
        for key, value in changes:
            with self.subTest(key=key, value=value):
                ds = fixture()
                ds.meta["COLUMN"]["result"]["CENSORING"][key] = value
                with self.assertRaises(ValueError):
                    derive_censored_results(ds)

    def test_missingness_and_unknown_codes_fail(self):
        for value in [NULL, "2", "nonsense", "inf"]:
            ds = fixture()
            ds.df.loc[0, "code"] = value
            with self.assertRaises(ValueError):
                derive_censored_results(ds)

    def test_observed_code_below_limit_fails(self):
        ds = fixture()
        ds.df.loc[0, "code"] = "0"
        with self.assertRaisesRegex(ValueError, "observed code"):
            derive_censored_results(ds)

    def test_unit_and_version_conflicts_fail(self):
        ds = fixture()
        ds.meta["COLUMN"]["result"]["UNIT"] = "mg/dL"
        with self.assertRaisesRegex(ValueError, "UNIT"):
            derive_censored_results(ds)
        ds = fixture()
        ds.meta["ANALYSIS_CONTRACT"]["VERSION"] = 2
        with self.assertRaisesRegex(ValueError, "VERSION"):
            derive_censored_results(ds)

    def test_stale_derivation_is_detected(self):
        ds = derive_censored_results(fixture())
        ds.df.loc[0, "result"] = "0.11"
        self.assertTrue(validate_dataset(ds).issues)
        with self.assertRaisesRegex(ValueError, "contradicts"):
            measurement_values(ds, ds.columns[-1], "RELEASED")

    def test_header_rename_keeps_bindings(self):
        ds = fixture()
        mapping = dict(zip(ds.df.columns, ["a", "b", "c"]))
        meta = deepcopy(ds.meta)
        meta["COLUMN"] = {mapping[k]: v for k, v in meta["COLUMN"].items()}
        renamed = ds.replace(df=ds.df.rename(columns=mapping),
                             columns=[ColumnSpec(mapping[c.name], mapping[c.name], c.tags) for c in ds.columns], meta=meta)
        result = derive_censored_results(renamed)
        self.assertEqual(result.df.c.iloc[0], "<0.15")
        self.assertEqual(resolve_id(result, "analysis").name, "c")

    def test_workflow_and_round_trips(self):
        output = execute_work(fixture())
        self.assertFalse(output.outputs[-1].issues)
        ds = output.final_dataset
        self.assertIn("LOG", ds.meta)
        with tempfile.TemporaryDirectory() as directory:
            for ext, write, read in [("tame", write_tame, read_tame), ("xlsx", write_xlsx, read_xlsx)]:
                path = Path(directory) / ("bound." + ext)
                write(path, ds)
                loaded = read(path)
                self.assertFalse(validate_dataset(loaded).issues)
                self.assertEqual(loaded.meta, ds.meta)

    def test_id_merge_disambiguates_identical_result_tags(self):
        columns = [ColumnSpec(c, c, ["RESULT", "NUM"]) for c in ["ALT", "GGT"]]
        ds = TameDataset(pd.DataFrame({"ALT": [10], "GGT": [20]}), columns,
                         meta={"COLUMN": {"ALT": {"ID": "alt", "UNIT": "U/L"}, "GGT": {"ID": "ggt", "UNIT": "IU/L"}}})
        renamed = ds.replace(df=pd.DataFrame({"a": [30], "b": [40]}),
                             columns=[ColumnSpec(c, c, ["RESULT", "NUM"]) for c in ["a", "b"]],
                             meta={"COLUMN": {"a": ds.meta["COLUMN"]["ALT"], "b": ds.meta["COLUMN"]["GGT"]}})
        merged = merge_datasets([ds, renamed], add_source_column=False).dataset
        self.assertEqual(merged.df.ALT.tolist(), [10, 30])
        self.assertEqual(merged.df.GGT.tolist(), [20, 40])
        self.assertEqual(merged.meta["COLUMN"], ds.meta["COLUMN"])

    def test_unit_mismatch_and_unknown_unit_are_not_merged(self):
        for unit in ["mg/dL", None]:
            a = derive_censored_results(fixture())
            b = a.replace(meta=deepcopy(a.meta))
            if unit is None:
                del b.meta["COLUMN"]["raw"]["UNIT"]
            else:
                b.meta["COLUMN"]["raw"]["UNIT"] = unit
            with self.assertRaisesRegex(ValueError, "UNIT_CONFLICT"):
                merge_datasets([a, b])

    def test_survey_concatenation_requires_review(self):
        ds = fixture()
        ds.meta["SURVEY"] = {"DESIGN": "STRATIFIED_CLUSTER_WR"}
        with self.assertRaisesRegex(ValueError, "Survey designs"):
            merge_datasets([ds, ds])


if __name__ == "__main__":
    unittest.main()

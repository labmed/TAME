from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from tametools.analysis import describe_dataset, exploratory_data_analysis, numeric_series_for, validate_dataset
from tametools.categories import category_validation_issues, normalize_categories
from tametools.io import read_tame, read_xlsx, write_tame, write_xlsx
from tametools.models import ColumnSpec, TameDataset
from tametools.planned_analysis import analyze_dataset, write_analysis_report
from tametools.tag_catalog import flat_tag_names
from tametools.tags import parse_header


def fixture():
    frame = pd.DataFrame({"amount": ["2", "4", "6"], "grade": ["0", "1", "2"], "class": ["a", "b", "b"]})
    columns = [ColumnSpec("amount", "amount", ["RESULT", "NUM", "PANEL(chemistry)", "ANALYTE(urea)", "SPECIMEN(serum)"]),
               ColumnSpec("grade", "grade", ["RESULT", "NUM", "NOMINAL", "GRADES"]),
               ColumnSpec("class", "class", ["NOMINAL", "CLASSES"])]
    meta = {"ANALYSIS_CONTRACT": {"VERSION": 1},
            "COLUMN": {"amount": {"ID": "urea", "UNIT": "mg/dL", "MEASUREMENT": {"COMPONENT": "urea", "SPECIMEN": "serum"}},
                       "grade": {"ID": "grade"}, "class": {"ID": "class"}},
            "CATEGORIES": {"GRADES": {"VALUES": ["0", "1", "2"], "STRICT": True},
                           "CLASSES": {"VALUES": ["a", "b"], "STRICT": True}},
            "ANALYSIS_PLAN": {"VERSION": 1, "MODE": "SAMPLE", "RESULT_TAGS": ["RESULT", "PANEL(chemistry)"],
                              "POLICIES": ["RELEASED"], "PRIMARY_POLICY": "RELEASED", "HISTOGRAM_BINS": 5}}
    return TameDataset(frame, columns, meta=meta)


class MeasurementTagTests(unittest.TestCase):
    def test_catalog_and_parser(self):
        self.assertTrue({"ANALYTE", "SPECIMEN", "PANEL", "CONDITION", "NOMINAL", "ORDINAL"} <= set(flat_tag_names()))
        self.assertEqual(parse_header("[[ANALYTE(Urea)::SPECIMEN(serum)::NUM]]x")[1],
                         ("ANALYTE(urea)", "SPECIMEN(serum)", "NUM"))

    def test_annotations_do_not_imply_results_or_units(self):
        ds = TameDataset(pd.DataFrame({"x": ["2"]}), [ColumnSpec("x", "x", ["ANALYTE(urea)", "SPECIMEN(serum)"])])
        self.assertFalse(ds.columns_with_tag("RESULT"))
        self.assertFalse(ds.columns_with_tag("CATEGORY"))

    def test_nominal_numeric_grammar_does_not_enable_averaging(self):
        ds = fixture()
        self.assertFalse(validate_dataset(ds).issues)
        summary = describe_dataset(ds).set_index("column")
        self.assertEqual(summary.loc["grade", "kind"], "categorical")
        self.assertTrue(pd.isna(summary.loc["grade", "mean"]))
        with self.assertRaisesRegex(ValueError, "not quantitative"):
            numeric_series_for(ds, "grade")
        eda = exploratory_data_analysis(ds)
        self.assertNotIn("grade", set(eda.numeric_percentiles.column))
        self.assertIn("grade", set(eda.category_distribution.column))

    def test_inherited_category_vocabulary_validation_and_normalization(self):
        ds = fixture()
        ds.df.loc[0, "class"] = " A "
        normalized, report = normalize_categories(ds)
        self.assertEqual(normalized.df.loc[0, "class"], "a")
        self.assertEqual(report.changed_cells.sum(), 1)
        ds.df.loc[0, "class"] = "invalid"
        self.assertTrue(category_validation_issues(ds))

    def test_custom_category_subtype(self):
        ds = fixture()
        ds.columns[2] = ColumnSpec("class", "class", ["LOCAL_LABEL", "CLASSES"])
        ds.meta["TAG_DEFINITIONS"] = {"LOCAL_LABEL": {"INHERITS": ["NOMINAL"]}}
        ds.df.loc[0, "class"] = "invalid"
        self.assertTrue(category_validation_issues(ds))

    def test_ordinal_requires_explicit_levels(self):
        ds = fixture()
        ds.columns[1] = ColumnSpec("grade", "grade", ["ORDINAL", "NUM"])
        self.assertTrue(validate_dataset(ds).issues)
        ds.meta["COLUMN"]["grade"]["LEVELS"] = ["0", "1", "2"]
        self.assertFalse(validate_dataset(ds).issues)
        for levels in [["0", "0"], [0, 1, 2], [], ["0", "1"]]:
            ds.meta["COLUMN"]["grade"]["LEVELS"] = levels
            with self.subTest(levels=levels):
                self.assertTrue(validate_dataset(ds).issues)

    def test_conflicting_scales_and_comparator_categories_fail(self):
        for tags in [["ORDINAL", "NOMINAL"], ["NOMINAL", "<NUM>"]]:
            ds = fixture()
            ds.columns[1] = ColumnSpec("grade", "grade", tags)
            self.assertTrue(validate_dataset(ds).issues)

    def test_analyte_and_specimen_conflicts_fail(self):
        for tag in ["ANALYTE(urea_nitrogen)", "SPECIMEN(urine)"]:
            ds = fixture()
            old = ds.columns[0]
            ds.columns[0] = ColumnSpec(old.name, old.name, [*old.tags, tag])
            with self.subTest(tag=tag), self.assertRaisesRegex(ValueError, "Conflicting|contradicts"):
                analyze_dataset(ds)
        ds = fixture()
        ds.meta["COLUMN"]["amount"]["MEASUREMENT"]["COMPONENT"] = "urea_nitrogen"
        self.assertTrue(validate_dataset(ds).issues)

    def test_tag_selector_matches_without_mutating_plan(self):
        ds = fixture()
        before = deepcopy(ds.meta)
        output = analyze_dataset(ds)
        self.assertEqual(output.tables["sample_summary"].result_id.tolist(), ["urea"])
        self.assertEqual(output.tables["sample_summary"]["mean"].iloc[0], 4)
        self.assertEqual(ds.meta, before)

    def test_renamed_reordered_columns_and_inherited_panel(self):
        ds = fixture()
        original = analyze_dataset(ds).tables["sample_summary"].drop(columns="column")
        ds.meta["TAG_DEFINITIONS"] = {"LOCAL_PANEL": {"INHERITS": ["PANEL(chemistry)"]}}
        ds.columns[0] = ColumnSpec("amount", "amount", ["RESULT", "NUM", "LOCAL_PANEL", "ANALYTE(urea)"])
        names = {"amount": "검사값", "grade": "등급", "class": "집단"}
        ds.meta["COLUMN"] = {names[k]: v for k, v in ds.meta["COLUMN"].items()}
        columns = [ColumnSpec(names[c.name], names[c.name], c.tags) for c in reversed(ds.columns)]
        ds = ds.replace(df=ds.df.rename(columns=names)[[c.name for c in columns]], columns=columns)
        pd.testing.assert_frame_equal(original, analyze_dataset(ds).tables["sample_summary"].drop(columns="column"))

    def test_empty_ambiguous_and_invalid_tag_selection_fail(self):
        for tags in [[], ["PANEL(absent)"], ["RESULT", "result"], ["bad::tag"], ["NOMINAL"], ["RESULT"]]:
            ds = fixture()
            ds.meta["ANALYSIS_PLAN"]["RESULT_TAGS"] = tags
            with self.subTest(tags=tags), self.assertRaises(ValueError):
                analyze_dataset(ds)
        ds = fixture()
        ds.meta["ANALYSIS_PLAN"]["RESULT_IDS"] = ["urea"]
        with self.assertRaisesRegex(ValueError, "exactly one"):
            analyze_dataset(ds)

    def test_selected_columns_need_id_and_unit(self):
        for key in ["ID", "UNIT"]:
            ds = fixture()
            del ds.meta["COLUMN"]["amount"][key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                analyze_dataset(ds)

    def test_explicit_ids_also_reject_categorical_result(self):
        ds = fixture()
        del ds.meta["ANALYSIS_PLAN"]["RESULT_TAGS"]
        ds.meta["ANALYSIS_PLAN"]["RESULT_IDS"] = ["grade"]
        ds.meta["COLUMN"]["grade"]["UNIT"] = "1"
        with self.assertRaisesRegex(ValueError, "NOMINAL/ORDINAL"):
            analyze_dataset(ds)

    def test_tag_plan_survives_storage_and_manifest_records_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ds = fixture()
            for suffix, writer, reader in [("tame", write_tame, read_tame), ("xlsx", write_xlsx, read_xlsx)]:
                path = root / ("input." + suffix)
                writer(path, ds)
                out = analyze_dataset(reader(path))
                self.assertEqual(out.tables["sample_summary"].analysis_n.tolist(), [3])
            write_analysis_report(out, root / "report")
            manifest = json.loads((root / "report/manifest.json").read_text())
            self.assertEqual(manifest["resolved_result_ids"], ["urea"])
            self.assertEqual(manifest["plan"]["RESULT_TAGS"], ["RESULT", "PANEL(chemistry)"])


if __name__ == "__main__":
    unittest.main()

from copy import deepcopy
from decimal import Decimal
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pandas as pd

from tametools.analysis import validate_dataset
from tametools.cellstate import NULL, cell_state, serialize_cell
from tametools.integration import integrate_datasets, restore_integration_sources
from tametools.io import read_tame, read_xlsx, write_tame, write_xlsx
from tametools.merge import merge_datasets
from tametools.models import ColumnSpec, TameDataset
from tametools.pipeline import execute_work
from tametools.toml_compat import dumps
from tametools.units import convert_cell, decimal_number, unit_factor


def context(prop="mass_concentration", component="creatinine", method="declared-method-v1"):
    return dict(COMPONENT=component, SPECIMEN="serum", METHOD=method, TIME="Pt", SCALE="Qn", PROPERTY=prop)


def source(sid="A", unit="mg/dL", values=None):
    values = ["1.2", "<0.1", NULL] if values is None else values
    frame = pd.DataFrame({"accession": ["001"] * len(values), "local_result": values, "note": ["unchanged"] * len(values)})
    columns = [ColumnSpec("accession", "accession", ["ID", "STR"]),
               ColumnSpec("local_result", "local_result", ["RESULT", "<NUM>", "NULLABLE"]),
               ColumnSpec("note", "note", ["STR"])]
    prop = "substance_concentration" if unit in {"umol/L", "mmol/L"} else "mass_concentration"
    return TameDataset(frame, columns, meta={"SOURCE": {"ID": sid, "SITE_ID": "site_" + sid, "RECORD_ID": "record"},
        "COLUMN": {"accession": {"ID": "record"}, "local_result": {"ID": "local", "UNIT": unit, "MEASUREMENT": context(prop)}}})


def profile(unit="mg/L"):
    prop = "substance_concentration" if unit in {"umol/L", "mmol/L"} else "mass_concentration"
    return {"VERSION": 1, "ID": "technical-test-v1", "REFERENCE": "Synthetic software regression fixture, not a clinical approval",
            "TARGETS": {"creatinine": {"UNIT": unit, "MEASUREMENT": context(prop)}},
            "SOURCES": {sid: {"MAPPINGS": [{"COLUMN_ID": "local", "TARGET_ID": "creatinine"}]} for sid in ("A", "B")}}


def cells(dataset):
    return [[str(serialize_cell(v)) for v in row] for row in dataset.df.itertuples(index=False, name=None)]


def rename(dataset):
    names = {c.name: "renamed_" + str(i) for i, c in enumerate(dataset.columns)}
    meta = deepcopy(dataset.meta)
    meta["COLUMN"] = {names[key]: value for key, value in meta["COLUMN"].items()}
    return dataset.replace(df=dataset.df.rename(columns=names), columns=[ColumnSpec(names[c.name], names[c.name], c.tags) for c in dataset.columns], meta=meta)


@unittest.skipUnless(importlib.util.find_spec("pint"), "optional Pint dependency is not installed")
class IntegrationTests(unittest.TestCase):
    def test_pint_scaling(self):
        self.assertEqual(unit_factor("g/dL", "g/L"), Decimal(10))
        self.assertEqual(unit_factor("mg/dL", "mg/L"), Decimal(10))
        self.assertAlmostEqual(float(unit_factor("U/L", "ukat/L")), 1/60)

    def test_invalid_conversion_factors_are_rejected(self):
        for factor in [0, -1, True, "NaN", "inf"]:
            with self.subTest(factor=factor), self.assertRaises(ValueError):
                convert_cell("<1", factor)

    def test_source_preservation_and_distinct_local_records(self):
        a, b = source(), source("B", "mg/L", ["12", "<1", NULL])
        before = cells(a), deepcopy(a.meta)
        result = integrate_datasets([a, b], profile())
        self.assertEqual(result.dataset.df.creatinine.tolist()[:2], ["12", "<1"])
        self.assertEqual(len(result.dataset.df), 6)
        self.assertEqual(result.dataset.df.source_record_id.tolist(), ["001"] * 6)
        self.assertEqual(result.dataset.df.source_id.tolist(), ["A"] * 3 + ["B"] * 3)
        self.assertEqual((cells(a), a.meta), before)
        originals = restore_integration_sources(result.dataset)
        self.assertEqual(cells(originals["A"]), cells(a))
        self.assertEqual(originals["A"].meta, a.meta)
        self.assertFalse(validate_dataset(result.dataset).issues)

    def test_all_comparators_and_scientific_notation(self):
        data = source(values=["<0.1", "<=0.1", ">2", ">=2", "=1", "1e-2", "-0.1", "0"])
        result = integrate_datasets([data], profile()).dataset
        self.assertEqual(result.df.creatinine.tolist(), ["<1", "<=1", ">20", ">=20", "=10", "0.1", "-1", "0"])

    def test_nonvalue_states_are_not_zero_or_dropped(self):
        data = source(values=[None, NULL, "", "  ", "\t"])
        result = integrate_datasets([data], profile()).dataset
        self.assertEqual([cell_state(v) for v in result.df.creatinine], [cell_state(v) for v in data.df.local_result])
        self.assertEqual(cells(restore_integration_sources(result)["A"]), cells(data))

    def test_cell_state_literal_is_restored_from_unmapped_notes(self):
        data = source()
        data.df["note"] = ["<<NULL>>", "\t", 'text "quoted"\nsecond line']
        result = integrate_datasets([data], profile()).dataset
        self.assertEqual(cells(restore_integration_sources(result)["A"]), cells(data))

    def test_missing_unit_and_unsupported_units_fail(self):
        for unit in [None, "MG/DL", "mg%", "mEq/L", "IU/L", "%", "mmol/mol", "degC"]:
            with self.subTest(unit=unit), self.assertRaises(ValueError):
                integrate_datasets([source(unit=unit)], profile())

    def test_dimensional_bridge_required(self):
        with self.assertRaisesRegex(ValueError, "DIMENSION_CONFLICT"):
            integrate_datasets([source()], profile("umol/L"))

    def test_explicit_analyte_specific_bridge(self):
        p = profile("umol/L")
        p["BRIDGES"] = {"cr": dict(COMPONENT="creatinine", FROM_UNIT="mg/dL", TO_UNIT="umol/L", FACTOR="88.4", REFERENCE="NHANES BIOPRO_J codebook")}
        p["SOURCES"]["A"]["MAPPINGS"][0]["BRIDGE"] = "cr"
        result = integrate_datasets([source()], p).dataset
        self.assertEqual(result.df.creatinine.tolist()[:2], ["106.08", "<8.84"])
        self.assertFalse(validate_dataset(result).issues)

    def test_bridge_cannot_relabel_component_or_reverse_units(self):
        for field, value in [("COMPONENT", "bilirubin"), ("FROM_UNIT", "mg/L"), ("TO_UNIT", "mmol/L"), ("FACTOR", -88.4), ("FACTOR", 0), ("FACTOR", True), ("REFERENCE", "")]:
            p = profile("umol/L")
            p["BRIDGES"] = {"cr": dict(COMPONENT="creatinine", FROM_UNIT="mg/dL", TO_UNIT="umol/L", FACTOR="88.4", REFERENCE="codebook")}
            p["BRIDGES"]["cr"][field] = value
            p["SOURCES"]["A"]["MAPPINGS"][0]["BRIDGE"] = "cr"
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                integrate_datasets([source()], p)

    def test_method_specimen_and_other_contexts_fail_closed(self):
        for field in ["COMPONENT", "SPECIMEN", "METHOD", "TIME", "SCALE", "PROPERTY", "DEVICE", "CALIBRATION"]:
            a = source()
            a.meta["COLUMN"]["local_result"]["MEASUREMENT"][field] = "different"
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "CONFLICT"):
                integrate_datasets([a], profile())

    def test_unknown_measurement_field_is_not_ignored(self):
        a = source()
        a.meta["COLUMN"]["local_result"]["MEASUREMENT"]["METHDO"] = "typo"
        with self.assertRaises(ValueError):
            integrate_datasets([a], profile())

    def test_constant_bounds_scale_but_are_not_pooled(self):
        a, b = source(), source("B", "mg/L")
        a.meta["COLUMN"]["local_result"].update(LLOD=.1, ULOQ=10, REFERENCE_INTERVAL=dict(LOW=.5, HIGH=1.5, POPULATION="local group"))
        b.meta["COLUMN"]["local_result"].update(LLOD=.2, REFERENCE_INTERVAL=dict(LOW=6, HIGH=16))
        result = integrate_datasets([a,b], profile()).dataset
        self.assertEqual(result.df.creatinine__llod.tolist(), ["1"] * 3 + ["0.2"] * 3)
        self.assertEqual(result.df.creatinine__ref_high.tolist(), ["15"] * 3 + ["16"] * 3)
        self.assertEqual(result.df.creatinine__uloq.iloc[0], "100")

    def test_per_row_bounds_and_source_unit_check(self):
        a = source()
        a.df["lower"] = [".1", ".2", NULL]
        a.columns.append(ColumnSpec("lower", "lower", ["NUM", "NULLABLE"]))
        a.meta["COLUMN"]["lower"] = {"ID": "lower", "UNIT": "mg/dL"}
        a.meta["COLUMN"]["local_result"].update(LLOD_ID="lower", REFERENCE_INTERVAL={"LOW_ID": "lower"})
        result = integrate_datasets([a], profile()).dataset
        self.assertEqual(result.df.creatinine__llod.tolist()[:2], ["1", "2"])
        self.assertEqual(cell_state(result.df.creatinine__llod.iloc[2]), "NULL")
        a.meta["COLUMN"]["lower"]["UNIT"] = "mg/L"
        with self.assertRaisesRegex(ValueError, "UNIT_CONFLICT"):
            integrate_datasets([a], profile())

    def test_invalid_bounds_fail(self):
        cases = [{"LLOD": 0}, {"ULOQ": -1}, {"LLOD": 10, "ULOQ": 1},
                 {"REFERENCE_INTERVAL": {"LOW": 2, "HIGH": 1}}, {"REFERENCE_INTERVAL": {"LOW": "<1"}},
                 {"LLOD": 1, "LLOD_ID": "local"}]
        for values in cases:
            a = source()
            a.meta["COLUMN"]["local_result"].update(values)
            with self.subTest(values=values), self.assertRaises(ValueError):
                integrate_datasets([a], profile())

    def test_censoring_binding_preserves_released_values_and_scales_limit(self):
        from test_analysis_contract import fixture
        from tametools.analysis_contract import derive_censored_results
        a = derive_censored_results(fixture())
        a.df["record"] = ["1", "2", "3"]
        a.columns.append(ColumnSpec("record", "record", ["ID", "STR"]))
        a.meta["COLUMN"]["record"] = {"ID": "record"}
        a.meta["SOURCE"] = {"ID": "A", "SITE_ID": "A", "RECORD_ID": "record"}
        a.meta["COLUMN"]["result"]["MEASUREMENT"] = context()
        p = profile("mg/dL")
        p["SOURCES"]["A"]["MAPPINGS"][0]["COLUMN_ID"] = "analysis"
        result = integrate_datasets([a], p).dataset
        self.assertEqual(result.df.creatinine.iloc[0], "<0.015")
        self.assertEqual(result.df.creatinine__llod.iloc[0], "0.015")
        original = restore_integration_sources(result)["A"]
        self.assertEqual(original.df.raw.iloc[0], "0.11")
        a.df.loc[0, "result"] = "0.11"
        with self.assertRaisesRegex(ValueError, "contradicts"):
            integrate_datasets([a], p)

    def test_source_and_mapping_ids_are_not_guessed(self):
        for kind in ["source", "record", "column", "target", "ignore", "unmapped"]:
            a, p = source(), profile()
            if kind == "source": a.meta["SOURCE"]["ID"] = "unmapped"
            if kind == "record": a.meta["SOURCE"]["RECORD_ID"] = "unmapped"
            if kind == "column": p["SOURCES"]["A"]["MAPPINGS"][0]["COLUMN_ID"] = "unmapped"
            if kind == "target": p["SOURCES"]["A"]["MAPPINGS"][0]["TARGET_ID"] = "unmapped"
            if kind == "ignore": p["SOURCES"]["A"]["IGNORE_IDS"] = ["unmapped"]
            if kind == "unmapped": del a.meta["COLUMN"]["local_result"]["ID"]
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                integrate_datasets([a], p)

    def test_duplicate_sources_mappings_and_output_columns_fail(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            integrate_datasets([source(), source()], profile())
        p = profile()
        p["SOURCES"]["A"]["MAPPINGS"] *= 2
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            integrate_datasets([source()], p)
        p = profile()
        p["TARGETS"]["creatinine__raw"] = deepcopy(p["TARGETS"]["creatinine"])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            integrate_datasets([source()], p)

    def test_bad_profiles_fail(self):
        for key, value in [("VERSION", 2), ("VERSION", True), ("ID", ""), ("REFERENCE", ""), ("TARGETS", {}), ("SOURCES", []), ("EXTRA", 1)]:
            p = profile()
            p[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                integrate_datasets([source()], p)

    def test_survey_design_is_not_erased(self):
        a = source()
        a.meta["SURVEY"] = {"WEIGHT_ID": "w"}
        with self.assertRaisesRegex(ValueError, "SURVEY"):
            integrate_datasets([a], profile())

    def test_different_methods_can_be_kept_in_separate_targets(self):
        a, b, p = source(), source("B"), profile()
        b.meta["COLUMN"]["local_result"]["MEASUREMENT"]["METHOD"] = "other-method"
        p["TARGETS"]["creatinine_other"] = deepcopy(p["TARGETS"]["creatinine"])
        p["TARGETS"]["creatinine_other"]["MEASUREMENT"]["METHOD"] = "other-method"
        p["SOURCES"]["B"]["MAPPINGS"][0]["TARGET_ID"] = "creatinine_other"
        result = integrate_datasets([a,b], p).dataset
        self.assertIsNone(result.df.creatinine.iloc[3])
        self.assertIsNone(result.df.creatinine_other.iloc[0])
        self.assertEqual(result.df.creatinine_other.iloc[3], "12")

    def test_legacy_merge_keeps_unit_conflict_guard(self):
        with self.assertRaisesRegex(ValueError, "UNIT_CONFLICT"):
            merge_datasets([source(), source("B", "mg/L")])

    def test_legacy_merge_cannot_discard_integrated_provenance(self):
        result = integrate_datasets([source()], profile()).dataset
        for inputs in [[result], [result, result]]:
            with self.assertRaisesRegex(ValueError, "restore original"):
                merge_datasets(inputs)

    def test_malformed_integration_controls_report_validation_issues(self):
        for kind in ["result", "catalogue", "source", "site", "count", "row_count", "key_fields", "columns", "meta"]:
            result = integrate_datasets([source()], profile()).dataset
            if kind == "result": result.meta["INTEGRATION_RESULT"] = []
            if kind == "catalogue": result.meta["SOURCE_CATALOG"] = []
            if kind == "source": result.meta["SOURCE_CATALOG"]["A"] = []
            if kind == "site": result.meta["SOURCE_CATALOG"]["A"]["SITE_ID"] = "changed"
            if kind == "count": result.meta["INTEGRATION_RESULT"]["EXPECTED_ROWS"] = True
            if kind == "row_count": result.meta["SOURCE_CATALOG"]["A"]["ROW_COUNT"] = True
            if kind == "key_fields": result.meta["INTEGRATION_RESULT"]["KEY_FIELDS"] = ["record"]
            if kind == "columns": result.meta["SOURCE_CATALOG"]["A"]["COLUMNS"] = [[]]
            if kind == "meta": result.meta["SOURCE_CATALOG"]["A"]["META"] = []
            with self.subTest(kind=kind):
                self.assertTrue(validate_dataset(result).issues)

    def test_header_changes_and_reordering_keep_id_binding(self):
        expected = integrate_datasets([source()], profile()).dataset
        actual = integrate_datasets([rename(source())], profile()).dataset
        self.assertEqual(actual.df.creatinine.tolist(), expected.df.creatinine.tolist())
        renamed = rename(expected)
        self.assertFalse(validate_dataset(renamed).issues)
        self.assertEqual(cells(restore_integration_sources(renamed)["A"]), cells(source()))
        shuffled = expected.with_df(expected.df.iloc[::-1].reset_index(drop=True))
        self.assertFalse(validate_dataset(shuffled).issues)

    def test_tampered_result_bound_unit_profile_and_provenance_are_detected(self):
        for kind in ["result", "bound", "unit", "profile", "raw", "site", "record", "duplicate", "filtered", "context"]:
            result = integrate_datasets([source()], profile()).dataset
            if kind == "result": result.df.loc[0, "creatinine"] = "99"
            if kind == "bound": result.df.loc[0, "creatinine__llod"] = "99"
            if kind == "unit": result.meta["COLUMN"]["creatinine"]["UNIT"] = "g/L"
            if kind == "profile": result.meta["INTEGRATION_RESULT"]["PROFILE"]["ID"] = "changed"
            if kind == "raw": result.df.loc[0, "source_row_json"] = '["changed"]'
            if kind == "site": result.df.loc[0, "site_id"] = "changed"
            if kind == "record": result.df.loc[0, "source_record_id"] = "changed"
            if kind == "duplicate": result.df.loc[1, "source_row"] = "1"
            if kind == "filtered": result = result.with_df(result.df.iloc[:2].copy())
            if kind == "context": result.meta["COLUMN"]["creatinine"]["MEASUREMENT"]["METHOD"] = "changed"
            with self.subTest(kind=kind):
                self.assertTrue(validate_dataset(result).issues)

    def test_no_nested_reintegration(self):
        result = integrate_datasets([source()], profile()).dataset
        with self.assertRaisesRegex(ValueError, "Already integrated"):
            integrate_datasets([result], profile())

    def test_tame_and_xlsx_round_trips(self):
        data = source(values=["1.2", "<0.1", NULL, None, "", "  "])
        result = integrate_datasets([data], profile()).dataset
        with tempfile.TemporaryDirectory() as tmp:
            for extension, write, read in [("tame", write_tame, read_tame), ("xlsx", write_xlsx, read_xlsx)]:
                path = Path(tmp) / ("integrated." + extension)
                write(path, result)
                loaded = read(path)
                self.assertEqual(cells(loaded), cells(result))
                self.assertEqual(loaded.meta, result.meta)
                self.assertFalse(validate_dataset(loaded).issues)
                self.assertEqual(cells(restore_integration_sources(loaded)["A"]), cells(data))

    def test_large_excel_payload_is_rejected_before_writing(self):
        data = source()
        data.df.loc[0, "note"] = "a" * 40000
        result = integrate_datasets([data], profile()).dataset
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "oversized.xlsx"
            with self.assertRaisesRegex(ValueError, "cell text limit"):
                write_xlsx(target, result)
            self.assertFalse(target.exists())
            write_tame(target.with_suffix(".tame"), result)
            self.assertEqual(cells(restore_integration_sources(read_tame(target.with_suffix(".tame")))["A"]), cells(data))

    def test_equals_comparator_and_source_ids_are_not_excel_formulas(self):
        import openpyxl
        data = source(values=["=1", ">=2", "<=3"])
        data.df.loc[0, "accession"] = "=1+2"
        result = integrate_datasets([data], profile()).dataset
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out.xlsx"
            write_xlsx(target, result)
            workbook = openpyxl.load_workbook(target, data_only=False)
            self.assertFalse(any(cell.data_type == "f" for row in workbook["DATA"] for cell in row))
            workbook.close()
            loaded = read_xlsx(target)
            self.assertEqual(cells(loaded), cells(result))
            self.assertFalse(validate_dataset(loaded).issues)

    def test_source_payload_section_markers_are_escaped(self):
        data = source()
        data.df.loc[0, "note"] = "</DATA> <META> text </META>"
        result = integrate_datasets([data], profile()).dataset
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out.tame"
            write_tame(target, result)
            self.assertEqual(cells(restore_integration_sources(read_tame(target))["A"]), cells(data))
            result.meta["SOURCE_CATALOG"]["A"]["META"]["note"] = "</META>"
            with self.assertRaisesRegex(ValueError, "reserved TAME section"):
                write_tame(target, result)

    def test_single_source_embedded_workflow(self):
        data = source()
        data.meta["INTEGRATION_PROFILE"] = profile()
        data.meta["WORKS"] = {"DEFAULT": ["INTEGRATE", "VALIDATE"]}
        result = execute_work(data)
        self.assertEqual(result.final_dataset.df.creatinine.iloc[0], "12")
        self.assertFalse(result.outputs[-1].issues)

    def test_empty_dataset(self):
        result = integrate_datasets([source(values=[])], profile()).dataset
        self.assertEqual(len(result.df), 0)
        self.assertFalse(validate_dataset(result).issues)

    def test_invalid_numeric_strings_are_not_silently_coerced(self):
        for value in ["NaN", "inf", True, "1,2", "1 mg/dL", "1e10000", "1-2", "negative"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                integrate_datasets([source(values=[value])], profile())

    def test_num_tag_does_not_accept_comparator(self):
        data = source()
        data.columns[1] = ColumnSpec("local_result", "local_result", ["NUM", "RESULT"])
        with self.assertRaisesRegex(ValueError, "comparator"):
            integrate_datasets([data], profile())

    def test_cli_preview_and_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_tame(root / "a.tame", source())
            (root / "profile.toml").write_text(dumps(profile()))
            env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(Path(__file__).resolve().parents[1] / "src"), os.environ.get("PYTHONPATH", "")]))
            command = [sys.executable, "-m", "tametools", "integrate", str(root / "a.tame"), "--profile", str(root / "profile.toml"), "--output", str(root / "out.tame")]
            preview = subprocess.run(command + ["--preview"], env=env, capture_output=True, text=True)
            self.assertEqual(preview.returncode, 0, preview.stderr + preview.stdout)
            self.assertFalse((root / "out.tame").exists())
            saved = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(saved.returncode, 0, saved.stderr + saved.stdout)
            self.assertFalse(validate_dataset(read_tame(root / "out.tame")).issues)
            for output in ["out.data.tame", "out.meta.tame", "out.csv"]:
                rejected = subprocess.run(command[:-1] + [str(root / output)], env=env, capture_output=True, text=True)
                self.assertNotEqual(rejected.returncode, 0)
                self.assertFalse((root / output).exists())


if __name__ == "__main__":
    unittest.main()

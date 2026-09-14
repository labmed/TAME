from __future__ import annotations

from pathlib import Path
import tempfile
import textwrap
import unittest

from tametools.analysis import validate_dataset
from tametools.io import read_tame
from tametools.plugin_base import list_plugins, run_plugin
from tametools.plugin_base.roles import RESULT_ROLE, result_binding, role_contract_payload


WIDE_MULTI_RESULT = textwrap.dedent(
    """
    <META>
    [SETTINGS]
    VALIDATE_ERROR = "REPORT"
    </META>
    <DATA>
    [[ID(patient)::STR]]pid\t[[ID(sample)::STR]]sample\t[[GROUP::CATEGORY]]group\t[[SEX]]sex\t[[AGE]]age\t[[RESULT_TIME::DATETIME]]when\t[[INSTRUMENT::CATEGORY]]inst\t[[REF_LOW::NUM]]ref_low\t[[REF_HIGH::NUM]]ref_high\t[[RESULT::NUM]]AST\t[[RESULT::NUM]]ALT
    P1\tS1\tA\tF\t30\t2026-01-01 08:00\tI1\t0\t100\t10\t20
    P2\tS2\tA\tM\t40\t2026-01-02 08:00\tI1\t0\t100\t12\t22
    P3\tS3\tA\tF\t50\t2026-01-03 08:00\tI2\t0\t100\t14\t24
    P4\tS4\tA\tM\t60\t2026-01-04 08:00\tI2\t0\t100\t16\t26
    P5\tS5\tB\tF\t30\t2026-01-05 08:00\tI1\t0\t100\t50\t70
    P6\tS6\tB\tM\t40\t2026-01-06 08:00\tI1\t0\t100\t52\t72
    P7\tS7\tB\tF\t50\t2026-02-01 08:00\tI2\t0\t100\t54\t74
    P8\tS8\tB\tM\t60\t2026-02-02 08:00\tI2\t0\t100\t56\t76
    </DATA>
    """
).strip()


MULTI_ROC = textwrap.dedent(
    """
    <DATA>
    [[RESULT::NUM]]score_a\t[[RESULT::NUM]]score_b\t[[LABEL::CATEGORY]]label
    0.1\t0.2\t0
    0.2\t0.3\t0
    0.8\t0.7\t1
    0.9\t0.95\t1
    </DATA>
    """
).strip()


MULTI_AUTOVERIFICATION = textwrap.dedent(
    """
    <META>
    [SETTINGS]
    VALIDATE_ERROR = "REPORT"

    [COLUMN.AST.PIVOT_CONTEXT]
    UNIT = "U/L"
    REF_LOW = "0"
    REF_HIGH = "40"

    [COLUMN.ALT.PIVOT_CONTEXT]
    UNIT = "U/L"
    REF_LOW = "10"
    REF_HIGH = "80"
    </META>
    <DATA>
    [[ID(patient)::STR]]pid\t[[ID(sample)::STR]]sample\t[[RESULT_TIME::DATETIME]]when\t[[INSTRUMENT::CATEGORY]]inst\t[[RESULT::NUM]]AST\t[[RESULT::NUM]]ALT
    P1\tS1\t2026-01-01 08:00\tA\t10\t20
    P1\tS2\t2026-01-02 08:00\tA\t70\t25
    P2\tS3\t2026-01-01 08:00\tB\t20\t5
    </DATA>
    """
).strip()


def _read_text(text: str):
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "multi_result.tame"
        path.write_text(text, encoding="utf-8")
        return read_tame(path)


class MultiResultRoleContractTest(unittest.TestCase):
    def test_result_role_contract_is_registered_for_result_plugins(self) -> None:
        plugins = {plugin.name: plugin for plugin in list_plugins()}
        for name in [
            "CHEMISTRY_ANALYSIS",
            "REFERENCE_INTERVAL",
            "ABNORMAL_FLAG",
            "AUTOVERIFICATION",
            "GROUP_TEST",
            "QC_ANALYSIS",
            "ROC_ANALYSIS",
            "RESULT_TREND",
            "METHOD_COMPARISON",
        ]:
            self.assertIn(name, plugins)
            payload = role_contract_payload(plugins[name].roles)
            result_roles = [role for role in payload if role["name"] == "result"]
            self.assertEqual(len(result_roles), 1, name)
            self.assertEqual(result_roles[0]["cardinality"], "one_or_more")
            self.assertEqual(result_roles[0]["iteration"], "per_result")

    def test_result_binding_prefers_result_columns_over_other_numeric_columns(self) -> None:
        dataset = _read_text(WIDE_MULTI_RESULT)
        bound = result_binding(dataset)
        self.assertEqual([column.name for column in bound.columns], ["AST", "ALT"])
        self.assertTrue(bound.is_multi)
        self.assertEqual(RESULT_ROLE.iteration, "per_result")

    def test_chemistry_summary_iterates_each_result_column(self) -> None:
        dataset = _read_text(WIDE_MULTI_RESULT)
        output = run_plugin(dataset, "CHEMISTRY_ANALYSIS", dataset.meta, "CHEMISTRY_ANALYSIS", {"MODE": "RESULT_SUMMARY"})
        self.assertEqual(set(output.table["원본결과컬럼"]), {"AST", "ALT"})
        self.assertEqual(set(output.table["검사항목명"]), {"AST", "ALT"})
        self.assertEqual(validate_dataset(output.dataset).issues, [])

    def test_group_qc_and_trend_iterate_each_result_column(self) -> None:
        dataset = _read_text(WIDE_MULTI_RESULT)

        group = run_plugin(dataset, "GROUP_TEST", dataset.meta, "GROUP_TEST", {"GROUP": "tag:GROUP"})
        self.assertEqual(set(group.table["source_result_column"]), {"AST", "ALT"})
        self.assertEqual(set(group.table["test"]), {"AST", "ALT"})
        self.assertEqual(validate_dataset(group.dataset).issues, [])

        qc = run_plugin(dataset, "QC_ANALYSIS", dataset.meta, "QC_ANALYSIS", {"MODE": "PRECISION"})
        self.assertEqual(set(qc.table["source_result_column"]), {"AST", "ALT"})
        self.assertEqual(set(qc.table["level"]), {"AST", "ALT"})
        self.assertEqual(validate_dataset(qc.dataset).issues, [])

        trend = run_plugin(dataset, "RESULT_TREND", dataset.meta, "RESULT_TREND", {"PERIOD": "M"})
        self.assertEqual(set(trend.table["source_result_column"]), {"AST", "ALT"})
        self.assertEqual(set(trend.table["test"]), {"AST", "ALT"})
        self.assertEqual(validate_dataset(trend.dataset).issues, [])

    def test_roc_iterates_multiple_score_results(self) -> None:
        dataset = _read_text(MULTI_ROC)
        summary = run_plugin(dataset, "ROC_ANALYSIS", dataset.meta, "ROC_ANALYSIS", {"POSITIVE": "1"})
        self.assertEqual(set(summary.table["score"]), {"score_a", "score_b"})
        self.assertEqual(len(summary.table), 2)
        self.assertEqual(validate_dataset(summary.dataset).issues, [])

        curve = run_plugin(dataset, "ROC_ANALYSIS", dataset.meta, "ROC_ANALYSIS", {"MODE": "CURVE", "POSITIVE": "1"})
        self.assertEqual(set(curve.table["score"]), {"score_a", "score_b"})
        self.assertGreater(len(curve.table), 2)
        self.assertEqual(validate_dataset(curve.dataset).issues, [])

    def test_autoverification_iterates_wide_results_with_context_references(self) -> None:
        dataset = _read_text(MULTI_AUTOVERIFICATION)
        output = run_plugin(
            dataset,
            "AUTOVERIFICATION",
            dataset.meta,
            "AUTOVERIFICATION",
            {"CRITICAL_HIGH_BY_TEST": {"AST": 60}},
        )

        self.assertEqual(len(output.table), 6)
        self.assertEqual(set(output.table["source_result_column"]), {"AST", "ALT"})
        self.assertEqual(set(output.table["test"]), {"AST", "ALT"})

        ast_hold = output.table.loc[
            (output.table["source_result_column"] == "AST") & (output.table["sample_id"] == "S2")
        ].iloc[0]
        self.assertEqual(ast_hold["decision"], "HOLD")
        self.assertIn("CRITICAL_HIGH", ast_hold["rule_id"])
        self.assertIn("REF_HIGH", ast_hold["rule_id"])
        self.assertIn("DELTA_PERCENT", ast_hold["rule_id"])

        alt_low = output.table.loc[
            (output.table["source_result_column"] == "ALT") & (output.table["sample_id"] == "S3")
        ].iloc[0]
        self.assertEqual(alt_low["decision"], "REVIEW")
        self.assertIn("REF_LOW", alt_low["rule_id"])
        self.assertEqual(validate_dataset(output.dataset).issues, [])

        exceptions = run_plugin(
            dataset,
            "AUTOVERIFICATION",
            dataset.meta,
            "AUTOVERIFICATION",
            {"CRITICAL_HIGH_BY_TEST": {"AST": 60}, "OUTPUT": "EXCEPTIONS"},
        )
        self.assertEqual(set(exceptions.table["decision"]), {"HOLD", "REVIEW"})
        self.assertEqual(len(exceptions.table), 2)

    def test_reference_interval_keeps_existing_multi_result_behavior_under_contract(self) -> None:
        dataset = _read_text(WIDE_MULTI_RESULT)
        output = run_plugin(dataset, "REFERENCE_INTERVAL", dataset.meta, "REFERENCE_INTERVAL", {})
        self.assertEqual(set(output.table["source_result_column"]), {"AST", "ALT"})
        self.assertEqual(set(output.table["test_name"]), {"AST", "ALT"})
        self.assertEqual(validate_dataset(output.dataset).issues, [])

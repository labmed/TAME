from pathlib import Path
import tempfile
import unittest

import pandas as pd

from tametools.actions import ActionError, execute_action
from tametools.cellstate import NULL
from tametools.io import write_tame
from tametools.models import ColumnSpec, TameDataset


class JoinContractTests(unittest.TestCase):
    def join(self, left, right, **options):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "right.tame"
            rdf = pd.DataFrame(right)
            write_tame(path, TameDataset(rdf, [ColumnSpec(c, c, ["STR", "NULLABLE"]) for c in rdf]))
            frame = pd.DataFrame(left)
            config = dict(TYPE="JOIN", RIGHT=str(path), ON=["key"], HOW="left")
            config.update(options)
            ds = TameDataset(frame, [ColumnSpec(c, c, ["STR", "NULLABLE"]) for c in frame],
                             meta={"ACTIONS": {"J": config}})
            return execute_action(ds, "J")

    def test_one_to_one_preserves_left_and_audits_input_rows(self):
        out = self.join({"key": ["c", "a", "b"]}, {"key": ["b", "a"], "value": ["2", "1"]},
                        VALIDATE="one_to_one", MISSING_KEYS="ERROR", REQUIRE_RIGHT_MATCH=True)
        self.assertEqual(out.dataset.df.key.tolist(), ["c", "a", "b"])
        self.assertEqual(out.table.iloc[0].left_unmatched_rows, 1)
        self.assertEqual(out.table.iloc[0].right_unmatched_rows, 0)
        self.assertEqual(out.table.iloc[0].rows_after, 3)

    def test_duplicates_rejected_on_either_side(self):
        for left, right in [(["a", "a"], ["a"]), (["a"], ["a", "a"])]:
            with self.subTest(left=left, right=right), self.assertRaises(ActionError):
                self.join({"key": left}, {"key": right}, VALIDATE="one_to_one")

    def test_missing_states_rejected_on_either_side(self):
        for value in [None, NULL, "", " \t", float("nan")]:
            for side in ["left", "right"]:
                left = {"key": [value if side == "left" else "a"]}
                right = {"key": [value if side == "right" else "a"]}
                with self.subTest(value=value, side=side), self.assertRaisesRegex(ActionError, "missing"):
                    self.join(left, right, MISSING_KEYS="ERROR")

    def test_orphan_right_rejected(self):
        with self.assertRaisesRegex(ActionError, "unmatched right"):
            self.join({"key": ["a"]}, {"key": ["a", "b"]}, REQUIRE_RIGHT_MATCH=True)

    def test_duplicate_expansion_requires_explicit_contract_to_reject(self):
        out = self.join({"key": ["a", "a", "b"]}, {"key": ["a", "a", "c"]})
        self.assertEqual(len(out.dataset.df), 5)
        self.assertEqual(out.table.iloc[0].right_unmatched_rows, 1)
        self.assertEqual(out.table.iloc[0].left_unmatched_rows, 1)

    def test_many_to_one(self):
        self.assertEqual(len(self.join({"key": ["a", "a"]}, {"key": ["a"]},
                                      VALIDATE="many_to_one").dataset.df), 2)

    def test_composite_differently_named_keys(self):
        out = self.join({"key": ["a", "a"], "cycle": ["1", "2"]},
                        {"person": ["a"], "wave": ["2"], "value": ["ok"]},
                        ON=[], LEFT_ON=["key", "cycle"], RIGHT_ON=["person", "wave"],
                        VALIDATE="one_to_one", MISSING_KEYS="ERROR", REQUIRE_RIGHT_MATCH=True)
        self.assertEqual(out.table.iloc[0].left_unmatched_rows, 1)
        self.assertEqual(out.dataset.df.iloc[1]["value"], "ok")

    def test_invalid_options(self):
        for options in [dict(VALIDATE="1:1"), dict(MISSING_KEYS="IGNORE"), dict(REQUIRE_RIGHT_MATCH="false")]:
            with self.subTest(options=options), self.assertRaises(ActionError):
                self.join({"key": ["a"]}, {"key": ["a"]}, **options)


if __name__ == "__main__":
    unittest.main()

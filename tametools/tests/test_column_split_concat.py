"""COLUMN.SPLIT / COLUMN.CONCAT 회귀 테스트.

- SPLIT 정규식 구분자(REGEX) 옵션과 잘못된 패턴의 깔끔한 오류
- COLUMN_TAGS 미등록 타깃의 전역 TAGS 폴백
- 원시 복합 컬럼을 카탈로그 태그(클래스)로 선택 + DROP_SOURCE 패턴
- p02 예제가 컬럼명 없이(완전 태그 기반) 동작하는지 고정
"""
from __future__ import annotations

from pathlib import Path
import tempfile
import textwrap
import unittest

from tametools.actions import ActionError, execute_action
from tametools.io import read_tame
from tametools.models import TameDataset


def _read(text: str) -> TameDataset:
    path = Path(tempfile.mkdtemp()) / "x.tame"
    path.write_text(textwrap.dedent(text).strip() + "\n", encoding="utf-8")
    return read_tame(path)


def _run(dataset: TameDataset, config: dict):
    meta = dict(dataset.meta)
    meta["ACTIONS"] = {"X": config}
    return execute_action(dataset.replace(meta=meta), "X")


COMPOUND = (
    "<DATA>\n[[SEX::AGE::STR]]c\t[[RESULT::NUM]]v\nM-23\t1\nF:45\t2\nmale/2mo\t3\n</DATA>"
)


class ColumnSplitConcatTest(unittest.TestCase):
    def test_split_regex_variable_separator(self) -> None:
        out = _run(_read(COMPOUND), {
            "TYPE": "COLUMN.SPLIT", "COLUMN": "tag:SEX", "INTO": ["성별", "나이"],
            "SEP": "[-:/]", "REGEX": True, "DROP_SOURCE": True,
            "COLUMN_TAGS": {"성별": ["SEX", "CATEGORY"], "나이": ["AGE"]},
        })
        names = {c.name for c in out.dataset.columns}
        self.assertEqual(names, {"성별", "나이", "v"})           # 복합 컬럼 제거됨
        self.assertEqual(out.dataset.first_column_with_tag("AGE").name, "나이")
        self.assertEqual(list(out.dataset.df["나이"]), ["23", "45", "2mo"])

    def test_split_invalid_regex_raises_clean_error(self) -> None:
        with self.assertRaises(ActionError) as ctx:
            _run(_read(COMPOUND), {
                "TYPE": "COLUMN.SPLIT", "COLUMN": "tag:SEX", "INTO": ["a", "b"],
                "SEP": "[", "REGEX": True,
            })
        self.assertIn("REGEX", str(ctx.exception))

    def test_literal_separator_is_not_regex_by_default(self) -> None:
        # SEP="." 는 기본(literal)에서 마침표로만 분리되어야 한다(정규식 '.'이 아님).
        ds = _read("<DATA>\n[[STR]]raw\nA.B\n</DATA>")
        out = _run(ds, {"TYPE": "COLUMN.SPLIT", "COLUMN": "tag:STR", "INTO": ["x", "y"], "SEP": "."})
        self.assertEqual(list(out.dataset.df["x"]), ["A"])
        self.assertEqual(list(out.dataset.df["y"]), ["B"])

    def test_column_tags_fallback_to_global_tags(self) -> None:
        # COLUMN_TAGS에 없는 타깃은 전역 TAGS로 폴백되어야 한다.
        out = _run(_read("<DATA>\n[[STR]]raw\nM/23/x\n</DATA>"), {
            "TYPE": "COLUMN.SPLIT", "COLUMN": "tag:STR", "INTO": ["a", "b", "c"],
            "SEP": "/", "TAGS": ["NOTE"],
            "COLUMN_TAGS": {"a": ["SEX", "CATEGORY"], "b": ["AGE"]},
        })
        tags = {c.name: c.tags for c in out.dataset.columns}
        self.assertEqual(tags["a"], ("SEX", "CATEGORY"))
        self.assertEqual(tags["b"], ("AGE",))
        self.assertEqual(tags["c"], ("NOTE",))                  # 폴백 확인

    def test_concat_sources_by_tag(self) -> None:
        ds = _read(
            "<DATA>\n[[ID(patient)::STR]]p\t[[ID(sample)::STR]]s\t[[RESULT::NUM]]v\nP1\tS1\t1\n</DATA>"
        )
        out = _run(ds, {
            "TYPE": "COLUMN.CONCAT", "COLUMNS": ["tag:ID(patient)", "tag:ID(sample)"],
            "OUTPUT": "key", "SEP": "::", "TAGS": ["ID", "STR"],
        })
        self.assertEqual(out.dataset.df["key"].iloc[0], "P1::S1")
        self.assertEqual(out.dataset.first_column_with_tag("ID").name in {"p", "s", "key"}, True)


class P02ExampleTagBasedTest(unittest.TestCase):
    """수정된 p02 예제가 컬럼명 없이 완전 태그 기반으로 동작하는지 고정."""

    def path(self) -> Path:
        return Path(__file__).resolve().parents[2] / "tutorial/02_pivot_longer_wider/p02_split_sex_age.tame"

    def test_p02_uses_tag_selector_not_column_name(self) -> None:
        ds = read_tame(self.path())
        cfg = ds.meta["ACTIONS"]["SPLIT_SEX_AGE"]
        self.assertTrue(str(cfg["COLUMN"]).startswith("tag:"))   # 컬럼명 아님
        self.assertTrue(cfg.get("DROP_SOURCE"))

    def test_p02_split_drops_compound_and_tags_outputs(self) -> None:
        ds = read_tame(self.path())
        out = execute_action(ds, "SPLIT_SEX_AGE")
        names = {c.name for c in out.dataset.columns}
        self.assertNotIn("sex_age", names)                       # 원시 복합 제거
        self.assertEqual(out.dataset.first_column_with_tag("SEX").name, "sex_split")
        self.assertEqual(out.dataset.first_column_with_tag("AGE").name, "age_split")


class PivotLongerMultiTagTest(unittest.TestCase):
    """PIVOT_LONGER의 COLS가 하나의 태그를 다중 동일태그 컬럼으로 확장하는지."""

    def test_cols_tag_expands_to_all_matching_columns(self) -> None:
        ds = _read(
            "<DATA>\n[[ID::STR]]pid\t[[RESULT::<NUM>]]AST\t[[RESULT::<NUM>]]ALT\t[[RESULT::<NUM>]]GGT\n"
            "P1\t10\t20\t30\nP2\t11\t21\t31\n</DATA>"
        )
        out = _run(ds, {
            "TYPE": "PIVOT_LONGER", "ID_COLS": ["tag:ID"], "COLS": ["tag:RESULT"],
            "NAMES_TO": "item", "VALUES_TO": "value",
            "NAMES_TAGS": ["ITEM", "CATEGORY"], "VALUES_TAGS": ["RESULT", "<NUM>"],
        })
        self.assertEqual(len(out.dataset.df), 6)                 # 3 result cols × 2 rows
        self.assertEqual(set(out.dataset.df["item"]), {"AST", "ALT", "GGT"})
        self.assertEqual(out.dataset.first_column_with_tag("RESULT").name, "value")
        self.assertNotIn("AST", {c.name for c in out.dataset.columns})  # wide 컬럼은 사라짐

    def test_tutorial02_liver_wide_pivot_longer_is_tag_based(self) -> None:
        from tametools.actions import execute_action_pipeline
        path = Path(__file__).resolve().parents[2] / "tutorial/02_pivot_longer_wider/liver_wide.tame"
        ds = read_tame(path)
        cfg = ds.meta["ACTIONS"]["PIVOT_RESULTS_LONGER"]
        self.assertEqual(cfg["COLS"], ["tag:RESULT"])            # 컬럼명 아님
        self.assertTrue(all(str(c).startswith("tag:") for c in cfg["ID_COLS"]))
        result = execute_action_pipeline(ds, "TO_LONG")
        self.assertEqual(len(result.final_dataset.df), 1000)     # 200명 × 5항목


if __name__ == "__main__":
    unittest.main()

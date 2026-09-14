"""사용자 정의 범주형 어휘(Category vocabulary) 정규화·검증.

기관마다 다르게 표기하는 범주형 결과(Positive/양성/+ , Negative/음성/- 등)를 메타데이터에서 선언한
표준 어휘로 통일한다. 부등호 수치(<NUM>)·성별·연령처럼 임상검사 데이터의 재현성을 위한 정규화 수단을
범주형으로 일반화한 것이다.

META 선언 예::

    [CATEGORIES.RESULT_QUAL]
    VALUES = ["POSITIVE", "NEGATIVE", "EQUIVOCAL"]
    MAP = { "양성"="POSITIVE", "+"="POSITIVE", "pos"="POSITIVE",
            "음성"="NEGATIVE", "-"="NEGATIVE", "+/-"="EQUIVOCAL" }
    STRICT = true

열은 ``[[CATEGORY::RESULT_QUAL]]판정`` 처럼 어휘 이름을 태그로 가리킨다(CATEGORY 태그 + 어휘명 토큰).
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from .cellstate import STATE_VALUE, cell_state
from .config import ci_get
from .models import TameDataset, ValidationIssue

__all__ = ["category_vocabularies", "normalize_categories", "category_validation_issues"]


def _row_number(row_idx: Any) -> int:
    try:
        return int(row_idx) + 2  # 1-based + 헤더 행
    except (TypeError, ValueError):
        return -1


def category_vocabularies(meta: dict) -> dict[str, dict]:
    """META[CATEGORIES.*] 를 {어휘: {values, synonyms(소문자→표준), strict}} 로 파싱한다."""
    raw = ci_get(meta, "CATEGORIES", {}) or {}
    result: dict[str, dict] = {}
    if not isinstance(raw, dict):
        return result
    for vocab, spec in raw.items():
        if not isinstance(spec, dict):
            continue
        values = [str(v) for v in (ci_get(spec, "VALUES", []) or [])]
        mapping = ci_get(spec, "MAP", {}) or {}
        strict = bool(ci_get(spec, "STRICT", False))
        synonyms: dict[str, str] = {}
        for canon in values:  # 표준값은 자기 자신으로 매핑
            synonyms[canon.strip().lower()] = canon
        if isinstance(mapping, dict):
            for source, target in mapping.items():
                synonyms[str(source).strip().lower()] = str(target)
        source_column = ci_get(spec, "SOURCE_COLUMN", None)
        source_maps = ci_get(spec, "SOURCE_MAPS", {}) or {}
        if source_maps and (not isinstance(source_column, str) or not source_column.strip()):
            raise ValueError(f"CATEGORIES.{vocab}: SOURCE_MAPS requires SOURCE_COLUMN")
        if not isinstance(source_maps, dict) or any(not isinstance(m, dict) for m in source_maps.values()):
            raise ValueError(f"CATEGORIES.{vocab}.SOURCE_MAPS must contain mapping tables")
        scoped = {str(source): {str(k).strip().lower(): str(v) for k, v in mapping.items()}
                  for source, mapping in source_maps.items()}
        if scoped and any(v not in values for m in scoped.values() for v in m.values()):
            raise ValueError(f"CATEGORIES.{vocab}: source mapping targets must be declared VALUES")
        result[str(vocab)] = {"values": values, "synonyms": synonyms, "strict": strict,
                              "source_column": source_column, "source_maps": scoped}
    return result


def _row_synonyms(dataset, spec, row_idx, position=None):
    """A scoped code is interpreted only in its own declared source."""
    if not spec.get("source_maps"):
        return spec["synonyms"]
    source_column = spec["source_column"]
    if source_column not in dataset.df.columns:
        raise ValueError(f"Category source column is absent: {source_column}")
    source = dataset.df[source_column].iloc[position] if position is not None else dataset.df.at[row_idx, source_column]
    canonical = {v.strip().lower(): v for v in spec["values"]}
    if cell_state(source) == STATE_VALUE:
        canonical.update(spec["source_maps"].get(str(source), {}))
    return canonical


def _vocab_for_column(dataset, column, vocabs: dict[str, dict]) -> str | None:
    if not dataset.column_has_tag(column, "CATEGORY"):
        return None
    for token in column.tags:
        if token in vocabs:
            return token
    return None


def normalize_categories(dataset: TameDataset) -> tuple[TameDataset, pd.DataFrame]:
    """CATEGORY 열을 선언된 어휘의 표준값으로 정규화한다(대소문자·앞뒤공백 무시)."""
    vocabs = category_vocabularies(dataset.meta)
    columns = ["column", "vocab", "changed_cells", "unmapped_cells"]
    if not vocabs:
        return dataset, pd.DataFrame(columns=columns)

    frame = dataset.df.copy()
    rows: list[dict[str, Any]] = []
    for column in dataset.columns:
        vocab = _vocab_for_column(dataset, column, vocabs)
        if not vocab:
            continue
        changed = 0
        unmapped = 0

        def _normalize(row_idx: Any, value: Any, position: int) -> Any:
            nonlocal changed, unmapped
            if cell_state(value) != STATE_VALUE:
                return value
            canonical = _row_synonyms(dataset, vocabs[vocab], row_idx, position).get(str(value).strip().lower())
            if canonical is None:
                unmapped += 1
                return value
            if canonical != str(value):
                changed += 1
            return canonical

        frame[column.name] = pd.Series([_normalize(i, value, pos) for pos, (i, value) in enumerate(frame[column.name].items())], index=frame.index, dtype=object)
        rows.append({"column": column.name, "vocab": vocab, "changed_cells": changed, "unmapped_cells": unmapped})

    return dataset.with_df(frame), pd.DataFrame(rows, columns=columns)


def category_validation_issues(dataset: TameDataset) -> list[ValidationIssue]:
    """STRICT 어휘에서 표준값으로 해석되지 않는 셀을 검증 이슈로 보고한다."""
    vocabs = category_vocabularies(dataset.meta)
    issues: list[ValidationIssue] = []
    for column in dataset.columns:
        vocab = _vocab_for_column(dataset, column, vocabs)
        if not vocab or not vocabs[vocab]["strict"]:
            continue
        spec = vocabs[vocab]
        allowed = {value.lower() for value in spec["values"]}
        series = dataset.df[column.name]
        for pos, (row_idx, value) in enumerate(series.items()):
            if cell_state(value) != STATE_VALUE:
                continue
            canonical = _row_synonyms(dataset, spec, row_idx, pos).get(str(value).strip().lower())
            if canonical is not None and canonical.lower() in allowed:
                continue
            issues.append(
                ValidationIssue(
                    row_number=_row_number(row_idx),
                    column=column.name,
                    tag=f"CATEGORY:{vocab}",
                    value=value,
                    message=f"Value is not in category vocabulary '{vocab}' (allowed: {', '.join(spec['values'])}).",
                    severity="error",
                )
            )
    return issues

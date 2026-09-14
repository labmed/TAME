"""시간형(DATE/DATETIME/TIME) 표준화 — ISO 8601 로 정규화.

xlsx(직렬값)·tsv(다양한 문자열) 입력이 혼재해도 일관되게 파싱하여 ISO 8601 로 통일한다.
태그가 DATETIME 이면 ``YYYY-MM-DDTHH:MM:SS``, DATE 이면 ``YYYY-MM-DD``, TIME 이면 ``HH:MM:SS`` 로 출력한다.

META 로 입력 포맷을 우선 지정할 수 있다(없으면 추론)::

    [DATETIME]
    FORMATS = ["%Y/%m/%d", "%d-%b-%Y", "%Y-%m-%d %H:%M"]
"""
from __future__ import annotations

import re
from typing import Any

import pandas as pd

from .cellstate import STATE_VALUE, cell_state
from .config import ci_get
from .models import TameDataset, ValidationIssue
from .review_profiles import parse_temporal_value

__all__ = ["normalize_datetimes", "datetime_validation_issues"]

_SERIAL_RE = re.compile(r"^\d+(?:\.\d+)?$")
_EXCEL_EPOCH = pd.Timestamp("1899-12-30")  # Excel 1900 날짜 시스템(윤년 버그 포함)의 기준


def _output_format(column) -> str | None:
    if column.has_tag("DATETIME"):
        return "%Y-%m-%dT%H:%M:%S"
    if column.has_tag("DATE"):
        return "%Y-%m-%d"
    if column.has_tag("TIME"):
        return "%H:%M:%S"
    return None


def _declared_formats(meta: dict) -> list[str]:
    section = ci_get(meta, "DATETIME", {}) or {}
    formats = ci_get(section, "FORMATS", []) if isinstance(section, dict) else []
    return [str(f) for f in (formats or [])]


def _parse_one(value: Any, formats: list[str], *, kind: str | None = None) -> pd.Timestamp | None:
    text = str(value).strip()
    if not text:
        return None
    for fmt in formats:  # 선언된 포맷 우선(결정론적)
        try:
            parsed = pd.to_datetime(text, format=fmt)
            if kind == "TIME":
                return parse_temporal_value(parsed, ("TIME",))
            return parsed
        except (ValueError, TypeError):
            continue
    if kind == "TIME":
        return parse_temporal_value(value, ("TIME",))
    if _SERIAL_RE.match(text):  # Excel 직렬값(대략 1954~2089 범위)
        serial = float(text)
        if 20000 <= serial <= 80000:
            return _EXCEL_EPOCH + pd.to_timedelta(serial, unit="D")
    try:
        parsed = pd.to_datetime(text, errors="coerce")
    except (ValueError, TypeError):
        return None
    return None if pd.isna(parsed) else parsed


def normalize_datetimes(dataset: TameDataset) -> tuple[TameDataset, pd.DataFrame]:
    """DATE/DATETIME/TIME 열을 ISO 8601 로 정규화한다."""
    formats = _declared_formats(dataset.meta)
    columns = ["column", "kind", "changed_cells", "unparsed_cells"]
    frame = dataset.df.copy()
    rows: list[dict[str, Any]] = []
    for column in dataset.columns:
        out_fmt = _output_format(column)
        if out_fmt is None:
            continue
        kind = "DATETIME" if column.has_tag("DATETIME") else ("DATE" if column.has_tag("DATE") else "TIME")
        changed = 0
        unparsed = 0

        def _normalize(value: Any) -> Any:
            nonlocal changed, unparsed
            if cell_state(value) != STATE_VALUE:
                return value
            parsed = _parse_one(value, formats, kind=kind)
            if parsed is None:
                unparsed += 1
                return value
            iso = parsed.strftime(out_fmt)
            if iso != str(value):
                changed += 1
            return iso

        frame[column.name] = frame[column.name].map(_normalize)
        rows.append({"column": column.name, "kind": kind, "changed_cells": changed, "unparsed_cells": unparsed})

    return dataset.with_df(frame), pd.DataFrame(rows, columns=columns)


def datetime_validation_issues(dataset: TameDataset) -> list[ValidationIssue]:
    """파싱 불가한 시간형 값을 검증 이슈로 보고한다."""
    formats = _declared_formats(dataset.meta)
    issues: list[ValidationIssue] = []
    for column in dataset.columns:
        kind = "DATETIME" if column.has_tag("DATETIME") else ("DATE" if column.has_tag("DATE") else ("TIME" if column.has_tag("TIME") else None))
        if kind is None:
            continue
        series = dataset.df[column.name]
        for row_idx, value in series.items():
            if cell_state(value) != STATE_VALUE:
                continue
            if _parse_one(value, formats, kind=kind) is None:
                try:
                    row_number = int(row_idx) + 2
                except (TypeError, ValueError):
                    row_number = -1
                issues.append(
                    ValidationIssue(
                        row_number=row_number,
                        column=column.name,
                        tag=kind,
                        value=value,
                        message=f"Value could not be parsed as a {kind}.",
                        severity="error",
                    )
                )
    return issues

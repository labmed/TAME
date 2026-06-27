"""엑셀 왕복 무결성 검사: 일반(General) 서식 자동형변환으로 인한 값 손상 탐지.

스프레드시트는 값을 "일반" 서식으로 받아들일 때 문자열을 자동으로 숫자/날짜로 강제 변환한다.
임상검사 자료에서 자주 문제가 되는 손상 유형은 다음과 같다.

- ``leading_zero_loss``  : 앞자리 0이 있는 등록번호/검체번호("00123456" → 123456)
- ``precision_loss``     : 16자리 이상 긴 숫자 식별자(바코드 등)가 부동소수점 정밀도/지수표기로 손상
- ``date_coercion``      : 날짜로 오인되는 코드/문자열("3-4", "MAR1", "OCT-4" 등) — 유전자명 손상[21,22]과 동일 메커니즘

이 모듈은 위 변환을 문서화된 일반서식 규칙에 따라 모델링하여, ``.tame`` 의 원본 값 대비 손상될
셀을 범주별로 집계한다. 부등호 결과("<3", ">5000")와 상태 토큰("<<NULL>>")은 일반서식에서
숫자/날짜로 변환되지 않으므로 손상으로 집계하지 않는다(과대 추정 방지).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .models import TameDataset

__all__ = ["excel_general_coercion", "check_excel_roundtrip", "ExcelIntegrityReport"]


_LEADING_ZERO = re.compile(r"^0\d+$")
_PURE_DIGITS = re.compile(r"^\d+$")

_MONTHS = r"(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|SEPT|OCT|NOV|DEC)"
_DATE_PATTERNS = (
    re.compile(r"^\d{1,2}[-/]\d{1,2}$"),                 # 3/4, 3-4, 12/5
    re.compile(r"^\d{1,2}[-/]\d{1,2}[-/]\d{2,4}$"),      # 3/4/24, 03-04-2024
    re.compile(rf"^{_MONTHS}[-/ ]?\d{{1,2}}$", re.IGNORECASE),   # MAR1, SEP-2, OCT 4
    re.compile(rf"^\d{{1,2}}[-/ ]?{_MONTHS}$", re.IGNORECASE),   # 1-DEC, 4 OCT
)

# 일반서식 변환과 무관한 값(부등호 결과, 상태 토큰)은 검사에서 제외한다.
_COMPARATOR = re.compile(r"^\s*(<=|>=|<|>)\s*\S")
_STATE_TOKEN = re.compile(r"^<<.*>>$")


def excel_general_coercion(text: Any) -> tuple[str | None, str]:
    """엑셀 일반서식이 ``text`` 를 어떻게 강제 변환하는지 모델링한다.

    반환: ``(category, coerced_text)``. 손상이 없으면 ``(None, 원본)``.
    """
    s = "" if text is None else str(text)
    if not s or _STATE_TOKEN.match(s) or _COMPARATOR.match(s):
        return (None, s)

    if _LEADING_ZERO.match(s):
        return ("leading_zero_loss", str(int(s)))

    if _PURE_DIGITS.match(s) and len(s) >= 16:
        # float64 유효자리(15~16) 초과 → 정밀도 손실/지수표기
        return ("precision_loss", f"{float(s):.6E}")

    for pattern in _DATE_PATTERNS:
        if pattern.match(s):
            return ("date_coercion", "<date-serial>")

    return (None, s)


@dataclass
class ExcelIntegrityReport:
    total_cells: int = 0
    altered_cells: int = 0
    by_category: dict[str, int] = field(default_factory=dict)
    by_column: dict[str, int] = field(default_factory=dict)
    examples: dict[str, list[tuple[str, str]]] = field(default_factory=dict)

    @property
    def altered_rate(self) -> float:
        return (self.altered_cells / self.total_cells) if self.total_cells else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_cells": self.total_cells,
            "altered_cells": self.altered_cells,
            "altered_rate": round(self.altered_rate, 6),
            "by_category": dict(self.by_category),
            "by_column": dict(self.by_column),
            "examples": {k: v[:3] for k, v in self.examples.items()},
        }


def check_excel_roundtrip(dataset: TameDataset) -> ExcelIntegrityReport:
    """데이터셋 전체 셀에 대해 엑셀 일반서식 변환 손상을 범주별로 집계한다."""
    report = ExcelIntegrityReport()
    for column in dataset.columns:
        series = dataset.df[column.name]
        for value in series:
            s = "" if value is None else str(value)
            if not s or _STATE_TOKEN.match(s):
                continue
            report.total_cells += 1
            category, coerced = excel_general_coercion(s)
            if category is not None:
                report.altered_cells += 1
                report.by_category[category] = report.by_category.get(category, 0) + 1
                report.by_column[column.name] = report.by_column.get(column.name, 0) + 1
                report.examples.setdefault(category, []).append((s, coerced))
    return report

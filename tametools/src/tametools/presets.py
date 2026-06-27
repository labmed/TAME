from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import pandas as pd

from .config import ci_get
from .metadata import remove_legacy_tag_section
from .models import ColumnSpec, OperationOutput, TameDataset
from .tags import merge_tags


@dataclass(frozen=True)
class PresetDefinition:
    name: str
    label: str
    description: str


@dataclass(frozen=True)
class ColumnRole:
    role: str
    label: str
    tags: tuple[str, ...]
    aliases: tuple[str, ...]
    required: bool = False


CLINICAL_CHEMISTRY_PRESET = PresetDefinition(
    name="CLINICAL_CHEMISTRY",
    label="Clinical Chemistry Preset",
    description="Apply tags, preprocessing actions, and analysis buttons for routine clinical chemistry datasets.",
)


CLINICAL_CHEMISTRY_ROLES = (
    ColumnRole("rowid", "Row ID", ("ROWID",), ("No.", "rowid", "row_id", "번호")),
    ColumnRole("result_time", "Result time", ("DATETIME", "RESULT_TIME"), ("검사시간", "result_time", "test_time", "검사일시")),
    ColumnRole("sample_id", "Sample ID", ("ID(sample)", "STR"), ("바코드번호", "barcode", "sample_id", "specimen_id")),
    ColumnRole("patient_id", "Patient ID", ("ID(patient)", "STR"), ("등록번호", "patient_id", "id", "환자번호")),
    ColumnRole("birthdate", "Birth date", ("BIRTHDATE", "DATE(%y%m%d)"), ("생년월일", "birthdate", "date_of_birth", "dob")),
    ColumnRole("test", "Test name", ("CATEGORY", "TESTNAME", "ITEM"), ("검사항목명", "test", "test_name", "item"), required=True),
    ColumnRole("result", "Result value", ("RESULT", "NUM"), ("보고값", "result", "value", "결과값"), required=True),
    ColumnRole("instrument", "Instrument", ("CATEGORY", "INSTRUMENT", "BY"), ("장비명", "instrument", "equipment", "analyzer")),
    ColumnRole("received_time", "Received time", ("DATETIME", "RECEIVED_AT"), ("접수일", "received_at", "received_time", "접수시간")),
    ColumnRole("sex", "Sex", ("SEX", "CATEGORY", "BY"), ("sex", "성별", "gender")),
    ColumnRole("age", "Age", ("AGE",), ("age", "나이")),
)


GENERIC_ROLE_TAGS = frozenset({"CATEGORY", "DATETIME", "NUM", "STR", "BY", "DATE(%y%m%d)"})


CLINICAL_CHEMISTRY_ANALYSES: dict[str, dict[str, Any]] = {
    "ITEM_COUNTS": {
        "LABEL": "검사항목별 건수",
        "DESCRIPTION": "검사항목별 데이터 수와 구성 확인",
        "PLUGIN": "CHEMISTRY_ANALYSIS",
        "OPTIONS": {"MODE": "ITEM_COUNTS", "AGE_BIN_WIDTH": 10, "COMPARATOR_POLICY": "VALUE", "MAX_ROWS": 500},
    },
    "RESULT_SUMMARY": {
        "LABEL": "검사항목별 결과 요약",
        "DESCRIPTION": "평균, 중앙값, 분위수, 참고구간 후보 범위 확인",
        "PLUGIN": "CHEMISTRY_ANALYSIS",
        "OPTIONS": {"MODE": "RESULT_SUMMARY", "AGE_BIN_WIDTH": 10, "COMPARATOR_POLICY": "VALUE", "MAX_ROWS": 500},
    },
    "RESULT_HISTOGRAM": {
        "LABEL": "결과 분포 히스토그램",
        "DESCRIPTION": "검사항목별 결과값 분포와 치우침 확인",
        "PLUGIN": "CHEMISTRY_ANALYSIS",
        "OPTIONS": {"MODE": "RESULT_HISTOGRAM", "AGE_BIN_WIDTH": 10, "COMPARATOR_POLICY": "VALUE", "MAX_ROWS": 500},
    },
    "REFERENCE_INTERVAL": {
        "LABEL": "참고구간 분석",
        "DESCRIPTION": "성별/연령그룹을 포함한 2.5-97.5 분위 참고구간 계산",
        "PLUGIN": "REFERENCE_INTERVAL",
        "OPTIONS": {"AGE_BIN_WIDTH": 10, "COMPARATOR_POLICY": "VALUE"},
    },
    "TAT_BY_TEST": {
        "LABEL": "검사항목별 TAT",
        "DESCRIPTION": "접수일-검사시간 차이로 항목별 turnaround time 확인",
        "PLUGIN": "CHEMISTRY_ANALYSIS",
        "OPTIONS": {"MODE": "TAT_BY_TEST", "AGE_BIN_WIDTH": 10, "COMPARATOR_POLICY": "VALUE", "MAX_ROWS": 500},
    },
    "TAT_BY_INSTRUMENT": {
        "LABEL": "장비별 TAT",
        "DESCRIPTION": "장비와 항목별 TAT 차이 확인",
        "PLUGIN": "CHEMISTRY_ANALYSIS",
        "OPTIONS": {"MODE": "TAT_BY_INSTRUMENT", "AGE_BIN_WIDTH": 10, "COMPARATOR_POLICY": "VALUE", "MAX_ROWS": 500},
    },
    "INSTRUMENT_BIAS": {
        "LABEL": "장비별 중앙값 편향",
        "DESCRIPTION": "동일 항목에서 장비별 중앙값 차이와 비율 확인",
        "PLUGIN": "CHEMISTRY_ANALYSIS",
        "OPTIONS": {"MODE": "INSTRUMENT_BIAS", "AGE_BIN_WIDTH": 10, "COMPARATOR_POLICY": "VALUE", "MAX_ROWS": 500},
    },
    "DAILY_WORKLOAD": {
        "LABEL": "일별 검사량",
        "DESCRIPTION": "날짜와 항목별 workload trend 확인",
        "PLUGIN": "CHEMISTRY_ANALYSIS",
        "OPTIONS": {"MODE": "DAILY_WORKLOAD", "AGE_BIN_WIDTH": 10, "COMPARATOR_POLICY": "VALUE", "MAX_ROWS": 500},
    },
    "HOURLY_WORKLOAD": {
        "LABEL": "시간대별 검사량",
        "DESCRIPTION": "검사시간의 hour 기준 workload profile 확인",
        "PLUGIN": "CHEMISTRY_ANALYSIS",
        "OPTIONS": {"MODE": "HOURLY_WORKLOAD", "AGE_BIN_WIDTH": 10, "COMPARATOR_POLICY": "VALUE", "MAX_ROWS": 500},
    },
    "AGE_SEX_RESULT": {
        "LABEL": "연령/성별 결과 분포",
        "DESCRIPTION": "연령대와 성별에 따른 항목별 결과 요약",
        "PLUGIN": "CHEMISTRY_ANALYSIS",
        "OPTIONS": {"MODE": "AGE_SEX_RESULT", "AGE_BIN_WIDTH": 10, "COMPARATOR_POLICY": "VALUE", "MAX_ROWS": 500},
    },
    "OUTLIERS_IQR": {
        "LABEL": "IQR 이상치 검토",
        "DESCRIPTION": "항목별 IQR 기반 이상치 수와 경계 확인",
        "PLUGIN": "CHEMISTRY_ANALYSIS",
        "OPTIONS": {"MODE": "OUTLIERS_IQR", "AGE_BIN_WIDTH": 10, "COMPARATOR_POLICY": "VALUE", "MAX_ROWS": 500},
    },
    "DELTA_CHECK": {
        "LABEL": "반복검사 Delta check",
        "DESCRIPTION": "동일 등록번호/항목의 이전 결과 대비 변화량 상위 확인",
        "PLUGIN": "CHEMISTRY_ANALYSIS",
        "OPTIONS": {"MODE": "DELTA_CHECK", "AGE_BIN_WIDTH": 10, "COMPARATOR_POLICY": "VALUE", "MAX_ROWS": 500},
    },
    "MISSING_QUALITY": {
        "LABEL": "결측/셀 상태 품질",
        "DESCRIPTION": "컬럼별 ABSENT/NULL/EMPTY/WS 등 셀 상태 확인",
        "PLUGIN": "CHEMISTRY_ANALYSIS",
        "OPTIONS": {"MODE": "MISSING_QUALITY", "AGE_BIN_WIDTH": 10, "COMPARATOR_POLICY": "VALUE", "MAX_ROWS": 500},
    },
}


def available_presets() -> list[PresetDefinition]:
    return [CLINICAL_CHEMISTRY_PRESET]


def preset_payloads() -> list[dict[str, str]]:
    return [
        {
            "name": preset.name,
            "label": preset.label,
            "description": preset.description,
        }
        for preset in available_presets()
    ]


def apply_preset(dataset: TameDataset, preset_name: str) -> OperationOutput:
    normalized = str(preset_name or "").strip().upper().replace("-", "_")
    if normalized not in {"CLINICAL_CHEMISTRY", "CHEMISTRY", "CLINICAL_CHEMISTRY_ANALYSIS"}:
        raise ValueError(f"Unsupported preset: {preset_name}")
    return apply_clinical_chemistry_preset(dataset)


def apply_clinical_chemistry_preset(dataset: TameDataset) -> OperationOutput:
    matched = _match_roles(dataset)
    meta = deepcopy(dataset.meta)
    settings = ci_get(meta, "SETTINGS", {})
    settings = dict(settings) if isinstance(settings, dict) else {}
    settings.setdefault("VALIDATE_ERROR", "REPORT")
    settings.setdefault("CRR", "VALUE")
    meta["SETTINGS"] = settings

    meta = remove_legacy_tag_section(meta)
    column_section = ci_get(meta, "COLUMN", {})
    column_section = dict(column_section) if isinstance(column_section, dict) else {}
    columns = list(dataset.columns)
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []

    for role in CLINICAL_CHEMISTRY_ROLES:
        column = matched.get(role.role)
        if column is None:
            status = "missing"
            if role.required:
                warnings.append(f"Required clinical chemistry column not found: {role.label}")
            rows.append({"role": role.role, "label": role.label, "column": "", "tags": "::".join(role.tags), "status": status})
            continue

        index = _column_index(columns, column.name)
        before = columns[index]
        tags = merge_tags(before.tags, role.tags)
        columns[index] = ColumnSpec(
            original_header=before.original_header,
            name=before.name,
            tags=tags,
        )
        entry = column_section.get(before.name, {})
        entry = dict(entry) if isinstance(entry, dict) else {}
        entry["TAGS"] = list(merge_tags(entry.get("TAGS", []), tags))
        column_section[before.name] = entry
        rows.append({"role": role.role, "label": role.label, "column": before.name, "tags": "::".join(tags), "status": "matched"})

    if column_section:
        meta["COLUMN"] = column_section
    _install_clinical_chemistry_actions(meta, matched)
    meta["ANALYSES"] = deepcopy(CLINICAL_CHEMISTRY_ANALYSES)
    meta.setdefault("WORKS", {"DEFAULT": ["DESCRIBE"]})
    meta["USER_GUIDE"] = _clinical_chemistry_user_guide(rows)

    updated = dataset.replace(columns=columns, meta=meta, raw_sections={})
    table = pd.DataFrame(rows, columns=["role", "label", "column", "tags", "status"])
    return OperationOutput(
        name="CLINICAL_CHEMISTRY_PRESET",
        dataset=updated,
        table=table,
        warnings=warnings,
        message=f"clinical_chemistry_preset matched={sum(row['status'] == 'matched' for row in rows)} analyses={len(CLINICAL_CHEMISTRY_ANALYSES)}",
    )


def _install_clinical_chemistry_actions(meta: dict[str, Any], matched: dict[str, ColumnSpec | None]) -> None:
    core_roles = ("result_time", "sample_id", "patient_id", "test", "result", "instrument", "received_time", "sex", "age")
    roles_by_name = {role.role: role for role in CLINICAL_CHEMISTRY_ROLES}
    # 컬럼명이 아니라 각 역할의 구체 태그(generic 제외)로 선택해 컬럼명 독립성을 유지한다.
    core_tags: list[str] = []
    for role_name in core_roles:
        if matched.get(role_name) is None:
            continue
        role = roles_by_name.get(role_name)
        specific = next((tag for tag in (role.tags if role else ()) if tag not in GENERIC_ROLE_TAGS), None)
        if specific and specific not in core_tags:
            core_tags.append(specific)
    actions = ci_get(meta, "ACTIONS", {})
    actions = dict(actions) if isinstance(actions, dict) else {}
    if core_tags:
        actions["CORE_COLUMNS"] = {
            "LABEL": "핵심 분석 컬럼만 남기기",
            "TYPE": "COLUMN.INCLUDE",
            "DESCRIPTION": "임상화학 분석에 필요한 핵심 컬럼만 태그로 선택해 남기는 전처리",
            "TAGS": core_tags,
        }
    meta["ACTIONS"] = actions

    pipelines = ci_get(meta, "ACTION_PIPELINES", {})
    pipelines = dict(pipelines) if isinstance(pipelines, dict) else {}
    if core_tags:
        pipelines["CORE_ANALYSIS_VIEW"] = ["CORE_COLUMNS"]
    meta["ACTION_PIPELINES"] = pipelines


def _clinical_chemistry_user_guide(rows: list[dict[str, Any]]) -> dict[str, Any]:
    missing_required = [row["label"] for row in rows if row["status"] == "missing" and row["role"] in {"test", "result"}]
    return {
        "AUDIENCE": "Non-programmer laboratory users",
        "QUICK_START": [
            "Open an XLSX or TAME dataset.",
            "Apply the Clinical Chemistry Preset if analyses are not already visible.",
            "Click an Analyses button; each run creates a new TAME file in the chain.",
            "Use Save TAME to keep the reusable dataset with metadata.",
        ],
        "COLUMN_MATCHING": {row["role"]: row["column"] for row in rows},
        "MISSING_REQUIRED": missing_required,
        "REUSABILITY_NOTE": "For a new dataset with the same column names, apply this preset once and reuse the generated analyses.",
    }


def _match_roles(dataset: TameDataset) -> dict[str, ColumnSpec | None]:
    return {role.role: _match_role(dataset, role) for role in CLINICAL_CHEMISTRY_ROLES}


def _match_role(dataset: TameDataset, role: ColumnRole) -> ColumnSpec | None:
    aliases = {alias.lower() for alias in role.aliases}
    for column in dataset.columns:
        if column.name.lower() in aliases:
            return column
    for tag in _specific_match_tags(role):
        column = dataset.first_column_with_tag(tag)
        if column is not None:
            return column
    return None


def _specific_match_tags(role: ColumnRole) -> tuple[str, ...]:
    return tuple(tag for tag in role.tags if tag.upper() not in GENERIC_ROLE_TAGS)


def _column_index(columns: list[ColumnSpec], name: str) -> int:
    for index, column in enumerate(columns):
        if column.name == name:
            return index
    raise ValueError(f"Column not found: {name}")

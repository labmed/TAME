from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from ..analysis import age_series_for, normalize_gender, numeric_series_for
from ..config import ci_get
from ..models import ColumnSpec, OperationOutput, TameDataset, merged_column_specs
from .base import register_plugin


@dataclass(frozen=True)
class GroupContext:
    test_column: ColumnSpec | None
    gender_column: ColumnSpec | None
    age_column: ColumnSpec | None


@dataclass(frozen=True)
class GroupLevel:
    name: str
    use_gender: bool = False
    use_age: bool = False


GROUP_LEVELS = (
    GroupLevel("TESTNAME"),
    GroupLevel("TESTNAME+GENDER", use_gender=True),
    GroupLevel("TESTNAME+AGE", use_age=True),
    GroupLevel("TESTNAME+GENDER+AGE", use_gender=True, use_age=True),
)


@register_plugin(
    "REFERENCE_INTERVAL",
    description="Compute reference intervals by TESTNAME with optional GENDER, AGE, and GENDER+AGE grouping.",
)
@register_plugin(
    "RI",
    description="Alias of the reference interval plugin.",
)
def reference_interval_plugin(dataset: TameDataset, meta: dict, step_name: str, options: dict) -> OperationOutput:
    comparator_policy = str(ci_get(options, "COMPARATOR_POLICY", ci_get(dataset.settings(), "CRR", "VALUE"))).upper()
    lower_q = float(ci_get(options, "LOW_Q", 0.025))
    upper_q = float(ci_get(options, "HIGH_Q", 0.975))

    result_columns = _result_columns(dataset)
    context = GroupContext(
        test_column=_testname_column(dataset),
        gender_column=dataset.first_column_with_tag("GENDER"),
        age_column=dataset.first_column_with_tag("AGE"),
    )

    warnings: list[str] = []
    if not result_columns:
        warnings.append("Missing RESULT::NUM or RESULT::<NUM> column. No reference interval rows were produced.")
    if context.test_column is None:
        warnings.append("Missing TESTNAME/ITEM tag. Reference intervals will be calculated per result column.")
    if context.gender_column is None:
        warnings.append("Missing GENDER tag. No gender-specific reference interval groups will be calculated.")
    if context.age_column is None:
        warnings.append("Missing AGE tag. No age-specific reference interval groups will be calculated.")

    rows: list[dict[str, Any]] = []
    for result_column in result_columns:
        work = _prepare_work_frame(dataset, result_column, context, comparator_policy=comparator_policy)
        if work.empty:
            continue
        for level in _group_levels_for(context):
            rows.extend(
                _summarize_group_level(
                    work,
                    result_column,
                    level,
                    comparator_policy=comparator_policy,
                    lower_q=lower_q,
                    upper_q=upper_q,
                )
            )

    result_dataset = _build_result_dataset(
        rows,
        source_dataset=dataset,
        comparator_policy=comparator_policy,
        lower_q=lower_q,
        upper_q=upper_q,
        warnings=warnings,
    )
    message = f"reference_interval rows={len(result_dataset.df)} result_columns={len(result_columns)}"
    return OperationOutput(
        name=step_name,
        dataset=result_dataset,
        table=result_dataset.df,
        warnings=warnings,
        message=message,
    )


def _result_columns(dataset: TameDataset) -> list[ColumnSpec]:
    return [
        column
        for column in dataset.columns_with_tag("RESULT")
        if column.has_tag("NUM") or column.has_tag("<NUM>")
    ]


def _testname_column(dataset: TameDataset) -> ColumnSpec | None:
    return dataset.first_column_with_tag("TESTNAME") or dataset.first_column_with_tag("ITEM")


def _group_levels_for(context: GroupContext) -> list[GroupLevel]:
    levels = [GROUP_LEVELS[0]]
    if context.gender_column is not None:
        levels.append(GROUP_LEVELS[1])
    if context.age_column is not None:
        levels.append(GROUP_LEVELS[2])
    if context.gender_column is not None and context.age_column is not None:
        levels.append(GROUP_LEVELS[3])
    return levels


def _prepare_work_frame(
    dataset: TameDataset,
    result_column: ColumnSpec,
    context: GroupContext,
    *,
    comparator_policy: str,
) -> pd.DataFrame:
    work = dataset.df.copy()
    work["_result_value"] = numeric_series_for(dataset, result_column, crr_policy=comparator_policy)
    work = work.loc[work["_result_value"].notna()].copy()
    if work.empty:
        return work

    if context.test_column is not None:
        work["_test_name"] = dataset.df[context.test_column.name].reindex(work.index).map(
            lambda value: _text_or_default(value, result_column.name)
        )
    else:
        work["_test_name"] = result_column.name

    if context.gender_column is not None:
        work["_gender_group"] = dataset.df[context.gender_column.name].reindex(work.index).map(normalize_gender)
    else:
        work["_gender_group"] = "ALL"

    if context.age_column is not None:
        ages = age_series_for(dataset, context.age_column).reindex(work.index)
        work["_age_group"] = ages.map(_age_band_label)
        bounds = work["_age_group"].map(_age_band_bounds)
        work["_age_low"] = bounds.map(lambda item: item[0] if item else None)
        work["_age_high"] = bounds.map(lambda item: item[1] if item else None)
    else:
        work["_age_group"] = "ALL"
        work["_age_low"] = None
        work["_age_high"] = None

    return work


def _summarize_group_level(
    work: pd.DataFrame,
    result_column: ColumnSpec,
    level: GroupLevel,
    *,
    comparator_policy: str,
    lower_q: float,
    upper_q: float,
) -> list[dict[str, Any]]:
    grouped_frame = work.copy()
    group_keys = ["_test_name"]

    if level.use_gender:
        grouped_frame = grouped_frame.loc[grouped_frame["_gender_group"].notna()].copy()
        group_keys.append("_gender_group")
    else:
        grouped_frame["_gender_group"] = "ALL"

    if level.use_age:
        grouped_frame = grouped_frame.loc[grouped_frame["_age_group"].notna()].copy()
        group_keys.append("_age_group")
    else:
        grouped_frame["_age_group"] = "ALL"
        grouped_frame["_age_low"] = None
        grouped_frame["_age_high"] = None

    if grouped_frame.empty:
        return []

    rows: list[dict[str, Any]] = []
    for group_key, group in grouped_frame.groupby(group_keys, dropna=False):
        if not isinstance(group_key, tuple):
            group_key = (group_key,)

        cursor = 0
        test_name = group_key[cursor]
        cursor += 1
        gender_group = group_key[cursor] if level.use_gender else "ALL"
        if level.use_gender:
            cursor += 1
        age_group = group_key[cursor] if level.use_age else "ALL"

        first_row = group.iloc[0]
        values = group["_result_value"].astype(float)
        rows.append(
            {
                "검사항목명": test_name,
                "그룹수준": level.name,
                "성별그룹": gender_group,
                "연령그룹": age_group,
                "연령하한": _float_or_none(first_row.get("_age_low")),
                "연령상한": _float_or_none(first_row.get("_age_high")),
                "원본결과컬럼": result_column.name,
                "대상수": int(values.shape[0]),
                "참고치하한": float(values.quantile(lower_q)),
                "참고치상한": float(values.quantile(upper_q)),
                "평균": float(values.mean()),
                "중앙값": float(values.median()),
                "표준편차": float(values.std(ddof=1)) if values.shape[0] > 1 else 0.0,
                "최소값": float(values.min()),
                "최대값": float(values.max()),
                "계산방법": f"quantile_{lower_q:.3f}_{upper_q:.3f}",
                "비교연산처리": comparator_policy,
            }
        )

    return rows


def _build_result_dataset(
    rows: list[dict[str, Any]],
    source_dataset: TameDataset,
    *,
    comparator_policy: str,
    lower_q: float,
    upper_q: float,
    warnings: list[str],
) -> TameDataset:
    headers = [
        "[[TESTNAME::ITEM]]검사항목명",
        "[[GROUP_LEVEL]]그룹수준",
        "[[BY::GENDER_GROUP]]성별그룹",
        "[[BY::AGE_GROUP]]연령그룹",
        "[[AGE_LOW::NUM]]연령하한",
        "[[AGE_HIGH::NUM]]연령상한",
        "[[SOURCE]]원본결과컬럼",
        "[[N::NUM]]대상수",
        "[[REF_LOW::NUM]]참고치하한",
        "[[REF_HIGH::NUM]]참고치상한",
        "[[RESULT::NUM]]평균",
        "[[RESULT::NUM]]중앙값",
        "[[RESULT::NUM]]표준편차",
        "[[RESULT::NUM]]최소값",
        "[[RESULT::NUM]]최대값",
        "[[METHOD]]계산방법",
        "[[SETTING]]비교연산처리",
    ]
    columns = merged_column_specs(headers)
    column_names = [column.name for column in columns]
    df = pd.DataFrame(rows, columns=column_names) if rows else pd.DataFrame(columns=column_names)
    df = df.where(pd.notna(df), None)
    df.columns = [column.name for column in columns]
    meta = {
        "INFO": {
            "DESCRIPTION": "Reference interval plugin output dataset",
        },
        "SETTINGS": {
            "VALIDATE_ERROR": "REPORT",
        },
        "WORKS": {
            "DEFAULT": ["DESCRIBE"],
        },
        "PLUGIN": {
            "NAME": "REFERENCE_INTERVAL",
            "COMPARATOR_POLICY": comparator_policy,
            "LOW_Q": lower_q,
            "HIGH_Q": upper_q,
            "SOURCE_PATH": source_dataset.source_path or "",
            "WARNINGS": warnings,
        },
    }
    return TameDataset(
        df=df,
        columns=columns,
        meta=meta,
        schema={},
        job={},
        raw_sections={},
        source_path=source_dataset.source_path,
    )


def _age_band_label(age_years: float | None) -> str | None:
    if age_years is None or pd.isna(age_years):
        return None
    if age_years >= 70:
        return "70+"
    lower = int(age_years // 10) * 10
    upper = lower + 9
    return f"{lower}-{upper}"


def _age_band_bounds(label: Any) -> tuple[float | None, float | None] | None:
    if label in (None, "", "ALL"):
        return None
    text = str(label)
    if text == "70+":
        return (70.0, None)
    if "-" not in text:
        return None
    left, right = text.split("-", 1)
    try:
        return (float(left), float(right))
    except ValueError:
        return None


def _text_or_default(value: Any, default: str) -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _float_or_none(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable

import pandas as pd

from tametools.age import age_band_label, age_bin_width_for_column
from tametools.analysis import age_series_for, numeric_series_for
from tametools.cellstate import state_counts
from tametools.config import ci_get
from tametools.models import ColumnSpec, OperationOutput, TameDataset, merged_column_specs
from tametools.pandas_compat import concat_dataframes
from tametools.sex import normalize_sex
from tametools.plugin_base.base import register_plugin
from tametools.plugin_base.roles import RESULT_ROLE, result_binding


@dataclass(frozen=True)
class ChemistryContext:
    test: ColumnSpec | None
    result: ColumnSpec | None
    instrument: ColumnSpec | None
    received_at: ColumnSpec | None
    result_at: ColumnSpec | None
    patient_id: ColumnSpec | None
    sex: ColumnSpec | None
    age: ColumnSpec | None


@register_plugin(
    "CHEMISTRY_ANALYSIS",
    description="Clinical chemistry analysis examples: workload, TAT, instrument bias, age/sex groups, outliers, and delta checks.",
    roles=(RESULT_ROLE,),
)
@register_plugin("CLINICAL_CHEMISTRY", description="Alias of CHEMISTRY_ANALYSIS.", roles=(RESULT_ROLE,))
def chemistry_analysis_plugin(dataset: TameDataset, meta: dict, step_name: str, options: dict) -> OperationOutput:
    mode = str(ci_get(options, "MODE", ci_get(meta, "MODE", "ITEM_COUNTS"))).strip().upper()
    comparator_policy = str(ci_get(options, "COMPARATOR_POLICY", ci_get(dataset.settings(), "CRR", "VALUE"))).upper()
    age_bin_width = int(ci_get(options, "AGE_BIN_WIDTH", 10))
    max_rows = int(ci_get(options, "MAX_ROWS", 500))
    ctx = _context(dataset)
    result_columns = list(result_binding(dataset, options).columns)
    if result_columns:
        ctx = replace(ctx, result=result_columns[0])
    warnings = _context_warnings(ctx, mode, allow_result_as_test=bool(result_columns))

    handlers: dict[str, Callable[[TameDataset, ChemistryContext, str, int, int], pd.DataFrame]] = {
        "ITEM_COUNTS": _item_counts,
        "RESULT_SUMMARY": _result_summary,
        "RESULT_HISTOGRAM": _result_histogram,
        "INSTRUMENT_BIAS": _instrument_bias,
        "TAT_BY_TEST": _tat_by_test,
        "TAT_BY_INSTRUMENT": _tat_by_instrument,
        "DAILY_WORKLOAD": _daily_workload,
        "HOURLY_WORKLOAD": _hourly_workload,
        "AGE_SEX_RESULT": _age_sex_result,
        "OUTLIERS_IQR": _outliers_iqr,
        "DELTA_CHECK": _delta_check,
        "MISSING_QUALITY": _missing_quality,
    }
    handler = handlers.get(mode)
    if handler is None:
        raise ValueError(f"Unsupported CHEMISTRY_ANALYSIS MODE: {mode}")

    # Guard: if the mode's required tag-derived columns are absent, return an empty
    # table with the explanatory warnings instead of crashing inside the handler.
    result_iteration_modes = {
        "RESULT_SUMMARY",
        "RESULT_HISTOGRAM",
        "INSTRUMENT_BIAS",
        "AGE_SEX_RESULT",
        "OUTLIERS_IQR",
        "DELTA_CHECK",
    }
    if warnings:
        table = pd.DataFrame()
    elif mode in result_iteration_modes and len(result_columns) > 1:
        tables: list[pd.DataFrame] = []
        for result_column in result_columns:
            part = handler(dataset, replace(ctx, result=result_column), comparator_policy, age_bin_width, max_rows)
            if not part.empty:
                part = part.copy()
                part.insert(0, "원본결과컬럼", result_column.name)
                tables.append(part)
        table = concat_dataframes(tables, ignore_index=True) if tables else pd.DataFrame()
    else:
        table = handler(dataset, ctx, comparator_policy, age_bin_width, max_rows)
    result_dataset = _result_dataset(table, dataset, mode=mode, options=options, warnings=warnings)
    return OperationOutput(
        name=step_name,
        dataset=result_dataset,
        table=table,
        warnings=warnings,
        message=f"chemistry_analysis mode={mode} rows={len(table)}",
    )


def _context(dataset: TameDataset) -> ChemistryContext:
    return ChemistryContext(
        test=_first(dataset, tags=("TESTNAME", "ITEM"), names=("검사항목명", "test", "item")),
        result=_first_result(dataset),
        instrument=_first(dataset, tags=("INSTRUMENT", "EQUIPMENT", "DEVICE", "BY"), names=("장비명", "instrument", "equipment")),
        received_at=_first(dataset, tags=("RECEIVED_AT", "RECEIVED", "ORDER_TIME"), names=("접수일", "received_at", "received")),
        result_at=_first(dataset, tags=("RESULT_TIME", "TEST_TIME", "검사시간"), names=("검사시간", "result_at", "test_time")),
        patient_id=_first(dataset, tags=("ID(patient)", "PATIENT_ID", "ID"), names=("등록번호", "patient_id", "id")),
        sex=dataset.first_column_with_tag("SEX") or _first(dataset, names=("sex", "성별")),
        age=dataset.first_column_with_tag("AGE") or _first(dataset, names=("age", "나이")),
    )


def _first_result(dataset: TameDataset) -> ColumnSpec | None:
    for column in dataset.columns_with_tag("RESULT"):
        if dataset.column_has_tag(column, "NUM") or dataset.column_has_tag(column, "<NUM>"):
            return column
    return _first(dataset, names=("보고값", "result", "value"))


def _first(dataset: TameDataset, *, tags: tuple[str, ...] = (), names: tuple[str, ...] = ()) -> ColumnSpec | None:
    for tag in tags:
        column = dataset.first_column_with_tag(tag)
        if column is not None:
            return column
    lowered = {name.lower() for name in names}
    for column in dataset.columns:
        if column.name.lower() in lowered:
            return column
    return None


def _context_warnings(ctx: ChemistryContext, mode: str, *, allow_result_as_test: bool = False) -> list[str]:
    required: dict[str, tuple[tuple[str, ColumnSpec | None], ...]] = {
        "ITEM_COUNTS": (("test", ctx.test),),
        "RESULT_SUMMARY": (("test", ctx.test), ("result", ctx.result)),
        "RESULT_HISTOGRAM": (("test", ctx.test), ("result", ctx.result)),
        "INSTRUMENT_BIAS": (("test", ctx.test), ("result", ctx.result), ("instrument", ctx.instrument)),
        "TAT_BY_TEST": (("test", ctx.test), ("received_at", ctx.received_at), ("result_at", ctx.result_at)),
        "TAT_BY_INSTRUMENT": (("test", ctx.test), ("instrument", ctx.instrument), ("received_at", ctx.received_at), ("result_at", ctx.result_at)),
        "DAILY_WORKLOAD": (("test", ctx.test), ("result_at", ctx.result_at)),
        "HOURLY_WORKLOAD": (("test", ctx.test), ("result_at", ctx.result_at)),
        "AGE_SEX_RESULT": (("test", ctx.test), ("result", ctx.result), ("sex", ctx.sex), ("age", ctx.age)),
        "OUTLIERS_IQR": (("test", ctx.test), ("result", ctx.result)),
        "DELTA_CHECK": (("test", ctx.test), ("result", ctx.result), ("patient_id", ctx.patient_id), ("result_at", ctx.result_at)),
    }
    missing = []
    for name, value in required.get(mode, ()):
        if value is not None:
            continue
        if name == "test" and allow_result_as_test:
            continue
        missing.append(name)
    return [f"Missing required chemistry column for {mode}: {name}" for name in missing]


def _work(dataset: TameDataset, ctx: ChemistryContext, comparator_policy: str) -> pd.DataFrame:
    work = dataset.df.copy()
    if ctx.test is not None:
        work["_test"] = dataset.df[ctx.test.name].map(_text)
    elif ctx.result is not None:
        work["_test"] = ctx.result.name
    if ctx.result is not None:
        work["_result"] = numeric_series_for(dataset, ctx.result, crr_policy=comparator_policy)
    if ctx.instrument is not None:
        work["_instrument"] = dataset.df[ctx.instrument.name].map(_text)
    if ctx.received_at is not None:
        work["_received_at"] = pd.to_datetime(dataset.df[ctx.received_at.name], errors="coerce")
    if ctx.result_at is not None:
        work["_result_at"] = pd.to_datetime(dataset.df[ctx.result_at.name], errors="coerce")
    if ctx.patient_id is not None:
        work["_patient_id"] = dataset.df[ctx.patient_id.name].map(_text)
    if ctx.sex is not None:
        work["_sex"] = dataset.df[ctx.sex.name].map(normalize_sex)
    if ctx.age is not None:
        work["_age_years"] = age_series_for(dataset, ctx.age)
    if "_received_at" in work.columns and "_result_at" in work.columns:
        work["_tat_minutes"] = (work["_result_at"] - work["_received_at"]).dt.total_seconds() / 60
    return work


def _item_counts(dataset: TameDataset, ctx: ChemistryContext, comparator_policy: str, age_bin_width: int, max_rows: int) -> pd.DataFrame:
    work = _work(dataset, ctx, comparator_policy).dropna(subset=["_test"])
    return (
        work.groupby("_test", dropna=False, observed=False)
        .size()
        .reset_index(name="건수")
        .rename(columns={"_test": "검사항목명"})
        .sort_values(["건수", "검사항목명"], ascending=[False, True], kind="stable")
        .head(max_rows)
        .reset_index(drop=True)
    )


def _result_summary(dataset: TameDataset, ctx: ChemistryContext, comparator_policy: str, age_bin_width: int, max_rows: int) -> pd.DataFrame:
    work = _work(dataset, ctx, comparator_policy).dropna(subset=["_test", "_result"])
    if work.empty:
        return pd.DataFrame()
    return _group_numeric_summary(work, ["_test"]).rename(columns={"_test": "검사항목명"}).head(max_rows)


def _result_histogram(dataset: TameDataset, ctx: ChemistryContext, comparator_policy: str, age_bin_width: int, max_rows: int) -> pd.DataFrame:
    work = _work(dataset, ctx, comparator_policy).dropna(subset=["_test", "_result"])
    rows: list[dict[str, Any]] = []
    for test_name, group in work.groupby("_test", dropna=False, observed=False):
        values = group["_result"].astype(float)
        bins = min(12, max(4, int(values.shape[0] ** 0.5)))
        cut = pd.cut(values, bins=bins, duplicates="drop")
        counts = cut.value_counts(sort=False)
        for interval, count in counts.items():
            rows.append(
                {
                    "검사항목명": test_name,
                    "구간": _interval_label(interval),
                    "건수": int(count),
                }
            )
    return pd.DataFrame(rows).head(max_rows)


def _instrument_bias(dataset: TameDataset, ctx: ChemistryContext, comparator_policy: str, age_bin_width: int, max_rows: int) -> pd.DataFrame:
    work = _work(dataset, ctx, comparator_policy).dropna(subset=["_test", "_instrument", "_result"])
    if work.empty:
        return pd.DataFrame()
    item_medians = work.groupby("_test", observed=False)["_result"].median().rename("_item_median")
    grouped = _group_numeric_summary(work, ["_test", "_instrument"]).merge(item_medians, left_on="_test", right_index=True, how="left")
    grouped["중앙값차이"] = grouped["중앙값"] - grouped["_item_median"]
    grouped["중앙값차이율"] = grouped["중앙값차이"] / grouped["_item_median"].replace(0, pd.NA) * 100
    return (
        grouped.rename(columns={"_test": "검사항목명", "_instrument": "장비명", "_item_median": "항목전체중앙값"})
        .sort_values(["검사항목명", "장비명"], kind="stable")
        .head(max_rows)
        .reset_index(drop=True)
    )


def _tat_by_test(dataset: TameDataset, ctx: ChemistryContext, comparator_policy: str, age_bin_width: int, max_rows: int) -> pd.DataFrame:
    work = _work(dataset, ctx, comparator_policy).dropna(subset=["_test", "_tat_minutes"])
    work = work.loc[work["_tat_minutes"] >= 0]
    return _tat_summary(work, ["_test"]).rename(columns={"_test": "검사항목명"}).head(max_rows)


def _tat_by_instrument(dataset: TameDataset, ctx: ChemistryContext, comparator_policy: str, age_bin_width: int, max_rows: int) -> pd.DataFrame:
    work = _work(dataset, ctx, comparator_policy).dropna(subset=["_test", "_instrument", "_tat_minutes"])
    work = work.loc[work["_tat_minutes"] >= 0]
    return _tat_summary(work, ["_test", "_instrument"]).rename(columns={"_test": "검사항목명", "_instrument": "장비명"}).head(max_rows)


def _daily_workload(dataset: TameDataset, ctx: ChemistryContext, comparator_policy: str, age_bin_width: int, max_rows: int) -> pd.DataFrame:
    work = _work(dataset, ctx, comparator_policy).dropna(subset=["_test", "_result_at"])
    work["날짜"] = work["_result_at"].dt.date.astype(str)
    return (
        work.groupby(["날짜", "_test"], dropna=False, observed=False)
        .size()
        .reset_index(name="건수")
        .rename(columns={"_test": "검사항목명"})
        .sort_values(["날짜", "검사항목명"], kind="stable")
        .head(max_rows)
        .reset_index(drop=True)
    )


def _hourly_workload(dataset: TameDataset, ctx: ChemistryContext, comparator_policy: str, age_bin_width: int, max_rows: int) -> pd.DataFrame:
    work = _work(dataset, ctx, comparator_policy).dropna(subset=["_test", "_result_at"])
    work["시간"] = work["_result_at"].dt.hour
    return (
        work.groupby(["시간", "_test"], dropna=False, observed=False)
        .size()
        .reset_index(name="건수")
        .rename(columns={"_test": "검사항목명"})
        .sort_values(["시간", "검사항목명"], kind="stable")
        .head(max_rows)
        .reset_index(drop=True)
    )


def _age_sex_result(dataset: TameDataset, ctx: ChemistryContext, comparator_policy: str, age_bin_width: int, max_rows: int) -> pd.DataFrame:
    work = _work(dataset, ctx, comparator_policy).dropna(subset=["_test", "_result", "_sex", "_age_years"])
    width = age_bin_width_for_column(dataset, ctx.age, default=age_bin_width) if ctx.age is not None else age_bin_width
    work["연령그룹"] = work["_age_years"].map(lambda value: age_band_label(value, width=width))
    grouped = _group_numeric_summary(work, ["_test", "_sex", "연령그룹"]).rename(columns={"_test": "검사항목명", "_sex": "성별"})
    return grouped.sort_values(["검사항목명", "성별", "연령그룹"], kind="stable").head(max_rows).reset_index(drop=True)


def _outliers_iqr(dataset: TameDataset, ctx: ChemistryContext, comparator_policy: str, age_bin_width: int, max_rows: int) -> pd.DataFrame:
    work = _work(dataset, ctx, comparator_policy).dropna(subset=["_test", "_result"])
    rows: list[dict[str, Any]] = []
    for test_name, group in work.groupby("_test", dropna=False, observed=False):
        values = group["_result"].astype(float)
        q1 = values.quantile(0.25)
        q3 = values.quantile(0.75)
        iqr = q3 - q1
        low = q1 - 1.5 * iqr
        high = q3 + 1.5 * iqr
        outlier_count = int(((values < low) | (values > high)).sum())
        rows.append(
            {
                "검사항목명": test_name,
                "대상수": int(values.shape[0]),
                "Q1": float(q1),
                "Q3": float(q3),
                "IQR": float(iqr),
                "하한경계": float(low),
                "상한경계": float(high),
                "이상치수": outlier_count,
                "이상치비율": outlier_count / values.shape[0] * 100 if values.shape[0] else 0.0,
            }
        )
    return pd.DataFrame(rows).sort_values(["이상치수", "검사항목명"], ascending=[False, True], kind="stable").head(max_rows)


def _delta_check(dataset: TameDataset, ctx: ChemistryContext, comparator_policy: str, age_bin_width: int, max_rows: int) -> pd.DataFrame:
    work = _work(dataset, ctx, comparator_policy).dropna(subset=["_test", "_patient_id", "_result_at", "_result"])
    if work.empty:
        return pd.DataFrame()
    work = work.sort_values(["_patient_id", "_test", "_result_at"], kind="stable")
    group = work.groupby(["_patient_id", "_test"], dropna=False, observed=False)
    work["이전결과"] = group["_result"].shift(1)
    work["이전검사시간"] = group["_result_at"].shift(1)
    work["절대변화"] = (work["_result"] - work["이전결과"]).abs()
    work["변화율"] = work["절대변화"] / work["이전결과"].abs().replace(0, pd.NA) * 100
    work["시간간격"] = (work["_result_at"] - work["이전검사시간"]).dt.total_seconds() / 3600
    deltas = work.dropna(subset=["이전결과", "절대변화"]).copy()
    if deltas.empty:
        return pd.DataFrame()
    result = pd.DataFrame(
        {
            "등록번호": deltas["_patient_id"],
            "검사항목명": deltas["_test"],
            "검사시간": deltas["_result_at"],
            "보고값": deltas["_result"],
            "이전결과": deltas["이전결과"],
            "이전검사시간": deltas["이전검사시간"],
            "절대변화": deltas["절대변화"],
            "변화율": deltas["변화율"],
            "시간간격": deltas["시간간격"],
        }
    )
    result = result.sort_values(["절대변화", "변화율"], ascending=[False, False], kind="stable").head(max_rows).reset_index(drop=True)
    result.insert(0, "순위", range(1, len(result) + 1))
    return result


def _missing_quality(dataset: TameDataset, ctx: ChemistryContext, comparator_policy: str, age_bin_width: int, max_rows: int) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for column in dataset.columns:
        counts = state_counts(dataset.df[column.name])
        abnormal = sum(count for state, count in counts.items() if state != "VALUE")
        rows.append(
            {
                "컬럼": column.name,
                "태그": "::".join(column.tags),
                "값셀수": int(counts["VALUE"]),
                "비정상셀수": int(abnormal),
                "ABSENT": int(counts["ABSENT"]),
                "NULL": int(counts["NULL"]),
                "EMPTY": int(counts["EMPTY"]),
                "WS": int(counts["WS"]),
            }
        )
    return pd.DataFrame(rows).sort_values(["비정상셀수", "컬럼"], ascending=[False, True], kind="stable").head(max_rows)


def _numeric_summary(values: pd.Series) -> pd.Series:
    values = values.astype(float).dropna()
    return pd.Series(
        {
            "대상수": int(values.shape[0]),
            "평균": float(values.mean()) if not values.empty else None,
            "표준편차": float(values.std(ddof=1)) if values.shape[0] > 1 else 0.0,
            "최소값": float(values.min()) if not values.empty else None,
            "Q1": float(values.quantile(0.25)) if not values.empty else None,
            "중앙값": float(values.median()) if not values.empty else None,
            "Q3": float(values.quantile(0.75)) if not values.empty else None,
            "최대값": float(values.max()) if not values.empty else None,
            "P2_5": float(values.quantile(0.025)) if not values.empty else None,
            "P97_5": float(values.quantile(0.975)) if not values.empty else None,
        }
    )


def _group_numeric_summary(work: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for key, group in work.groupby(keys, dropna=False, observed=False):
        if not isinstance(key, tuple):
            key = (key,)
        row = {column: value for column, value in zip(keys, key)}
        row.update(_numeric_summary(group["_result"]).to_dict())
        rows.append(row)
    columns = keys + ["대상수", "평균", "표준편차", "최소값", "Q1", "중앙값", "Q3", "최대값", "P2_5", "P97_5"]
    return pd.DataFrame(rows, columns=columns)


def _tat_summary(work: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for key, group in work.groupby(keys, dropna=False, observed=False):
        if not isinstance(key, tuple):
            key = (key,)
        values = group["_tat_minutes"].astype(float)
        row = {column: value for column, value in zip(keys, key)}
        row.update(
            {
                "건수": int(values.shape[0]),
                "평균TAT분": float(values.mean()),
                "중앙값TAT분": float(values.median()),
                "P90TAT분": float(values.quantile(0.9)),
                "P95TAT분": float(values.quantile(0.95)),
                "최소TAT분": float(values.min()),
                "최대TAT분": float(values.max()),
            }
        )
        rows.append(row)
    return pd.DataFrame(rows).sort_values(keys, kind="stable").reset_index(drop=True)


def _result_dataset(
    table: pd.DataFrame,
    source_dataset: TameDataset,
    *,
    mode: str,
    options: dict,
    warnings: list[str],
) -> TameDataset:
    safe_table = table.copy() if table is not None else pd.DataFrame()
    safe_table = safe_table.where(pd.notna(safe_table), None)
    columns = merged_column_specs([_header(name) for name in safe_table.columns])
    safe_table.columns = [column.name for column in columns]
    meta = {
        "INFO": {"DESCRIPTION": f"Clinical chemistry analysis output: {mode}"},
        "SETTINGS": {"VALIDATE_ERROR": "REPORT"},
        "WORKS": {"DEFAULT": ["DESCRIBE"]},
        "PLUGIN": {
            "NAME": "CHEMISTRY_ANALYSIS",
            "MODE": mode,
            "SOURCE_PATH": source_dataset.source_path or "",
            "WARNINGS": warnings,
            "OPTIONS": dict(options or {}),
        },
        "VISUALIZATIONS": _visualizations_for(mode),
    }
    return TameDataset(
        df=safe_table,
        columns=columns,
        meta=meta,
        schema={},
        job={},
        raw_sections={},
        source_path=source_dataset.source_path,
    )


def _visualizations_for(mode: str) -> dict[str, dict[str, Any]]:
    charts: dict[str, dict[str, Any]] = {
        "ITEM_COUNTS": {"TYPE": "BAR", "TITLE": "검사항목별 건수", "X": "검사항목명", "Y": "건수"},
        "RESULT_SUMMARY": {"TYPE": "BAR", "TITLE": "검사항목별 중앙값", "X": "검사항목명", "Y": "중앙값"},
        "RESULT_HISTOGRAM": {"TYPE": "BAR", "TITLE": "검사항목별 결과 분포", "X": "구간", "Y": "건수", "SERIES": "검사항목명", "MAX_POINTS": 80},
        "INSTRUMENT_BIAS": {"TYPE": "BAR", "TITLE": "장비별 중앙값 차이", "X": "장비명", "Y": "중앙값차이", "SERIES": "검사항목명"},
        "TAT_BY_TEST": {"TYPE": "BAR", "TITLE": "검사항목별 중앙값 TAT", "X": "검사항목명", "Y": "중앙값TAT분"},
        "TAT_BY_INSTRUMENT": {"TYPE": "BAR", "TITLE": "장비별 중앙값 TAT", "X": "장비명", "Y": "중앙값TAT분", "SERIES": "검사항목명"},
        "DAILY_WORKLOAD": {"TYPE": "LINE", "TITLE": "일별 검사량", "X": "날짜", "Y": "건수", "SERIES": "검사항목명"},
        "HOURLY_WORKLOAD": {"TYPE": "LINE", "TITLE": "시간대별 검사량", "X": "시간", "Y": "건수", "SERIES": "검사항목명"},
        "AGE_SEX_RESULT": {"TYPE": "BAR", "TITLE": "연령/성별 중앙값", "X": "연령그룹", "Y": "중앙값", "SERIES": "성별", "MAX_POINTS": 80},
        "OUTLIERS_IQR": {"TYPE": "BAR", "TITLE": "IQR 이상치 수", "X": "검사항목명", "Y": "이상치수"},
        "DELTA_CHECK": {"TYPE": "BAR", "TITLE": "반복검사 절대 변화 상위", "X": "순위", "Y": "절대변화", "SERIES": "검사항목명", "MAX_POINTS": 50},
        "MISSING_QUALITY": {"TYPE": "BAR", "TITLE": "컬럼별 비정상 셀 수", "X": "컬럼", "Y": "비정상셀수"},
    }
    return {"MAIN": charts.get(mode, {"TYPE": "BAR", "TITLE": mode, "X": "", "Y": ""})}


def _header(name: str) -> str:
    tags: dict[str, tuple[str, ...]] = {
        "원본결과컬럼": ("SOURCE", "RESULT", "CATEGORY"),
        "검사항목명": ("TESTNAME", "CATEGORY"),
        "장비명": ("INSTRUMENT", "CATEGORY"),
        "등록번호": ("ID(patient)", "STR"),
        "검사시간": ("DATETIME",),
        "이전검사시간": ("DATETIME",),
        "날짜": ("DATE",),
        "시간": ("HOUR", "NUM"),
        "순위": ("RANK", "NUM"),
        "건수": ("COUNT", "NUM"),
        "대상수": ("N", "NUM"),
        "평균": ("MEAN", "NUM"),
        "표준편차": ("SD", "NUM"),
        "최소값": ("MIN", "NUM"),
        "Q1": ("PERCENTILE(25)", "NUM"),
        "중앙값": ("MEDIAN", "PERCENTILE(50)", "NUM"),
        "Q3": ("PERCENTILE(75)", "NUM"),
        "최대값": ("MAX", "NUM"),
        "P2_5": ("PERCENTILE(2.5)", "NUM"),
        "P97_5": ("PERCENTILE(97.5)", "NUM"),
        "평균TAT분": ("MEAN", "DURATION", "NUM"),
        "중앙값TAT분": ("MEDIAN", "DURATION", "NUM"),
        "P90TAT분": ("PERCENTILE(90)", "DURATION", "NUM"),
        "P95TAT분": ("PERCENTILE(95)", "DURATION", "NUM"),
        "최소TAT분": ("MIN", "DURATION", "NUM"),
        "최대TAT분": ("MAX", "DURATION", "NUM"),
        "비정상셀수": ("COUNT", "NUM"),
        "이상치수": ("COUNT", "NUM"),
        "이상치비율": ("PERCENT", "NUM"),
        "하한경계": ("REF_LOW", "NUM"),
        "상한경계": ("REF_HIGH", "NUM"),
        "중앙값차이": ("BIAS", "MEDIAN", "NUM"),
        "중앙값비율": ("RATIO", "MEDIAN", "NUM"),
        "이전결과": ("RESULT", "NUM"),
        "보고값": ("RESULT", "NUM"),
        "절대변화": ("DELTA", "NUM"),
        "변화율": ("DELTA", "PERCENT", "NUM"),
        "시간간격": ("DURATION", "NUM"),
    }
    if name in tags:
        return f"[[{'::'.join(tags[name])}]]{name}"
    if any(token in name for token in ("값", "평균", "중앙", "편차", "최소", "최대", "Q1", "Q3", "P2", "P9", "TAT", "차이", "율", "경계", "변화", "간격")):
        return f"[[NUM]]{name}"
    return str(name)


def _interval_label(interval: Any) -> str:
    if hasattr(interval, "left") and hasattr(interval, "right"):
        return f"{float(interval.left):g}-{float(interval.right):g}"
    return str(interval)


def _text(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None

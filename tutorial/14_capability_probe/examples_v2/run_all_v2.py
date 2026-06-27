"""2차 예제 20개(Q01~Q10 전처리 + D01~D10 분석)를 실행·검증한다.

1차(examples/run_all.py)와 의도적으로 겹치지 않는 ACTION 타입과 분석 모드만 사용한다.
모든 예제는 컬럼명을 직접 쓰지 않고 태그(tag:<ROLE> / TAGS)로만 대상을 고른다.
원본(한글 컬럼명)과 컬럼명 변경(영어) 두 조건에서 같은 META/태그로 동일 결과가 나오는지 본다.

실행 (어느 위치에서든):
    python3 tutorial/14_capability_probe/examples_v2/run_all_v2.py

종료 코드 0 = 전체 PASS, 1 = 하나 이상 실패.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROBE_DIR = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(os.path.dirname(PROBE_DIR))
sys.path.insert(0, os.path.join(REPO_ROOT, "tametools", "src"))
os.chdir(REPO_ROOT)

from tametools.actions import execute_action  # noqa: E402
from tametools.io import read_tame  # noqa: E402
from tametools.models import ColumnSpec, TameDataset  # noqa: E402
from tametools.plugin_base import run_plugin  # noqa: E402
from tametools.tags import build_header  # noqa: E402

PROBE = "tutorial/14_capability_probe/clinical_chem.tame"
PRE_META = "tutorial/14_capability_probe/examples_v2/preprocessing.v2.meta.tame"
ROC = "tutorial/14_capability_probe/examples/roc_labeled.tame"

results: list[tuple[str, str, bool, str]] = []

RENAME_MAP = {
    "등록번호": "patient_code", "검체번호": "specimen_code", "성별": "sex_value",
    "생년월일": "birth_date", "나이": "age_years", "채취일시": "collected_at",
    "접수일시": "received_at", "보고일시": "reported_at", "검사항목명": "analyte",
    "보고값": "measurement", "단위": "unit_text", "참고치하한": "reference_low",
    "참고치상한": "reference_high", "장비": "analyzer", "수집기관": "site_group",
    "진료과": "department_code", "점수": "risk_score", "질환여부": "outcome_flag",
}


def check(group: str, title: str, ok: bool, detail: str) -> None:
    results.append((group, title, bool(ok), detail))


def renamed_columns(dataset: TameDataset) -> TameDataset:
    rename = {old: new for old, new in RENAME_MAP.items() if old in dataset.df.columns}
    frame = dataset.df.rename(columns=rename)
    columns = [
        ColumnSpec(
            original_header=build_header(rename.get(column.name, column.name), column.tags),
            name=rename.get(column.name, column.name),
            tags=column.tags,
        )
        for column in dataset.columns
    ]
    return dataset.replace(df=frame, columns=columns, raw_sections={})


def last_col(ds: TameDataset) -> ColumnSpec:
    return ds.dataset.columns[-1] if hasattr(ds, "dataset") else ds.columns[-1]


# --------------------------------------------------------------- 전처리 10개 (ACTION)

def preprocessing(pre: TameDataset, group: str) -> None:
    def run(name: str):
        return execute_action(pre, name)

    o = run("Q01_ADD_BATCH")
    c = o.dataset.columns[-1]
    check(group, "Q01 상수 컬럼 추가", c.tags == ("BATCH", "CATEGORY") and set(o.dataset.df[c.name]) == {"2026Q2"},
          f"새 컬럼 태그={c.tags}")

    o = run("Q02_CONCAT_KEY")
    c = o.dataset.columns[-1]
    ok = c.name == "분석키" and c.tags == ("ID", "STR") and o.dataset.df[c.name].str.contains("::").all()
    check(group, "Q02 식별자 결합", ok, f"태그={c.tags}, 예시={o.dataset.df[c.name].iloc[0]}")

    o = run("Q03_SPLIT_DATETIME")
    cols = {c.name: c.tags for c in o.dataset.columns}
    ok = cols.get("채취날짜") == ("DATE",) and cols.get("채취시각") == ("TIME",) \
        and o.dataset.df["채취시각"].str.contains(":").all()
    check(group, "Q03 컬럼 분할", ok, f"날짜예시={o.dataset.df['채취날짜'].iloc[0]}")

    o = run("Q04_KEEP_CORE")
    names = [c.name for c in o.dataset.columns]
    has_tag = lambda t: o.dataset.first_column_with_tag(t) is not None
    ok = all(has_tag(t) for t in ("ID", "ITEM", "RESULT", "REF_LOW", "REF_HIGH")) \
        and o.dataset.first_column_with_tag("INSTRUMENT") is None
    check(group, "Q04 핵심 컬럼만 유지", ok, f"남은 컬럼수={len(names)}")

    o = run("Q05_DROP_OPERATIONAL")
    ok = all(o.dataset.first_column_with_tag(t) is None for t in ("INSTRUMENT", "GROUP", "WARD")) \
        and o.dataset.first_column_with_tag("RESULT") is not None
    check(group, "Q05 운영 컬럼 제거", ok, f"남은 컬럼수={len(o.dataset.columns)}")

    o = run("Q06_WIDE_BY_ITEM")
    item_cols = [c.name for c in o.dataset.columns if c.name != o.dataset.columns[0].name]
    ast_meta = o.dataset.column_metadata("AST")
    flagged = run_plugin(o.dataset, "ABNORMAL_FLAG", o.dataset.meta, "Q06_FLAG", {"MODE": "FLAG"})
    flag_cols = [c.name for c in flagged.dataset.columns_with_tag("FLAG")] if flagged.dataset is not None else []
    ok = {"AST", "ALT", "GGT", "ALP", "TP", "Cr"} <= set(item_cols) \
        and ast_meta.get("PIVOT_CONTEXT", {}).get("UNIT") == "U/L" \
        and ast_meta.get("PIVOT_CONTEXT", {}).get("REF_HIGH") == "40" \
        and "AST_이상플래그" in flag_cols
    check(group, "Q06 long→wide", ok, f"펼친 항목수={len(item_cols)}, 행={len(o.dataset.df)}, AST메타={ast_meta.get('PIVOT_CONTEXT', {})}, 플래그={len(flag_cols)}")

    o = run("Q07_LONG_REF_BOUNDS")
    vals = set(o.dataset.df["참고치경계"].unique())
    ok = vals and len(o.dataset.df) == 2 * len(pre.df)
    check(group, "Q07 wide→long", ok, f"경계값={sorted(str(v) for v in vals)}, 행={len(o.dataset.df)}")

    o = run("Q08_ROUND_RESULT")
    rcol = o.dataset.first_column_with_tag("RESULT").name
    series = o.dataset.df[rcol].astype(str)
    numeric = series[~series.str.startswith("<") & (series != "")]
    ok = (~numeric.str.contains(r"\.")).all() and series.str.startswith("<").any()
    check(group, "Q08 결과 반올림", ok, "정수화+비교자 보존")

    o = run("Q09_SORT_PATIENT_TIME")
    pid = o.dataset.first_column_with_tag("ID(patient)").name
    ok = list(o.dataset.df[pid]) == sorted(o.dataset.df[pid], key=str) and len(o.dataset.df) == len(pre.df)
    check(group, "Q09 정렬", ok, f"행={len(o.dataset.df)}")

    o = run("Q10_CONVERT_CR")
    status_counts = dict(zip(o.table["status"], o.table["count"]))
    ok = status_counts.get("CONVERTED", 0) > 0 and o.dataset.first_column_with_tag("RESULT") is not None
    check(group, "Q10 단위 변환", ok, f"변환={status_counts.get('CONVERTED', 0)}건")


# --------------------------------------------------------------- 분석 10개 (PLUGIN)

def analysis(ds: TameDataset, dr: TameDataset, group: str) -> None:
    def chem(mode, **opt):
        return run_plugin(ds, "CHEMISTRY_ANALYSIS", ds.meta, mode, {"MODE": mode, **opt})

    o = chem("ITEM_COUNTS")
    check(group, "D01 항목별 건수", o is not None and len(o.table) >= 6, f"항목={len(o.table)}")

    o = chem("RESULT_SUMMARY")
    cols = set(o.table.columns)
    # 출력 컬럼명이 한글 고정(평균/중앙값)이라 영어/태그로 참조 불가 → 검토 포인트로 기록
    check(group, "D02 결과 요약통계", {"평균", "중앙값"} <= cols and len(o.table) >= 6,
          f"통계컬럼수={len(cols)}, 항목={len(o.table)}")

    o = chem("INSTRUMENT_BIAS")
    check(group, "D03 장비간 편향", o is not None and len(o.table) > 0, f"행={len(o.table)}")

    o = chem("DELTA_CHECK")
    check(group, "D04 델타체크", o is not None and len(o.table) >= 0, f"행={len(o.table)}")

    o = chem("AGE_SEX_RESULT")
    check(group, "D05 연령·성별 결과", o is not None and len(o.table) > 0, f"행={len(o.table)}")

    o = chem("MISSING_QUALITY")
    check(group, "D06 결측·품질", o is not None and len(o.table) > 0, f"행={len(o.table)}")

    o = chem("DAILY_WORKLOAD")
    check(group, "D07 일별 업무량", o is not None and len(o.table) > 0, f"일수={len(o.table)}")

    o = run_plugin(ds, "QC_ANALYSIS", ds.meta, "SIGMA", {"MODE": "SIGMA"})
    check(group, "D08 QC 시그마", o is not None and len(o.table) > 0, f"항목={len(o.table)}")

    o = chem("OUTLIERS_IQR")
    check(group, "D09 항목별 이상치 탐지", o is not None and len(o.table) > 0, f"행={len(o.table)}")

    o = run_plugin(dr, "ROC_ANALYSIS", dr.meta, "CURVE", {"MODE": "CURVE", "POSITIVE": "1"})
    cols = set(o.table.columns) if o.table is not None else set()
    check(group, "D10 ROC 곡선", len(cols) > 0 and len(o.table) > 1, f"점={len(o.table)}, 열={sorted(cols)[:3]}")


def main() -> int:
    pre = read_tame(PROBE, meta_path=PRE_META)
    ds = read_tame(PROBE)
    dr = read_tame(ROC)

    preprocessing(pre, "전처리/원본")
    preprocessing(renamed_columns(pre), "전처리/컬럼명 변경")
    analysis(ds, dr, "분석/원본")
    analysis(renamed_columns(ds), renamed_columns(dr), "분석/컬럼명 변경")

    width = max(len(t) for _, t, _, _ in results)
    current = ""
    passed = 0
    for group, title, ok, detail in results:
        if group != current:
            print(f"\n[{group}]")
            current = group
        mark = "PASS" if ok else "FAIL"
        passed += ok
        print(f"  {mark}  {title.ljust(width)}  {detail}")
    print(f"\n총 {passed}/{len(results)} PASS")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

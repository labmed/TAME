"""데이터 전처리 10개 + 임상검사 분석 10개 예제를 모두 실행하고 결과를 검증한다.

이 스크립트는 두 가지 용도를 동시에 만족한다.
1) 예제 실행 데모: 20개 예제가 실제로 무엇을 하는지 한 번에 보여준다.
2) 회귀 검증: 각 예제의 기대 결과를 assert해 추후 코드 수정 후에도 동작을 확인한다.

실행 (어느 위치에서든):
    python3 tutorial/14_capability_probe/examples/run_all.py

종료 코드 0 = 전체 PASS, 1 = 하나 이상 실패.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROBE_DIR = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(os.path.dirname(PROBE_DIR))
sys.path.insert(0, os.path.join(REPO_ROOT, "tametools", "src"))
os.chdir(REPO_ROOT)  # JOIN의 상대경로(RIGHT) 해석을 위해 루트로 이동

from tametools.actions import execute_action  # noqa: E402
from tametools.io import read_tame  # noqa: E402
from tametools.models import ColumnSpec, TameDataset  # noqa: E402
from tametools.plugin_base import run_plugin  # noqa: E402
from tametools.tags import build_header  # noqa: E402

PROBE = "tutorial/14_capability_probe/clinical_chem.tame"
PRE_META = "tutorial/14_capability_probe/examples/preprocessing.meta.tame"
PAIRED = "tutorial/14_capability_probe/examples/paired_methods.tame"
ROC = "tutorial/14_capability_probe/examples/roc_labeled.tame"

results: list[tuple[str, str, bool, str]] = []

RENAME_MAP = {
    "등록번호": "patient_code",
    "검체번호": "specimen_code",
    "성별": "sex_value",
    "생년월일": "birth_date",
    "나이": "age_years",
    "채취일시": "collected_at",
    "접수일시": "received_at",
    "보고일시": "reported_at",
    "검사항목명": "analyte",
    "보고값": "measurement",
    "단위": "unit_text",
    "참고치하한": "reference_low",
    "참고치상한": "reference_high",
    "장비": "analyzer",
    "수집기관": "site_group",
    "진료과": "department_code",
    "점수": "risk_score",
    "질환여부": "outcome_flag",
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


def first_tagged_name(dataset: TameDataset, tag: str) -> str:
    column = dataset.first_column_with_tag(tag)
    if column is None:
        raise AssertionError(f"missing tag: {tag}")
    return column.name


# --------------------------------------------------------------- 전처리 10개 (ACTION)

def preprocessing(pre: TameDataset, group: str) -> None:
    def run_action(name: str):
        return execute_action(pre, name)

    o = run_action("P01_DEDUP")
    # 키(등록번호+검사항목명+채취일시) 기준 중복 제거: 주입한 완전중복 15 + 같은 키를 공유하는
    # 결측 결과행 등이 함께 제거되어 41행이 줄어든다.
    check(group, "P01 중복 제거", int(o.table["removed"].iloc[0]) == 41,
          f"removed={int(o.table['removed'].iloc[0])} (기대 41, 키 기준)")

    o = run_action("P02_DROP_MISSING")
    removed = len(pre.df) - len(o.dataset.df)
    check(group, "P02 결측행 제거", removed == 20, f"removed={removed} (기대 20)")

    o = run_action("P03_FILTER_HIGH")
    n = len(o.dataset.df)
    check(group, "P03 수치 필터(>100)", n == 57, f"rows={n} (기대 57)")

    o = run_action("P04_DERIVE_RATIO")
    ratio = o.dataset.first_column_with_tag("RATIO")
    has = ratio is not None and ratio.tags == ("RATIO", "NUM")
    check(group, "P04 산술 파생", has, f"새 컬럼={ratio.name if ratio else '(missing)'}")

    o = run_action("P05_AGE_FROM_BIRTH")
    visit_age = o.dataset.first_column_with_tag("AGE(visit)")
    ok = visit_age is not None and visit_age.tags == ("AGE(visit)", "NUM") and o.dataset.df[visit_age.name].notna().any()
    check(group, "P05 날짜 파생(연령)", ok, f"{visit_age.name if visit_age else '(missing)'} 컬럼 생성")

    o = run_action("P06_IMPUTE_RESULT")
    filled = int(o.table["filled"].iloc[0])
    check(group, "P06 결측 대치", filled == 20, f"filled={filled} (기대 20)")

    o = run_action("P07_OUTLIER_FILTER")
    removed = int(o.table["removed"].iloc[0])
    check(group, "P07 이상치 제거", removed >= 1, f"removed={removed} (>=1)")

    o = run_action("P08_RECODE_DEPT")
    ward_col = first_tagged_name(o.dataset, "WARD")
    vals = set(o.dataset.df[ward_col].unique())
    ok = {"내과", "외과", "응급의학과"} <= vals and not ({"IM", "GS", "EM"} & vals)
    check(group, "P08 값 재코딩", ok, f"진료과 값={sorted(vals)}")

    o = run_action("P09_JOIN_DEMO")
    ok = o.dataset.first_column_with_tag("INSURANCE_TYPE") is not None and len(o.dataset.df) == len(pre.df)
    check(group, "P09 태그 키 조인", ok, "INSURANCE_TYPE 컬럼 결합")

    o = run_action("P10_BIN_AGE")
    age_group = first_tagged_name(o.dataset, "AGE_GROUP")
    vals = set(o.dataset.df[age_group].unique())
    check(group, "P10 구간 범주화", {"<40", "40-59", "60+"} <= vals, f"연령군={sorted(vals)}")


# --------------------------------------------------------------- 분석 10개 (PLUGIN)

def analysis(ds: TameDataset, dp: TameDataset, dr: TameDataset, group: str) -> None:
    o = run_plugin(ds, "REFERENCE_INTERVAL", ds.meta, "RI", {})
    check(group, "C01 참고치 산출", o is not None and len(o.table) > 0, f"행={len(o.table)}")

    o = run_plugin(ds, "ABNORMAL_FLAG", ds.meta, "FLAG", {"MODE": "FLAG"})
    flags = set(o.dataset.df["이상플래그"].unique())
    check(group, "C02 이상결과 플래그", {"H", "L", "N"} <= flags, f"플래그={sorted(flags)}")

    o = run_plugin(ds, "ABNORMAL_FLAG", ds.meta, "RATE", {"MODE": "RATE", "GROUP_BY_TAGS": ["GROUP"]})
    check(group, "C03 이상률 집계", "abnormal_rate" in o.table.columns and len(o.table) > 0, f"행={len(o.table)}")

    o = run_plugin(dp, "METHOD_COMPARISON", dp.meta, "MC", {})
    row = o.table.set_index("test").loc["AST"]
    ok = abs(float(row["pb_slope"]) - 1.05) < 0.1
    check(group, "C04 방법비교 회귀", ok, f"AST PB기울기={row['pb_slope']} (기대≈1.05)")

    o = run_plugin(ds, "GROUP_TEST", ds.meta, "GT", {})
    check(group, "C05 그룹 검정", "p_value" in o.table.columns and len(o.table) > 0, f"검정={o.table['method'].iloc[0]}")

    o = run_plugin(ds, "CORRELATION", ds.meta, "CORR", {})
    check(group, "C06 상관분석", o is not None and len(o.table) > 0, f"쌍={len(o.table)}")

    o = run_plugin(ds, "QC_ANALYSIS", ds.meta, "QC", {"MODE": "PRECISION"})
    ok = "cv_percent" in o.table.columns and float(o.table["cv_percent"].iloc[0]) > 0
    check(group, "C07 QC 정밀도(CV%)", ok, f"항목={len(o.table)}")

    o = run_plugin(ds, "RESULT_TREND", ds.meta, "TREND", {"PERIOD": "M"})
    check(group, "C08 시계열 추세", "moving_avg" in o.table.columns and len(o.table) > 1, f"기간행={len(o.table)}")

    o = run_plugin(dr, "ROC_ANALYSIS", dr.meta, "ROC", {"POSITIVE": "1"})
    auc = float(o.table["auc"].iloc[0])
    check(group, "C09 진단성능(ROC)", auc > 0.8, f"AUC={auc} (>0.8)")

    o = run_plugin(ds, "CHEMISTRY_ANALYSIS", ds.meta, "TAT", {"MODE": "TAT_BY_TEST"})
    check(group, "C10 TAT 분석", o is not None and len(o.table) > 0, f"항목={len(o.table)}")


def main() -> int:
    pre = read_tame(PROBE, meta_path=PRE_META)
    ds = read_tame(PROBE)
    dp = read_tame(PAIRED)
    dr = read_tame(ROC)

    preprocessing(pre, "전처리/원본")
    preprocessing(renamed_columns(pre), "전처리/컬럼명 변경")
    analysis(ds, dp, dr, "분석/원본")
    analysis(renamed_columns(ds), renamed_columns(dp), renamed_columns(dr), "분석/컬럼명 변경")
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

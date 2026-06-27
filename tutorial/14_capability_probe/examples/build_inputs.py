"""20개 예제 실행에 필요한 부가 입력 파일을 결정론적으로 생성한다.

주 데이터는 상위 폴더의 clinical_chem.tame(= ../make_dataset.py 산출물)를 그대로 쓰고,
여기서는 특수 예제용 보조 입력만 만든다.

- demographics.csv     : JOIN(P10) 예제용 인구통계 마스터 (등록번호 키, 호환용)
- demographics.tame    : JOIN(P10) 예제용 태그 기반 인구통계 마스터
- paired_methods.tame  : METHOD_COMPARISON(C9) 예제용 2개 장비 페어드 측정
- roc_labeled.tame     : ROC_ANALYSIS(C10) 예제용 점수+이진 라벨

실행: python3 build_inputs.py  (이 폴더에서)
"""
from __future__ import annotations

import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
PROBE = os.path.join(HERE, "..", "clinical_chem.tame")


def _patient_ids() -> list[str]:
    """clinical_chem.tame에서 등록번호 목록을 읽어 demographics 키를 맞춘다."""
    ids: list[str] = []
    seen: set[str] = set()
    with open(PROBE, encoding="utf-8") as fh:
        in_data = False
        header_seen = False
        for line in fh:
            line = line.rstrip("\n")
            if line == "<DATA>":
                in_data = True
                continue
            if line == "</DATA>":
                break
            if in_data:
                if not header_seen:
                    header_seen = True
                    continue
                pid = line.split("\t")[0]
                if pid and pid not in seen:
                    seen.add(pid)
                    ids.append(pid)
    return ids


def build_demographics() -> None:
    ids = _patient_ids() or [f"P{i:04d}" for i in range(1, 121)]
    plans = ["plan_1", "plan_2"]
    regions = ["region_1", "region_2", "region_3", "region_4"]
    lines = ["등록번호,보험유형,거주지역"]
    tame_rows = []
    for index, pid in enumerate(ids):
        plan = plans[index % 2]
        region = regions[index % len(regions)]
        lines.append(f"{pid},{plan},{region}")
        tame_rows.append(f"{pid}\t{plan}\t{region}")
    with open(os.path.join(HERE, "demographics.csv"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    tame = (
        "<META>\n"
        "[INFO]\n"
        "DESCRIPTION = \"태그 기반 JOIN 예제용 인구통계 마스터\"\n"
        "\n"
        "[INFO.SCENARIO]\n"
        "PURPOSE = \"왼쪽 데이터의 컬럼명이 바뀌고 여러 ID 계열 태그가 있어도 ID(patient) 태그로 조인한다.\"\n"
        "</META>\n"
        "<DATA>\n"
        "[[ID(patient)::STR]]patient_key\t[[INSURANCE_TYPE::CATEGORY]]insurance_type\t[[RESIDENCE_REGION::CATEGORY]]residence_region\n"
        + "\n".join(tame_rows)
        + "\n</DATA>\n"
    )
    with open(os.path.join(HERE, "demographics.tame"), "w", encoding="utf-8") as fh:
        fh.write(tame)


def build_paired_methods() -> None:
    random.seed(20260624)
    header = "[[ID(patient)::STR]]등록번호\t[[ITEM::CATEGORY]]검사항목명\t[[INSTRUMENT::CATEGORY]]장비\t[[RESULT::<NUM>]]보고값"
    rows = []
    # AST: 두 장비 간 관계 y = 1.05x + 2, GGT: y = 0.98x - 1 (서로 다른 bias)
    specs = {"AST": (1.05, 2.0, 12.0, 80.0), "GGT": (0.98, -1.0, 25.0, 90.0)}
    for i in range(80):
        pid = f"P{i:04d}"
        for item, (slope, intercept, lo, hi) in specs.items():
            x = random.uniform(lo, hi)
            y = slope * x + intercept + random.gauss(0, 3)
            rows.append(f"{pid}\t{item}\ttest_analyzer_2\t{x:.1f}")
            rows.append(f"{pid}\t{item}\ttest_analyzer_1\t{max(0.1, y):.1f}")
    text = (
        "<META>\n"
        "[INFO]\n"
        "DESCRIPTION = \"방법비교(Passing-Bablok/Deming/Bland-Altman) 예제: 동일 검체 2개 장비 측정\"\n"
        "\n"
        "[INFO.SCENARIO]\n"
        "PURPOSE = \"컬럼명이 바뀌어도 ID(patient)/ITEM/INSTRUMENT/RESULT 태그만으로 방법비교를 실행한다.\"\n"
        "</META>\n"
    )
    text += "<DATA>\n" + header + "\n" + "\n".join(rows) + "\n</DATA>\n"
    with open(os.path.join(HERE, "paired_methods.tame"), "w", encoding="utf-8") as fh:
        fh.write(text)


def build_roc_labeled() -> None:
    random.seed(7)
    header = "[[RESULT::NUM]]점수\t[[LABEL::CATEGORY]]질환여부"
    rows = []
    for i in range(200):
        disease = i % 2
        score = random.gauss(70 if disease else 42, 12)
        rows.append(f"{max(0.0, score):.1f}\t{'1' if disease else '0'}")
    text = (
        "<META>\n"
        "[INFO]\n"
        "DESCRIPTION = \"진단 성능(ROC) 예제: 연속 점수와 이진 라벨\"\n"
        "\n"
        "[INFO.SCENARIO]\n"
        "PURPOSE = \"컬럼명이 바뀌어도 RESULT/LABEL 태그만으로 ROC 분석을 실행한다.\"\n"
        "</META>\n"
    )
    text += "<DATA>\n" + header + "\n" + "\n".join(rows) + "\n</DATA>\n"
    with open(os.path.join(HERE, "roc_labeled.tame"), "w", encoding="utf-8") as fh:
        fh.write(text)


if __name__ == "__main__":
    build_demographics()
    build_paired_methods()
    build_roc_labeled()
    print("built: demographics.csv, demographics.tame, paired_methods.tame, roc_labeled.tame")

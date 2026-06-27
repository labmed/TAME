"""REFERENCE_INTERVAL 검증용 합성 데이터 생성기 (결정론적).

알려진 분포에서 샘플링해 ground truth(참값)를 만든다.
- 정규분포: 모수적 기준(mean±1.96sd)과 비교 가능
- 로그정규(우편향): 비모수 방법이 필요한 시나리오
- 성별/연령 층화, 소표본(신뢰도 등급), 이상치 주입

ground truth(이상치 미제거 시 기대 분위수)는 플러그인과 동일한 pandas .quantile(linear)로 계산해
expected.json 에 저장한다. 재실행: python3 tutorial/18_ri_validation/make_ri_datasets.py
"""
from __future__ import annotations
import json
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parent
SEED = 20260625


def _write(path: Path, headers: list[str], rows: list[tuple]) -> None:
    lines = ["<META>", "[INFO]", 'DESCRIPTION = "REFERENCE_INTERVAL 검증용 합성 데이터"',
             "[SETTINGS]", 'VALIDATE_ERROR = "REPORT"', 'CRR = "VALUE"', "</META>",
             "<DATA>", "\t".join(headers)]
    lines += ["\t".join(str(x) for x in r) for r in rows]
    lines.append("</DATA>")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _q(values: list[float]) -> tuple[float, float]:
    s = pd.Series(values, dtype=float)
    return float(s.quantile(0.025)), float(s.quantile(0.975))


def _parametric(values: list[float]) -> tuple[float, float]:
    arr = np.array(values, dtype=float)
    mean = float(np.mean(arr))
    sd = float(np.std(arr, ddof=1))
    z_low = NormalDist().inv_cdf(0.025)
    z_high = NormalDist().inv_cdf(0.975)
    return mean + z_low * sd, mean + z_high * sd


def _log_parametric(values: list[float]) -> tuple[float, float]:
    arr = np.log(np.array(values, dtype=float))
    mean = float(np.mean(arr))
    sd = float(np.std(arr, ddof=1))
    z_low = NormalDist().inv_cdf(0.025)
    z_high = NormalDist().inv_cdf(0.975)
    return float(np.exp(mean + z_low * sd)), float(np.exp(mean + z_high * sd))


def _robust(values: list[float]) -> tuple[float, float]:
    arr = np.array(values, dtype=float)
    median = float(np.median(arr))
    mad = float(np.median(np.abs(arr - median)))
    sd = 1.4826 * mad
    if sd <= 0:
        sd = max(float(np.quantile(arr, 0.75) - np.quantile(arr, 0.25)) / 1.349, 0.0)
    z_low = NormalDist().inv_cdf(0.025)
    z_high = NormalDist().inv_cdf(0.975)
    return median + z_low * sd, median + z_high * sd


def build() -> dict:
    rng = np.random.default_rng(SEED)
    expected: dict = {}

    # 1) 분포: 정규 vs 로그정규(왜도), 단일그룹 대표본
    glu = [round(float(v), 1) for v in rng.normal(100, 10, 2000)]
    ferr = [round(float(v), 1) for v in rng.lognormal(np.log(50), 0.5, 2000)]
    _write(OUT / "ri_dist.tame", ["[[ITEM::TESTNAME::CATEGORY]]검사명", "[[RESULT::NUM]]결과값"],
           [("GLU_NORMAL", v) for v in glu] + [("FERR_SKEW", v) for v in ferr])
    expected["GLU_NORMAL"] = _q(glu)
    expected["GLU_NORMAL_analytic"] = (100 - 1.959964 * 10, 100 + 1.959964 * 10)
    expected["GLU_NORMAL_parametric"] = _parametric(glu)
    expected["FERR_SKEW"] = _q(ferr)
    expected["FERR_SKEW_log_parametric"] = _log_parametric(ferr)

    # 2) 성별/연령 층화
    rows = []
    m = [round(float(v), 1) for v in rng.normal(110, 8, 1000)]
    f = [round(float(v), 1) for v in rng.normal(90, 8, 1000)]
    rows += [("TST_SEX", "M", int(a), v) for a, v in zip(rng.integers(20, 70, 1000), m)]
    rows += [("TST_SEX", "F", int(a), v) for a, v in zip(rng.integers(20, 70, 1000), f)]
    for lo, mu in [(20, 70), (40, 90), (60, 110)]:
        ages = rng.integers(lo, lo + 20, 800)
        vals = rng.normal(mu, 12, 800)
        rows += [("ALP_AGE", "M", int(a), round(float(v), 1)) for a, v in zip(ages, vals)]
    _write(OUT / "ri_partition.tame",
           ["[[ITEM::TESTNAME::CATEGORY]]검사명", "[[SEX::CATEGORY::BY]]성별", "[[AGE]]나이", "[[RESULT::NUM]]결과값"], rows)
    expected["TST_SEX_M"] = _q(m)
    expected["TST_SEX_F"] = _q(f)

    # 3) 소표본(신뢰도 등급): 30=insufficient, 60=review, 200=ok (MIN_N=120 기준)
    rows = []
    for name, n in [("SMALL30", 30), ("MID60", 60), ("BIG200", 200)]:
        rows += [(name, round(float(v), 1)) for v in rng.normal(100, 10, n)]
        expected[name + "_n"] = n
    _write(OUT / "ri_n.tame", ["[[ITEM::TESTNAME::CATEGORY]]검사명", "[[RESULT::NUM]]결과값"], rows)

    # 4) 이상치 주입(Tukey 검증): 정규 500 + 극단치 5개
    base = [round(float(v), 1) for v in rng.normal(100, 10, 500)]
    extreme = [400, 420, 5, 3, 450]
    _write(OUT / "ri_outliers.tame", ["[[ITEM::TESTNAME::CATEGORY]]검사명", "[[RESULT::NUM]]결과값"],
           [("OUTL", v) for v in base + extreme])
    expected["OUTL_with"] = _q(base + extreme)
    expected["OUTL_clean"] = _q(base)

    # 5) robust/rank CI 검증: review n의 오염, rank CI n=120
    robust_base = [round(float(v), 1) for v in rng.normal(100, 8, 57)]
    robust_values = robust_base + [250.0, 280.0, 300.0]
    rank_values = [float(v) for v in range(1, 121)]
    _write(
        OUT / "ri_robust_rank.tame",
        ["[[ITEM::TESTNAME::CATEGORY]]검사명", "[[RESULT::NUM]]결과값"],
        [("ROBUST_REVIEW", v) for v in robust_values] + [("RANK120", v) for v in rank_values],
    )
    expected["ROBUST_REVIEW_nonparametric"] = _q(robust_values)
    expected["ROBUST_REVIEW_robust"] = _robust(robust_values)
    expected["RANK120_rank_ci"] = {
        "ref_low_ci": (1.0, 7.0),
        "ref_high_ci": (114.0, 120.0),
    }

    (OUT / "expected.json").write_text(json.dumps(expected, indent=2), encoding="utf-8")
    return expected


if __name__ == "__main__":
    exp = build()
    print("생성:", *(p.name for p in OUT.glob("*.tame")))
    for k, v in exp.items():
        if isinstance(v, int):
            formatted = v
        elif isinstance(v, dict):
            formatted = v
        else:
            formatted = tuple(round(x, 2) for x in v)
        print(f"  {k}: {formatted}")

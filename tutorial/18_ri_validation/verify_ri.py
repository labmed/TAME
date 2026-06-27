"""REFERENCE_INTERVAL 플러그인을 실제 tametools CLI로 실행해 ground truth와 대조 검증한다.

실행: PYTHONPATH=tametools/src python3 tutorial/18_ri_validation/verify_ri.py
종료코드 0 = 핵심 검증 모두 통과.
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
D = ROOT / "tutorial" / "18_ri_validation"
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "tametools" / "src")}
sys.path.insert(0, str(ROOT / "tametools" / "src"))
import numpy as _np  # noqa: E402
from tametools.config import ci_get  # noqa: E402
from tametools.io import read_tame  # noqa: E402
from tametools.plugin_base import run_plugin  # noqa: E402

EXP = json.loads((D / "expected.json").read_text(encoding="utf-8"))
results: list[tuple[str, bool, str]] = []


def run_ri_dataset(src: str, *opts: str) -> "object":
    out = Path(tempfile.mkdtemp()) / "ri.tame"
    cmd = [sys.executable, "-m", "tametools", "run-plugin", str(D / src), "REFERENCE_INTERVAL",
           "--output", str(out), *sum(([f"--option", o] for o in opts), [])]
    p = subprocess.run(cmd, cwd=ROOT, env=ENV, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stdout + p.stderr)
    return read_tame(out)


def run_ri(src: str, *opts: str) -> "object":
    return run_ri_dataset(src, *opts).df


def level(df, test, lvl="TESTNAME"):
    return df[(df["test_name"] == test) & (df["group_level"] == lvl)]


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


def approx(a, b, tol=1e-6):
    return abs(float(a) - float(b)) <= tol


# 1) 코어 비모수 분위수 정확성 (기본 OUTLIER_METHOD=NONE → 표본 분위수와 정확 일치)
none = run_ri("ri_dist.tame", "MIN_N=120")
check("default OUTLIER_METHOD=NONE", set(none["outlier_method"]) == {"NONE"}, sorted(set(none["outlier_method"])))
for test in ("GLU_NORMAL", "FERR_SKEW"):
    r = level(none, test).iloc[0]
    exp = EXP[test]
    ok = approx(r["ref_low"], exp[0]) and approx(r["ref_high"], exp[1])
    check(f"core quantile {test}", ok, f"plugin=[{float(r['ref_low']):.2f},{float(r['ref_high']):.2f}] expected={tuple(round(x,2) for x in exp)}")

# 2) 성별 층화 정확성
part = run_ri("ri_partition.tame")
for sex, key in (("male", "TST_SEX_M"), ("female", "TST_SEX_F")):
    s = level(part, "TST_SEX", "TESTNAME+SEX")
    r = s[s["sex_group"] == sex].iloc[0]
    exp = EXP[key]
    ok = approx(r["ref_low"], exp[0]) and approx(r["ref_high"], exp[1])
    check(f"sex partition {sex}", ok, f"plugin=[{float(r['ref_low']):.2f},{float(r['ref_high']):.2f}] expected={tuple(round(x,2) for x in exp)}")

# 3) 연령 층화: 상승 추세(중앙값 단조 증가 경향)
age = level(part[part["test_name"] == "ALP_AGE"], "ALP_AGE", "TESTNAME+AGE").sort_values("age_low")
medians = [float(x) for x in age["median"]]
check("age trend increasing", medians[0] < medians[-1] and len(age) >= 5, f"medians={[round(m,1) for m in medians]}")

# 4) 신뢰도 등급
ndf = run_ri("ri_n.tame", "MIN_N=120")
grades = {r["test_name"]: r["reliability"] for _, r in ndf[ndf["group_level"] == "TESTNAME"].iterrows()}
check("reliability SMALL30=insufficient_n", grades.get("SMALL30") == "insufficient_n", grades.get("SMALL30"))
check("reliability MID60=review", grades.get("MID60") == "review", grades.get("MID60"))
check("reliability BIG200=ok", grades.get("BIG200") == "ok", grades.get("BIG200"))

# 5) 부트스트랩 CI가 점추정을 브래킷
b = ndf[(ndf["test_name"] == "BIG200") & (ndf["group_level"] == "TESTNAME")].iloc[0]
lo_in = float(b["ref_low_ci90_low"]) <= float(b["ref_low"]) <= float(b["ref_low_ci90_high"])
hi_in = float(b["ref_high_ci90_low"]) <= float(b["ref_high"]) <= float(b["ref_high_ci90_high"])
check("bootstrap CI brackets estimates", lo_in and hi_in,
      f"low {float(b['ref_low']):.2f} in [{float(b['ref_low_ci90_low']):.2f},{float(b['ref_low_ci90_high']):.2f}]")

# 6) 이상치 주입: 명시적 TUKEY가 극단치 제거
ot_dataset = run_ri_dataset("ri_outliers.tame", "OUTLIER_METHOD=TUKEY")
ot = ot_dataset.df
o = ot[ot["group_level"] == "TESTNAME"].iloc[0]
check("tukey removes injected outliers", int(o["outliers_removed"]) >= 5, f"removed={int(o['outliers_removed'])}")

# 7) METHOD 옵션: 정규형/로그정규형 모수 산출
param = run_ri("ri_dist.tame", "METHOD=PARAMETRIC", "BOOTSTRAP_N=0")
r = level(param, "GLU_NORMAL").iloc[0]
exp = EXP["GLU_NORMAL_parametric"]
check("parametric GLU_NORMAL", approx(r["ref_low"], exp[0]) and approx(r["ref_high"], exp[1]),
      f"plugin=[{float(r['ref_low']):.2f},{float(r['ref_high']):.2f}] expected={tuple(round(x,2) for x in exp)}")

log_param = run_ri("ri_dist.tame", "METHOD=LOG_PARAMETRIC", "BOOTSTRAP_N=0")
r = level(log_param, "FERR_SKEW").iloc[0]
exp = EXP["FERR_SKEW_log_parametric"]
check("log-parametric FERR_SKEW", approx(r["ref_low"], exp[0]) and approx(r["ref_high"], exp[1]),
      f"plugin=[{float(r['ref_low']):.2f},{float(r['ref_high']):.2f}] expected={tuple(round(x,2) for x in exp)}")

# 8) ROBUST: review 등급에서 median/MAD 기반 후보 참고구간
robust = run_ri("ri_robust_rank.tame", "METHOD=ROBUST", "BOOTSTRAP_N=0")
r = level(robust, "ROBUST_REVIEW").iloc[0]
exp = EXP["ROBUST_REVIEW_robust"]
nonparam_exp = EXP["ROBUST_REVIEW_nonparametric"]
check("robust review method label", r["method"] == "robust_median_mad_0.025_0.975", r["method"])
check("robust review limits", approx(r["ref_low"], exp[0]) and approx(r["ref_high"], exp[1]),
      f"plugin=[{float(r['ref_low']):.2f},{float(r['ref_high']):.2f}] expected={tuple(round(x,2) for x in exp)}")
check("robust less sensitive than nonparametric", float(r["ref_high"]) < float(nonparam_exp[1]),
      f"robust_high={float(r['ref_high']):.2f}, nonparam_high={float(nonparam_exp[1]):.2f}")

# 9) RANK CI: n=120 비모수 한계가 기대 order statistic을 사용
ranked = run_ri("ri_robust_rank.tame", "CI_METHOD=RANK", "BOOTSTRAP_N=0")
r = level(ranked, "RANK120").iloc[0]
exp = EXP["RANK120_rank_ci"]
check("rank CI method label", r["ci_method"] == "rank_nonparametric_0.90", r["ci_method"])
check("rank CI lower limit order stats",
      approx(r["ref_low_ci90_low"], exp["ref_low_ci"][0]) and approx(r["ref_low_ci90_high"], exp["ref_low_ci"][1]),
      f"plugin=[{float(r['ref_low_ci90_low']):.1f},{float(r['ref_low_ci90_high']):.1f}]")
check("rank CI upper limit order stats",
      approx(r["ref_high_ci90_low"], exp["ref_high_ci"][0]) and approx(r["ref_high_ci90_high"], exp["ref_high_ci"][1]),
      f"plugin=[{float(r['ref_high_ci90_low']):.1f},{float(r['ref_high_ci90_high']):.1f}]")

# ---- 개선점 반영 확인 (FAIL 아님, 정보) ----
tukey_dataset = run_ri_dataset("ri_dist.tame", "OUTLIER_METHOD=TUKEY")
tukey = tukey_dataset.df
log_tukey = run_ri("ri_dist.tame", "OUTLIER_METHOD=LOG_TUKEY")
print("\n[개선점 반영 확인 — 기본 NONE, 명시적 TUKEY/LOG_TUKEY 비교]")
for test in ("GLU_NORMAL", "FERR_SKEW"):
    n = level(none, test).iloc[0]
    k = level(tukey, test).iloc[0]
    lk = level(log_tukey, test).iloc[0]
    nwidth = float(n["ref_high"]) - float(n["ref_low"])
    kwidth = float(k["ref_width"])
    lwidth = float(lk["ref_width"])
    shrink = (1 - kwidth / nwidth) * 100
    log_shrink = (1 - lwidth / nwidth) * 100
    print(f"  {test}: NONE=[{float(n['ref_low']):.1f},{float(n['ref_high']):.1f}] → TUKEY=[{float(k['ref_low']):.1f},{float(k['ref_high']):.1f}]"
          f"  제거 {int(k['outliers_removed'])}/{int(k['original_n'])} ({100*int(k['outliers_removed'])/int(k['original_n']):.1f}%), 폭 -{shrink:.1f}%")
    print(f"           LOG_TUKEY=[{float(lk['ref_low']):.1f},{float(lk['ref_high']):.1f}]"
          f"  제거 {int(lk['outliers_removed'])}/{int(lk['original_n'])} ({100*int(lk['outliers_removed'])/int(lk['original_n']):.1f}%), 폭 -{log_shrink:.1f}%")

ferr_tukey = level(tukey, "FERR_SKEW").iloc[0]
ferr_log_tukey = level(log_tukey, "FERR_SKEW").iloc[0]
check("LOG_TUKEY less aggressive on skew", int(ferr_log_tukey["outliers_removed"]) < int(ferr_tukey["outliers_removed"]),
      f"TUKEY={int(ferr_tukey['outliers_removed'])}, LOG_TUKEY={int(ferr_log_tukey['outliers_removed'])}")
tukey_warnings = ci_get(tukey_dataset.meta, "PLUGIN", {}).get("WARNINGS", [])
check("tukey high-removal warning", any("High outlier removal rate" in str(w) and "FERR_SKEW" in str(w) for w in tukey_warnings),
      tukey_warnings)

# 10) PARAMETRIC 정규성 경고 + 음수 하한 클램프(왜도 데이터 안전장치)
pm_ds = run_ri_dataset("ri_dist.tame", "METHOD=PARAMETRIC", "BOOTSTRAP_N=0")
ferr_pm = level(pm_ds.df, "FERR_SKEW").iloc[0]
pm_warns = ci_get(pm_ds.meta, "PLUGIN", {}).get("WARNINGS", [])
check("parametric skew warning", any("skewed data" in str(w) and "FERR_SKEW" in str(w) for w in pm_warns), "")
check("parametric negative lower clamped to 0", approx(ferr_pm["ref_low"], 0.0), f"ref_low={float(ferr_pm['ref_low']):.3f}")
check("parametric clamp warning", any("clamped to 0" in str(w) for w in pm_warns), "")

# 11) CLAMP_NONNEGATIVE=false → 음수 하한 보존(분석가 선택)
raw_df = run_ri("ri_dist.tame", "METHOD=PARAMETRIC", "BOOTSTRAP_N=0", "CLAMP_NONNEGATIVE=false")
ferr_raw = level(raw_df, "FERR_SKEW").iloc[0]
check("clamp disabled keeps negative", float(ferr_raw["ref_low"]) < 0, f"ref_low={float(ferr_raw['ref_low']):.3f}")

# 12) ROBUST 무경고 전환 → 경고 + review에만 robust 적용
rob_ds = run_ri_dataset("ri_n.tame", "METHOD=ROBUST", "BOOTSTRAP_N=0")
rob = rob_ds.df
rob_warns = ci_get(rob_ds.meta, "PLUGIN", {}).get("WARNINGS", [])
big = rob[(rob.test_name == "BIG200") & (rob.group_level == "TESTNAME")].iloc[0]
mid = rob[(rob.test_name == "MID60") & (rob.group_level == "TESTNAME")].iloc[0]
check("robust override warning", any("overridden to NONPARAMETRIC" in str(w) for w in rob_warns), "")
check("robust applies to review only", "robust" in str(mid["method"]) and "nonparametric" in str(big["method"]),
      f"MID60={mid['method']}, BIG200={big['method']}")

# 13) 검증(transference) 모드: 합격/불합격 판정
vp = level(run_ri("ri_n.tame", "VERIFY_LOW=80", "VERIFY_HIGH=120", "BOOTSTRAP_N=0"), "BIG200").iloc[0]
check("verify pass within RI", vp["verify_pass"] == "pass" and float(vp["verify_outside_rate"]) <= 0.10,
      f"outside={float(vp['verify_outside_rate']):.1%} verdict={vp['verify_pass']}")
vf = level(run_ri("ri_n.tame", "VERIFY_LOW=95", "VERIFY_HIGH=105", "BOOTSTRAP_N=0"), "BIG200").iloc[0]
check("verify fail when narrow RI", vf["verify_pass"] == "fail" and float(vf["verify_outside_rate"]) > 0.10,
      f"outside={float(vf['verify_outside_rate']):.1%} verdict={vf['verify_pass']}")

# 14) BIWEIGHT 강건 추정: 정규≈비모수, 이상치에 견고
bw = level(run_ri("ri_dist.tame", "METHOD=BIWEIGHT", "BOOTSTRAP_N=0"), "GLU_NORMAL").iloc[0]
check("biweight normal ~ nonparametric", abs(float(bw.ref_low) - 79.8) < 3 and abs(float(bw.ref_high) - 119.6) < 3,
      f"[{float(bw.ref_low):.1f},{float(bw.ref_high):.1f}]")
bwo = level(run_ri("ri_outliers.tame", "METHOD=BIWEIGHT", "BOOTSTRAP_N=0"), "OUTL").iloc[0]
check("biweight robust to injected outliers", float(bwo.ref_high) < 200, f"high={float(bwo.ref_high):.1f}")

# 15) BIWEIGHT API 검증: 내부 helper import 없이 오염 전/후 출력 안정성을 확인
_x = _np.random.default_rng(1).normal(100, 10, 5000)
_xc = _np.concatenate([_x, [1000.0, 2000.0, 5.0, 3.0]])
_bwp = Path(tempfile.mkdtemp()) / "biweight_contamination.tame"
_bw_rows = [f"CLEAN\t{v:.12g}" for v in _x]
_bw_rows.extend(f"CONTAM\t{v:.12g}" for v in _xc)
_bwp.write_text(
    "<DATA>\n[[ITEM::TESTNAME::CATEGORY]]test\t[[RESULT::NUM]]value\n"
    + "\n".join(_bw_rows)
    + "\n</DATA>",
    encoding="utf-8",
)
_bwt = run_plugin(read_tame(_bwp), "REFERENCE_INTERVAL", {}, "RI", {"METHOD": "BIWEIGHT", "BOOTSTRAP_N": 0}).table
_bw_clean = _bwt[_bwt.test_name == "CLEAN"].iloc[0]
_bw_contam = _bwt[_bwt.test_name == "CONTAM"].iloc[0]
check("biweight lower limit robust", abs(float(_bw_clean.ref_low) - float(_bw_contam.ref_low)) < 0.5,
      f"clean={float(_bw_clean.ref_low):.2f}, contaminated={float(_bw_contam.ref_low):.2f}")
check("biweight upper limit robust", abs(float(_bw_clean.ref_high) - float(_bw_contam.ref_high)) < 0.5,
      f"clean={float(_bw_clean.ref_high):.2f}, contaminated={float(_bw_contam.ref_high):.2f}")

# 16) 파티션 정당성(Harris-Boyd) — partition_tests 테이블(API 접근)
pds = read_tame(D / "ri_partition.tame")
ptab = run_plugin(pds, "REFERENCE_INTERVAL", pds.meta, "RI", {"BOOTSTRAP_N": 0}).tables["partition_tests"]
srow = ptab[(ptab.test_name == "TST_SEX") & (ptab.partition_factor == "SEX")].iloc[0]
check("partition SEX recommended (M vs F)", srow.decision == "partition_recommended" and float(srow.z) > float(srow.z_critical),
      f"z={srow.z} z_c={srow.z_critical}")
agerows = ptab[(ptab.test_name == "ALP_AGE") & (ptab.partition_factor == "AGE")]
check("partition AGE detects effect and non-effect", {"partition_recommended", "combine_ok"} <= set(agerows.decision),
      list(agerows.decision))
tst_age = ptab[(ptab.test_name == "TST_SEX") & (ptab.partition_factor == "AGE")]
check("partition AGE combine_ok when no age effect", all(d in ("combine_ok", "insufficient_n") for d in tst_age.decision),
      list(tst_age.decision))

# 17) DECIMALS 임상 반올림
dec = level(run_ri("ri_dist.tame", "DECIMALS=0", "BOOTSTRAP_N=0"), "FERR_SKEW").iloc[0]
check("decimals rounding to integer", float(dec.ref_low) == round(float(dec.ref_low)) and float(dec.ref_high) == round(float(dec.ref_high)),
      f"[{dec.ref_low},{dec.ref_high}]")

# 18) CLSI(Weibull) 순위 분위수 옵션
clsi = level(run_ri("ri_dist.tame", "QUANTILE_METHOD=CLSI", "BOOTSTRAP_N=0"), "GLU_NORMAL").iloc[0]
_gv = read_tame(D / "ri_dist.tame").df
_gvv = _gv[_gv["검사명"] == "GLU_NORMAL"]["결과값"].astype(float).to_numpy()
check("clsi quantile matches numpy weibull",
      approx(clsi.ref_low, float(_np.quantile(_gvv, 0.025, method="weibull"))) and
      approx(clsi.ref_high, float(_np.quantile(_gvv, 0.975, method="weibull"))) and "clsi" in str(clsi.method),
      str(clsi.method))

# 19) Dixon-Reed: 단일 극단치 제거(군집 다중치는 masking — Reed 규칙의 알려진 한계)
dxp = Path(tempfile.mkdtemp()) / "dx.tame"
dxp.write_text("<DATA>\n[[ITEM::TESTNAME::CATEGORY]]t\t[[RESULT::NUM]]v\n" +
               "\n".join(f"D\t{x}" for x in list(range(10, 31)) + [300]) + "\n</DATA>", encoding="utf-8")
dxr = run_plugin(read_tame(dxp), "REFERENCE_INTERVAL", {}, "RI", {"OUTLIER_METHOD": "DIXON", "BOOTSTRAP_N": 0}).table.iloc[0]
check("dixon removes single extreme", int(dxr.outliers_removed) >= 1, f"removed={int(dxr.outliers_removed)}")

# 20) CI 적정성 플래그(소표본 wide, 대표본 ok)
cad = run_ri("ri_n.tame", "CI_WIDTH_MAX_FRAC=0.25")
big_ad = cad[(cad.test_name == "BIG200") & (cad.group_level == "TESTNAME")].iloc[0]["ci_adequacy"]
small_ad = cad[(cad.test_name == "SMALL30") & (cad.group_level == "TESTNAME")].iloc[0]["ci_adequacy"]
check("ci adequacy flags small vs big", big_ad == "ok" and small_ad == "wide", f"BIG200={big_ad}, SMALL30={small_ad}")

width = max(len(n) for n, _, _ in results)
passed = sum(1 for _, ok, _ in results if ok)
print(f"\n[검증 결과] (실제 CLI run-plugin)")
for name, ok, detail in results:
    print(f"  {'PASS' if ok else 'FAIL'}  {name.ljust(width)}  {detail}")
print(f"\n총 {passed}/{len(results)} PASS")
sys.exit(0 if passed == len(results) else 1)

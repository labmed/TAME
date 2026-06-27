# 18. REFERENCE_INTERVAL 플러그인 검증

알려진 분포에서 **샘플링**해 ground truth(참값)를 만들고, 실제 `tametools run-plugin
REFERENCE_INTERVAL` 결과와 대조해 플러그인을 검증한다. 모수적(정규)·비모수적(우편향) 등 다양한
시나리오를 포함한다.

## 재현

```bash
python3 tutorial/18_ri_validation/make_ri_datasets.py          # 데이터 + expected.json 생성
PYTHONPATH=tametools/src python3 tutorial/18_ri_validation/verify_ri.py   # 실제 CLI 실행·대조
```

`verify_ri.py`는 실제 CLI를 호출하고 ground truth와 비교한다. 전부 통과하면 `총 N/N PASS`로 끝난다.

## 검증 시나리오 (예시 파일)

| 파일 | 시나리오 | 검증 포인트 |
| --- | --- | --- |
| `ri_dist.tame` | 정규 N(100,10) 2000개, 로그정규(우편향) 2000개 | 코어 비모수 분위수 정확성, 왜도 처리 |
| `ri_partition.tame` | 성별(M 110±8 / F 90±8), 연령 추세 | TESTNAME+SEX / +AGE 층화 정확성 |
| `ri_n.tame` | n=30 / 60 / 200 | 신뢰도 등급(insufficient_n / review / ok) |
| `ri_outliers.tame` | 정규 500 + 극단치 5개 | Tukey 이상치 제거 |
| `ri_robust_rank.tame` | review 표본 오염, n=120 순위 CI | `METHOD=ROBUST`, `CI_METHOD=RANK` |

## 검증 결과 (실제 실행)

- **기본 이상치 정책 안전화**: 기본 `OUTLIER_METHOD=NONE`을 확인하고, 표본 2.5/97.5 분위수와 **완전 일치**
  (GLU_NORMAL [79.8,119.6], FERR_SKEW [18.78,140.14]).
- **층화 정확**: 성별 M [95.2,127.0]/F [73.9,104.5] 참값 일치, 연령 중앙값 상승 추세 재현.
- **신뢰도 등급 정확**: 30→insufficient_n→do_not_use, 60→review, 200→ok (MIN_N=120 기준, CLSI EP28).
- **부트스트랩 CI**가 점추정을 정상 브래킷.
- **명시적 Tukey가 주입 극단치 제거**(5개 주입 → 7개 제거)하고, 제거율이 높으면 경고를 남김.
- **LOG_TUKEY가 우편향 데이터에서 raw TUKEY보다 보수적**임을 확인.
- **METHOD 옵션 검증**: `PARAMETRIC`은 정규형 sample mean±z·sd, `LOG_PARAMETRIC`은 로그정규형 한계와 일치.
- **ROBUST 옵션 검증**: `review` 등급에서 median/MAD 기반 한계가 오염된 비모수 상한보다 안정적임을 확인.
- **RANK CI 검증**: n=120에서 비모수 순위 기반 CI가 기대 order statistic을 가리킴.
- **비모수가 왜도를 올바르게 처리**: 로그정규 상한 140은 양수·비대칭. 모수적(mean±1.96sd)이면
  하한이 음수가 되어 부적절 → 비모수 기본값이 타당.

## 반영한 개선점

1. **기본 `OUTLIER_METHOD=NONE`**: 원시척도 Tukey가 우편향 참고치를 과도하게 좁히는 문제를 피한다.
2. **명시적 `OUTLIER_METHOD=TUKEY` 제거율 경고**: 기본 임계값 `OUTLIER_WARN_RATE=0.02`를 넘으면
   `OperationOutput.warnings`와 결과 META에 경고를 남긴다.
3. **`OUTLIER_METHOD=LOG_TUKEY` 추가**: 양수 우편향 검사에서 로그척도 IQR fence를 사용할 수 있다.
4. **`METHOD=NONPARAMETRIC|PARAMETRIC|LOG_PARAMETRIC|ROBUST` 추가**: 비모수, 정규형, 로그정규형, review 표본용 robust 참고한계를 선택할 수 있다.
5. **`CI_METHOD=BOOTSTRAP|RANK` 추가**: 기본 bootstrap 외에 비모수 순위 기반 CI를 선택할 수 있다.

## 임상 안전 보강 (2026-06-26)

6. **PARAMETRIC 정규성 가드**: 모수적/로그모수적 방법을 왜도 데이터(`|skewness|>SKEW_WARN`, 기본 1.0)에
   적용하면 경고를 남긴다(`SKEW_WARN`로 조정).
7. **음수 하한 클램프**: 비음수 데이터에서 하한이 0 미만이면 0으로 클램프하고 경고한다
   (`CLAMP_NONNEGATIVE=false`로 해제 가능). 강양성 분석물의 음수 참고하한 보고를 방지.
8. **ROBUST 무경고 전환 차단**: `METHOD=ROBUST`가 review 범위 밖(n≥120 또는 n<40)에서
   NONPARAMETRIC로 전환될 때 경고를 남긴다(사용자 방법 선택이 조용히 무시되지 않음).
9. **검증(transference) 모드**: 후보 참고구간을 주면 외부 비율과 EP28식 합격/불합격을 산출한다.
   - 단일검사: `--option VERIFY_LOW=80 --option VERIFY_HIGH=120`
   - 다검사: META/ API에서 `VERIFY = { GLU = [70,110], CREA = [0.6,1.2] }`
   - 임계: `VERIFY_MAX_OUTSIDE`(기본 0.10 = 20개 중 ≤2개 외부 시 합격)
   - 출력 컬럼: `verify_low/high`, `verify_n_below/above`, `verify_outside_rate`, `verify_pass`

## 임상 완성도 보강 (2026-06-26 추가)

10. **파티션 정당성 검정(Harris-Boyd)**: `partition_tests` 테이블에 성별(M vs F)·연령(인접대) 쌍별
    `z = |Δmean|/√(s_a²/n_a + s_b²/n_b)`, 임계 `z_c = 3·√(n̄/120)`, 판정
    (partition_recommended/combine_ok/insufficient_n)을 산출. 어떤 분할이 통계적으로 정당한지 안내.
11. **`METHOD=BIWEIGHT` (Tukey biweight M-추정량)**: median±MAD(대칭)보다 정밀한 강건 위치·척도
    추정(astropy 표준식, location c=6/scale c=9). 정규에서 모수적과 일치, 오염에 위치/척도 불변(검증).
12. **`DECIMALS` 임상 반올림**: 참고한계·CI를 지정 소수자리로 반올림.

## minor 보강 (2026-06-26 추가)

13. **`QUANTILE_METHOD=LINEAR|CLSI`**: CLSI는 Weibull 순위(rank=q·(n+1), EP28 순위규약). numpy
    `method='weibull'`과 일치 검증. 라벨 `nonparametric_quantile_clsi_...`.
14. **CI폭 적정성 플래그**: `ci_adequacy`(ok/wide/not_calculated) 컬럼 + 경고. 한계 CI 폭이
    `CI_WIDTH_MAX_FRAC`(기본 0.25)×참고구간폭을 넘으면 'wide'(한계 부정밀).
15. **`OUTLIER_METHOD=DIXON`**: Reed 1/3 규칙(양 극단). 단일 극단치에 적합. **다중 군집치는 masking**
    (Reed/Dixon의 알려진 한계)이므로 다중 이상치엔 TUKEY/LOG_TUKEY 권장.

## 남은 참고

- BIWEIGHT/ROBUST는 대칭 강건법 → 강한 왜도에는 NONPARAMETRIC/LOG_PARAMETRIC 권장(문서화).
- DIXON은 단일 극단치용(다중치 masking) — 문서화.

요약: 계산 정확성·층화·신뢰도·CI가 정상이고, 모수적 안전장치·검증(transference) 모드·파티션 정당성
검정·biweight 강건법·CLSI 순위 분위수·CI폭 플래그·Dixon이 갖춰져 **분석가 감독 하
establishment/verification 도구로 임상 사용 가능** 수준. (verify_ri.py 40/40 PASS, 전체 unittest 581 OK)

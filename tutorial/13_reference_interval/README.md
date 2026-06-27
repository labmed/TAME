# 13. 참고치(Reference Interval) 산출과 신뢰도 검토

이 튜토리얼은 `REFERENCE_INTERVAL` 플러그인으로 간기능 검사(AST/ALT/GGT/ALP/TP)의
참고치를 산출하고, **그 결과를 임상검사실 기준(CLSI EP28-A3c)으로 검토**하는 흐름을 다룬다.

다른 튜토리얼이 "어떻게 실행하나"에 집중한다면, 이 튜토리얼은 "산출된 참고치를 믿어도
되는가"를 함께 본다. 여기서 드러나는 한계는 명세서 V2의 개선 항목으로 이어진다.

사용 데이터는 별도 파일 없이 `01_tagged_eda/liver_function_1000.tame`를 그대로 쓴다.
(1,000행, 5개 검사항목 × 2기관, `SEX`/`AGE`/`ITEM`/`RESULT::<NUM>` 태그 부여됨)

## 1. 참고치 산출 계획 확인

```bash
tametools ri-plan \
  tutorial/01_tagged_eda/liver_function_1000.tame
```

플러그인이 어떤 태그를 참고치 축으로 인식했는지 보여준다.

```
result_columns: ['보고값']
item_column: 검사항목명
age_column: 나이
sex_column: 성별
by_columns: ['성별', '수집기관']
```

`RESULT::<NUM>`, `ITEM`, `AGE`, `SEX` 태그만으로 별도 설정 없이 분석 축이 잡힌다는 점이
태그 기반 설계의 핵심 이점이다.

## 2. 참고치 산출

```bash
tametools run-plugin \
  tutorial/01_tagged_eda/liver_function_1000.tame REFERENCE_INTERVAL \
  --output ri_result.tame
```

산출 수준은 4단계로 자동 전개된다.

- `TESTNAME` (검사항목 전체)
- `TESTNAME + SEX`
- `TESTNAME + AGE` (10세 구간)
- `TESTNAME + SEX + AGE`

각 행은 `n`, `ref_low`, `ref_high`(2.5/97.5 백분위수), `mean`, `median`, `sd`, `method`를 담는다.
결과는 `.tame`로 저장되므로 그대로 export하거나 다음 분석에 체인할 수 있다.

## 3. 결과 신뢰도 검토

플러그인은 참고치 행마다 임상검사실 검토에 필요한 신뢰도 필드를 함께 만든다.
기본값은 `METHOD = NONPARAMETRIC`, 2.5/97.5 백분위수, `MIN_N = 120`, `CI_METHOD = BOOTSTRAP`,
신뢰구간, `OUTLIER_METHOD = NONE`이다. 기본 실행은 정상적인 우편향 꼬리를 자동 제거하지 않고
원자료 기반 후보 참고구간을 만든다. 산출 수준별 대표 `n`은 아래처럼 달라진다.

| 산출 수준 | 대표 n | EP28 n≥120 충족 |
| --- | --- | --- |
| `TESTNAME` | 200 | 충족 |
| `TESTNAME + SEX` | 87~113 | 대부분 미달 |
| `TESTNAME + AGE` | 24~42 | 미달 |
| `TESTNAME + SEX + AGE` | 10~ | 크게 미달 |

출력의 `reliability`는 `ok`, `review`, `insufficient_n` 중 하나다. `ok`만 그대로 후보
참고구간으로 보고, 나머지는 검사실 책임자가 표본 수, 분할 기준, 이상치, 임상 적합성을
검토해야 한다.

## 4. 주요 출력 컬럼

```
test_name sex_group age_group   n  ref_low  ref_high  ref_low_ci90_low  ref_high_ci90_high  outliers_removed  reliability      recommendation
      AST       ALL       ALL 200    14.49     77.71             13.50               83.60                 1  ok               candidate_for_verification
      AST      male     20-29  12     ...        ...               ...                 ...                 0  insufficient_n   do_not_use_as_final_interval
```

추가로 `OperationOutput.tables`에는 다음 표가 들어간다.

- `reference_intervals`: 주 참고치 산출표
- `summary_by_test`: 검사별 산출 그룹 수와 신뢰도 요약
- `reliability_summary`: 그룹 수준별 `ok/review/insufficient_n` 집계
- `outliers`: `OUTLIER_METHOD=TUKEY` 또는 `LOG_TUKEY` 사용 시 제외된 원본 행과 값
- `limits_long`: 보고서/차트용 long-form 참고한계 표

CLI에는 `[charts]`가 표시되고, 웹앱은 결과 TAME의 `VISUALIZATIONS`를 이용해 `RI_REF_WIDTH`,
`RI_N_BY_GROUP`, `RI_HIGH_LIMIT` 차트를 보여준다.

## 5. 차트 포함 docx 보고서 생성

보고서 생성에는 `tametools[report]` extra가 필요하다.

```bash
tametools run-plugin \
  tutorial/01_tagged_eda/liver_function_1000.tame REFERENCE_INTERVAL \
  --option REPORT_PATH=/tmp/reference_interval_report.docx \
  --output ri_result.tame
```

`REPORT_PATH`를 지정하면 주 참고치 표, 신뢰도 요약표, 이상치 표, 차트가 들어간 docx가 생성된다.
반복 실행용 옵션 예시는 `report_config.meta.tame`에 있다.

## 6. 선택 옵션

```bash
tametools run-plugin \
  tutorial/01_tagged_eda/liver_function_1000.tame REFERENCE_INTERVAL \
  --option METHOD=PARAMETRIC \
  --option OUTLIER_METHOD=TUKEY
```

- `METHOD=NONPARAMETRIC`: 기본값. 표본 분위수 기반.
- `METHOD=PARAMETRIC`: 정규형 `mean ± z·sd` 기반.
- `METHOD=LOG_PARAMETRIC`: 양수 결과에 대해 로그정규형 산출.
- `METHOD=ROBUST`: `review` 등급(기본 40≤n<120) 그룹에 median/MAD 기반 후보 한계 적용. `ok` 그룹은 비모수 산출 유지.
- `CI_METHOD=BOOTSTRAP`: 기본값. 선택한 산출 method 기준 percentile bootstrap CI.
- `CI_METHOD=RANK`: `METHOD=NONPARAMETRIC` 전용 순위 기반 CI. 표본 수가 부족해 순위 CI가 정의되지 않으면 경고.
- `OUTLIER_METHOD=NONE`: 기본값. 참고치 산출 전에 자동 제거하지 않음.
- `OUTLIER_METHOD=TUKEY`: 원시척도 IQR fence. 제거율이 `OUTLIER_WARN_RATE`(기본 0.02)를 넘으면 경고.
- `OUTLIER_METHOD=LOG_TUKEY`: 양수 우편향 검사에서 로그척도 IQR fence.

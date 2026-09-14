# 참고구간 설정·평가 플러그인

`REFERENCE_INTERVAL_EP28` 또는 `RI_EP28`을 실행하면 산출법 비교, 참고한계의 신뢰구간,
이상치 기록·제거 민감도, 집단 분할 평가, 외부 참고구간의 20명 검증과 한글 보고서를 만든다.
CLSI EP28-A3c의 참고구간 설정·검증 절차를 참고한다.
검사실이 문서화해야 하는 참고집단 선정과 검사 전·분석 조건도 설정에 남긴다.

기존 `REFERENCE_INTERVAL`은 호환성을 위해 유지한다. **이 문서의 기능은 새 이름으로 실행한다.**
새 플러그인은 표본 수에 따라 선택한 방법을 임의로 다른 방법으로 바꾸지 않는다.
알고리즘의 수치 검증은 임상 사용 승인이나 CLSI의 제품 인증을 의미하지 않는다.

## 1. 빠른 실행

프로젝트 루트에서 분석·보고서 의존성을 갖춘 Python을 사용한다.

```bash
python -m pip install -e './tametools[analysis]'
python -m tametools plugins
python -m tametools analyze data.tame RI_EP28 \
  --option RESULT_IDS=calcium --option SUBJECT_ID=person \
  --option METHOD=NONPARAMETRIC --option OUTLIER_METHOD=TUKEY \
  --option OUTLIER_ACTION=FLAG --option OUTPUT_DIR=ri_study_001
```

`RESULT_IDS`, `SUBJECT_ID`는 화면의 열 이름이 아니라 `COLUMN.ID`다. 결과 열에 `RESULT`,
`NUM` 태그와 `COLUMN.<열>.ID`, `UNIT`를 선언한다. 실제 ID에 맞게 바꿔 실행한다.
`OUTPUT_DIR`는 새 경로여야 한다. 기존 보고서를 덮어쓰지 않는다.
보고서의 값은 원래 단위로 표시하며 단위 변환을 추정하지 않는다.

이 플러그인은 tametools 0.4.0의 기본 플러그인 로더에서 제공됩니다.

## 2. 설정 위치와 연구 계획

설정은 TAME의 `[RI_EP28]`, `[REFERENCE_INTERVAL_EP28]`, 실행 시 옵션 순으로 덮어쓴다.
모르는 옵션·모호한 `DIXON` 이름·잘못된 결과 ID는 오류로 처리한다.

```toml
<META>
[RI_EP28]
RESULT_IDS = ["calcium"]
SUBJECT_ID = "person"
METHOD = "NONPARAMETRIC"
METHODS = ["PARAMETRIC", "ROBUST", "LOG_PARAMETRIC"]
COVERAGE = 0.95
CI_LEVEL = 0.90
CI_METHOD = "AUTO"
QUANTILE_TYPE = 6
BOOTSTRAP_N = 2000
SEED = 20260911
OUTLIER_METHOD = "TUKEY"
OUTLIER_ACTION = "FLAG"
OUTLIER_METHODS = ["TUKEY", "REED", "HORN"]
PARTITION_BY = ["SEX", "AGE"]
SEX_ID = "sex"
AGE_ID = "age"
AGE_CUTS = [20, 40, 60, 80]
PARTITION_SCALE = "RAW"
OUTPUT_DIR = "ri_study_003"

[[RI_EP28.INCLUDE]]
COLUMN_ID = "age"
MIN = 20
MAX_EXCLUSIVE = 80

[RI_EP28.POPULATION]
REFERENCE_INDIVIDUALS_CONFIRMED = false
DESCRIPTION = "연구 모집단과 검체 종류를 기술"
SELECTION_CRITERIA = "실제 적용한 건강 선정·제외 기준을 기술"
PREANALYTICAL = "공복·채혈·자세·운동·약물·운송·보관 등의 실제 조건"
ANALYTICAL = "장비·시약·보정·추적성·QC와 검체 처리 조건"
</META>
```

외부 META 파일로 저장했다면 `analyze data.tame RI_EP28 --meta ri_config.meta.tame`으로 사용한다.
건강 참고 대상자임을 확인한 경우에만 `REFERENCE_INDIVIDUALS_CONFIRMED=true`로 설정한다.
true에는 나머지 네 문서화 필드가 모두 필요하다. 이는 사용자의 연구 기록이며 프로그램이
건강 상태나 검사법 동등성을 증명한다는 뜻이 아니다.

`INCLUDE`에는 `COLUMN_ID`와 숫자 `MIN`/`MAX_EXCLUSIVE`, `IN_NUMBERS`, 또는 `IN_TEXT`를 쓴다.
조건 목록은 AND다. 결과별 ELIGIBILITY와 RESULT_STATUS도 적용한다. 임의로 결과를 고른 뒤
유리한 구간이나 분할을 채택하는 방식은 피한다.

## 3. 참고구간 산출법 9가지

| METHOD | 계산 | 주의점 |
|---|---|---|
| NONPARAMETRIC | 관측 순서통계량의 중앙 백분위; 기본 R type 6, 선택 type 7 | type 6은 `(n+1)p` 순위 보간. 분할별 120명 이상 권고 |
| PARAMETRIC | 평균 ± 정규분포 z×표본 SD | 원 척도의 분포 가정 검토. 음수 하한을 임의로 0으로 바꾸지 않음 |
| LOG_PARAMETRIC | ln 변환 후 모수법, 원 단위로 역변환 | 모든 포함값 >0 필요 |
| BOXCOX_PARAMETRIC | 연속 최대우도 λ 적합 후 모수법·역변환 | 양수·비상수 표본. 역변환 정의역을 벗어나면 산출 불가 |
| BOXCOX_SHIFTED_PARAMETRIC | 원점 `a`와 power를 선언한 범위에서 profile 최대우도로 적합한 modified Box–Cox | `BOXCOX`의 원점·power 탐색 범위와 경계 도달을 감사. 원 연구의 비공개 적합 설정까지 같다는 뜻은 아님 |
| ROBUST | 부록 B의 반복 biweight 위치, robust 척도, 위치 불확실성, t(n−1) 한계 | median±MAD 근사와 다름. 비대칭과 MAD=0을 검토 |
| LOG_ROBUST | ln 척도에서 같은 robust 계산 후 역변환 | 양수 필요; 비대칭에 대한 대안으로 사전 지정 |
| BOXCOX_ROBUST | Box-Cox 척도에서 같은 robust 계산 후 역변환 | λ를 각 bootstrap 재표집에서도 다시 적합 |
| HARRELL_DAVIS | beta 가중 순서통계량 백분위 | 이상치에 민감. 특히 n<100에서 주의 |

`METHOD`가 주 방법이고 `METHODS`가 비교할 추가 방법이다. 표본이 작거나 분포가 맞지 않아도
자동으로 산출법을 교체하지 않는다. 계산 실패는 해당 결과 행의 `status`와 `notes`에 남는다.

EP28 9.1은 robust의 특정 최소 n을 정하지 않는다. 기본 `MIN_CALCULATE_N=3`은 계산을
시도할 하한일 뿐 연구 표본 수 권고가 아니다. n<120 경고를 없애기 위해 `MIN_N`을 낮출 수는 없다.
중앙 95% 비모수 점추정은 n<39에서 순위 해상도 부족으로 보류한다. 다른 COVERAGE에는 해당
꼬리 확률로 해상도를 계산한다. 작은 표본의 보간값을 임상적 참고한계로 오인하지 않게 한다.

ROBUST는 초기 MAD/0.6745에 비례한 1e−10 수렴 기준과 최대 1,000회 반복을 쓴다.
단위나 변환 척도가 달라도 고정 절대 오차 때문에 일찍 멈추지 않도록 한 선택이다.
R 패키지의 고정 1e−6 기준과 차이가 있으며, 원 단위 복원 시 증폭되는 차이를 검증 자료에 공개한다.
이 구현은 부록 B의 기본 알고리즘 및 명시적 변환 확장이다. 원문 Table 7의 모든 향상된
반사·꼬리별 변환 기법을 재현한다고 주장하지 않는다.

## 4. 참고한계의 신뢰구간

참고구간의 포함률 `COVERAGE=.95`와 참고한계 CI의 신뢰수준 `CI_LEVEL=.90`은 별개다.

| CI_METHOD | 적용 |
|---|---|
| AUTO | 비모수 n≥120: 순서통계량; 정규·로그정규 모수법: 모수 CI; 그 밖: bootstrap |
| RANK | 비모수법 전용. 이항분포의 꼬리 확률로 하한·상한의 순위 CI 계산 |
| PARAMETRIC | 정규·로그정규 전용. 참고한계 SE의 대표본 근사; 로그법은 CI도 역변환 |
| BOOTSTRAP | 각 방법을 재표집마다 다시 실행; percentile 또는 basic CI |
| NONE | CI 생략 사실을 표시. 정밀도가 확인된 것으로 평가하지 않음 |

비모수 n=120, 중앙 95% RI, 90% CI는 하한 순위 1–7, 상한 순위 114–120이다.
필요한 순위가 관측 범위 밖이면 끝 관측값으로 잘라 넣지 않고 해당 끝점을 미정으로 남긴다.
실제 이산 CI의 달성 신뢰수준도 기록한다. 더 높은 CI_LEVEL에는 더 많은 표본이 필요하다.

모수 CI의 SE는 `SD × sqrt((1 + z²/2)/n)`이며 referenceIntervals의 대표본 식과 대조했다.
정규성 비유의는 가정의 증명이 아니다. Shapiro–Wilk는 n=3–5,000에만 시행하고, 변환 방법은
적합한 척도에서 검정한다. 보고서의 기본 Q-Q 그림은 원 척도이며 제목에 그 사실을 표시한다.

`BOOTSTRAP_TYPE=PERCENTILE`(기본) 또는 `BASIC`, `BOOTSTRAP_N=50..20000`, `SEED`를 기록한다.
기본 반복 수는 2,000이다. 실습의 200회는 실행 예시로서 꼬리 CI 안정성이 충분하다는 뜻이 아니다.
유효 재표집이 95% 또는 50회 미만이면 CI를 보류하고, 나머지 경우도 실패 건수를 보고한다.
재표집 CI는 선택·이상치 처리 후 표본에 조건부이며 대상자 선정과 분할 선택의 불확실성을 포함하지 않는다.
R과 NumPy의 난수 생성기 및 `boot.ci` 보간법이 달라 같은 seed만으로 수치가 같아지지는 않는다.
`BOOTSTRAP_QUANTILE=TYPE7`이 기본이다. `R_BOOT`는 R `boot::boot.ci`의 normal-quantile
끝점 보간을 사용한다. `POINT_ESTIMATE=BOOTSTRAP_MEAN`은 재표집별 하한·상한의 평균을
점추정으로 사용하며, 원 논문처럼 이 규칙을 명시한 재현 분석에만 사전 지정한다.

## 5. 공개 참고인 연구 방법의 명시적 재현 옵션

케냐 IFCC 연구에서 사용한 방법 계열을 실행하려면 다음 설정을 명시한다. 이 설정은 공개
논문에서 확인할 수 있는 절차를 구현한다. 원 논문의 개별 대상자 최종 선별표, 원점 최적화
경계와 bootstrap 표본이 공개되지 않았다면 출판 수치의 완전 재현을 보장하지 않는다.

```toml
METHOD = "BOXCOX_SHIFTED_PARAMETRIC"
NORMAL_Z = 1.96
PARAMETRIC_TRIM_Z = 2.81
CI_METHOD = "BOOTSTRAP"
BOOTSTRAP_N = 50
BOOTSTRAP_TYPE = "PERCENTILE"
BOOTSTRAP_QUANTILE = "R_BOOT"
POINT_ESTIMATE = "BOOTSTRAP_MEAN"
PARTITION_DIAGNOSTICS = ["SDR_BR", "NESTED_SDR"]

[RI_EP28.BOXCOX]
ORIGIN = "FIT"
ORIGIN_GAP_BOUNDS = [0.01, 10.0]
LAMBDA_BOUNDS = [-5.0, 5.0]

[RI_EP28.LAVE]
REFERENCE_IDS = ["Alb", "Glb", "UA", "Glu", "nonHDL", "TG", "ALT", "AST", "LDH", "CK", "GGT", "CRP"]
TARGET_IDS = ["AST"]
STRATIFY_BY = ["SEX"]
METHOD = "BOXCOX_SHIFTED_PARAMETRIC"
MAX_ABNORMAL = 1
EXPANSION = 0.05
ITERATIONS = 6
MIN_N = 40
MISSING_POLICY = "EXCLUDE"
```

LAVE는 각 대상 검사 자체를 선별 패널에서 제외하고 다른 참조 검사만 센다. 매 반복에서
원 코호트를 다시 평가하며 참조 검사 구간은 동기식으로 갱신한다. 결측 패널은 정상으로 세지
않고 `ERROR` 또는 `EXCLUDE`를 반드시 정한다. `lave_membership.csv`와
`lave_iterations.csv`에 대상자별 판정과 반복별 구간을 남긴다.

`PARAMETRIC_TRIM_Z=2.81`은 변환 척도에서 한 번 주변 관측을 제외한 뒤 재적합한다.
LAVE와 개별 검사 이상치 처리는 별개이며 `parametric_trim.csv`에 제외를 기록한다.
`PARTITION_DIAGNOSTICS`에는 에티오피아 연구의 `WILCOXON`, 케냐 연구의 `SDR_BR`,
두 요인의 `NESTED_SDR`를 지정할 수 있다. `REPORTING_UNITS={"AST"=1}`처럼 보고 단위를
주면 하한·상한 차이가 보고 단위의 세 배 이상인지 함께 기록한다. 어느 진단도 자동으로
분할을 채택하지 않는다.

`NORMALITY_TESTS=["SHAPIRO", "KS_LILLIEFORS"]`는 적합 척도의 Shapiro–Wilk와
평균·분산 추정에 보정된 Lilliefors KS를 보고한다. 논문의 단순 “KS” 기술만으로 SPSS의
세부 설정까지 확정할 수 없으므로 검정 이름을 결과에 명시한다.

## 6. 이상치 설정과 제거

`OUTLIER_ACTION=FLAG`가 기본이다. `REMOVE`를 명시하면 해당 집단의 계산에서 제외하지만
입력 TAME와 원본 관측값은 보존한다. 각 집단에서 판정하므로 전체 집단과 하위 집단의
제외 대상이 달라질 수 있다. `group_membership.csv`로 실제 사용된 관측을 확인한다.

| OUTLIER_METHOD | 동작 |
|---|---|
| NONE | 이상치 판정 없음 |
| TUKEY | type 7 사분위수의 IQR fence 밖을 표시. 기본 k=1.5, 경계와 같은 값은 유지 |
| LOG_TUKEY | ln 척도 Tukey. 포함값이 모두 양수여야 함 |
| HORN | Box-Cox λ를 −2..2, 0.1 간격에서 적합한 뒤 fence 판정. R Horn 구현과 경계 포함 여부까지 비교 |
| DIXON_Q | R outliers의 양측 Q 검정. n=3..30, n에 따라 r10/r11/r21/r22. α=.01/.05/.10 |
| REED | EP28 9.2에 설명된 D/R ≥ 1/3 규칙. Dixon Q와 구분 |
| COOK | 절편만 있는 모형의 Cook distance, 임계값 min(4/n,1). R 비교용 보조 선택지 |

`DIXON`이라는 모호한 이름은 허용하지 않는다. `DIXON_Q`는 n>30에서 Reed로 바뀌지 않는다.
적용 범위를 벗어나면 해당 집단을 평가 불가로 남긴다. `TUKEY_K` 기본값은 1.5다.
Horn의 λ 탐색은 R MASS grid와 맞춘 것이며 구간 추정의 연속 Box-Cox MLE와 다르다.
Horn의 fence 경계 포함은 R 구현에 맞춘 선택이며 EP28의 일반 Tukey 엄격 경계와 구분한다.
IQR=0에서는 fence 자동 제거를 억제하고 경고한다. 양수 변환에 임의 상수를 더하지 않는다.

Reed는 제거 후 재검사를 위해 기본 `REED_ITERATIONS=10`까지 반복한다. 마지막 회차에도
표시가 생기면 추가 검토·재검사가 필요하다고 알린다. `REED_BLOCK_MAX=1`이 기본이며
2 또는 3을 지정하면 여러 극단값이 서로를 가리는 마스킹을 평가한다. 더 극단적인 값을
임시로 뺀 상태에서 가장 덜 극단적인 블록 값을 시험한다. 각 회차·비율·대상을 기록한다.
반복 검사는 꼬리를 과도하게 제거할 수 있으므로 실제 배제 원인을 확인해야 한다.

`OUTLIER_METHODS`를 지정하면 NONE·주 방법·선택 방법의 **제거 가정 민감도 표**를 추가한다.
이 표는 주 분석의 FLAG/REMOVE를 바꾸지 않으며 별도의 최적 방법 추천이나 임상 승인이 아니다.
`OUTLIER_WARN_RATE=.02`를 넘는 표시 비율은 검토 경고다.

## 7. Partition과 집단별 평가

`PARTITION_BY=["SEX","AGE", ...]`에 성별·연령 또는 사용자 지정 factor의 COLUMN.ID를 넣는다.
AGE는 years로 환산할 수 있는 AGE 메타데이터와 명시적 `AGE_CUTS`가 필요하다.
구간은 `[하한,상한)`이며 끝값은 다음 구간에 포함된다. 마지막 상한은 제외된다.
top-coded 연령보다 높은 경계로 공개자료에 없는 세부 연령을 구분하지 않는다.
성별 코드의 의미를 추측하지 않는다. CATEGORIES 또는 `SEX_MAP={"1":"남성","2":"여성"}`처럼
실제 코드북에 맞는 매핑을 선언한다.

전체, 각 factor의 단독 분할, 모든 factor의 결합 분할을 계산한다. 분할 값이 누락된 사람은
전체에 남을 수 있지만 해당 분할에서는 빠지며, 누락 분모를 기록한다. 각 검사별 기본 최대
100개 집단이다. `MAX_GROUPS`로 한도를 설정할 수 있다.

Harris–Boyd는 각 factor를 전체 및 다른 factor의 층 안에서 두 집단씩 비교한다.

`z = |평균1−평균2| / sqrt(SD1²/n1 + SD2²/n2)`

`z* = 3 sqrt((n1+n2)/240)`

z>z* 또는 큰 SD/작은 SD>1.5를 분할 검토 신호로 남긴다. 정확한 1.5 경계는 원문의
부등식 설명을 따라 엄격 초과를 사용하며 근처 값은 반올림된 수치로 결정하지 않는다.
`PARTITION_SCALE=RAW` 또는 `LOG`를 명시한다. 각 집단의 n<120, 정규성 기각, 불균형 비율도
CSV에 기록한다. 검정 결과로 자동 분할하거나 구간을 채택하지 않는다.

선택한 주 방법의 공통 구간 밖으로 나가는 집단별 하위·상위 꼬리 비율과 정확 이항 CI도 제공한다.
중앙 95%에서 4.1% 초과는 분할 검토, 3.2% 초과는 경계 신호다. 이는 **과다한 꼬리의 보조 점검**이며
Lahti 문헌의 모든 통합·분할 기준을 구현한 최종 결정기가 아니다. 다른 COVERAGE에는 이 임계값을 적용하지 않는다.
여러 집단의 동시 비교, 상대 빈도, 다중 비교와 생리학적 이유는 별도 판단이 필요하다.

## 8. 외부 참고구간의 20명 검증

새 참고구간을 20명으로 만들고 검증했다고 부르는 절차와 구별한다. 검증에는 외부 구간이
미리 정해져 있어야 하며, 동일한 단위, 출처와 비교 가능한 검사법·참고집단을 문서화한다.

```toml
[RI_EP28]
SUBJECT_ID = "person"
OUTLIER_METHOD = "REED"
OUTLIER_ACTION = "FLAG"
VERIFY_BATCH = 1

[RI_EP28.VERIFY.calcium]
LOW = 8.6
HIGH = 10.2
UNIT = "mg/dL"
REFERENCE = "여기에 실제 외부 참고구간의 출처·검사법을 기록. 숫자는 설정 문법 예시임."
```

위 숫자는 권장 칼슘 구간이 아니다. 해당 검사실의 검증 대상 구간으로 반드시 바꾼다.
`POPULATION`의 참고 대상자 확인과 네 문서화 필드도 필요하다. 결과 ID마다 구간을 지정한다.
긴 형태의 같은 RESULT_ID에 여러 검사가 섞이면 INCLUDE로 한 검사를 먼저 선택한다.

| 단계 | 외부 구간 밖 인원 | 판정 |
|---|---:|---|
| 첫 20명 | 0–2 | 통계적 검증 기준 충족 |
| 첫 20명 | 3–4 | 새로운 독립 대상자 20명으로 두 번째 검증 |
| 첫 20명 | 5 이상 | 기준 미충족, 방법·참고집단·구간 재검토 |
| 두 번째 20명 | 0–2 | 통계적 검증 기준 충족 |
| 두 번째 20명 | 3 이상 | 기준 미충족 |

한계와 같은 값은 구간 안이다. 정확히 20개의 유효한 서로 다른 대상자 결과가 필요하다.
검열값 대체·제외가 포함되면 통과 판정을 보류한다. 이상치가 표시되면 원인을 검토하고
실제 부적합 결과를 제외한 경우 새 대상자로 20개를 보충해야 한다. 자동 REMOVE로 분모를
줄인 채 검증하지 않는다. 최종 사용·보충 대상자는 INCLUDE로 명시한다.

두 번째 배치는 `VERIFY_BATCH=2`, `FIRST_BATCH_OUTSIDE=3 또는 4`,
`FIRST_BATCH_SUBJECT_IDS=[첫 20명의 ID]`가 필요하다. 중복 ID가 있으면 평가를 보류한다.
단순히 ID만 바꾼 동일인을 탐지할 수는 없으므로 연구의 실제 독립성은 검사실이 확인한다.

## 9. 입력 검증과 결과 상태

명시적 SUBJECT_ID 또는 OBSERVATION의 대상자 키가 있으면 검사별 중복·누락 ID를 거부한다.
대상자 식별이 없으면 n은 행 수이고 결과를 탐색적으로 표시한다. 여러 단위가 같은 검사에 섞인
입력, 명목형 농도 결과, 파싱 불가능한 정량값은 별도 정리 없이 통과시키지 않는다.

검열 정책은 `ERROR`가 기본이다. `EXCLUDE`는 제외하고도 검열 건수·편향 경고를 남긴다.
`VALUE`는 부등호 경계값, `RELEASED`는 선언된 원자료 플래그/대체값 연결을 사용한다.
후자의 두 값은 관측 농도 복원이 아니다. 그런 결과는 `censoring_sensitivity_only`로 표시한다.
NULL과 ABSENT를 구분하며 0으로 채우지 않는다.

| status | 의미 |
|---|---|
| exploratory_only | 건강 참고집단 또는 독립 대상자 확인이 부족한 후보 구간 |
| small_sample_review | 문서화된 집단이지만 처리 후 n<MIN_N |
| candidate_requires_verification | 계산·기본 조건을 만족한 후보; 검사실 검증·승인 필요 |
| ci_review / distribution_review | 정밀도 또는 분포 가정 검토 필요 |
| censoring_sensitivity_only | 검열 처리에 따른 탐색 결과 |
| insufficient_n / insufficient_order_resolution | 계산 분모 또는 순위 해상도 부족 |
| not_evaluable / outlier_method_not_evaluable | 수치·변환·선택한 방법 적용 불가; notes 확인 |
| degenerate_distribution | 폭 0 등의 부적합 분포 |

한 상태가 모든 주의사항을 담지는 않는다. 예를 들어 exploratory_only이면서 작은 표본·비대칭
경고가 함께 있을 수 있으므로 notes와 CI를 읽는다. 보고서 첫 부분에는 표본 수·정밀도·분포·음수
하한·분할·검증 판정에 따른 **해당 실행의 한글 해석**을 자동 작성한다.

## 10. 산출 파일과 재현

| 파일 | 내용 |
|---|---|
| reference_interval_report_ko.html | 그래프가 포함된 단독 열람 한글 보고서 |
| reference_interval_report_ko.docx | 편집·공유 가능한 한글 보고서 |
| reference_intervals.tame | 다음 작업에 연결할 수 있는 결과 데이터셋 |
| tables/reference_intervals.csv | 방법별 한계·CI·n·분포 검토·상태·상세 계산 |
| tables/outliers.csv, group_membership.csv | 표시된 원 관측과 집단별 실제 사용 여부 |
| tables/outlier_sensitivity.csv | 이상치 제거 가정에 따른 구간 변화 |
| tables/partition_tests.csv, partition_tails.csv | 분할 통계량, 집단별 꼬리·분모·CI |
| tables/verification.csv | 사전 외부 구간의 20명 검증 판정 |
| tables/input_audit.csv, input_summary.csv | 전체 입력의 사용/제외 이유와 분모 |
| effective_input.json | 적용된 설정·메타데이터·입력 해시 |
| manifest.json | 모듈 SHA-256, 실행 라이브러리 버전, 산출물 해시 |

`source_row`는 DATA 표에서 헤더를 1행으로 센 위치이며 META를 포함한 텍스트 파일의 물리적 줄 번호가 아니다.
문서에는 기본 200행, 그래프 12집단까지 표시한다. `REPORT_MAX_ROWS`, `PLOT_MAX_GROUPS`로 조정한다.
CSV에는 생략 없이 전체 결과를 보존한다. `REPORT_PATH`로 DOCX 이름을 지정할 수 있다.
PDF는 Word의 PDF 내보내기 또는 HTML의 인쇄로 만들 수 있다. 실습 폴더에 Word 변환 스크립트를 제공한다.

직접 산출법으로서 복합조사 가중치·간접 참고구간·연속 연령 곡선·한쪽 참고한계·Box-Cox 자동 shift·
다중 집단 자동 최적 분할·BCa bootstrap은 제공하지 않는다. 필요하면 별도 방법으로 연구를 설계한다.

## 11. 근거와 수치 검증

- 사용자 제공 CLSI EP28-A3c: 7–8장(선정·검사 조건), 9.1(n/정밀도), 9.2(이상치),
  9.3(partition), 9.4(산출 예제), 11.2(검증), 부록 B(robust).
- [CLSI EP28 공식 페이지](https://clsi.org/shop/standards/ep28/).
- [CRAN referenceIntervals 1.3.1](https://cran.r-project.org/package=referenceIntervals), R 4.3.1에서 실제 실행.
- [Horn, Pesce & Copeland 1998](https://pubmed.ncbi.nlm.nih.gov/9510871/).
- [Harris & Boyd 1990](https://pubmed.ncbi.nlm.nih.gov/2302771/).
- [Lahti et al. 2002](https://pubmed.ncbi.nlm.nih.gov/11805016/), [2004](https://pubmed.ncbi.nlm.nih.gov/15010425/).

핵심 R 비교, 확장 독립 공식, 부록 B, 공통 재표집 및 경계조건 시험의 실행 결과는 함께 제공되는
`REFERENCE_INTERVAL_EP28_VALIDATION_KO.md`에 기록한다. R의 GPL 패키지는 비교 환경에만 설치했고
Python 제품 모듈에 R 소스나 바이너리를 포함하지 않는다. 사용자 제공 원문 PDF도 제품 배포물에 넣지 않는다.

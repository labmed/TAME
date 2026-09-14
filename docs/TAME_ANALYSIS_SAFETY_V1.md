# TAME 분석 의미 계약과 tametools 0.3.0

2026-09-11. 로컬 참조 구현의 선택적 확장이다. TSV/TOML 컨테이너 문법은 바꾸지 않는다.
`clinical` 명령을 추가하지 않으며 `validate`, `analyze`, `integrate`를 사용한다.
임상 운영 인증, 검사법 동등성 인증, 완전한 UCUM 구현 또는 CLSI 준수 인증이 아니다.

## 1. 소비 도구의 기능 확인

```toml
[REQUIREMENTS]
SEMANTICS_VERSION = 1
[REQUIREMENTS.CAPABILITIES]
measurement_tags = 1
observation = 1
result_status = 1
reference_interval = 1
interval_censoring = 1
analysis_models = 1
integration_affine = 1
effective_input_audit = 1
```

사용한 기능만 선언할 수 있다. 알려지지 않은 이름·버전, 잘못된 의미 버전은 검증과 실행에서
거부한다. 단순 파일 읽기는 오류를 조사하기 위해 허용한다. 이 선언이 과거 소비 도구에
검증 기능을 소급해서 설치하지는 않는다. 새 의미를 사용하는 파일은 0.3.0 이상 또는 동등한
검증 기능을 확인한 소비 도구에서 처리한다. `validate` 성공만으로 임상적 적합성이 입증되지 않는다.

## 2. 관측 단위와 반복측정

```toml
[OBSERVATION]
VERSION = 1
ROW_UNIT = "measurement"
KEY_IDS = ["patient", "sample", "analyte", "repeat"]
SUBJECT_ID = "patient"
TIME_ID = "collected_at"
REPEAT_POLICY = "CLUSTER"
```

각 값은 표시명이 아닌 `COLUMN.ID`다. KEY_IDS는 비결측이고 행마다 유일해야 한다.
ROW_UNIT은 `person`, `specimen`, `measurement`, `run`이다. TIME_ID는 선택 사항이며,
선언하면 비결측·시간대 오프셋이 있는 시각이어야 한다. UTC로 비교하되 원 문자열은 유지한다.
REPEAT_POLICY는 `ERROR`, `DESCRIPTIVE`, `CLUSTER`다. SUBJECT_ID가 반복되는 경우 ERROR는
실패한다. person 행 단위에서는 같은 대상자가 반복될 수 없다. SUBJECT_ID가 반복될 때
DESCRIPTIVE는 행 단위 기술통계만 허용하며 독립 표본 추론을 거부한다. CLUSTER 추론은 아래 모형에서
같은 SUBJECT_ID를 CLUSTER_ID로 지정해야 한다. 반복측정 혼합모형과 다수준 임의효과를
자동 적합하는 것은 아니다. 선언이 없는 기존 입력은 행 단위 미선언으로 보고한다.

## 3. 척도와 선택 계약

NUM은 숫자 표기, NOMINAL/ORDINAL은 측정 척도다. 범주 코드의 평균·단위 변환은 거부한다.
ORDINAL과 <NUM>의 동시 선언, NOMINAL/ORDINAL과 MEASUREMENT.SCALE=Qn의 모순,
ANALYTE/SPECIMEN 태그와 MEASUREMENT의 모순은 통합·계획·조사·결과 역할 바인딩에서
동일하게 거부한다. 상속형 태그도 검사한다.

플러그인의 RESULT, RESULT_COLUMN, SCORE, RESULT_IDS, RESULT_TAGS는 상호 배타적이다.
`RESULT="id:stable_id"`를 사용할 수 있다. 존재하지 않는 이름·ID, 빈 명시 선택, 여러 열과
일치하는 단일 `tag:` 선택은 자동 선택으로 바뀌지 않고 실패한다. RESULT_TAGS는 AND 조건이며
모든 일치 열에 고유 ID가 필요하고 ID 순으로 정렬한다. 단위가 필요한 분석에서 UNIT_ID를
함께 선언했다면 행 단위 코드가 고정 COLUMN.UNIT와 모두 일치해야 한다. 서로 다른 단위는
먼저 분리하여 검토된 프로파일로 통합한다.

## 4. 결과 상태와 측정 맥락

```toml
[COLUMN.result.RESULT_CONTEXT]
STATUS_ID = "status"
ACCEPTED_STATUSES = ["final", "corrected"]
RAW_ID = "raw_result"
CHANGE_REFERENCE = "Reviewed correction protocol v1"
REASON_ID = "exclusion_reason"
DILUTION_ID = "dilution_factor"
HIL_IDS = ["hemolysis_index", "icterus_index", "lipemia_index"]
RETEST_ID = "retest_code"
CANCEL_ID = "cancel_code"
[COLUMN.result.RESULT_CONTEXT.EXCLUDE_WHEN]
ALL_OF = [{ COLUMN_ID="cancel_code", IN_TEXT=["cancelled"] }]
```

STATUS_ID와 ACCEPTED_STATUSES는 필수이며 나머지는 선택적이다. 상태 코드는 정확한 문자열로
비교한다. 승인 목록 밖 상태·상태 결측·제외 조건에 해당하거나 조건 변수가 결측인 행은
분석 적격에서 제외하고 사유를 남긴다. RESULT_CONTEXT는 숫자 추출 API에도 적용된다.
RAW_ID는 별도 열이며 변경 근거가 필요하다. 희석배수는 양수인지 확인하지만 다시 곱하지 않는다.
HIL·재검·취소 열을 연결하는 것만으로 기관별 임계값을 추측하지 않는다. 제외 규칙을 명시한다.
계획 보고서의 result_status 표와 유효 메타데이터에서 사유와 원본 열 연결을 확인한다.

선택적 COLUMN.MEASUREMENT_CONTEXT는 REFERENCE와 다음 중 하나 이상을 선언한다.
METHOD_ID, DEVICE_ID, CALIBRATION_ID, REAGENT_LOT_ID, CALIBRATOR_LOT_ID는 각각 고정
MEASUREMENT의 METHOD, DEVICE, CALIBRATION, REAGENT_LOT, CALIBRATOR_LOT와 행별로
비교한다. TIME_ID/VALID_FROM/VALID_TO는 함께 선언하고 시간대가 있는 `[시작, 종료)` 구간을
사용한다. 불일치·유효기간 밖·시각 결측은 미평가 사유가 된다. ID 일치가 실제 검사법 동등성을
증명하지는 않는다.

## 5. 참고구간과 검열 판정

```toml
[COLUMN.result]
ID = "result"
UNIT = "mg/L"
[COLUMN.result.REFERENCE_INTERVAL]
LOW_ID = "reference_low"
HIGH_ID = "reference_high"
UNIT = "mg/L"
POPULATION = "Reviewed target population"
REFERENCE = "Source and version"
TIME_ID = "collected_at"
VALID_FROM = "2025-01-01T00:00:00+09:00"
VALID_TO = "2027-01-01T00:00:00+09:00"
[COLUMN.result.REFERENCE_INTERVAL.APPLIES_WHEN]
ALL_OF = [{ COLUMN_ID="age_years", MIN=18 }]
```

각 하한·상한은 LOW/HIGH 상수 또는 LOW_ID/HIGH_ID 중 하나를 쓴다. 참조 열은 정량 태그와
동일 단위를 선언한다. 한쪽 한계만 있으면 나머지 방향은 무한 구간이다. 두 한계가 모두 없으면
정상 판정하지 않는다. LOW > HIGH는 오류다. POPULATION/REFERENCE는 설명이며 대상 선택은
APPLIES_WHEN으로 실행한다. 유효기간의 세 필드는 함께 선언한다. 다중 결과에 전역
REF_LOW/REF_HIGH를 묵시적으로 공용 적용하지 않는다. 기존 PIVOT_CONTEXT의 단위와 숫자
문자열 한계는 검증하여 읽는다.

ABNORMAL_FLAG는 `N/H/L/NE/IND`와 별도 사유 열을 출력한다. NE는 미평가, IND는 가능한
값의 구간이 참고구간 경계를 걸쳐 확정할 수 없는 경우다. 참고구간 5-10에서 `<5`는 L,
`<=5`는 IND, `>10`은 H이다. FLAG/RATE는 농도 대체 정책을 사용하지 않는다.
RATE의 분모 n/evaluated_n은 N/H/L만 포함한다. total_n, not_evaluated_n,
indeterminate_n을 따로 출력하며 평가 가능한 행이 없으면 비율은 NULL이다.

`ALLOW_UNDECLARED_UNITS=true`는 단위가 없던 합성 시험의 명시적 호환 옵션이다.
단위 검증을 한 것으로 표현하면 안 된다. AUTOVERIFICATION은 이 경우 REVIEW이며,
검열 결과·참고구간 미평가·불확실 결과도 PASS로 자동 처리하지 않는다. 이 플러그인은
검토용 규칙 예제이지 검사 결과의 임상 자동 승인 시스템이 아니다.

## 6. 행별 검열 구간

기존 CENSORING의 고정 왼쪽 검열은 유지한다. 새로운 CENSORING_INTERVAL은 원 공개 대체값,
구간 정보와 분석 정책을 분리한다. 결과 열은 RELEASED_ID의 숫자/결측을 그대로 유지한다.

```toml
[COLUMN.result.CENSORING_INTERVAL]
VERSION = 1
RELEASED_ID = "released_value"
KIND_ID = "censor_kind"
LOWER_ID = "censor_lower"
UPPER_ID = "censor_upper"
REFERENCE = "Released source documentation"
```

네 참조 열과 결과 열은 별개다. released/lower/upper는 동일 단위의 정량 열이다.
KIND는 EXACT, LEFT(<), LEFT_CLOSED(<=), RIGHT(>), RIGHT_CLOSED(>=), INTERVAL이다.
EXACT는 공개값과 결측 양쪽 한계, LEFT 계열은 상한만, RIGHT 계열은 하한만,
INTERVAL은 증가하는 유한한 닫힌 구간 양쪽 한계가 필요하다. KIND가 결측이면 공개값과 한계도
결측이어야 한다. CENSORING과 동시에 선언할 수 없다.

RELEASED는 실제 공개된 값을 사용한다. DELETE는 검열 행을 제외한다. VALUE는 한쪽 검열의
경계값을 쓰지만 INTERVAL에는 모호하므로 실패한다. MIDPOINT는 INTERVAL의 중간값을,
한쪽 검열에는 경계값을 쓰는 명시적 민감도 정책이다. 실제 농도를 복원하거나 검열 분포를
적합하는 추정량이 아니다. MIDPOINT는 이 구간 계약이 있는 결과만 선택한 계획에서 사용한다.
참고구간 판정은 공개 대체값이 아닌 실제 선언 구간으로 수행한다.

## 7. 검사법 비교와 QC

METHOD_COMPARISON은 METHOD_A(x/기준), METHOD_B(y/비교)를 요구한다. METHOD_COLUMN 또는
INSTRUMENT가 방법을 지정하고, KEY_IDS(권장) 또는 KEY가 검체·시점·반복 키를 지정한다.
세 번째 방법은 기본 오류이며 OTHER_METHODS=EXCLUDE로 명시할 때만 제외한다.
중복은 DUPLICATES=ERROR가 기본이다. MEAN은 명시적으로만 허용하고 중복 행 수를 보고한다.
동일 단위를 요구하며 검열값은 기본 DELETE다. DEMING_LAMBDA는 y/x 측정오차 **분산비**이며
기본 1, 양의 유한 값이어야 한다. 편향은 y-x이고 t 기반 평균 편향 95% CI를 출력한다.
EVALUATION_CONCENTRATIONS는 동일 단위의 평가 농도이며 Deming 예측 편향과 외삽 여부를
보고한다. LoA는 점추정이다. 독립 쌍을 가정하며 반복 대상자·조사설계 추론을 지원하지 않는다.
[CLSI EP09 개요](https://clsi.org/shop/standards/ep09/)와 기능 범위를 구분하며 표준 준수를 주장하지 않는다.

QC_ANALYSIS의 LEVEY_JENNINGS는 입력에서 평균/SD를 산출하는 회고적 탐색이다. 운영 규칙을
붙이지 않는다. WESTGARD는 RUN_ID/TIME_ID/LEVEL_ID/LOT_ID/INSTRUMENT_ID와 BASELINES를
요구한다. 각 기준에는 RESULT_ID, LEVEL, LOT, INSTRUMENT, MEAN, SD, UNIT, REFERENCE,
VALID_FROM, VALID_TO가 모두 필요하다. SD > 0, 각 점에 정확히 하나의 적용 기준, run/수준별
한 값, 명확한 시각 순서를 검증한다. 기준은 평가 자료에서 재추정하지 않는다.
1_3s, 같은 방향의 2_2s, **동일 run 내부의 R_4s**만 지원한다. 결측 점은 연속 규칙을 끊는다.
기준 기간/lot/장비가 바뀌면 수준별 연속 규칙도 분리한다.
[Westgard 원 설명](https://www.westgard.com/westgard-rules.html)을 참조한다.

## 8. 통합 출력과 단위

INTEGRATION_PROFILE.VERSION=1 입력을 계속 받으며 새 출력 INTEGRATION_RESULT.VERSION은 2다.
기존 버전 1 출력은 원본 복원 후 기존 열 범위로 재검증한다. 버전 2는 검토한 TARGETS의
TAGS=["ANALYTE(...)", "SPECIMEN(...)", "PANEL(...)"]를 생성·재검증에 함께 사용한다.
출처 패널을 무조건 합치지 않는다. 결과 생성 후 태그를 임의로 바꾸기보다 프로파일을 고친 뒤
원본에서 재생성한다. 태그와 COMPONENT/SPECIMEN 모순은 실패한다.

기존 질량·물질량·효소활성 농도에 Eq/L·mEq/L(equivalent_concentration),
%(reported_percent), mmol/mol(substance_ratio)을 추가한다. 서로 같은 코드는 항등 변환한다.
equivalent와 mole 사이 BRIDGE는 ION_CHARGE가 필요하며 FACTOR가 전하·단위 배율과 맞아야 한다.
[UCUM의 equivalent 설명](https://ucum.org/ucum)처럼 칼슘을 나트륨과 같은 1:1로 치환하지 않는다.
%와 mmol/mol은 자동 10배 변환하지 않는다. 성분·방향·근거가 있는 BRIDGE를 사용하며
OFFSET을 포함한 양의 기울기 변환 `target = FACTOR * source + OFFSET`을 지원한다.
CALIBRATION이 바뀌면 FROM_CALIBRATION/TO_CALIBRATION을 함께 명시해 양쪽 선언과 대조한다.
예를 들어 [NGSP의 HbA1c 관계식](https://ngsp.org/ifccngsp.asp)은 단순 차원 배율과 다르다.
이는 일반 검사법 편향을 자동 보정하라는 뜻이 아니다.

UNIT_ALIASES는 검토한 원 표기→지원 코드 매핑이다. 지원 코드를 다른 의미로 재정의할 수 없다.
__raw_unit은 별칭 정규화 전 표기를 보존한다. __status/__status_reason은 분석 적격 상태,
__censor_kind/__censor_lower/__censor_upper는 변환된 검열 정보, __released는 알려진 공개값이다.
참고구간은 결과에 ID로 연결된다. 적용 대상/기간 밖 참고구간은 통합 결과에서 결측으로 두며
원 한계·조건·상태는 SOURCE_CATALOG에 남는다. SURVEY를 버리는 통합은 계속 거부한다.

## 9. 조정 모형과 결측 민감도

ANALYSIS_PLAN.VERSION=1에 선택적 MODELS, MISSINGNESS 배열을 추가한다.
MODELS 필수: ID, Y_ID, COVARIANCE, POLICY. 선택: NUMERIC_IDS, CATEGORICAL, GROUP,
CLUSTER_ID. 적어도 하나의 공변량이 필요하며 절편을 포함한다. 범주 공변량은 COLUMN_ID,
정확한 문자열 LEVELS, REFERENCE를 모두 선언한다. 미등록 코드는 오류다. 검열 공변량의
묵시적 대체는 허용하지 않는다. 모형은 선형 연관성 추정이며 인과효과나 진단 모델이 아니다.

SAMPLE은 statsmodels OLS의 HC3 또는 군집 보정(CLUSTER, 소표본 보정, 군집 수-1 자유도)을
사용한다. SURVEY는 samplics 0.4.55 SurveyGLM의 선형 모형을 사용한다. 전체 양의 가중치
설계의 층·PSU를 유지하며 모형 비적격 행의 점수 기여만 0으로 한다. CI는 분석에 대표된
PSU 수-층 수 자유도의 t 구간이다. 기존의 singleton·설계 행 수·가중치 검증을 유지한다.
원자료, 설계, 대상, 0 가중치, 결과 결측, 공변량 제외, 완전 사례 분모를 구별한다.
랭크 부족·표본 부족·비유한 추정은 실패한다. 잔차 진단, 비선형성 검토, 교란 통제의 충분성은
연구자가 검토해야 한다. 자동 모형 선택, 로지스틱 모형, 혼합모형, 다중 대치, FPC·반복 가중치
설계는 이번 지원 범위에 포함하지 않는다.

MISSINGNESS 필수: ID, RESULT_ID, POLICY, LOWER, UPPER, ASSUMPTION. GROUP은 선택이다.
결측 결과가 명시한 범위에 있다는 **가정하의 평균 범위**를 계산한다. SURVEY에서는 가중치를
사용한다. 정책으로 제외된 검열값도 해당 정책상 결측에 포함한다. 적격 밖·0 가중치 행은
분모에서 제외한다. 이는 참고구간, 신뢰구간, 실제 대치값 또는 MAR/MNAR 판정이 아니다.
결과가 없는 이유를 자동 해결하지 않으며 가정과 결측 가중치 비율을 함께 보고한다.

## 10. 실행 증거와 호환성

보고서 effective_input.json은 실행 시점 META, SCHEMA, JOB, 열 순서/태그/상속 태그 및
지원 기능의 스냅샷이다. manifest에 해당 해시, 확정된 결과 ID와 열 메타데이터, 원 파일 해시,
셀 해시, 계획 해시, Python/플랫폼/패키지 버전, 제품 소스 모듈 해시와 결과 파일 해시를 기록한다.
메모리에서 적격 조건만 바꿔도 유효 입력 해시는 달라진다. 원 파일 해시는 실행 메타데이터
해시의 대체물이 아니다. 원 관측값은 집계 보고서에 자동 첨부하지 않으므로 완전 재실행에는
별도로 보관한 해당 셀 자료도 필요하다. 해시는 무결성 확인 수단이지 전자서명·익명화가 아니다.

기존 모호한 방법 선택, 단위 없는 판정, 고정 기준 없는 WESTGARD 호출은 수정이 필요하다.
이러한 오류를 숨기기 위해 과거 자동 동작을 복원하지 않는다. 전체 임상 운영 검증 또는
native Excel 재시험과 이번 Python 분석 검증을 혼동하지 않는다.

# 임상화학 측정 태그 보완

tametools 0.3.0의 통합·조사·플러그인 공통 척도 검사와 출력 태그 보존은
[실행 가능한 분석 의미 계약](TAME_ANALYSIS_SAFETY_V1.md)을 참조한다.

2026-09-11. 기존 TAME 컨테이너 문법을 유지하는 로컬 구현 보완이다. 새 한정자 문법을
만든 것이 아니라 기존 `TAG(qualifier)` 문법에 의미·검사 규칙·분석 선택 기능을 추가했다.

## 검토에서 확인한 문제

| 문제 | 영향 | 반영한 보완 |
|---|---|---|
| 숫자로 표기된 범주와 농도 구분 부족 | 등급 코드가 평균·분위수·상관에 들어갈 수 있음 | `NOMINAL`, `ORDINAL`을 명시하면 정량 분석에서 차단 |
| 범주 어휘 검증이 직접 `CATEGORY` 태그만 확인 | 상속형 범주의 어휘 검증·정규화 누락 | `dataset.column_has_tag`를 사용하여 상속 적용 |
| 일반적인 `RESULT`만으로 검사 의미 구분 불가 | 같은 단위의 요소와 요소질소, 혈청과 다른 검체 혼동 | `ANALYTE(...)`, `SPECIMEN(...)`과 측정 메타데이터 모순 검사 |
| 계획의 결과 선택이 ID 목록에 한정됨 | 여러 파일의 같은 분석 패널을 재사용하기 번거로움 | `RESULT_TAGS` 교집합 선택, 실제 선택 ID 기록 |
| `UNIT`의 열 역할과 고정 단위를 혼동하기 쉬움 | 단위 문자열을 태그로 쓰거나 이름·수치에서 추정 | 카탈로그 설명 수정, 고정 단위는 계속 `COLUMN.UNIT` |

## 태그와 저장 위치

| 태그 | 의미 | 실행 효과·제한 |
|---|---|---|
| `RESULT` | 측정·계산 결과의 역할 | 정량 `analyze`에는 `NUM` 또는 `<NUM>`과 단위가 추가로 필요 |
| `NOMINAL` | 명목형 범주 | `CATEGORY` 상속; 숫자 코드여도 정량 요약 제외 |
| `ORDINAL` | 명시적인 순서형 범주 | `CATEGORY` 상속; `COLUMN.LEVELS` 필수, 간격·농도는 추정하지 않음 |
| `ANALYTE(code)` | 검토된 측정 성분 코드 | `RESULT`나 `NUM`을 자동 부여하지 않음 |
| `SPECIMEN(code)` | 열 전체에 고정된 검체 맥락 | 검체 종류를 값으로 담은 열은 기존 `SAMPLE_TYPE` 사용 |
| `CONDITION(code)` | 선언된 측정 조건 | 예: `CONDITION(random)`; 개별 참여자의 공복 여부를 추론하지 않음 |
| `PANEL(name)` | 재사용할 분석 항목 묶음 | 하나의 열에 여러 패널 가능; 실제 처방 패널이라는 주장은 아님 |
| `UNIT` | 단위값을 담은 열의 역할 | wide 결과의 고정 단위는 `COLUMN.UNIT`, `UNIT(mg/dL)`로 대체하지 않음 |

`ANALYTE(urea)`와 `ANALYTE(urea_nitrogen)`은 다른 성분이다. 문자열이 같은 단위여도
같은 농도를 의미하지 않는다. 태그는 검토한 의미를 기록하며, 자동으로 LOINC를 부여하거나
검사법의 동등성·교환가능성을 입증하지 않는다.

```toml
[COLUMN.sc]
ID = "creatinine"
UNIT = "mg/dL"
TAGS = ["RESULT", "NUM", "NULLABLE", "ANALYTE(creatinine)",
        "SPECIMEN(serum)", "PANEL(chemistry)", "PANEL(renal)"]
[COLUMN.sc.MEASUREMENT]
COMPONENT = "creatinine"
SPECIMEN = "serum"
```

한 열에 서로 다른 `ANALYTE` 또는 `SPECIMEN` 한정자를 선언하면 오류다. 같은 열의
`MEASUREMENT.COMPONENT` 또는 `MEASUREMENT.SPECIMEN`도 제공했다면 태그와 일치해야 한다.
검사는 태그 상속을 포함한다. 부분적인 측정 메타데이터가 유효하다는 것이 기존 `integrate`
계약의 전체 검체·방법·단위 조건을 충족한다는 뜻은 아니다.

알려지지 않은 검체는 `SPECIMEN(not_reported)`처럼 명시하거나 근거와 함께 미선언 상태로
둔다. 단위가 불완전하면 임의로 보충하지 않는다. `ANALYTE`와 메타데이터를 모두 잘못
기입한 오류까지 탐지할 수 있는 것은 아니므로 원 코드북 검토가 선행되어야 한다.

## 범주형 결과

UCI CKD의 `al`, `su`는 원 설명에서 명목형 0–5 코드로 제공된다. 이 실습은 원 정의를 따라
`NOMINAL`로 다루며, 임의의 농도나 순서형 척도로 재해석하지 않는다.

```toml
[COLUMN.al]
ID = "al"
TAGS = ["RESULT", "NOMINAL", "SCORE_CODE", "NULLABLE"]
[CATEGORIES.SCORE_CODE]
VALUES = ["0", "1", "2", "3", "4", "5"]
STRICT = true
```

검증·정규화는 기존 `CATEGORIES` 어휘를 재사용한다. `NOMINAL` 자체는 허용 코드 목록을
발명하지 않는다. 이름만 `CATEGORY`였던 기존 상속형 사용자 태그도 이제 어휘 검증과
정규화가 적용된다. 문자열 정규화는 별도 `normalize-categories` 작업이며 원본을 남기려면
반드시 `--output`을 지정한다. 정규화 없이 `IN_TEXT` 조건을 실행하면 원문과 정확히 비교한다.

순서가 실제로 정의된 다른 자료에는 다음처럼 `ORDINAL`을 사용할 수 있다. 이 코드는
명세 예시이며 UCI 관측을 수정하거나 새로운 환자 자료로 사용한 것이 아니다.

```toml
[COLUMN.grade]
TAGS = ["RESULT", "ORDINAL", "NULLABLE"]
LEVELS = ["negative", "trace", "1+", "2+", "3+"]
```

`LEVELS`는 순서대로 나열한 고유한 비어 있지 않은 문자열이다. 미등록 값은 오류다.
`ORDINAL`과 `NOMINAL` 동시 선언 및 범주와 `<NUM>` 결합은 거부한다. `NUM`을 함께 써서
코드의 숫자 문법을 검사할 수는 있지만 평균을 허용하지는 않는다. 순서형 회귀나 순서 점수
평균을 구현했다는 의미는 아니다. 범주 빈도표의 기본 정렬은 기존 빈도 순서를 유지한다.

## 태그로 분석 항목 선택

```toml
[ANALYSIS_PLAN]
VERSION = 1
MODE = "SAMPLE"
RESULT_TAGS = ["RESULT", "PANEL(chemistry)"]
POLICIES = ["RELEASED"]
PRIMARY_POLICY = "RELEASED"
```

`RESULT_IDS` 또는 `RESULT_TAGS` 중 정확히 하나를 사용한다. `RESULT_TAGS`는 모든 태그의
교집합이며 상속을 적용한다. 선택 항목은 안정적인 `COLUMN.ID`로 정렬하므로 열 표시명과
열 순서가 달라져도 같은 항목 순서를 얻는다. 빈 선택, 잘못된 태그 토큰, 정규화 후 중복 태그,
ID·단위 누락, 범주형 결과의 정량 선택은 오류다. 첫 번째 일치 열을 임의로 선택하지 않는다.

계획 원문은 그대로 보관하고 보고서 매니페스트의 `resolved_result_ids`에 실제 선택 ID를
별도 기록한다. `PAIRS`, 집단 조건, 검열값 연결은 계속 안정적인 ID로 명시한다. 태그
선택이 결측 처리·단위 변환·표본 가중치 선택까지 자동으로 수행하는 것은 아니다.

## 검증 범위와 남은 과제

이 보완은 `describe`, EDA, `analyze`와 공용 `numeric_series_for`를 사용하는 분석 경로에
적용된다. 임의의 사용자 수식이나 pandas를 직접 사용하는 외부 플러그인의 동작을 강제하지는
않는다. 외부 소비자는 새 태그의 의미를 확인해야 하며, 예전 wheel이 새 계약을 실행한다고
가정하지 않는다. 구버전 계획 판독기는 새 `RESULT_TAGS` 옵션을 지원하지 않는다.

기존 `CENSORING` 연결, 원값·결측 토큰, 단위 변환·방법 동등성 검토 계약을 재사용한다.
검열 플래그 하나를 추가하는 것만으로 검출한계·대체값·검체 차이가 해결되지 않으므로
이들의 메타데이터를 중복 태그로 대체하지 않았다. 고정된 정상/이상 태그로 모든 기관의
참고구간을 일반화하거나, QC 규칙을 개별 환자 이상값 규칙으로 바꾸지도 않았다.

[실제 UCI 자료 한글 튜토리얼](TAME_CLINICAL_ANALYSIS_V1.md),
[UCI 자료·이용 조건](https://archive.ics.uci.edu/dataset/336/chronic+kidney+disease),
[기존 분석계획](TAME_ANALYSIS_PLAN_V1.md), [기존 통합 명세](TAME_INTEGRATION_CONTRACT_V1.md).

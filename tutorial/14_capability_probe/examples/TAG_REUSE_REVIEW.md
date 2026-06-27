# Capability Probe: Tag-Based Reuse Review

## 검증 목적

이 폴더는 같은 예제를 두 조건에서 실행한다.

1. 원본 한국어 컬럼명 데이터
2. 메모리에서 영어 컬럼명으로 바꾼 데이터

두 조건 모두 같은 태그와 같은 META로 전처리 10개, 분석 10개가 통과해야 한다.
현재 기준 결과는 `run_all.py` 기준 `40/40 PASS`이다.

## 이번에 반영한 개선

- `clinical_chem.tame`의 역할 태그를 보강했다.
  - `ID(patient)`, `ID(sample)`, `BIRTHDATE`, `COLLECTION_AT`, `TESTNAME`, `GROUP`, `WARD`
- `preprocessing.meta.tame`의 ACTION을 컬럼명 기반에서 태그 기반으로 바꿨다.
  - 예: `COLUMN = "보고값"` 대신 `COLUMN = "tag:RESULT"`
  - 예: `BY = ["검사항목명"]` 대신 `BY = ["tag:ITEM"]`
  - 예: 환자 키는 generic `tag:ID` 대신 `tag:ID(patient)` 사용
- JOIN 검증용 오른쪽 테이블을 `demographics.tame`으로 추가했다.
  - `ID(patient)`, `INSURANCE_TYPE`, `RESIDENCE_REGION` 태그를 가진다.
- `run_all.py`가 원본 데이터와 컬럼명 변경 데이터를 모두 실행하도록 확장됐다.
- JOIN ACTION이 `ON = ["tag:ID(patient)"]`를 해석할 수 있게 됐다.
- `ABNORMAL_FLAG` RATE 모드가 `GROUP_BY_TAGS` 옵션을 받을 수 있게 됐다.
- `GROUP_TEST`는 기본 그룹 컬럼 탐색에서 `GROUP`, `BY`, `SEX` 순서를 사용한다.
- tag catalog에 `INSURANCE_TYPE`, `RESIDENCE_REGION`을 추가했다.
- 괄호 한정자 태그가 공식 문법으로 승격됐다.
  - `ID(patient)`는 `ID`를 상속하고 `PATIENT_ID`와 호환된다.
  - `ID(sample)`는 `ID`를 상속하고 `SAMPLE_ID`와 호환된다.
  - `ID(hospital)`는 `ID`를 상속하고 `HOSPITAL_ID`와 호환된다.

## 확인된 한계

- `RESULT` 태그가 여러 개인 경우 모든 plugin이 `per-result` 반복을 보장하지 않는다.
  - `REFERENCE_INTERVAL`은 여러 RESULT를 처리하는 구조가 있다.
  - `METHOD_COMPARISON`, `GROUP_TEST`, `QC_ANALYSIS`, `ROC_ANALYSIS`, `RESULT_TREND`, `CHEMISTRY_ANALYSIS`는 아직 첫 RESULT 중심 로직이 남아 있다.
- ACTION의 태그 선택 문법이 일관된 계약으로 정의되어 있지 않다.
  - `COLUMN = "tag:RESULT"`와 `TAGS = ["RESULT"]`가 혼재한다.
  - `EXPR = "{tag:RESULT} / {tag:REF_HIGH}"`는 동작하지만, 같은 태그가 여러 개일 때 반복/오류 정책이 명확하지 않다.
- 오른쪽 JOIN 테이블은 태그를 가지려면 현재 TAME이 가장 확실하다.
  - CSV + sidecar meta를 JOIN에서 자동 적용하는 기능은 아직 없다.
- plugin의 역할 요구사항이 코드/문서/META에 분산되어 있다.
  - 어떤 태그가 `one`, `many`, `zero_or_one`, `per_result`인지 plugin별로 선언하는 공통 구조가 필요하다.

## 프로그램 개선 백로그

1. Plugin role contract 도입

   각 plugin이 필요한 역할 태그, cardinality, 반복 방식을 선언해야 한다.

   ```toml
   [PLUGIN_CONTRACT.METHOD_COMPARISON.ROLES.RESULT]
   TAGS = ["RESULT"]
   CARDINALITY = "one_or_more"
   ITERATION = "per_result"

   [PLUGIN_CONTRACT.METHOD_COMPARISON.ROLES.METHOD]
   TAGS = ["INSTRUMENT"]
   CARDINALITY = "one"
   ```

2. Contract 기반 validate 추가

   전역 validate는 `RESULT` 중복을 오류로 보면 안 된다.
   선택된 plugin/action contract가 `one`을 요구할 때만 중복 태그를 issue로 보고해야 한다.

3. 공통 role resolver 추가

   ACTION과 plugin이 같은 resolver를 써야 한다.

   - `resolve_one(tag)`
   - `resolve_many(tag)`
   - `resolve_any([tag1, tag2])`
   - `resolve_pair(left_tag, right_tag)`
   - ambiguity warning/error 정책
   - qualifier-aware fallback 정책: `ID(patient)` 우선, 필요 시 legacy `PATIENT_ID`, 마지막으로 generic `ID`

4. 다중 RESULT 반복 지원 확대

   기본 원칙은 `RESULT`가 여러 개면 각 RESULT에 대해 분석하고 출력에 `result_column`을 남기는 것이다.
   단일 RESULT가 필요한 특수 plugin만 contract에서 `CARDINALITY = "one"`을 선언한다.

5. ACTION 표현식의 다중 태그 정책 정리

   `DERIVE`에서 `{tag:RESULT}`가 여러 컬럼과 매칭되면 다음 중 하나를 명시해야 한다.

   - `ITERATION = "per_result"`
   - `RESULT_TAG = "RESULT"`
   - `CARDINALITY = "one"`

6. JOIN의 CSV sidecar meta 지원

   `RIGHT = "table.csv"`와 `RIGHT_META = "table.meta.tame"` 조합을 지원하면 CSV도 태그 기반 조인에 쓸 수 있다.

7. 결과 TAME provenance 강화

   결과 META에 다음을 구조화해 남겨야 한다.

   - 사용한 plugin/action
   - resolved role mapping
   - 원본 컬럼명과 변경 컬럼명
   - 입력/출력 태그
   - warning/contract validation 결과

## 추가 태그 후보

- `ENCOUNTER_ID`: 방문/내원 단위 식별자
- `VISIT_ID`: encounter와 같은 용도지만 병원 시스템에서 흔한 명칭
- `ID(encounter)`, `ID(visit)`, `ID(order)`: 새 공식 문법에서 권장되는 qualifier 태그
- `ORDER_ID`: 이미 있음, `ID(order)`의 legacy alias로 계속 지원
- `COLLECTION_AT`: 이미 있음, specimen collection timestamp
- `FACILITY`: 병원/기관/사이트 구분
- `DEPARTMENT`: 진료과 일반 태그, `WARD`와 구분 필요
- `INSURANCE_TYPE`: 이번에 추가
- `RESIDENCE_REGION`: 이번에 추가
- `QC_MATERIAL`: QC 물질명
- `QC_LOT`: QC lot 또는 material lot
- `CALIBRATION_LOT`: calibrator lot
- `METHOD_CODE`: 검사법 코드
- `REFERENCE_METHOD`: 비교 기준 방법
- `OUTCOME`: 이미 label 계열로 사용 가능, ROC/진단성능 예제에서 더 적극 사용 필요

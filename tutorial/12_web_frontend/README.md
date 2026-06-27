# 12. Web Frontend

이 튜토리얼은 FastAPI + SvelteKit 웹앱에서 `tametools`를 사용하는 흐름을 다룬다.

## 1. 실행

저장소 루트에서 백엔드를 실행한다.

```bash
python3 -m pip install ./tametools[report,web] --no-build-isolation
cd web/frontend
npm install
VITE_TAMETOOLS_API_BASE="" npm run build
```

백엔드를 실행한다.

```bash
cd web/backend
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
```

브라우저에서 `http://127.0.0.1:8765`을 연다.

## 2. 웹에서 XLSX -> TAME 변환

이미 `DATA`와 `META` 시트로 구성된 `.xlsx`는 TAME workbook으로 인식되어 시트 선택 단계 없이 바로 열린다. `DATA` 시트는 Data 탭으로, `META` 시트의 TOML 내용은 Meta 탭으로 표시된다.

일반 Excel workbook은 아래 절차로 변환한다.

1. `Open XLSX/TAME`을 누르고 `.xlsx` 파일을 선택한다.
2. 왼쪽 `Workbook Review`에서 시트별 상태를 확인한다.
3. 경고를 확인한다.
   - `Control sheet`: `META`, `SCHEMA`, `JOB` 같은 제어 시트다.
   - `Duplicate header`: 같은 헤더가 있어 TAME 변환 후 컬럼 구분이 어려울 수 있다.
   - `blank header`: 빈 헤더는 `column_n` 형태로 대체된다.
   - `formula cells`: 변환에는 workbook에 저장된 cached value가 사용된다.
4. `Conversion mode`를 선택한다.
   - `Use one worksheet as DATA`: 선택한 시트 하나를 TAME DATA로 사용한다.
   - `Merge selected worksheets`: 여러 시트를 `SHEET::STR` 태그 provenance 컬럼이 있는 하나의 DATA로 병합한다. 기본 컬럼명은 `시트명`이며 실제 컬럼명은 `META[MULTISHEET].SOURCE_COLUMN_NAME`에 기록된다.
5. `Convert to TAME workspace`를 누른다.
6. Data 탭에서 AG Grid로 값을 확인한다.
7. `Save TAME`을 눌러 `.tame` 파일로 저장한다.

## 3. 웹에서 TAME -> XLSX 변환

1. `Open XLSX/TAME`을 누르고 `.tame` 파일을 선택한다.
2. Data 탭에서 표를 확인한다.
3. Meta 탭에서 `META` TOML 내용을 확인한다.
4. `Save XLSX`를 누른다.
5. `SHEET` 태그 컬럼이 있으면 시트별 `.xlsx`로 복원되고, 없으면 `DATA` 시트 하나로 저장된다.

## 4. 컬럼 태그와 META를 추가하고 바로 EDA 실행

태그가 없는 Excel 파일을 TAME workspace로 변환한 뒤 아래처럼 진행한다.

1. 오른쪽 `Columns` 목록에서 결과값 컬럼을 선택한다.
2. `Column Tags & META`에서 `RESULT`와 `NUM` 또는 `<NUM>`을 추가한다.
   - 순수 숫자 결과값: `RESULT, NUM`
   - `<30`, `>100` 같은 comparator 포함 결과값: `RESULT, <NUM>`
3. 성별 컬럼에는 `SEX`, 나이 컬럼에는 `AGE`, 검사항목 컬럼에는 `TESTNAME` 또는 `ITEM`을 추가한다.
4. 식별자 컬럼은 가능하면 `ID`만 쓰지 말고 `ID(patient)`, `ID(sample)`, `ID(hospital)`처럼 qualifier 태그를 붙인다.
5. 같은 패널에서 `LABEL`, `UNIT`, `UCUM_UNIT`, `LOINC`, `LOCAL_CODE`, `PHI` 같은 `META[COLUMN]` 추가정보를 입력한다.
6. `Apply column tags/META`를 누른다. 웹앱은 현재 payload를 다시 정규화해 Meta 탭의 `[COLUMN]`과 태그 카탈로그도 갱신한다.
7. `Review`를 눌러 태그별 값 분포를 확인한다. `SEX` 컬럼은 `male: 150 (M: 80, male: 70)`처럼 canonical 값과 원본값 count를 함께 표시한다.
   `AGE` 컬럼은 기본 단위가 year이며, `AGE_BIN_WIDTH`가 있으면 해당 구간 단위 분포와 함께 `10a -> 10`, `2m -> 2mo`, `1day -> 1d`처럼 저장 표준 변환 결과와 변환 불가 값을 보여준다.
8. `SEX` 값이 `M`, `F`, `남`, `여`이면 `Standardize SEX`로 `male`, `female` 같은 표준값으로 바꿀 수 있다.
9. `SEX` 값이 `1`, `0`이면 오른쪽 `SEX Coding`에서 `1`과 `0`의 의미를 선택한 뒤 표준화한다.
10. `Run EDA`를 누른다.
11. 좌측 `TAME Chain`에 `*.eda.tame` 노드가 새로 생기는지 확인한다.
12. 생성된 EDA TAME의 Meta 탭에서 `[[LOG]]` 기록을 확인한다.
13. EDA 탭에서 아래 표를 확인한다.
   - `validation_issues`: 태그 기반 검증 오류
   - `describe`: 컬럼별 요약
   - `comparator_profile`: `<NUM>` comparator 분포
   - `comparator_policy_impact`: comparator 정책별 사용 가능 수치 수
   - `harmonization_preview`: threshold 통일 가능성
14. 필요하면 `Save TAME`으로 현재 선택된 TAME 파일을 저장한다.

참고치 분석은 같은 방식으로 `Reference Interval`을 누른다. 결과는 `*.reference_interval.tame` 노드로 추가되고, 계산에 사용한 작업 내용은 생성된 파일의 Meta `[[LOG]]` 항목에 기록된다.

## 5. 사용자 정의 태그와 AGE_BIN_WIDTH

웹에서 `AGE5`처럼 새 태그를 만들 수 있다.

1. 오른쪽 `TAG_DEFINITIONS` 패널에서 `New/custom tag`에 `AGE5`를 입력한다.
2. `Inherits tags`에는 `AGE`를 입력한다.
3. `AGE_BIN_WIDTH`에는 `5`를 입력한다.
4. `Apply TAG_DEFINITIONS`를 누른다.
5. 나이 컬럼을 선택하고 `AGE5` 태그를 추가한 뒤 `Apply column tags/META`를 누른다.
6. `Review`를 실행하면 AGE 분포가 5년 단위로 표시된다.

이 방식은 컬럼명이 바뀌어도 `AGE5`가 `AGE`를 상속하므로 기존 AGE 기반 분석에 계속 연결된다.

## 6. Wide RESULT와 PIVOT_CONTEXT

`AST`, `ALT`처럼 검사 항목이 여러 결과 컬럼으로 펼쳐진 wide 데이터는 컬럼별 참고치가 필요하다.

1. `AST` 컬럼에 `RESULT, NUM` 태그를 붙인다.
2. 같은 패널의 `PIVOT_CONTEXT`에 `TESTNAME = AST`, `UNIT = U/L`, `REF_LOW = 0`, `REF_HIGH = 40`을 입력한다.
3. `ALT` 컬럼도 같은 방식으로 `REF_HIGH = 41`처럼 컬럼별 참고치를 입력한다.
4. `Run Plugin`에서 `ABNORMAL_FLAG`를 실행한다.
5. 결과 데이터셋에 `AST_이상플래그`, `ALT_이상플래그`가 생성되고, 각 컬럼은 자신의 `PIVOT_CONTEXT` 참고치로 판정된다.

## 7. 헤더 태그를 바꿔 익명화된 TAME 생성

1. 원본 `.xlsx` 또는 `.tame` 파일을 연다.
2. 환자 식별자 컬럼을 선택하고 `ID(patient)` 태그를 추가한다.
3. 검체 식별자 컬럼은 `ID(sample)`, 기관 식별자 컬럼은 `ID(hospital)` 태그를 추가한다.
4. 이름 컬럼은 `NAME` 태그를 추가한다.
5. 오른쪽 `Anonymize` 영역을 확인한다.
   - `Hash tags`: 기본값 `ID,ID(patient),ID(hospital),PATIENT_ID,HOSPITAL_ID`
   - `Drop tags`: 기본값 `NAME`
   - `Salt`: 같은 원본 값이라도 프로젝트별 해시를 다르게 만들 때 입력한다.
6. `Generate anonymized TAME`을 누른다.
7. 좌측 `TAME Chain`에 `*.anonymized.tame` 노드가 새로 생기는지 확인한다.
8. Data 탭에서 `ID`, `HOSPITAL_ID` 값이 `anon_...` 형태로 바뀌고 `NAME` 컬럼이 제거되었는지 확인한다.
9. Meta 탭에서 `[[LOG]]`와 `OPERATION = "anonymize"` 기록을 확인한다.
10. EDA 탭에서 mapping table을 확인한다.
11. `Save TAME`을 눌러 익명화된 `.tame` 파일을 저장한다.

주의: mapping table은 재식별 가능성이 있는 정보이므로 연구 데이터와 분리해 접근 통제된 위치에 저장해야 한다.

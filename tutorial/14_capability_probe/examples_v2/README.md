# 실무 예제 20개 (2차) — 컬럼명 0회 사용, 태그만으로 분석

1차(`../examples/`)와 **의도적으로 겹치지 않는** ACTION 타입·분석 모드만 골라
전처리 10개(Q01~Q10) + 분석 10개(D01~D10)를 저장형으로 보관한다.

설계 원칙(엄격판):

- **기존 컬럼 선택은 컬럼명을 단 한 번도 쓰지 않는다.** 전부 `tag:<ROLE>` 또는 `TAGS`로만 고른다.
- 신규로 만드는 컬럼의 *이름*(`NAME`/`OUTPUT`/`INTO`)만 리터럴을 허용한다(이름이 없으면 컬럼을 못 만든다).
- 같은 META를 **한글 컬럼명 데이터**와 **영어 컬럼명 데이터** 두 조건에 적용해 결과가 같아야 한다.

## 한 번에 검증

```bash
python3 tutorial/14_capability_probe/examples_v2/run_all_v2.py
```

20개 × (원본 한글 / 영어 컬럼명) = **40/40 PASS**, 종료코드 0이면 회귀 없음.
데이터는 1차와 동일한 `../clinical_chem.tame`(1,277행)를 재사용한다.

## 전처리 10개 (Q01~Q10) — 1차에 없던 ACTION 타입

| ID | 목적 | ACTION 타입 | 태그 선택 |
| --- | --- | --- | --- |
| Q01 | 상수 컬럼 추가(배치 라벨) | `ADD_COLUMN` | 신규 `BATCH` |
| Q02 | 식별자 결합(복합키) | `COLUMN.CONCAT` | `tag:ID(patient)`+`tag:ID(sample)` |
| Q03 | 컬럼 분할(일시→날짜/시각) | `COLUMN.SPLIT` | `tag:COLLECTION_AT` |
| Q04 | 핵심 컬럼만 유지 | `COLUMN.INCLUDE` | `ID,ITEM,RESULT,REF_LOW,REF_HIGH` |
| Q05 | 운영 컬럼 제거 | `COLUMN.EXCLUDE` | `INSTRUMENT,GROUP,WARD` |
| Q06 | long→wide(검체별 항목) | `PIVOT_WIDER` | `tag:ID(sample)`/`tag:ITEM`/`tag:RESULT` |
| Q07 | wide→long(참고치 경계) | `PIVOT_LONGER` | `tag:REF_LOW`+`tag:REF_HIGH` |
| Q08 | 결과 반올림(비교자 보존) | `ROUND` | `tag:RESULT` |
| Q09 | 정렬(환자·시각) | `SORT` | `tag:ID(patient)`,`tag:COLLECTION_AT` |
| Q10 | 단위 변환(Cr mg/dL→umol/L) | `UNIT.CONVERT` | `tag:RESULT`/`tag:UNIT`/`tag:ITEM` |

`ACTION_PIPELINES.CLEAN_V2 = [Q09, Q02, Q04]`로 태그만으로 연속 전처리 흐름을 묶었다.

## 분석 10개 (D01~D10) — 1차에 없던 분석 모드

| ID | 목적 | 플러그인/모드 |
| --- | --- | --- |
| D01 | 항목별 건수 | `CHEMISTRY_ANALYSIS MODE=ITEM_COUNTS` |
| D02 | 결과 요약통계 | `CHEMISTRY_ANALYSIS MODE=RESULT_SUMMARY` |
| D03 | 장비 간 편향 | `CHEMISTRY_ANALYSIS MODE=INSTRUMENT_BIAS` |
| D04 | 델타 체크 | `CHEMISTRY_ANALYSIS MODE=DELTA_CHECK` |
| D05 | 연령·성별 결과 | `CHEMISTRY_ANALYSIS MODE=AGE_SEX_RESULT` |
| D06 | 결측·품질 | `CHEMISTRY_ANALYSIS MODE=MISSING_QUALITY` |
| D07 | 일별 업무량 | `CHEMISTRY_ANALYSIS MODE=DAILY_WORKLOAD` |
| D08 | QC 시그마 | `QC_ANALYSIS MODE=SIGMA` |
| D09 | 항목별 이상치 탐지 | `CHEMISTRY_ANALYSIS MODE=OUTLIERS_IQR` |
| D10 | ROC 곡선 | `ROC_ANALYSIS MODE=CURVE` (별도 라벨 데이터) |

분석은 모두 `run-plugin`이 태그 카탈로그(RESULT/ITEM/REF_*/INSTRUMENT/시각 태그 등)에서
역할 컬럼을 자동 탐색하므로 컬럼명을 옵션으로 지정하지 않는다.

## 검토에서 도출한 개선점

전수 통과(40/40)이지만, **통과한다는 것이 "태그만으로 완전히 재사용 가능"을 뜻하지는 않는다.**

1. **수정됨:** `COLUMN.CONCAT`/`ADD_COLUMN` 같은 생성 액션의 `TAGS` fallback과
   `ADD_COLUMN TEMPLATE = "{tag:ITEM}:{tag:RESULT}"` 형태의 태그 치환을 지원한다.
   `run_all_v2.py`는 Q02 생성 컬럼 태그까지 검증한다.
2. **개선됨:** 분석 결과도 `OperationOutput.dataset`과 의미 태그를 갖는다. `평균`,
   `중앙값`, `P97_5`, ROC `auc/sensitivity/specificity` 같은 컬럼을 태그로 다시 찾을 수 있다.
   남은 과제는 표시명과 stable field id를 더 명확히 분리하는 것이다.
3. **개선됨:** PIVOT 계열은 출력 META에 원본 출처와 일부 컨텍스트를 보존한다. Q06 wide의
   각 항목 컬럼은 `PIVOT_CONTEXT`에 `UNIT/REF_LOW/REF_HIGH`를 저장하며,
   `ABNORMAL_FLAG`는 이 메타만으로 wide 결과 컬럼별 `H/L/N` 플래그를 만든다.
4. **남은 한계:** 플러그인 다중 RESULT 반복이 전역 계약으로 보장되지는 않는다. `ABNORMAL_FLAG`는
   `PIVOT_CONTEXT`가 있는 wide RESULT를 반복 처리하지만, 다른 분석 플러그인은 아직 첫 RESULT 중심이다.
5. **개선됨:** `COLUMN = "tag:ID"`처럼 단일 컬럼 선택자가 여러 컬럼과 매칭되면 이제 에러가 나며,
   `TAGS = ["ID"]`처럼 다중 선택 문법은 여러 컬럼을 계속 선택한다.


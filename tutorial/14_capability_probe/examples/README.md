# 실무 예제 20개 + 컬럼명 독립성 검증

데이터 전처리 10개 + 임상검사 분석 10개를 **저장된 형태로** 보관해, 코드 수정 후에도
동일하게 재실행해 동작을 확인할 수 있게 한 폴더다. 현재 검증의 핵심은
**컬럼명이 바뀌어도 같은 태그와 META만으로 동일한 전처리/분석이 실행되는지**이다.
환자/검체처럼 여러 식별자가 함께 있을 때는 `ID(patient)`, `ID(sample)`처럼 괄호
한정자를 가진 공식 태그를 사용해 선택 대상을 좁힌다.

## 구성

| 파일 | 용도 |
| --- | --- |
| `../clinical_chem.tame` | 주 데이터(1,277행). `../make_dataset.py`로 재생성 |
| `preprocessing.meta.tame` | 전처리 예제 10개(P01~P10)의 ACTION 정의 (META 사이드카) |
| `demographics.csv` | P09 JOIN용 인구통계 마스터(호환용) |
| `demographics.tame` | P09 JOIN용 태그 기반 인구통계 마스터 |
| `paired_methods.tame` | C04 방법비교용 2개 장비 페어드 측정 |
| `roc_labeled.tame` | C09 ROC용 점수+이진 라벨 |
| `build_inputs.py` | 위 보조 입력 3종을 결정론적으로 재생성 |
| `run_all.py` | **20개 전부 실행 + 기대결과 검증(회귀 확인)** |

## 한 번에 검증 (권장)

```bash
python3 tutorial/14_capability_probe/examples/run_all.py
```

20개 예제를 원본 컬럼명과 변경 컬럼명 데이터에 각각 실행하고 기대 결과를 assert한다.
전부 통과하면 `총 40/40 PASS`와 종료코드 0. 코드 수정 후 이 명령으로 태그 기반 재사용성
회귀 여부를 바로 확인한다.

보조 입력이 없거나 갱신이 필요하면:

```bash
python3 tutorial/14_capability_probe/examples/build_inputs.py
```

## 전처리 10개 — 개별 실행 (CLI)

모두 저장소 루트에서 `--meta preprocessing.meta.tame`를 붙여 실행한다.

```bash
tametools actions tutorial/14_capability_probe/clinical_chem.tame \
  --meta tutorial/14_capability_probe/examples/preprocessing.meta.tame

tametools run-action tutorial/14_capability_probe/clinical_chem.tame P01_DEDUP \
  --meta tutorial/14_capability_probe/examples/preprocessing.meta.tame --output out.tame
```

| ID | 목적 | ACTION 타입 | 주요 태그 |
| --- | --- | --- | --- |
| P01 | 중복 레코드 제거(키 기준) | `DEDUP` | `ID(patient)`, `ITEM`, `COLLECTION_AT` |
| P02 | 결측 결과행 제거 | `ROW.EXCLUDE` (is_empty) | `RESULT` |
| P03 | 수치 범위 필터(보고값>100) | `ROW.INCLUDE` (gt) | `RESULT` |
| P04 | 산술 파생(참고치대비비) | `DERIVE` | `RESULT`, `REF_HIGH` → `RATIO` |
| P05 | 날짜 파생(생년월일→방문시연령) | `DATE_DERIVE` (age) | `BIRTHDATE`, `COLLECTION_AT` → `AGE(visit)` |
| P06 | 결측 대치(항목별 중앙값) | `IMPUTE` | `RESULT`, `ITEM` |
| P07 | 이상치 제거(IQR) | `OUTLIER_FILTER` | `RESULT`, `ITEM` |
| P08 | 값 재코딩(진료과 표준화) | `RECODE` | `WARD` |
| P09 | 키 기반 조인(인구통계) | `JOIN` | `ID(patient)` |
| P10 | 연속형 구간 범주화(연령군) | `BIN` | `AGE(baseline)` → `AGE_GROUP` |

대표 전처리 흐름은 `ACTION_PIPELINES.CLEAN`로 묶어 두었다.
`AGE`는 CSS class처럼 넓은 의미의 연령 컬럼을 모두 가리킨다. 원본 등록 연령은
`AGE(baseline)`, 채취일시 기준 파생 연령은 `AGE(visit)`로 한정자를 붙인다.
따라서 단일 컬럼이 필요한 `P10`은 `COLUMN = "tag:AGE(baseline)"`을 사용하고,
여러 연령 컬럼을 모두 다루는 액션은 `TAGS = ["AGE"]` 같은 다중 선택자를 사용할 수 있다.

```bash
tametools run-action-pipeline tutorial/14_capability_probe/clinical_chem.tame CLEAN \
  --meta tutorial/14_capability_probe/examples/preprocessing.meta.tame --output cleaned.tame
```

## 분석 10개 — 개별 실행 (CLI)

대부분 태그만으로 바로 실행한다. 컬럼명을 직접 지정하는 옵션은 쓰지 않는다.

| ID | 목적 | 명령 |
| --- | --- | --- |
| C01 | 참고치 산출 | `run-plugin clinical_chem.tame REFERENCE_INTERVAL` |
| C02 | 이상결과 플래그(H/L/N) | `run-plugin clinical_chem.tame ABNORMAL_FLAG --option MODE=FLAG` |
| C03 | 이상률 집계 | `run-plugin clinical_chem.tame ABNORMAL_FLAG --option MODE=RATE --option GROUP_BY=tag:GROUP` |
| C04 | 방법비교 회귀 | `run-plugin examples/paired_methods.tame METHOD_COMPARISON` |
| C05 | 그룹 간 검정 | `run-plugin clinical_chem.tame GROUP_TEST` |
| C06 | 상관분석 | `run-plugin clinical_chem.tame CORRELATION` |
| C07 | QC 정밀도(CV%) | `run-plugin clinical_chem.tame QC_ANALYSIS --option MODE=PRECISION` |
| C08 | 결과 시계열 추세 | `run-plugin clinical_chem.tame RESULT_TREND --option PERIOD=M` |
| C09 | 진단성능(ROC) | `run-plugin examples/roc_labeled.tame ROC_ANALYSIS --option POSITIVE=1` |
| C10 | TAT 분석 | `run-plugin clinical_chem.tame CHEMISTRY_ANALYSIS --option MODE=TAT_BY_TEST` |

(경로는 모두 저장소 루트 기준. `clinical_chem.tame`은
`tutorial/14_capability_probe/clinical_chem.tame`로 적는다.)

## 태그 기반 재사용성 기준

- 예제의 ACTION은 실제 컬럼명 대신 `tag:<ROLE>` 또는 `TAG/TAGS`를 우선 사용한다.
- 식별자 키는 generic `tag:ID` 대신 `tag:ID(patient)`처럼 qualifier까지 포함해 선택한다.
- `run_all.py`는 같은 TAME을 메모리에서 영어 컬럼명으로 바꾼 뒤 같은 META로 한 번 더 실행한다.
- 새 데이터셋은 컬럼명이 달라도 역할 태그가 같으면 같은 전처리/분석 결과가 나와야 한다.
- `RESULT` 태그는 기본적으로 여러 개가 허용된다. 중복 허용 여부와 반복 방식은 plugin/action 계약에서 정해야 한다.

## 참고
- 구현 진행 기록: [../../../docs/TAME_ANALYSIS_CONTRACT_V1.md](../../../docs/TAME_ANALYSIS_CONTRACT_V1.md)
</content>

# 02. PIVOT_LONGER / PIVOT_WIDER

임상검사실 데이터는 보통 두 가지 표 형태로 오간다.

| 형식 | 설명 | 예시 |
|------|------|------|
| **Wide** | LIS/Excel 원본에 흔한 환자 1행 구조 | AST, ALT, GGT, ALP, TP가 각각 별도 컬럼 |
| **Long** | tametools 분석에 유리한 환자-항목 1행 구조 | 검사항목명 컬럼과 보고값 컬럼 |

`PIVOT_LONGER`는 wide 데이터를 long 데이터로 바꾸고, `PIVOT_WIDER`는 long 데이터를 wide 또는 교차표 형태로 바꾼다.

## 파일

| 파일 | 형식 | 행수 | 설명 |
|------|------|------|------|
| `liver_wide.tame` | Wide | 200 | 환자 1행 원본. `PIVOT_LONGER`로 Long 변환 |
| `liver_long_pivot_wider.tame` | Long | 1000 | 분석용 Long 형식. `PIVOT_WIDER`로 Wide/집계 변환 |

## PIVOT_LONGER

`liver_wide.tame`의 ACTION 정의:

```toml
[ACTIONS.PIVOT_RESULTS_LONGER]
TYPE = "PIVOT_LONGER"
ID_COLS = ["등록번호", "성별", "나이", "수집기관", "검사기기"]
COLS = ["AST", "ALT", "GGT", "ALP", "TP"]
NAMES_TO = "검사항목명"
VALUES_TO = "보고값"
NAMES_TAGS = ["ITEM", "CATEGORY"]
VALUES_TAGS = ["RESULT", "<NUM>"]
```

- `ID_COLS`: 변환 후 유지할 컬럼
- `COLS`: long으로 접을 컬럼. 생략하면 `ID_COLS` 외 전체 컬럼을 접는다.
- `NAMES_TO`: 원래 컬럼명이 들어갈 새 컬럼
- `VALUES_TO`: 원래 값이 들어갈 새 컬럼
- `NAMES_TAGS`, `VALUES_TAGS`: 새 컬럼에 붙일 태그

실행:

```bash
tametools columns tutorial/02_pivot_longer_wider/liver_wide.tame

tametools run-action tutorial/02_pivot_longer_wider/liver_wide.tame PIVOT_RESULTS_LONGER \
  --output /tmp/liver_longer.tame

tametools run-action-pipeline tutorial/02_pivot_longer_wider/liver_wide.tame TO_LONG \
  --output /tmp/liver_longer.tame

tametools columns /tmp/liver_longer.tame
tametools eda /tmp/liver_longer.tame
```

변환 후 구조:

```text
등록번호, 성별, 나이, 수집기관, 검사기기
검사항목명  [[ITEM::CATEGORY]]
보고값      [[RESULT::<NUM>]]
```

## PIVOT_WIDER

`liver_long_pivot_wider.tame`의 ACTION 정의:

```toml
[ACTIONS.WIDE_BY_ITEM]
TYPE = "PIVOT_WIDER"
ID_COLS = ["등록번호", "성별", "나이", "수집기관"]
NAMES_FROM = "검사항목명"
VALUES_FROM = "보고값"
AGG = "first"

[ACTIONS.MEAN_BY_INST]
TYPE = "PIVOT_WIDER"
ID_COLS = ["검사항목명", "성별"]
NAMES_FROM = "수집기관"
VALUES_FROM = "보고값"
AGG = "mean"
```

- `ID_COLS`: wide 결과에서 행을 식별할 컬럼
- `NAMES_FROM`: 새 wide 컬럼 이름이 될 값을 가진 컬럼
- `VALUES_FROM`: wide 셀 값이 될 컬럼
- `AGG`: 중복 조합 처리 방식. `first`, `last`, `min`, `max`, `mean`, `sum` 지원

실행:

```bash
tametools run-action-pipeline tutorial/02_pivot_longer_wider/liver_long_pivot_wider.tame TO_WIDE \
  --output /tmp/liver_wide_restored.tame

tametools run-action-pipeline tutorial/02_pivot_longer_wider/liver_long_pivot_wider.tame INST_COMPARISON \
  --output /tmp/liver_inst_comparison.tame

tametools run-action-pipeline tutorial/02_pivot_longer_wider/liver_long_pivot_wider.tame SEX_COMPARISON \
  --output /tmp/liver_sex_comparison.tame
```

예상 결과:

```text
등록번호  성별    나이  수집기관    ALP   ALT   AST   GGT   TP
P0001   female  21   test_hospital_1  55.9  27.0  38.9  22.6  6.8
P0002   female  35   test_hospital_1 110.7  40.0  20.2   8.0  6.9
```

## 유의사항

- `PIVOT_LONGER` 후에도 `<7`, `<8` 같은 `<NUM>` 비교자 값은 그대로 보존된다.
- `PIVOT_WIDER`에서 `AGG="mean"`, `sum`, `min`, `max`를 쓰면 `VALUES_FROM` 컬럼을 수치형으로 변환한 뒤 집계한다.
- 새 wide 컬럼은 `VALUES_FROM` 컬럼의 태그를 상속한다.

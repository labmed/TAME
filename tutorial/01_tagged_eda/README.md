# 01. Tagged EDA Notebook Guide

이 튜토리얼은 Jupyter notebook을 실행하듯이 한 단계씩 따라가며 TAME 태그 기반 데이터 검토,
정제, EDA, 참고구간 플러그인 실행까지 확인하는 가이드다.

목표는 세 가지다.

- 헤더와 META의 태그가 `tametools` 분석 축으로 어떻게 연결되는지 확인한다.
- 원본 성별 값을 표준화하고 검증 이슈가 없어졌는지 확인한다.
- `EDA`와 `REFERENCE_INTERVAL` 플러그인을 같은 데이터셋에서 실행한다.

아래 명령은 저장소 루트에서 같은 터미널 또는 bash kernel에서 순서대로 실행한다고 가정한다.
설치된 패키지를 사용할 경우 `TT="python3 -m tametools"` 대신 `TT="tametools"`로 바꿔도 된다.

## Notebook Setup

### Cell 0. 실행 환경 준비

```bash
export PYTHONPATH=tametools/src
TT="python3 -m tametools"

TUTORIAL_DIR="tutorial/01_tagged_eda"
SAMPLE="$TUTORIAL_DIR/sample_eda.tame"
DATA="$TUTORIAL_DIR/liver_function_1000.tame"
OUT="/tmp/tametools_tutorial01"

mkdir -p "$OUT"
```

확인할 것:

- 이후 셀에서 생성되는 파일은 모두 `/tmp/tametools_tutorial01` 아래에 저장된다.
- 원본 튜토리얼 파일은 수정하지 않는다.

### Cell 1. 튜토리얼 파일 확인

```bash
ls -1 "$TUTORIAL_DIR"
```

파일 구성:

- `sample_eda.tame`: 3행짜리 최소 예제. 일부러 잘못된 AGE/RESULT 값이 들어 있다.
- `liver_function_eda.tame`: 60행 소규모 예제.
- `liver_function_1000.tame`: 1,000행 실습용 예제. 이후 셀의 주 데이터다.

## Part A. TAME 태그 감 잡기

### Cell 2. 최소 예제의 DATA 헤더 보기

```bash
sed -n '1,25p' "$SAMPLE"
```

핵심은 DATA 헤더에 태그가 같이 들어간다는 점이다.

```text
[[ID::STR]]등록번호
[[SEX]]성별
[[AGE]]나이
[[ITEM]]검사항목명
[[RESULT::<NUM>]]보고값
[[BY]]진료과
```

태그 의미:

- `ID::STR`: 식별자이며 문자열로 다룬다.
- `SEX`: 성별 값으로 다룬다.
- `AGE`: 연령 값으로 다룬다.
- `ITEM`: long-form 검사 항목명이다.
- `RESULT::<NUM>`: `<3`, `>10` 같은 comparator 포함 숫자를 허용하는 결과값이다.
- `BY`: 그룹별 요약 축이다.

### Cell 3. 최소 예제 검증

```bash
$TT validate "$SAMPLE"
```

기대 체크포인트:

```text
[validate]
issues: 2
```

이 예제에서는 `나이=bad`, `보고값=positive`가 태그 규칙에 맞지 않는다.
이 단계는 TAME에서 태그가 단순 라벨이 아니라 실제 검증 규칙으로 작동한다는 것을 보여준다.

## Part B. 1,000행 간기능 데이터 구조 확인

### Cell 4. 데이터셋 개요 확인

```bash
$TT info "$DATA"
```

기대 체크포인트:

```text
rows: 1000
columns: 7
works: ['DEFAULT', 'RI']
```

`DEFAULT` work는 `VALIDATE -> DESCRIBE -> EDA`를 실행한다.  
`RI` work는 `VALIDATE -> REFERENCE_INTERVAL` 플러그인을 실행한다.

### Cell 5. 컬럼과 태그 확인

```bash
$TT columns "$DATA"
```

기대 체크포인트:

```text
등록번호    ID::STR
성별        SEX::BY::CATEGORY
나이        AGE
검사항목명  ITEM::CATEGORY
보고값      RESULT::<NUM>
수집기관    BY::CATEGORY
검사기기    CATEGORY
```

태그 설계:

- `성별`: `SEX`, `BY`, `CATEGORY`를 함께 가진다. 성별 표준화, 그룹 요약, 범주 분포에 모두 쓰인다.
- `나이`: `AGE`만 가진다. 참고구간 플러그인에서 `AGE_BIN_WIDTH`로 구간화한다.
- `검사항목명`: `ITEM`이다. AST/ALT/GGT/ALP/TP long-form key 역할을 한다.
- `보고값`: `RESULT::<NUM>`이다. `<7`, `<8` 같은 값도 숫자 분석에 쓸 수 있다.
- `수집기관`: `BY::CATEGORY`이다. 기관별 요약 축이다.
- `검사기기`: `CATEGORY`만 가진다. 분포 확인 대상이지 RI 층화 축은 아니다.

### Cell 6. META의 work와 RI 옵션 확인

```bash
sed -n '1,45p' "$DATA"
```

확인할 부분:

```toml
[WORKS]
DEFAULT = ["VALIDATE", "DESCRIBE", "EDA"]
RI = ["VALIDATE", "REFERENCE_INTERVAL"]

[REFERENCE_INTERVAL]
AGE_BIN_WIDTH = 20
BOOTSTRAP_N = 0
REPORT_PATH = "/tmp/tametools_tutorial01/reference_interval_report.docx"
```

주의:

- `REFERENCE_INTERVAL`은 플러그인이다.
- `[REFERENCE_INTERVAL]` 섹션은 플러그인 옵션이다.
- 이 튜토리얼은 빠른 실행을 위해 `BOOTSTRAP_N = 0`을 사용한다.
- `REPORT_PATH`가 있으므로 `RI` work를 실행하면 docx 보고서와 PNG 차트가 함께 생성된다.

## Part C. 원본 데이터 검토

### Cell 7. 전체 review 실행

```bash
$TT review "$DATA"
```

확인할 섹션:

- `[review:dataset]`: 행/열 수와 META 구조
- `[review:columns]`: 컬럼별 태그
- `[review:summary]`: 컬럼별 상태와 요약
- `[review:SEX]`: 성별 표준화 가능 여부
- `[review:AGE]`: 연령 분포
- `[review:CATEGORY]`: 항목/기관/기기 범주 분포
- `[review:validation]`: 태그 검증 이슈

### Cell 8. 성별 값만 자세히 보기

```bash
$TT review "$DATA" tag:SEX
```

기대 체크포인트:

```text
M: 385
F: 320
Male: 110
여: 75
남: 70
Female: 40
suggested_fix: tametools fix FILE --standardize-sex --output fixed.tame
```

원본 데이터는 성별 값이 6가지 표현으로 섞여 있다. 다음 단계에서 이를 `male`/`female`로 표준화한다.

### Cell 9. 참고구간 플러그인이 사용할 축 확인

```bash
$TT ri-plan "$DATA"
```

기대 체크포인트:

```text
result_columns: ['보고값']
item_column: 검사항목명
age_column: 나이
sex_column: 성별
by_columns: ['성별', '수집기관']
```

`ri-plan`은 참고구간을 계산하지 않는다. 플러그인이 어떤 태그를 분석 축으로 인식하는지 미리 보여주는 점검 명령이다.

## Part D. 안전한 정제와 검증

### Cell 10. 원본을 안전 정제 파일로 저장

```bash
FIXED="$OUT/liver_fixed.tame"

$TT fix "$DATA" --all-safe --output "$FIXED"
```

기대 체크포인트:

```text
fix: standardize-sex changed_values=1000
fix: standardize-age changed_values=0
saved: /tmp/tametools_tutorial01/liver_fixed.tame
issues_after_fix: 0
```

`--all-safe`는 현재 데이터에서 안전하게 적용 가능한 표준화만 수행한다. 여기서는 성별 표현이 표준화된다.

### Cell 11. 정제 결과 검증

```bash
$TT validate "$FIXED"
```

기대 체크포인트:

```text
[validate]
issues: 0
```

이제 이후 분석은 `$FIXED`를 입력으로 사용한다.

### Cell 12. 표준화된 성별 확인

```bash
$TT review "$FIXED" tag:SEX
```

확인할 것:

- 원본의 `M`, `F`, `Male`, `Female`, `남`, `여`가 표준 성별 값으로 정리된다.
- validation issue가 없어야 한다.

## Part E. 기본 파이프라인 실행

### Cell 13. DEFAULT work 실행

```bash
$TT run "$FIXED" DEFAULT > "$OUT/default_pipeline.txt"
sed -n '1,120p' "$OUT/default_pipeline.txt"
```

기대 체크포인트:

```text
work: DEFAULT
[VALIDATE]
[DESCRIBE]
[EDA]
```

`DEFAULT`는 파일 안의 META에 정의된 work다.

```toml
DEFAULT = ["VALIDATE", "DESCRIBE", "EDA"]
```

### Cell 14. EDA 결과에서 섹션만 빠르게 확인

```bash
grep '^\[' "$OUT/default_pipeline.txt"
```

주요 출력:

- `[VALIDATE]`
- `[DESCRIBE]`
- `[EDA]`
- `[summary]`
- `[category_distribution]`
- `[numeric_percentiles]`
- `[numeric_percentile_bands]`
- `[result_by_summary]`
- `[comparator_profile]`
- `[comparator_policy_impact]`
- `[harmonization_preview]`

### Cell 15. EDA를 직접 실행하고 comparator 정책 바꿔 보기

```bash
$TT eda "$FIXED" --comparator-policy HARMONIZE > "$OUT/eda_harmonize.txt"
sed -n '1,120p' "$OUT/eda_harmonize.txt"
```

확인할 것:

- `<7`, `<8` 같은 comparator 포함 값이 결과 분석에 어떤 정책으로 반영되는지 확인한다.
- `result_by_summary`에는 각 그룹별 결과와 `all` 전체 결과가 함께 나타난다.

## Part F. REFERENCE_INTERVAL 플러그인 실행

### Cell 16. RI work로 플러그인 실행

```bash
RI_OUT="$OUT/liver_ri.tame"

$TT run "$FIXED" RI --output "$RI_OUT" > "$OUT/ri_pipeline.txt"
sed -n '1,80p' "$OUT/ri_pipeline.txt"
```

기대 체크포인트:

```text
work: RI
[VALIDATE]
[REFERENCE_INTERVAL]
reference_interval rows=75 result_columns=1 ok=5 report=/tmp/tametools_tutorial01/reference_interval_report.docx
[charts]
chart: RI_REF_WIDTH type=BAR x=partition y=ref_width
chart: RI_N_BY_GROUP type=BAR x=partition y=n
chart: RI_HIGH_LIMIT type=LINE x=partition y=ref_high
[files]
file: /tmp/tametools_tutorial01/reference_interval_report.docx
saved: /tmp/tametools_tutorial01/liver_ri.tame
final_rows: 75
```

이 단계는 내장 요약 step이 아니라 `REFERENCE_INTERVAL` 플러그인을 실행한다.
`REPORT_PATH`가 META에 들어 있으므로 이 셀에서 참고구간 결과 TAME, docx 보고서, chart PNG가 함께 만들어진다.

주의:

- `[charts]` 출력은 결과 TAME에 저장되는 차트 선언, 즉 `META[VISUALIZATIONS]` contract를 보여준다.
- 이 튜토리얼에서는 `REPORT_PATH`가 지정되어 있으므로 `[files]`도 함께 나오고 실제 보고서/PNG 파일도 생성된다.
- `REPORT_PATH`가 없으면 `[charts]` 선언만 저장되고 docx/PNG 파일은 생성되지 않는다.

### Cell 17. 플러그인 출력 데이터셋 확인

```bash
$TT info "$RI_OUT"
$TT columns "$RI_OUT" | sed -n '1,30p'
```

확인할 컬럼:

- `test_name`
- `partition`
- `group_level`
- `sex_group`
- `age_group`
- `n`
- `ref_low`
- `ref_high`
- `reliability`
- `recommendation`

결과도 `.tame` 파일이므로 다시 `describe`, `review`, `export`, 다른 플러그인의 입력으로 사용할 수 있다.
결과 TAME 안에는 아래처럼 웹앱/보고서 렌더러가 재사용할 차트 선언도 들어 있다.

```bash
grep -n "VISUALIZATIONS" "$RI_OUT"
```

### Cell 18. docx 보고서와 PNG 차트 확인하기

```bash
RI_REPORT="$OUT/reference_interval_report.docx"

ls -1 "$RI_REPORT" "${RI_REPORT%.docx}_charts"
```

기대 체크포인트:

```text
/tmp/tametools_tutorial01/reference_interval_report.docx
/tmp/tametools_tutorial01/reference_interval_report_charts:
chart_01.png
chart_02.png
chart_03.png
```

생성되는 파일:

- `reference_interval_report.docx`: 표와 차트가 들어간 보고서
- `reference_interval_report_charts/chart_01.png`
- `reference_interval_report_charts/chart_02.png`
- `reference_interval_report_charts/chart_03.png`

보고서 생성에는 `python-docx`와 `matplotlib`가 필요하다. 설치 환경에 report extra가 없으면
에러 메시지의 안내에 따라 `tametools[report]` extra를 설치해야 한다.

### Cell 19. 같은 플러그인을 직접 실행해서 다른 보고서 경로로 저장하기

```bash
RI_DIRECT="$OUT/liver_ri_direct.tame"
RI_DIRECT_REPORT="$OUT/reference_interval_report_direct.docx"

$TT analyze "$FIXED" REFERENCE_INTERVAL \
  --option BOOTSTRAP_N=0 \
  --option REPORT_PATH="$RI_DIRECT_REPORT" \
  --output "$RI_DIRECT" > "$OUT/ri_direct.txt"

sed -n '1,60p' "$OUT/ri_direct.txt"
ls -1 "$RI_DIRECT_REPORT" "${RI_DIRECT_REPORT%.docx}_charts"
```

`analyze FILE REFERENCE_INTERVAL`은 `run-plugin FILE REFERENCE_INTERVAL`의 사용자 친화 alias다.
반복 분석 스크립트에서는 이 형태가 가장 명시적이다.

## Part G. 결과 저장과 자동화 출력

### Cell 20. 정제 데이터와 RI 결과를 xlsx로 저장

```bash
$TT save "$FIXED" "$OUT/liver_fixed.xlsx"
$TT save "$RI_OUT" "$OUT/liver_ri.xlsx"

ls -1 "$OUT"
```

생성 파일:

- `liver_fixed.tame`
- `liver_fixed.xlsx`
- `liver_ri.tame`
- `liver_ri.xlsx`
- 중간 로그 파일들

### Cell 21. 자동화용 JSON 출력 확인

```bash
$TT info "$FIXED" --json
$TT ri-plan "$FIXED" --json
```

JSON 출력은 CI, 배치 실행, 웹 프론트엔드 연동에서 쓰기 좋다.

## 정리

이 튜토리얼에서 확인한 흐름은 다음과 같다.

1. DATA 헤더와 META의 태그가 검증 규칙과 분석 축이 된다.
2. `review`로 데이터 상태, 태그 적용, 표준화 필요 여부를 먼저 확인한다.
3. `fix --all-safe`로 원본을 건드리지 않고 정제 파일을 만든다.
4. `validate`로 정제 결과가 분석 가능한지 확인한다.
5. `run DEFAULT`로 `VALIDATE -> DESCRIBE -> EDA`를 반복 실행한다.
6. `run RI` 또는 `analyze REFERENCE_INTERVAL`로 참고구간 플러그인을 실행한다.
7. 플러그인 결과도 `.tame` 파일이므로 후속 분석과 export에 그대로 사용할 수 있다.

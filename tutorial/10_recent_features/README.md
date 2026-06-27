# 10. Recent Features Quickstart

이 문서는 최근 추가한 기능을 한 번에 따라보는 빠른 튜토리얼이다.

대상 기능:

- `META[FORMAT]`, `META[COLUMN]`, `[[LOG]]` 구조
- `review`, `validate`, `fix`, `save` 책임 분리
- `tags-to-meta`, `tags-to-header` 태그 정리
- ACTION / ACTION_PIPELINES / 웹 자동 버튼
- 사용자 정의 태그와 EDA 연동
- 익명화 결과와 매핑 CSV 동시 저장
- `csv`, `jsonl`, `r_bundle` export
- R에서 bundle 로딩

## 1. 메타 구조 확인

현재 canonical 메타 구조는 아래처럼 나뉜다.

```toml
[FORMAT]
ABSENT_TOKEN = "<<ABSENT>>"

[COLUMN."보고값"]
TAGS = ["RESULT", "NUM"]
MIN = "0"
MAX = "1000"

[COLUMN."보고시각"]
TAGS = ["RESULT_TIME", "DATETIME"]
MIN = "2026-01-01 00:00:00"
MAX = "2026-12-31 23:59:59"

[COLUMN."업무시각"]
TAGS = ["TIME"]
MIN = "08:00"
MAX = "18:00"

[SETTINGS]
CRR = "VALUE"

[[LOG]]
OPERATION = "create"
TOOL = "tametools"
```

- `[FORMAT]`: 파일 round-trip에 필요한 값 상태 토큰
- `[COLUMN.<name>]`: 컬럼 태그, 단위, 라벨 같은 컬럼 메타데이터. `NUM`, `AGE`, `DATE`, `DATETIME`, `TIME` 컬럼은 `MIN`/`MAX` 또는 `LOWER_LIMIT`/`UPPER_LIMIT`로 검증 한계를 둘 수 있다.
- `[SETTINGS]`: 검증/분석 정책
- `[[LOG]]`: 새 TAME 생성 이력

## 2. review / validate / fix / save

```bash
tametools review tutorial/01_tagged_eda/sample_eda.tame tag:SEX
tametools review detail tutorial/01_tagged_eda/sample_eda.tame 성별
tametools validate tutorial/01_tagged_eda/sample_eda.tame
tametools fix tutorial/01_tagged_eda/sample_eda.tame --standardize-sex --output /tmp/sample_fixed.tame
tametools save /tmp/sample_fixed.tame /tmp/sample_fixed.xlsx
```

- `review`는 표준화 가능성, 원본값 분포, 변환 불가 값을 사람이 검토하게 보여준다.
- `validate`는 에러 판정만 수행한다. 이 샘플은 의도된 AGE/RESULT 오류가 있어
  `--fail-on-issues`를 붙이면 종료코드 1로 실패한다.
- `fix`와 `save`만 새 파일을 만든다.

## 3. 태그를 헤더와 META 사이에서 이동

```bash
tametools tags-to-meta tutorial/01_tagged_eda/sample_eda.tame \
  --output /tmp/sample_tags_in_meta.tame

tametools tags-to-header /tmp/sample_tags_in_meta.tame \
  --output /tmp/sample_tags_in_header.tame
```

`tags-to-meta`는 헤더 태그를 제거하고 `[COLUMN.<name>].TAGS`로 합친다. `tags-to-header`는 META 태그를 헤더에 다시 합친다.

## 4. ACTION / Pipeline / 웹 버튼

`META[ACTIONS]`, `META[ACTION_PIPELINES]`, `META[ANALYSES]`, `META[WEB_PLUGINS]`에 정의된 항목은 웹앱에서 버튼으로 생성된다. 데이터 변경이 있는 ACTION은 원본을 덮지 않고 새 TAME 노드를 만들고, 생성된 파일의 `[[LOG]]`에 실행 이력을 남긴다.

```toml
[ACTIONS.CORE_COLUMNS]
ACTION = "COLUMN.INCLUDE"
COLUMNS = ["검사항목명", "보고값", "sex", "age"]

[ACTION_PIPELINES.CORE_ANALYSIS_VIEW]
ACTIONS = ["CORE_COLUMNS"]
```

## 5. 사용자 정의 태그와 EDA

```toml
[TAG_DEFINITIONS.AGE5]
LABEL = "Age with 5-year groups"
INHERITS = ["AGE"]
AGE_BIN_WIDTH = 5
```

`AGE5`는 `AGE`의 검증과 표준화 규칙을 상속하고, AGE 그룹 분석에서는 5세 단위를 사용한다.

## 6. 익명화와 매핑 저장

```bash
tametools anonymize tutorial/02_anonymize/sample_phi.tame \
  --output /tmp/sample_phi_anon.tame \
  --mapping-output-dir /tmp/sample_phi_mappings
```

확인:

```bash
ls -1 /tmp/sample_phi_mappings
sed -n '1,10p' /tmp/sample_phi_mappings/manifest.csv
```

여기서 `manifest.csv`는 컬럼별 mapping 파일 목록을 보여준다.

## 7. R 전달용 export

직접 CSV:

```bash
tametools export /tmp/sample_phi_anon.tame /tmp/sample_phi_anon.csv
```

R bundle:

```bash
tametools export /tmp/sample_phi_anon.tame /tmp/sample_phi_anon_bundle --format r_bundle
```

확인:

```bash
ls -1 /tmp/sample_phi_anon_bundle
sed -n '1,10p' /tmp/sample_phi_anon_bundle/columns.csv
```

## 8. R에서 읽기

```r
source("/tmp/sample_phi_anon_bundle/load_tame_bundle.R")
bundle <- read_tame_bundle("/tmp/sample_phi_anon_bundle")

str(bundle$data)
bundle$columns
```

`bundle$data`는 데이터 본문이고, `bundle$columns`는 태그 정의 테이블이다.

## 9. comparator 컬럼이 많을 때

`RESULT::<NUM>`을 순수 numeric 분석에 바로 쓰기 어렵다면 export 전에 분리한다.

```bash
tametools split-comparator tutorial/09_export_for_r/sample_export.tame \
  --output /tmp/sample_export_split.tame

tametools export /tmp/sample_export_split.tame /tmp/sample_export_split_bundle --format r_bundle
```

이 방식이면 R에서 `보고값__num`을 바로 numeric 컬럼으로 다루기 쉽다.

## 10. 권장 흐름

- 내부 체인 분석: `.tame` 유지
- 외부 R 협업: `r_bundle`
- 원본 복원 가능성을 유지해야 할 때: 익명화 결과와 매핑 디렉터리를 분리 보관

# 09. Export For R

이 예제는 `.tame`를 `csv`, `jsonl`, `r_bundle`로 export하고, R에서 읽기 쉬운 형태를 어떻게 고르는지 보여준다.

## 파일

- `sample_export.tame`

## 언제 어떤 형식을 쓸까

- 가장 단순한 공유: `csv`
- 탭 구분 텍스트가 필요하면: `tsv`
- 메타와 태그를 같이 넘기기: `r_bundle`
- 큰 데이터와 빠른 로딩: `r_bundle --bundle-data-format parquet`
- `<NUM>` comparator 컬럼을 순수 숫자로 분석하려면 export 전에 `split-comparator` 또는 `harmonize-comparator`를 고려

## 입력 데이터 확인

```bash
python3 -m tametools columns tutorial/09_export_for_r/sample_export.tame
python3 -m tametools states tutorial/09_export_for_r/sample_export.tame
```

## direct export

```bash
python3 -m tametools export tutorial/09_export_for_r/sample_export.tame tutorial/09_export_for_r/sample_export.csv
python3 -m tametools export tutorial/09_export_for_r/sample_export.tame tutorial/09_export_for_r/sample_export.tsv
python3 -m tametools export tutorial/09_export_for_r/sample_export.tame tutorial/09_export_for_r/sample_export.jsonl
```

이때 출력 데이터는 canonical 컬럼명과 직렬화된 값 토큰을 사용한다.

- `ABSENT` -> `<<ABSENT>>`
- `NULL` -> `<<NULL>>`
- `EMPTY` -> `<<EMPTY>>`
- `WS` -> `<<WS:n>>`

확인:

```bash
sed -n '1,10p' tutorial/09_export_for_r/sample_export.csv
sed -n '1,10p' tutorial/09_export_for_r/sample_export.tsv
sed -n '1,10p' tutorial/09_export_for_r/sample_export.jsonl
```

## R bundle export

```bash
python3 -m tametools export \
  tutorial/09_export_for_r/sample_export.tame \
  tutorial/09_export_for_r/sample_export_bundle \
  --format r_bundle
```

생성물:

- `data.csv`
- `columns.csv`
- `meta.toml`
- `bundle.json`
- `README_R.md`
- `load_tame_bundle.R`

확인:

```bash
ls -1 tutorial/09_export_for_r/sample_export_bundle
sed -n '1,10p' tutorial/09_export_for_r/sample_export_bundle/columns.csv
sed -n '1,20p' tutorial/09_export_for_r/sample_export_bundle/README_R.md
```

Parquet bundle을 원하면:

```bash
python3 -m pip install -e './tametools[parquet]'
python3 -m tametools export \
  tutorial/09_export_for_r/sample_export.tame \
  tutorial/09_export_for_r/sample_export_bundle_parquet \
  --format r_bundle \
  --bundle-data-format parquet
```

이 경우 Python 쪽은 `pyarrow`, R 쪽은 `arrow` 패키지가 필요하다.

현재 환경에 `pyarrow`가 없으면 CLI는 아래처럼 짧게 실패한다.

```text
error: Parquet export requires pyarrow or fastparquet.
```

## R에서 읽기

bundle 폴더 안의 `load_tame_bundle.R`를 그대로 사용할 수 있다.

```r
source("tutorial/09_export_for_r/sample_export_bundle/load_tame_bundle.R")
bundle <- read_tame_bundle("tutorial/09_export_for_r/sample_export_bundle")

str(bundle$data)
bundle$columns
subset(bundle$columns, grepl("RESULT", tags, fixed = TRUE))
```

직접 `csv`를 읽을 때는 컬럼명을 바꾸지 않도록 `check.names = FALSE`를 권장한다.

```r
df <- read.csv(
  "tutorial/09_export_for_r/sample_export.csv",
  stringsAsFactors = FALSE,
  check.names = FALSE,
  na.strings = character()
)
```

## comparator 컬럼을 R-friendly 하게 만들기

`[[RESULT::<NUM>]]보고값`은 `<20`, `30` 같은 값이 같이 들어갈 수 있으므로 export 후 문자형으로 읽히는 것이 자연스럽다.

순수 숫자 분석을 쉽게 하려면:

```bash
python3 -m tametools split-comparator tutorial/09_export_for_r/sample_export.tame \
  --output tutorial/09_export_for_r/sample_export_split.tame

python3 -m tametools export \
  tutorial/09_export_for_r/sample_export_split.tame \
  tutorial/09_export_for_r/sample_export_split_bundle \
  --format r_bundle
```

그러면 `보고값__cmp`, `보고값__num` 컬럼이 추가되어 R에서 후처리가 쉬워진다.

## 권장 흐름

- 후속 분석도 `tametools`에서 이어질 예정이면 `.tame` 유지
- R 사용자에게 전달만 하면 되면 `r_bundle`
- 수치 분석 중심이고 `arrow` 사용 가능하면 `r_bundle + parquet`
- `<NUM>`가 많으면 export 전에 `split-comparator` 실행

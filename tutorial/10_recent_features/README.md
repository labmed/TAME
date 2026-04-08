# 10. Recent Features Quickstart

이 문서는 최근 추가한 기능을 한 번에 따라보는 빠른 튜토리얼이다.

대상 기능:

- 익명화 결과와 매핑 CSV 동시 저장
- `csv`, `jsonl`, `r_bundle` export
- R에서 bundle 로딩

## 1. 익명화와 매핑 저장

```bash
python3 -m tametools anonymize tutorial/02_anonymize/sample_phi.tame \
  --output /tmp/sample_phi_anon.tame \
  --mapping-output-dir /tmp/sample_phi_mappings
```

확인:

```bash
ls -1 /tmp/sample_phi_mappings
sed -n '1,10p' /tmp/sample_phi_mappings/manifest.csv
```

여기서 `manifest.csv`는 컬럼별 mapping 파일 목록을 보여준다.

## 2. R 전달용 export

직접 CSV:

```bash
python3 -m tametools export /tmp/sample_phi_anon.tame /tmp/sample_phi_anon.csv
```

R bundle:

```bash
python3 -m tametools export /tmp/sample_phi_anon.tame /tmp/sample_phi_anon_bundle --format r_bundle
```

확인:

```bash
ls -1 /tmp/sample_phi_anon_bundle
sed -n '1,10p' /tmp/sample_phi_anon_bundle/columns.csv
```

## 3. R에서 읽기

```r
source("/tmp/sample_phi_anon_bundle/load_tame_bundle.R")
bundle <- read_tame_bundle("/tmp/sample_phi_anon_bundle")

str(bundle$data)
bundle$columns
```

`bundle$data`는 데이터 본문이고, `bundle$columns`는 태그 정의 테이블이다.

## 4. comparator 컬럼이 많을 때

`RESULT::<NUM>`을 순수 numeric 분석에 바로 쓰기 어렵다면 export 전에 분리한다.

```bash
python3 -m tametools split-comparator tutorial/09_export_for_r/sample_export.tame \
  --output /tmp/sample_export_split.tame

python3 -m tametools export /tmp/sample_export_split.tame /tmp/sample_export_split_bundle --format r_bundle
```

이 방식이면 R에서 `보고값__num`을 바로 numeric 컬럼으로 다루기 쉽다.

## 5. 권장 흐름

- 내부 체인 분석: `.tame` 유지
- 외부 R 협업: `r_bundle`
- 원본 복원 가능성을 유지해야 할 때: 익명화 결과와 매핑 디렉터리를 분리 보관

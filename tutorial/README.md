# TameTools Tutorial

이 폴더는 `tametools/` 아래의 tag-first `tametools` 예제 모음이다.

## 준비

저장소 루트에서 아래처럼 설치한다.

```bash
python3 -m pip install -e ./tametools
```

설치 없이 바로 실행하려면 아래처럼 `PYTHONPATH`를 지정해도 된다.

```bash
PYTHONPATH=tametools/src python3 -m tametools info tutorial/01_tagged_eda/sample_eda.tame
```

## 구성

- `01_tagged_eda`: 태그 기반 요약, 검증, EDA
- `02_anonymize`: 병원ID 익명화, 이름 제거
- `03_comparator_handling`: `<NUM>` 값 분리, 정책 비교, threshold harmonization
- `04_merge_multihospital`: 여러 병원 tame 파일 merge
- `05_plugins`: 플러그인 구조와 참고치 계산 플러그인
- `06_value_states`: `ABSENT`, `NULL`, `EMPTY`, `WS` 상태 표현
- `07_multisheet_storage`: 여러 엑셀 시트를 하나의 DATA 테이블로 저장하고 다시 시트로 복원
- `08_image_columns`: `IMAGE::B64` / `IMAGE::PATH` 변환
- `09_export_for_r`: `csv/jsonl/r_bundle` export와 R 로딩
- `10_recent_features`: 최근 추가 기능 빠르게 따라하기
- `11_command_pipelines`: `META[PIPELINES]`에 명령 체인을 저장하고 실행
- `plugin_examples`: 외부 플러그인 예제 모듈

## 최근 추가 기능 빠르게 보기

아래 순서대로 보면 최근 변경분을 가장 빨리 검토할 수 있다.

1. `02_anonymize`: 익명화 결과와 매핑 CSV 저장
2. `09_export_for_r`: direct export와 `r_bundle`
3. `10_recent_features`: 익명화 후 R 전달까지 한 번에 따라하기
4. sidecar 재사용: `tametools split-tame FILE --meta-output template.meta.tame`
5. 새 자료 재실행: `tametools run new.xlsx DEFAULT --meta template.meta.tame`
6. 기존 tame에 새 xlsx 데이터만 반영: `tametools import-xlsx template.tame new.xlsx --output updated.tame`
7. 일부 행만 샘플로 추출: `tametools sample FILE --rows 100 --output sample.tame`
8. `META[PIPELINES]`에 명령 체인 저장 후 실행: `tametools run-pipeline FILE DEFAULT`
9. 전체 예제 따라하기: `tutorial/11_command_pipelines`

## 핵심 개념

- 컬럼명은 사람이 읽는다.
- 태그는 `tametools`가 읽는다.
- `[[RESULT::NUM]]보고값` 같은 태그를 기준으로 검증과 분석이 동작한다.
- canonical header syntax는 `[[TAG::TAG]]표시명`이다.
- 태그 내부에서는 `HOSPITAL_ID`처럼 `_`를 그대로 사용할 수 있다.
- `<NUM>`는 comparator-aware numeric 컬럼이다.
- `<30`, `<25`처럼 서로 다른 threshold가 섞이면 EDA에서 경고를 보여주고, 필요하면 harmonize 할 수 있다.
- 플러그인 결과도 `.tame`로 저장되므로 다음 분석 단계에 그대로 체인할 수 있다.
- `.meta.tame`는 태그, 설정, 파이프라인을 들고 다니는 sidecar로 쓸 수 있고, 새 `.xlsx`나 `.data.tame`에 붙여 같은 분석을 반복할 수 있다.
- `import-xlsx`는 기존 `.tame`를 템플릿으로 삼아 새 `.xlsx`의 헤더를 검증하고 데이터만 교체한다.
- `sample`은 전체 데이터셋에서 일부 행만 추출해 경량 샘플을 만들고, 필요하면 태그나 컬럼 기준으로 그룹별 샘플을 뽑을 수 있다.
- `META[PIPELINES]`는 같은 데이터셋에 대해 `sample`, `run`, `run-plugin`, `export` 같은 명령을 순서대로 묶어 두는 경량 명령 체인이다.
- 빈 셀, `NULL`, 빈 문자열, whitespace-only 문자열은 서로 다른 값 상태로 round-trip 할 수 있다.
- 여러 엑셀 시트는 `[[SHEET::STR]]시트명` 컬럼을 통해 하나의 테이블과 여러 시트 사이를 상호 변환할 수 있다.
- 이미지는 `IMAGE::B64`로 내장하거나 `IMAGE::PATH`로 추출해 머신러닝용 폴더 구조와 상호 변환할 수 있다.
- `export`는 direct file, `sql`, 또는 `r_bundle`로 내보낼 수 있다.

# TameTools Tutorial

이 폴더는 `tametools/` 아래의 tag-first `tametools` 예제 모음이다.

## 준비

저장소 루트에서 아래처럼 설치한다.

```bash
python3 -m pip install './tametools[report,web]' --no-build-isolation
tametools info tutorial/01_tagged_eda/sample_eda.tame
```

코드를 수정하면서 개발하려면 editable 설치를 쓴다.

```bash
python3 -m pip install -e ./tametools --no-build-isolation
```

## 구성

- `01_tagged_eda`: 태그 기반 요약, 검증, EDA
- `02_pivot_longer_wider`: `PIVOT_LONGER` / `PIVOT_WIDER` 기반 wide/long 변환
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
- `12_web_frontend`: FastAPI + SvelteKit 웹앱에서 XLSX/TAME 변환, EDA, 익명화
- `13_reference_interval`: 참고치 산출과 CLSI EP28-A3c 기준 신뢰도 검토 (명세서 V2 근거)
- `14_capability_probe`: 실무 예제 20개를 정의·실행해 기능 커버리지와 구현 백로그 점검
- `16_hands_on_guide`: 태그 없는 원자료에서 META/태그를 단계별로 바꾸며 전처리·분석·보고서를 실행하는 발주처 검토용 실습
- `17_tag_spec_extension`: 태그 명세, 한정자 태그, 사용자 정의 태그, `PIVOT_CONTEXT` 기반 분석 예제
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
9. 웹앱으로 XLSX/TAME 변환과 태그 편집 실행: `tutorial/12_web_frontend`
10. 헤더 태그와 `META[COLUMN]` 사이 이동: `tametools tags-to-meta FILE --output meta_tags.tame`
11. ACTION과 ACTION_PIPELINES를 웹 버튼 또는 CLI에서 실행
12. 사용자 정의 태그를 플러그인으로 추가하고 EDA에서 활용
13. 발주처 hands-on 재현: `python3 tutorial/16_hands_on_guide/run_hands_on.py`
14. 태그 확장 가이드 예제: `tametools run-action-pipeline tutorial/17_tag_spec_extension/custom_tags_age5.tame DEFAULT`

## 핵심 개념

- 컬럼명은 사람이 읽는다.
- 태그는 `tametools`가 읽는다.
- `[[RESULT::NUM]]보고값` 같은 태그를 기준으로 검증과 분석이 동작한다.
- canonical header syntax는 `[[TAG::TAG]]표시명`이다.
- 정리된 컬럼 메타데이터는 `META[COLUMN.<name>]`에 둔다. 기존 `[TAGS]`는 읽을 수 있지만 새 저장은 `[COLUMN.<name>].TAGS`가 기준이다.
- 헤더 태그는 META 태그 위에 더해지는 빠른 입력 방식이며, `tags-to-meta`와 `tags-to-header`로 서로 이동/병합할 수 있다.
- 태그 내부에서는 `HOSPITAL_ID`처럼 `_`를 그대로 사용할 수 있다.
- 괄호 한정자를 가진 `BASE(qualifier)` 문법도 공식 태그다. 예를 들어 `[[ID(patient)::STR]]등록번호`, `[[ID(sample)::STR]]검체번호`, `[[ID(hospital)::STR]]기관ID`처럼 쓴다.
- 한정자 태그는 기본 태그를 상속한다. `ID(patient)`는 `ID`로도 검색되지만, JOIN처럼 단일 키가 필요한 작업에서는 `tag:ID(patient)`처럼 구체적으로 선택하는 것이 기본이다.
- 기존 `PATIENT_ID`, `SAMPLE_ID`, `HOSPITAL_ID`는 각각 `ID(patient)`, `ID(sample)`, `ID(hospital)`와 호환되는 legacy alias로 계속 읽힌다.
- `<NUM>`는 comparator-aware numeric 컬럼이다.
- `<30`, `<25`처럼 서로 다른 threshold가 섞이면 EDA에서 경고를 보여주고, 필요하면 harmonize 할 수 있다.
- 플러그인 결과도 `.tame`로 저장되므로 다음 분석 단계에 그대로 체인할 수 있다.
- `.meta.tame`는 태그, 설정, 파이프라인을 들고 다니는 sidecar로 쓸 수 있고, 새 `.xlsx`나 `.data.tame`에 붙여 같은 분석을 반복할 수 있다.
- `import-xlsx`는 기존 `.tame`를 템플릿으로 삼아 새 `.xlsx`의 헤더를 검증하고 데이터만 교체한다.
- `sample`은 전체 데이터셋에서 일부 행만 추출해 경량 샘플을 만들고, 필요하면 태그나 컬럼 기준으로 그룹별 샘플을 뽑을 수 있다.
- `META[PIPELINES]`는 같은 데이터셋에 대해 `sample`, `run`, `run-plugin`, `export` 같은 명령을 순서대로 묶어 두는 경량 명령 체인이다.
- `ACTIONS`와 `ACTION_PIPELINES`는 `ROW.INCLUDE`, `COLUMN.EXCLUDE`, `PIVOT_LONGER`, `PIVOT_WIDER`, `ADD_COLUMN` 같은 전처리 단위를 버튼으로 묶어 반복 실행하게 한다.
- `TAG_DEFINITIONS`와 웹 플러그인은 `AGE5`처럼 기존 태그를 상속한 사용자 정의 태그를 추가할 수 있다.
- 빈 셀, `NULL`, 빈 문자열, whitespace-only 문자열은 서로 다른 값 상태로 round-trip 할 수 있다.
- 여러 엑셀 시트는 `SHEET::STR` 태그가 붙은 provenance 컬럼을 통해 하나의 테이블과 여러 시트 사이를 상호 변환할 수 있다. 기본 컬럼명은 `시트명`이다.
- 생성된 분석/수정 결과는 원본을 덮지 않고 새 `.tame`로 만들며, 실행 이력은 `[[LOG]]`에 누적된다.
- 이미지는 `IMAGE::B64`로 내장하거나 `IMAGE::PATH`로 추출해 머신러닝용 폴더 구조와 상호 변환할 수 있다.
- `export`는 direct file, `sql`, 또는 `r_bundle`로 내보낼 수 있다.

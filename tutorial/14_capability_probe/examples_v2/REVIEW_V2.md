# 2차 예제 실행 검토 — 태그 기반 재사용성 개선점

검증 목표는 한 가지다: **컬럼명이 달라져도 같은 태그·META면 같은 전처리/분석이 돌아가는가.**
2차 예제 20개를 한글·영어 두 컬럼명에서 각각 실행해 40/40 PASS를 받았다(`run_all_v2.py`).
아래는 통과 이면에서 드러난 한계와, 우선순위를 매긴 개선 제안이다.

근거 데이터: `../clinical_chem.tame`(1,277행, RESULT 1종, 항목 6종, 장비 2종, 환자 반복방문 포함).

---

## A. 이번 개선으로 해결된 마찰

### A1. `ADD_COLUMN`의 `TEMPLATE`가 태그를 받는다
- 이전 현상: `TEMPLATE = "{tag:ITEM}-{tag:RESULT}"` 실행 시
  `ValueError: Invalid format specifier`가 발생했다.
- 현재 상태: `ADD_COLUMN`이 `{tag:ROLE}` placeholder를 먼저 안전한 내부 placeholder로
  치환한 뒤 `format_map`을 수행한다. 따라서 기존 `{컬럼명}` 템플릿도 유지하면서
  `{tag:ITEM}:{tag:RESULT}` 같은 컬럼명 독립 템플릿을 쓸 수 있다.
- 검증: 단위 테스트 `test_add_column_template_resolves_tag_references`가 `AST:10`,
  `ALT:20` 생성을 확인한다.

### A2. 생성 컬럼 `TAGS` fallback이 동작한다
- 이전 현상: Q02 `COLUMN.CONCAT`은 `TAGS = ["ID", "STR"]`를 선언했지만 생성 컬럼
  `분석키`에 태그가 붙지 않았다.
- 원인: 빈 `COLUMN_TAGS` 기본값도 "컬럼별 태그 설정이 존재한다"고 판단해 전역 `TAGS`
  fallback으로 가지 않았다.
- 현재 상태: `COLUMN_TAGS`에 해당 컬럼 키가 있을 때만 컬럼별 태그를 쓰고, 없으면
  전역 `TAGS`를 적용한다.
- 검증: `run_all_v2.py`는 Q02에서 `분석키` 태그가 `("ID", "STR")`인지 확인한다.

### A3. 단일 `tag:` 선택자의 모호성이 에러로 잡힌다
- 이전 위험: `COLUMN = "tag:ID"`처럼 단일 컬럼을 요구하는 액션에서 `ID(patient)`와
  `ID(sample)`가 모두 매칭되면 첫 컬럼을 조용히 선택할 수 있었다.
- 현재 상태: 단일 컬럼 선택 경로는 같은 태그가 여러 컬럼에 매칭되면 에러를 낸다.
  다중 컬럼 선택이 의도라면 `TAGS = ["ID"]`를 사용한다.
- 검증: `test_single_tag_selector_rejects_ambiguous_matches`가 `tag:ID` ambiguity를 확인한다.

### A4. `PIVOT_CONTEXT`가 후속 플러그인에서 실제 참고치로 쓰인다
- 이전 한계: Q06(`PIVOT_WIDER`)은 항목별 `UNIT/REF_LOW/REF_HIGH`를
  `META[COLUMN.<item>].PIVOT_CONTEXT`에 보존했지만, 후속 분석이 이 값을 자동으로
  읽지 못했다.
- 현재 상태: `ABNORMAL_FLAG`는 row-wise `REF_LOW`/`REF_HIGH` 컬럼이 없고 wide RESULT
  컬럼에 `PIVOT_CONTEXT.REF_LOW/REF_HIGH`가 있으면 각 RESULT 컬럼을 반복 처리한다.
  `MODE=FLAG`는 `AST_이상플래그` 같은 컬럼별 플래그를 만들고, `MODE=RATE`는 항목별
  이상률을 집계한다.
- 검증: `run_all_v2.py`는 Q06 피벗 직후 `ABNORMAL_FLAG`를 실행해 원본/컬럼명 변경
  양쪽에서 6개 플래그 컬럼이 생기는지 확인한다.
- 단위 테스트: `test_abnormal_flag_plugin_uses_pivot_context_reference_limits`.

---

## B. 남은 구조 한계

### B1. 분석 결과의 stable field id와 표시명 분리가 아직 부족함
- 현재 상태: `CHEMISTRY_ANALYSIS`와 `ROC_ANALYSIS` 등은 `OperationOutput.dataset`을 만들고
  의미 태그를 붙인다. 예를 들어 `RESULT_SUMMARY`의 `평균`은 `MEAN::NUM`,
  `중앙값`은 `MEDIAN::PERCENTILE(50)::NUM`, `P97_5`는 `PERCENTILE(97.5)::NUM`으로 저장된다.
  ROC 출력도 `AUC`, `THRESHOLD`, `SENSITIVITY`, `SPECIFICITY` 태그를 가진다.
- 한계: 컬럼의 물리적 이름은 여전히 `평균`, `중앙값`, `검사항목명` 같은 표시명 중심이다.
  국제화나 외부 코드 재사용성을 높이려면 `mean`, `median`, `test_name` 같은 stable field id와
  로캘별 label을 분리해야 한다.
- 제안: 플러그인 결과 schema에 stable field id, 표시 label, 의미 태그를 함께 선언한다.

### B2. `PIVOT_CONTEXT` 활용이 아직 공통 resolver가 아님
- 현재 상태: Q06(`PIVOT_WIDER`)의 각 항목 열은 원본 `RESULT` 태그를 상속하고,
  `META[COLUMN.<item>].PIVOT`에 `NAMES_FROM`, `VALUES_FROM`, `NAME_VALUE`를 저장한다.
  항목별 `UNIT/REF_LOW/REF_HIGH`가 한 값으로 고정되면 `PIVOT_CONTEXT`에도 보존한다.
  Q07(`PIVOT_LONGER`)도 `SOURCE_COLUMNS`와 `SOURCE_TAGS`를 META에 남긴다.
- 개선: `ABNORMAL_FLAG`는 이 메타를 직접 읽어 wide 결과를 컬럼별로 판정한다.
- 한계: 이 동작은 아직 `ABNORMAL_FLAG` 내부 구현이다. `CHEMISTRY_ANALYSIS`,
  `QC_ANALYSIS`, `ROC_ANALYSIS` 등은 공통 role resolver를 통해 `PIVOT_CONTEXT`를
  자동 활용하지 않는다.
- 제안: 분석 플러그인의 role resolver가 컬럼 메타의 `PIVOT_CONTEXT`를 읽어 per-column
  reference limit과 unit을 일관되게 제공하게 한다.

### B3. 플러그인 다중 RESULT 반복 미보장
- 본 데이터는 RESULT 컬럼이 1개라 40/40이 통과하지만, RESULT가 여러 개인 wide 입력에서
  `GROUP_TEST`·`CORRELATION`·`QC_ANALYSIS`·`ROC_ANALYSIS`·`RESULT_TREND`·`CHEMISTRY_ANALYSIS`는
  여전히 첫 RESULT 중심이다(`examples/TAG_REUSE_REVIEW.md` 한계와 동일).
- 제안: 1차에서 제안한 **plugin role contract**(역할 태그·cardinality·iteration 선언)를
  도입하고, `CARDINALITY="one_or_more"`+`ITERATION="per_result"`를 기본으로.

### B4. ACTION의 태그 선택 문법 불일치
- `COLUMN="tag:RESULT"`(단수) vs `TAGS=["RESULT"]`(복수) vs `tag:ID(patient)`(한정자)가
  액션마다 혼재. 공통 resolver(`resolve_one/resolve_many/resolve_pair`)로 통일 필요.
- 부수효과: Q04에서 `TAGS=["ID"]`가 `ID(patient)`·`ID(sample)`를 **둘 다** 선택했다(태그
  상속이 의도대로 동작). 이는 장점이지만, "하나만"이 필요한 액션과 정책을 분리해야 한다.

---

## C. 사용성

- 플러그인 출력/시각화 설정처럼 표시명에 의존하는 지점에 대해 `validate`가
  "로컬 표시명 의존" 경고를 내주면 재사용성 회귀를 조기에 잡는다.
- 비ASCII(한글) inline-table 키는 TOML에서 따옴표 필수(`{ "채취날짜" = [...] }`).
  META 작성 가이드에 명시(예제에서 1회 실수 후 수정).
- 분석 모드 카탈로그(어떤 MODE가 어떤 태그를 요구하는지)를 `tags`/`plugins` 명령으로
  노출하면 발견성이 오른다.

---

## D. 우선순위 요약

| 순위 | 항목 | 난이도 | 효과 |
| --- | --- | --- | --- |
| 1 | B1 분석 출력 stable field id 도입 | 중간 | 결과 재분석·국제화 |
| 2 | B2 pivot-context 공통 resolver화 | 중간 | pivot 후 임상분석 연결 |
| 3 | B3 다중 RESULT 반복(role contract) | 높음 | wide 데이터 전반 |
| 4 | B4 태그 선택 문법 통일 | 중간 | 일관성·예측가능성 |

재현: `python3 tutorial/14_capability_probe/examples_v2/run_all_v2.py` (40/40 PASS).
관련 문서: `../examples/TAG_REUSE_REVIEW.md`, `../../../docs/IMPLEMENTATION_GAPS.md`.

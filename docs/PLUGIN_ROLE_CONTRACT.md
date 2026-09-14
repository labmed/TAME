# 플러그인 Role Contract

검토일: 2026-06-24

플러그인 전체 작성법, 차트 계약, 보고서 플러그인 템플릿은 `docs/PLUGIN_AUTHOR_GUIDE.md`를 함께 본다.

임상데이터에서는 같은 파일 안에 결과 컬럼이 하나일 수도 있고, `AST`, `ALT`, `Cr`처럼 여러 결과가
wide 형태로 들어올 수도 있다. 초보 사용자가 컬럼명을 직접 지정하지 않아도 같은 분석이 돌아가려면
플러그인이 사용하는 역할(role)을 공통 규칙으로 해석해야 한다.

## RESULT Role

현재 공통 계약은 `tametools.plugin_base.roles.RESULT_ROLE`에 정의되어 있다.

| 속성 | 값 |
| --- | --- |
| role | `result` |
| tags | `RESULT`, fallback `NUM`, `<NUM>` |
| cardinality | `one_or_more` |
| iteration | `per_result` |
| numeric | `true` |

중요한 정책:

- `RESULT` 태그가 있으면 `RESULT` 컬럼만 결과로 본다.
- `RESULT`가 전혀 없을 때만 `NUM`/`<NUM>` fallback을 사용한다.
- `REF_LOW`, `REF_HIGH`처럼 숫자이지만 결과가 아닌 컬럼을 RESULT로 오인하지 않는다.
- 여러 RESULT가 있으면 가능한 플러그인은 각 RESULT를 독립적으로 반복 분석한다.
- 반복 분석 출력은 `source_result_column` 또는 `원본결과컬럼`으로 출처를 남긴다.

## Contract 등록 플러그인

다음 플러그인은 `PluginSpec.roles`에 `RESULT_ROLE`을 등록한다.

- `REFERENCE_INTERVAL`
- `CHEMISTRY_ANALYSIS`
- `ABNORMAL_FLAG`
- `AUTOVERIFICATION`
- `METHOD_COMPARISON`
- `GROUP_TEST`
- `QC_ANALYSIS`
- `ROC_ANALYSIS`
- `RESULT_TREND`

## 검증

`tametools/tests/test_multi_result_contract.py`가 다음을 검증한다.

- 플러그인 목록에서 RESULT role contract가 노출되는지
- RESULT resolver가 `AST`, `ALT`만 선택하고 `REF_LOW`, `REF_HIGH`를 제외하는지
- `CHEMISTRY_ANALYSIS RESULT_SUMMARY`가 wide RESULT를 각각 요약하는지
- `GROUP_TEST`, `QC_ANALYSIS`, `RESULT_TREND`가 wide RESULT별로 반복 실행되는지
- `ROC_ANALYSIS`가 다중 score RESULT를 각각 분석하는지
- `AUTOVERIFICATION`이 wide RESULT별 `PIVOT_CONTEXT` 참고치, critical rule, delta rule을
  독립적으로 적용하는지
- `REFERENCE_INTERVAL`의 기존 다중 RESULT 동작이 contract 아래에서 유지되는지

실행:

```bash
python3 -m unittest discover -s tametools/tests -p test_multi_result_contract.py
```

## 남은 과제

- `CORRELATION`은 wide numeric matrix 자체가 분석 대상이므로 현재 별도 경로를 유지한다.
- `AUTOVERIFICATION`은 wide RESULT별 기본 판정, `PIVOT_CONTEXT` 참고치, critical/delta rule을
  처리한다. 향후에는 critical/ref/delta limit 자체도 별도 role contract로 노출할 수 있다.
- role contract를 CLI의 `plugins` 또는 `tags` 출력에 표시하면 초보자가 어떤 태그가 필요한지
  더 쉽게 확인할 수 있다.

# 17. Tag Spec And Extension

사용자 요청에 맞춘 태그 명세/확장 가이드용 실행 예제다.

## 전체 실행과 자가검증

아래 runner는 문서에 들어가는 실제 stdout을 `outputs/*.stdout.txt`로 캡처하고, 핵심 계약을
자가검증한다.

```bash
python3 tutorial/17_tag_spec_extension/run_tag_spec_examples.py
```

검증 항목:

- `AGE5` review 출력이 `AGE_BIN_WIDTH=5`에 따라 `distribution_5y`를 사용한다.
- `RESULT_INDEX` 파생 컬럼이 생성되고 태그가 유지된다.
- `CHEMISTRY_ANALYSIS AGE_SEX_RESULT`가 `1-4`, `5-9`, `10-14` 연령군을 만든다.
- `ABNORMAL_FLAG`가 `AST`, `ALT` 각각의 `PIVOT_CONTEXT` 참고치를 사용한다.

## AGE5 사용자 정의 태그

```bash
tametools review tutorial/17_tag_spec_extension/custom_tags_age5.tame
tametools run-action-pipeline tutorial/17_tag_spec_extension/custom_tags_age5.tame DEFAULT --output /tmp/custom_tags_age5_result.tame
tametools run-plugin tutorial/17_tag_spec_extension/custom_tags_age5.tame REFERENCE_INTERVAL
tametools run-plugin tutorial/17_tag_spec_extension/custom_tags_age5.tame CHEMISTRY_ANALYSIS --option MODE=AGE_SEX_RESULT
```

`AGE5`는 `AGE`를 상속하므로 AGE 검증과 분석에 그대로 잡히며, `AGE_BIN_WIDTH = 5`로
5세 단위 그룹을 만든다.

## PIVOT_CONTEXT

```bash
tametools run-plugin tutorial/17_tag_spec_extension/wide_pivot_context.tame ABNORMAL_FLAG --option MODE=FLAG
```

wide RESULT 컬럼 `AST`, `ALT` 각각의 `PIVOT_CONTEXT.REF_LOW/REF_HIGH`가 이상 플래그 판정에 쓰인다.

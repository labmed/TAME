# 05. Plugins And Reference Interval

이 예제는 `tametools` 플러그인 구조와 참고치 계산 플러그인을 보여준다.

## 파일

- `sample_reference_interval.tame`
- `sample_reference_interval_missing_tags.tame`
- `../plugin_examples/example_count_plugin.py`

## 참고치 플러그인

`REFERENCE_INTERVAL` 플러그인은 다음 수준의 결과를 한 번에 만든다.

- `TESTNAME`
- `TESTNAME + GENDER`
- `TESTNAME + AGE`
- `TESTNAME + GENDER + AGE`

연령 그룹은 아래 규칙을 쓴다.

- `0-9`, `10-19`, `20-29`, `30-39`, `40-49`, `50-59`, `60-69`
- `70+`

## 실행 예

플러그인 목록 확인:

```bash
python3 -m tametools plugins tutorial/05_plugins/sample_reference_interval.tame
```

참고치 계산:

```bash
python3 -m tametools run-plugin \
  tutorial/05_plugins/sample_reference_interval.tame \
  REFERENCE_INTERVAL \
  --output tutorial/05_plugins/reference_interval_result.tame
```

파이프라인으로 실행:

```bash
python3 -m tametools run \
  tutorial/05_plugins/sample_reference_interval.tame \
  RI \
  --output tutorial/05_plugins/reference_interval_result_pipeline.tame
```

태그 누락 경고 확인:

```bash
python3 -m tametools run-plugin \
  tutorial/05_plugins/sample_reference_interval_missing_tags.tame \
  REFERENCE_INTERVAL
```

생성된 결과를 다시 분석:

```bash
python3 -m tametools describe tutorial/05_plugins/reference_interval_result.tame
```

## 출력 데이터 형식

플러그인 결과도 `.tame`로 저장된다. 그래서 다시 `describe`, `merge`, 다른 플러그인 실행에 그대로 넣을 수 있다.

주요 컬럼은 아래와 같다.

- `[[TESTNAME::ITEM]]검사항목명`
- `[[GROUP_LEVEL]]그룹수준`
- `[[BY::GENDER_GROUP]]성별그룹`
- `[[BY::AGE_GROUP]]연령그룹`
- `[[AGE_LOW::NUM]]연령하한`
- `[[AGE_HIGH::NUM]]연령상한`
- `[[REF_LOW::NUM]]참고치하한`
- `[[REF_HIGH::NUM]]참고치상한`

## 외부 플러그인 예제

`tutorial/plugin_examples/example_count_plugin.py`는 외부 모듈 플러그인 예제다.

실행 예:

```bash
PYTHONPATH=tametools/src:. python3 -m tametools run-plugin \
  tutorial/05_plugins/sample_reference_interval.tame \
  COUNT_BY_TEST \
  --output tutorial/05_plugins/count_by_test_result.tame
```

이 예제는 `META[PLUGINS].MODULES`에 등록된 외부 모듈을 `tametools`가 읽어오는 방식을 보여준다.

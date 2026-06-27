# 05. Plugins And Reference Interval

이 예제는 `tametools` 플러그인 구조와 참고치 계산 플러그인을 보여준다.

## 파일

- `sample_reference_interval.tame`
- `sample_reference_interval_missing_tags.tame`
- `sample_report_plugin.tame`
- `../plugin_examples/example_count_plugin.py`
- `../plugin_examples/combined_report_plugin.py`

## 참고치 플러그인

`REFERENCE_INTERVAL` 플러그인은 다음 수준의 결과를 한 번에 만든다.

- `TESTNAME`
- `TESTNAME + SEX`
- `TESTNAME + AGE`
- `TESTNAME + SEX + AGE`

연령 그룹은 아래 규칙을 쓴다.

- `0-9`, `10-19`, `20-29`, `30-39`, `40-49`, `50-59`, `60-69`
- `70+`

## 실행 예

플러그인 목록 확인:

```bash
tametools plugins tutorial/05_plugins/sample_reference_interval.tame
```

참고치 계산:

```bash
tametools run-plugin \
  tutorial/05_plugins/sample_reference_interval.tame \
  REFERENCE_INTERVAL \
  --output tutorial/05_plugins/reference_interval_result.tame
```

파이프라인으로 실행:

```bash
tametools run \
  tutorial/05_plugins/sample_reference_interval.tame \
  RI \
  --output tutorial/05_plugins/reference_interval_result_pipeline.tame
```

태그 누락 경고 확인:

```bash
tametools run-plugin \
  tutorial/05_plugins/sample_reference_interval_missing_tags.tame \
  REFERENCE_INTERVAL
```

생성된 결과를 다시 분석:

```bash
tametools describe tutorial/05_plugins/reference_interval_result.tame
```

## 출력 데이터 형식

플러그인 결과도 `.tame`로 저장된다. 그래서 다시 `describe`, `merge`, 다른 플러그인 실행에 그대로 넣을 수 있다.

주요 컬럼은 아래와 같다.

- `[[TESTNAME::ITEM]]검사항목명`
- `[[GROUP_LEVEL]]그룹수준`
- `[[BY::SEX_GROUP]]성별그룹`
- `[[BY::AGE_GROUP]]연령그룹`
- `[[AGE_LOW::NUM]]연령하한`
- `[[AGE_HIGH::NUM]]연령상한`
- `[[REF_LOW::NUM]]참고치하한`
- `[[REF_HIGH::NUM]]참고치상한`

## 외부 플러그인 예제

`tutorial/plugin_examples/example_count_plugin.py`는 외부 모듈 플러그인 예제다.

실행 예 (저장소 루트에서, 설치된 `tametools` 사용):

```bash
PYTHONPATH=. tametools run-plugin \
  tutorial/05_plugins/sample_reference_interval.tame \
  COUNT_BY_TEST \
  --allow-plugins \
  --output tutorial/05_plugins/count_by_test_result.tame
```

이 예제는 `META[PLUGINS].MODULES`에 등록된 외부 모듈을 `tametools`가 읽어오는 방식을 보여준다.
외부 플러그인 모듈(`tutorial.plugin_examples.example_count_plugin`)이 저장소 안에 있으므로
`PYTHONPATH=.`로 저장소 루트를 import 경로에 넣어야 한다(없으면 `No module named 'tutorial'`).
직접 만든 플러그인을 import 경로(site-packages 등)에 두면 `PYTHONPATH` 없이 `tametools`만으로 실행된다.

## 차트 포함 보고서 플러그인 예제

`tutorial/plugin_examples/combined_report_plugin.py`는 아래 패턴을 한 번에 보여준다.

- `result_binding()` role helper로 RESULT 컬럼을 태그 기반으로 해석
- `run_plugin()`으로 `REFERENCE_INTERVAL`, `CHEMISTRY_ANALYSIS` 재사용
- `OperationOutput.tables`로 다중 표 반환
- `OperationOutput.charts`와 `META[VISUALIZATIONS]`로 차트 선언
- `write_docx_report()`로 표와 차트가 포함된 docx 생성

실행 예:

```bash
tametools run-plugin \
  tutorial/05_plugins/sample_report_plugin.tame \
  COMBINED_CHEMISTRY_REPORT \
  --allow-plugins \
  --option REPORT_PATH=/tmp/combined_chemistry_report.docx
```

정상 실행 시 CLI에 `[charts]`, `[files]`가 표시되고 `/tmp/combined_chemistry_report.docx`가 생성된다.
재제출용 샘플 산출물은 `tutorial/05_plugins/combined_chemistry_report.docx`에도 생성해 두었다.

플러그인 작성 세부 계약은 `docs/PLUGIN_AUTHOR_GUIDE.md`를 참고한다.

## 독립 검증 보고서 플러그인 예제

`tutorial/plugin_examples/indep_lab_report_plugin.py`는 위 계약이 데모용 특수처리가 아님을
보이는 제3자 독립 예제다. 위 예제와 **다른 조합**을 쓴다.

- 재사용 플러그인 3종: `QC_ANALYSIS`, `ABNORMAL_FLAG`, `RESULT_TREND`
- 차트 타입 혼합: BAR 2개 + LINE 1개
- 입력 `sample_indep_lab_report.tame`의 컬럼명이 전부 비표준(측정치/검사명/보고시각 등)이라
  태그만으로 동작하는지 함께 검증

```bash
tametools run-plugin \
  tutorial/05_plugins/sample_indep_lab_report.tame \
  INDEP_LAB_REPORT \
  --allow-plugins \
  --option REPORT_PATH=/tmp/indep_lab_report.docx
```

회귀 테스트: `tametools/tests/test_plugin_extensibility.py`의 `IndependentReportPluginTests`
(다중 표·BAR+LINE 차트·docx 이미지 3개·CLI 출력·컬럼명 독립성 고정).

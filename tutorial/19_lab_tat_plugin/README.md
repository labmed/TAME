# 19. 검사실 단계별 TAT 플러그인 작성과 실행

이 튜토리얼은 `LAB_TAT_ANALYSIS` 플러그인을 이용해 처방부터 최종보고까지 단계별 소요시간을
분석하고, 결과를 다시 `.tame`로 저장해 후속 작업에 연결하며, 차트가 포함된 docx 보고서를 생성하는
흐름을 보여준다.

## 1. 새 시간 태그

검사 흐름 전용 시간 태그는 모두 `DATETIME`을 상속한다.

| 태그 | 의미 |
| --- | --- |
| `ORDER_PLACED_AT` | 처방시간 |
| `SPECIMEN_COLLECTED_AT` | 채혈시간 |
| `LAB_RECEIVED_AT` | 검사실 도착시간 |
| `LAB_SECTION_RECEIVED_AT` | 검사실 검사파트 도착시간 |
| `TEST_STARTED_AT` | 검사시행시간 |
| `RESULT_CREATED_AT` | 결과생성시간 |
| `PRELIMINARY_REPORTED_AT` | 중간보고시간 |
| `FINAL_REPORTED_AT` | 최종보고시간 |

예제 파일은 `sample_lab_tat.tame`이다. 컬럼명은 자유롭게 바뀌어도 되지만, 태그는 유지되어야 한다.

## 2. 실행

```bash
PYTHONPATH=tametools/src python3 -m tametools run-plugin \
  tutorial/19_lab_tat_plugin/sample_lab_tat.tame \
  LAB_TAT_ANALYSIS \
  --output tutorial/19_lab_tat_plugin/outputs/lab_tat_stage_summary.tame
```

예제 META에는 `REPORT_PATH`가 들어 있으므로 위 명령은 동시에
`tutorial/19_lab_tat_plugin/outputs/lab_tat_report.docx`도 생성한다.

## 3. Chaining

플러그인은 단계별 요약표를 `OperationOutput.dataset`으로 반환한다. 따라서 `--output`으로 저장한
결과 TAME는 다시 `describe`, `export`, 다른 플러그인의 입력으로 쓸 수 있다.

```bash
PYTHONPATH=tametools/src python3 -m tametools describe \
  tutorial/19_lab_tat_plugin/outputs/lab_tat_stage_summary.tame
```

결과 TAME에는 `META[VISUALIZATIONS]`도 포함되어 웹앱과 보고서 렌더러가 같은 차트 선언을 재사용한다.

## 4. 주요 출력

`OperationOutput.tables`에는 다음 표가 들어간다.

- `stage_summary`: 그룹·단계별 n, 평균, 중앙값, P90, P95, 목표 초과율.
- `row_stage_times`: 원 행별·단계별 long-form 소요시간.
- `invalid_intervals`: 누락, 음수 시간 등 검토 필요 구간.
- `bottlenecks`: P95가 큰 상위 병목 단계.
- `timestamp_columns`: 각 태그가 어떤 원본 컬럼에 매핑됐는지.

차트는 다음 3개다.

- `LAB_TAT_MEDIAN`: 단계별 중앙값.
- `LAB_TAT_P95`: 단계별 P95.
- `LAB_TAT_BREACH_RATE`: 목표 초과율.

## 5. 옵션

```toml
[LAB_TAT_ANALYSIS]
GROUP_BY = "tag:TESTNAME"
TARGET_COLLECTION_TO_LAB_MINUTES = 25
TARGET_LAB_TO_SECTION_MINUTES = 15
TARGET_ORDER_TO_FINAL_MINUTES = 180
REPORT_PATH = "tutorial/19_lab_tat_plugin/outputs/lab_tat_report.docx"
```

- `GROUP_BY`: 그룹 컬럼. 기본은 `tag:TESTNAME`; `NONE`이면 전체를 하나로 분석한다.
- `TARGET_<STAGE_KEY>_MINUTES`: 단계별 목표시간. 초과 건수와 초과율 계산에 사용한다.
- `REPORT_PATH`: 지정 시 차트가 포함된 docx 보고서를 생성한다.
- `REPORT_MAX_ROWS`: docx에 넣을 표 최대 행 수.

## 6. 플러그인 구성 방식

구현 파일은 `tametools/src/tametools/plugins/lab_tat.py`다.

핵심 구조:

1. `@register_plugin("LAB_TAT_ANALYSIS")`로 플러그인을 등록한다.
2. `TIME_POINTS`에 태그와 라벨을 정의한다.
3. `STAGES`에 시작 태그, 종료 태그, 단계 순서를 정의한다.
4. 입력 TAME에서 `dataset.first_column_with_tag()`로 컬럼을 찾는다.
5. 각 행의 timestamp를 파싱해 long-form `row_stage_times`를 만든다.
6. long-form 표를 group/stage로 집계해 `stage_summary`를 만든다.
7. `chart_spec()`으로 CLI·웹·docx가 공유하는 차트 선언을 만든다.
8. `with_visualizations()`로 결과 TAME에 차트 메타를 넣는다.
9. `REPORT_PATH`가 있으면 `write_docx_report()`로 보고서를 작성한다.
10. `OperationOutput(dataset=..., table=..., tables=..., charts=..., files=...)`를 반환한다.

이 패턴이 tametools 플러그인의 기본 작성 방식이다. 자세한 작성 가이드는
`docs/LAB_TAT_PLUGIN_AUTHOR_GUIDE.md`에 정리되어 있다.

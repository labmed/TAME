# LAB_TAT_ANALYSIS 플러그인 작성 가이드

이 문서는 검사실 단계별 시간 분석 플러그인을 예로 들어 최종 사용자가 tametools 플러그인을 작성하고
운영하는 방식을 설명한다. 핵심 원칙은 컬럼명이 아니라 태그에 의존하고, 출력은 다시 `.tame`로 저장해
후속 분석에 연결하며, 차트와 보고서는 같은 `OperationOutput` 계약에서 생성하는 것이다.

## 1. 태그 설계

`LAB_TAT_ANALYSIS`는 다음 8개 시간 태그를 사용한다.

| 태그 | 의미 | 부모 태그 |
| --- | --- | --- |
| `ORDER_PLACED_AT` | 처방시간 | `DATETIME` |
| `SPECIMEN_COLLECTED_AT` | 채혈시간 | `DATETIME` |
| `LAB_RECEIVED_AT` | 검사실 도착시간 | `DATETIME` |
| `LAB_SECTION_RECEIVED_AT` | 검사실 검사파트 도착시간 | `DATETIME` |
| `TEST_STARTED_AT` | 검사시행시간 | `DATETIME` |
| `RESULT_CREATED_AT` | 결과생성시간 | `DATETIME` |
| `PRELIMINARY_REPORTED_AT` | 중간보고시간 | `DATETIME` |
| `FINAL_REPORTED_AT` | 최종보고시간 | `DATETIME` |

태그를 분리한 이유는 `DATETIME`만으로는 “어느 업무 단계의 시간인지” 알 수 없기 때문이다. 전용 태그를
쓰면 컬럼명이 `채혈일시`, `collection_time`, `검체채취`처럼 달라도 같은 플러그인이 동작한다.

## 2. 플러그인 등록

내장 플러그인은 `tametools/src/tametools/plugins/*.py`에 작성한다. `plugin_base.manager`가
해당 폴더의 `.py` 파일을 경로 기반으로 자동 로드하므로 별도 모듈 목록을 수정하지 않는다.

```python
from tametools.plugin_base.base import register_plugin
from tametools.models import OperationOutput, TameDataset

@register_plugin("MY_PLUGIN")
def my_plugin(dataset: TameDataset, meta: dict, step_name: str, options: dict) -> OperationOutput:
    ...
```

외부 플러그인은 TAME META에 모듈을 등록하고 `--allow-plugins`로 신뢰 실행한다.

```toml
[PLUGINS]
MODULES = ["my_lab_plugins.tat"]
```

## 3. 컬럼 찾기

컬럼명으로 찾지 말고 태그로 찾는다.

```python
order_time = dataset.first_column_with_tag("ORDER_PLACED_AT")
final_time = dataset.first_column_with_tag("FINAL_REPORTED_AT")
```

여러 병원 파일을 받는다면 컬럼명은 거의 항상 달라진다. 태그 기반 플러그인은 파일별 rename 로직 없이
재사용된다.

## 4. 단계 정의

단계는 시작 태그와 종료 태그의 쌍이다.

```python
Stage("collection_to_lab", "채혈-검사실도착", "SPECIMEN_COLLECTED_AT", "LAB_RECEIVED_AT", 2)
```

이렇게 선언형으로 두면 새 단계를 추가할 때 계산 로직이 아니라 단계 목록만 수정하면 된다.

## 5. Long-form 중간표

플러그인은 먼저 원 행별·단계별 long-form 표를 만든다.

| source_row | group | stage_key | start_time | end_time | duration_minutes | status |
| --- | --- | --- | --- | --- | --- | --- |
| 2 | CBC | collection_to_lab | ... | ... | 18.0 | ok |

long-form 표가 중요한 이유:

- 음수 시간, 누락 시간, 목표 초과 건을 원 행까지 추적할 수 있다.
- 그룹 집계 기준이 바뀌어도 같은 중간표를 재사용할 수 있다.
- 보고서에는 요약표와 함께 원인 추적용 표를 일부 포함할 수 있다.

## 6. Chainable TAME 출력

플러그인은 `stage_summary`를 다시 `TameDataset`으로 만든다.

```python
return OperationOutput(
    name=step_name,
    dataset=result_dataset,
    table=stage_summary,
    tables={"stage_summary": stage_summary, "row_stage_times": row_stage_times},
)
```

CLI에서 `--output out.tame`을 지정하면 이 `dataset`이 저장된다.

```bash
tametools run-plugin sample_lab_tat.tame LAB_TAT_ANALYSIS --output lab_tat_stage_summary.tame
tametools describe lab_tat_stage_summary.tame
```

이 방식이 tametools의 chaining이다. 플러그인 결과가 일회성 표가 아니라 다음 명령의 입력이 된다.

## 7. 차트 계약

차트는 `chart_spec()`으로 선언한다.

```python
from tametools.reporting import chart_spec, with_visualizations

charts = [
    chart_spec("LAB_TAT_MEDIAN", type="bar", x="stage", y="median_minutes", series="group"),
    chart_spec("LAB_TAT_P95", type="bar", x="stage", y="p95_minutes", series="group"),
]
result_dataset = with_visualizations(result_dataset, charts)
```

같은 차트 선언이 세 곳에서 재사용된다.

- CLI 출력의 `[charts]`
- 웹앱 결과 차트
- docx 보고서의 PNG 렌더링

## 8. 보고서 생성

`REPORT_PATH` 옵션이 있으면 `write_docx_report()`를 호출한다.

```python
from tametools.reporting import write_docx_report

written = write_docx_report(
    report_path,
    title="Laboratory TAT Stage Analysis Report",
    outputs=[report_output],
    summary="...",
)
files.append(str(written))
```

반환 `OperationOutput.files`에 파일 경로를 넣으면 CLI와 웹에서 생성 파일을 확인할 수 있다.

## 9. 검증 체크리스트

플러그인 작성 후 최소한 다음을 확인한다.

- `tametools plugins`에 플러그인이 보이는가.
- 태그 컬럼명이 바뀌어도 `first_column_with_tag()`로 찾는가.
- `run-plugin --output out.tame` 결과가 다시 `describe`에 들어가는가.
- 누락/음수 시간이 `invalid_intervals`에 남는가.
- `REPORT_PATH` 지정 시 docx와 차트 이미지가 생성되는가.
- 테스트에 직접 `run_plugin()` 호출과 CLI smoke를 모두 포함했는가.

## 10. 실행 예

```bash
PYTHONPATH=tametools/src python3 -m tametools run-plugin \
  tutorial/19_lab_tat_plugin/sample_lab_tat.tame \
  LAB_TAT_ANALYSIS \
  --output tutorial/19_lab_tat_plugin/outputs/lab_tat_stage_summary.tame
```

위 명령은 예제 META의 `REPORT_PATH`에 따라 docx 보고서도 함께 만든다.

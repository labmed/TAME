# tametools 플러그인 작성 가이드

검토일: 2026-06-24

이 문서는 외부 개발자가 임상데이터 처리 플러그인을 안전하게 추가하기 위한 표준 작성법이다. 목표는
컬럼명이 바뀌어도 태그와 META만으로 재사용되고, CLI·웹·보고서 산출물이 같은 계약으로 동작하게 하는 것이다.

## 1. 기본 핸들러 시그니처

플러그인은 아래 함수를 등록한다.

```python
def handler(dataset: TameDataset, meta: dict, step_name: str, options: dict) -> OperationOutput:
    ...
```

- `dataset`: DATA와 META를 함께 가진 `TameDataset`.
- `meta`: 현재 META 전체.
- `step_name`: 실행 단계 이름. 출력 `OperationOutput.name`에 그대로 쓰는 것을 권장한다.
- `options`: CLI `--option KEY=VALUE`, META `[PLUGIN_NAME]`, pipeline 옵션을 합친 실행 옵션.

## 2. 등록 경로 3가지

1. 내장 플러그인: `tametools/src/tametools/plugins/*.py`에서 `@register_plugin(...)` 사용.
2. 외부 모듈 플러그인: TAME META에 모듈을 등록.

```toml
[PLUGINS]
MODULES = ["tutorial.plugin_examples.combined_report_plugin"]
```

3. 웹 플러그인: 웹 UI에서 작성한 사용자 코드는 `WEB_PLUGINS`로 저장되고, 서버가 임시 모듈로 물질화한 뒤
   동일한 `register_plugin` 경로로 실행한다.

## 3. OperationOutput 표준 필드

`OperationOutput`은 플러그인의 공식 산출물이다.

| 필드 | 용도 |
| --- | --- |
| `name` | 실행 단계 이름 |
| `dataset` | 재저장·후속분석 가능한 TAME 데이터셋 |
| `table` | CLI에 바로 표시할 주 표 |
| `tables` | 보고서형 플러그인의 다중 표 딕셔너리 |
| `charts` | CLI·웹·docx에서 공유하는 차트 선언 목록 |
| `files` | docx, png 등 생성 파일 경로 |
| `issues` | 검증 이슈 |
| `warnings` | 사용자에게 보여줄 경고 |
| `message` | 짧은 실행 요약 |

표준 원칙:

- 후속 분석이 가능하면 `dataset`을 반드시 반환한다.
- 표가 여러 개면 `tables={"summary": frame, ...}`처럼 의미 있는 이름을 사용한다.
- 차트는 `tametools.reporting.chart_spec()`으로 생성한다.
- 보고서 파일은 `files`에 넣어 CLI와 웹에서 노출되게 한다.

## 4. Role Helper 사용

컬럼명에 의존하지 말고 role helper를 사용한다.

```python
from tametools.plugin_base.roles import RESULT_ROLE, result_binding

@register_plugin("MY_PLUGIN", roles=(RESULT_ROLE,))
def my_plugin(dataset, meta, step_name, options):
    bound = result_binding(dataset, options)
    for result_column in bound.columns:
        ...
```

`RESULT_ROLE` 정책:

- `RESULT` 태그가 있으면 `RESULT` 컬럼만 분석한다.
- `RESULT`가 전혀 없을 때만 `NUM`/`<NUM>` fallback을 사용한다.
- `RESULT`가 여러 개면 각 결과 컬럼을 독립적으로 반복 분석한다.
- 단일 컬럼이 필요한 role은 별도 `PluginRole(cardinality="one")`로 정의하고, 중복 태그는 validate 단계에서
  경고 또는 오류로 잡도록 한다.

## 5. 기존 플러그인 재사용

새 기능은 기존 플러그인을 직접 호출해 합성할 수 있다.

```python
from tametools.plugin_base import run_plugin

ri = run_plugin(dataset, "REFERENCE_INTERVAL", meta, "REFERENCE_INTERVAL", options)
summary = run_plugin(dataset, "CHEMISTRY_ANALYSIS", meta, "SUMMARY", {"MODE": "RESULT_SUMMARY"})
```

이 패턴은 중복 구현을 줄이고, 검증된 role contract와 출력 형식을 재사용한다. 실제 예시는
`tutorial/plugin_examples/combined_report_plugin.py`의 `COMBINED_CHEMISTRY_REPORT`이다.

## 6. 차트 계약

차트는 `OperationOutput.charts`와 결과 dataset의 `META[VISUALIZATIONS]` 양쪽에서 쓰는 공통 계약이다.

```python
from tametools.reporting import chart_spec, with_visualizations

chart = chart_spec(
    "SUMMARY_MEDIAN",
    type="bar",
    title="검사항목별 중앙값",
    x="검사항목명",
    y="중앙값",
    series="",
    table="summary",
)
dataset = with_visualizations(dataset, [chart])
```

지원 필드:

| 필드 | 의미 |
| --- | --- |
| `name` | 차트 식별자 |
| `TYPE` | `BAR`, `LINE`, `SCATTER` |
| `TITLE` | 표시 제목 |
| `X` | x축 컬럼 |
| `Y` | y축 컬럼 |
| `SERIES` | 색상/선 구분 컬럼 |
| `TABLE` | 다중 표 중 사용할 표 이름 |
| `ROWS` | 표 없이 직접 넘기는 행 데이터 |
| `MAX_POINTS` | 렌더링 최대 행 수 |

웹은 `META[VISUALIZATIONS]`를 읽어 화면 차트를 만들고, docx 보고서는 `OperationOutput.charts`를
`matplotlib` PNG로 렌더링해 삽입한다.

## 7. 보고서 플러그인 템플릿

```python
from tametools.models import OperationOutput
from tametools.reporting import chart_spec, write_docx_report

chart = chart_spec("SUMMARY_MEDIAN", type="bar", x="검사항목명", y="중앙값", table="summary")
report = OperationOutput(
    name=step_name,
    table=summary_table,
    tables={"summary": summary_table, "reference_interval": ri_table},
    charts=[chart],
)
write_docx_report("/tmp/report.docx", title="임상화학 요약 보고서", outputs=[report])
return OperationOutput(
    name=step_name,
    dataset=result_dataset,
    table=summary_table,
    tables=report.tables,
    charts=[chart],
    files=["/tmp/report.docx"],
)
```

실행 예:

```bash
tametools run-plugin \
  tutorial/05_plugins/sample_report_plugin.tame \
  COMBINED_CHEMISTRY_REPORT \
  --option REPORT_PATH=/tmp/combined_chemistry_report.docx
```

## 8. 검증 권장사항

- 플러그인 예제 TAME 파일을 `tutorial/05_plugins/`에 둔다.
- CLI 실행, `run_plugin()` 직접 호출, docx zip 구조 검사를 자동 테스트에 포함한다.
- 다중 RESULT 입력, 태그 누락, 컬럼명 변경, 빈 데이터, 비교연산자 `<30` 같은 임상 데이터 edge case를
  최소 1개 이상 포함한다.
- 보고서 플러그인은 생성 파일 경로와 차트 삽입 여부를 테스트한다.

## 9. 단계별 TAT 플러그인 예제

시간 태그를 새로 정의하고, long-form 중간표, chainable TAME 출력, 차트, docx 보고서를 모두 포함하는
예제는 `LAB_TAT_ANALYSIS`다.

- 구현: `tametools/src/tametools/plugins/lab_tat.py`
- 예제 데이터: `tutorial/19_lab_tat_plugin/sample_lab_tat.tame`
- 작성 가이드: `docs/LAB_TAT_PLUGIN_AUTHOR_GUIDE.md`

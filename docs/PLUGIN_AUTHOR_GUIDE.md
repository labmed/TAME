# tametools 플러그인 작성 가이드

이 문서는 외부 개발자가 임상데이터 처리 플러그인을 안전하게 추가하기 위한 표준 작성법이다. 목표는
컬럼명이 바뀌어도 태그와 META만으로 재사용되고, CLI·웹·보고서 산출물이 같은 계약으로 동작하게 하는 것이다.

## 0. 첫 플러그인 만들기

### 파일 하나 쓰기

```python
# my_plugins/row_count.py
from tametools.plugin_api import OperationOutput, option, register_plugin


@register_plugin("ROW_COUNT", description="앞쪽 몇 행과 전체 행 수")
def row_count(dataset, meta, step_name, options):
    n = option(options, "N", 5)                       # --option N=3 의 "3" 도, META 의 3 도 정수로 읽는다
    return OperationOutput(name=step_name, table=dataset.df.head(n), message=f"rows={len(dataset.df)}")
```

플러그인이 tametools 에서 가져다 쓰는 것은 모두 **`tametools.plugin_api`** 에 있다. 다른 모듈(`tametools.sync`, `tametools.cli` 등)은
내부 구현이라 예고 없이 바뀔 수 있으므로 직접 import 하지 않는다.

### 어디에 두는가

| 두는 곳 | 실행할 때 | 맞는 경우 |
| --- | --- | --- |
| 아무 폴더. 환경변수 `TAMETOOLS_PLUGIN_PATH` 에 그 폴더를 적는다 (또는 `%LOCALAPPDATA%\tametools\plugins`) | `--allow-plugins` 필요 | 만들면서 시험할 때, 혼자 쓸 때 |
| 설치본의 `tametools.exe` 옆 `plugins\` 폴더 | 그대로 실행 | 설치본을 쓰는 검사실에 파일로 전달할 때 |
| pip 패키지 (13절) | 그대로 실행 | 버전을 붙여 여러 곳에 배포할 때 |

파일 하나(`row_count.py`)여도 되고, `__init__.py` 가 있는 폴더(`row_count/`)여도 된다. 폴더 안에서는 `from .helpers import ...` 처럼
나눠 쓸 수 있다. 이름이 `_` 로 시작하는 파일과 폴더는 플러그인으로 읽지 않으므로 공용 도우미(`_helper.py`)로 쓴다.

### 실행하고 확인하기

```bash
set TAMETOOLS_PLUGIN_PATH=C:\work\my_plugins
tametools plugins --allow-plugins
tametools run-plugin data.tame ROW_COUNT --allow-plugins --option N=3 --output out.tame
```

`tametools plugins --allow-plugins` 의 목록에 `ROW_COUNT [folder ...]` 가 보이면 읽힌 것이다. 읽지 못한 파일은 같은 출력의
`warning: drop-in plugin not loaded: ...` 줄에 이유와 함께 나온다.

### 안 될 때

| 보이는 것 | 뜻 |
| --- | --- |
| `error: unknown plugin: X` + `hint: X is defined in ...` | 파일은 있는데 `--allow-plugins` 를 주지 않았다 |
| `error: unknown plugin: X` + `hint: a plugin file was not loaded: ...` | 파일을 읽다가 오류가 났다(오타, 없는 라이브러리). 뒤의 오류 내용을 본다 |
| `Plugin X must return an OperationOutput, but its handler returned nothing` | `return` 을 빠뜨렸다 |
| `... but its handler returned DataFrame` | 표는 `OperationOutput(name=step_name, table=표)` 로 감싸서 돌려준다 |
| `warning: ... also registers X and was ignored` | 동봉 플러그인과 이름이 같다. 다른 이름을 쓴다 |
| `Option N must be a whole number: 'abc'` | 옵션 값이 형에 맞지 않는다 (`option()` 이 알려 준다) |

파이썬 오류의 전체 경로가 필요하면 명령에 `--debug` 를 붙인다. 표만 돌려주는 플러그인도 `--output` 으로 저장된다
(열 태그는 값에서 추정한다. 태그를 직접 정하려면 `dataset` 을 돌려준다).

### 플러그인 API 와 버전

`tametools.plugin_api` 가 내보내는 이름이 플러그인 API 다. 같은 `PLUGIN_API_VERSION`(지금 2) 안에서는 그 이름들의 뜻이 유지되고,
플러그인을 깨뜨리는 변경이 생기면 번호가 오른다. 새 API 가 필요한 플러그인은 `register_plugin(..., api=2)` 로 적어 두면,
그 API 가 없는 tametools 에서는 읽는 시점에 "tametools 를 올리라"는 안내와 함께 거부된다.

- 버전 1: 등록(`register_plugin` 등), `run_plugin`, `PluginWorkflow`, 질문 폼, sync 기록.
- 버전 2: 플러그인 자신의 버전(`version=`)과 결과 열(`output=`), 최소 버전이 붙은 의존성(`"NAME>=1.2"`), `plugin_module`,
  `require_result`. 다른 플러그인이 부른 플러그인은 바깥 실행에 허용된 범위로 실행된다(5절). 버전 1의 이름은 뜻이 바뀌지 않았으므로
  버전 1로 쓴 플러그인은 그대로 동작한다. 새 기능을 쓰는 플러그인은 `api=2` 를 적는다.

### 옵션 읽기

같은 옵션이 오는 길에 따라 형이 다르다. META 와 대화상자에서는 형이 있는 값(`5`, `True`, `["a", "b"]`),
명령줄에서는 글자(`"5"`, `"true"`, `"a,b"`)다. `option(options, "N", 5)` 는 기본값의 형(여기서는 정수)으로 맞춰 돌려주고,
맞출 수 없으면 옵션 이름을 넣어 오류를 낸다. `kind=`(`str`, `int`, `float`, `bool`, `list`)와 `choices=` 로 더 좁힐 수 있다.
질문을 선언한 플러그인(11절)은 명령줄 값도 선언한 형으로 바뀐 채로 받는다.

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

## 2. 등록 경로

플러그인은 읽히는 순간 `@register_plugin(...)` 으로 자신을 등록한다. 읽히는 길은 다음과 같다.

1. 동봉 플러그인: `tametools/src/tametools/plugins/` 의 파일과 패키지(설치본에서는 실행파일 옆 `plugins\`). 항상 읽는다.
2. pip 로 설치한 플러그인 패키지: entry point 그룹 `tametools.plugins` (13절). 항상 읽는다.
3. 플러그인 폴더: `TAMETOOLS_PLUGIN_PATH`, `%LOCALAPPDATA%\tametools\plugins`. `--allow-plugins` 가 있어야 읽는다.
4. 외부 모듈 플러그인: TAME META에 모듈을 등록. `--allow-plugins` 가 있어야 읽는다.

```toml
[PLUGINS]
MODULES = ["tutorial.plugin_examples.combined_report_plugin"]
```

5. 웹 플러그인: 웹 UI에서 작성한 사용자 코드는 `WEB_PLUGINS`로 저장되고, 서버가 임시 모듈로 물질화한 뒤
   동일한 `register_plugin` 경로로 실행한다.

먼저 읽힌 이름이 이긴다(동봉 > 설치 > 폴더·모듈). 뒤에 온 같은 이름은 무시되고 `tametools plugins` 와 실행 시 경고로 알려 준다.

플러그인의 설정은 그 이름의 META 구획(`[MY_PLUGIN]`)에 적는다. 이 자리는 tametools 가 직접 읽는 구획(`[INFO]`, `[COLUMN]`,
`[WORKS]`, `[SYNC]` 등)과 같은 층이므로, 그런 구획의 이름이나 tametools 의 작업 단계 이름(`VALIDATE`, `DESCRIBE`, `SAMPLE` 등)으로는
플러그인을 등록할 수 없다(읽는 시점에 "이름이 tametools 자체에 쓰인다"는 안내와 함께 거부된다). 전체 목록은
[TAME_META_SECTIONS.md](TAME_META_SECTIONS.md) 에 있다. 설정 구획의 이름을 한 글자 틀리게 쓰면 아무도 읽지 않아 기본값으로
실행되는데, `tametools validate` 가 그런 구획을 "혹시 [이름]?" 으로 알려 준다.

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
- 차트는 `chart_spec()`(`tametools.plugin_api`)으로 생성한다.
- 보고서 파일은 `files`에 넣어 CLI와 웹에서 노출되게 한다.

## 4. Role Helper 사용

컬럼명에 의존하지 말고 role helper를 사용한다.

```python
from tametools.plugin_api import RESULT_ROLE, register_plugin, result_binding

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

플러그인은 다른 플러그인을 세 가지 방법으로 쓴다. 어느 방법이든 쓰는 플러그인을 `dependencies` 에 적는다.

```python
from tametools.plugin_api import OperationOutput, PluginWorkflow, plugin_module, register_plugin, require_result, run_plugin

@register_plugin("MY_REPORT", version="1.0", dependencies=("QC_ANALYSIS", "MY_STATS>=1.2"), api=2)
def my_report(dataset, meta, step_name, options):
    # 1) 실행해서 결과를 받는다
    rules = run_plugin(dataset, "QC_ANALYSIS", meta, "QC_ANALYSIS", {"MODE": "WESTGARD"})

    # 2) 그 플러그인의 함수를 모듈처럼 쓴다
    table = plugin_module("MY_STATS").summarize(dataset.df)

    # 3) 여러 단계를 이름 붙여 잇는다 (각 단계의 결과와 이력이 따로 남는다)
    flow = PluginWorkflow(dataset)
    flow.plugin("rules", "QC_ANALYSIS", options={"MODE": "WESTGARD"})
    flow.plugin("review", "QC_RUN_REVIEW", source="rules")
    return flow.finish(step_name, primary="review")
```

실제 예시는 `tutorial/plugin_examples/combined_report_plugin.py`(직접 실행)와 동봉 플러그인 `QC_WORKFLOW`(워크플로)다.

### 안쪽 플러그인이 실행되는 조건

- **허용 범위는 바깥 실행을 따른다.** 사람이 `--allow-plugins` 를 준 실행에서는 안쪽에서도 폴더·모듈 플러그인을 쓸 수 있고, 주지 않은
  실행에서는 쓸 수 없다. `run_plugin`·`PluginWorkflow.plugin` 의 `allow_external` 은 적지 않는다. 플러그인 안에서 `True` 를 적어도
  범위가 넓어지지 않고, `False` 는 그 호출만 좁힌다.
- **파일의 선택을 따른다.** `META[PLUGINS].ENABLED` 가 있으면 선택된 플러그인과 그 `dependencies` 만 실행된다. 적지 않은 플러그인을
  부르면 그 파일에서는 거부된다. `ENABLED` 가 없는 파일에서는 실행되지만, 결과에 "A used B without declaring it" 경고가 붙어
  빠뜨린 선언을 알려 준다.
- **버전을 확인한다.** `dependencies=("MY_STATS>=1.2",)` 처럼 적으면 실행 전에 그 플러그인의 `version` 과 비교하고, 낮으면
  "MY_REPORT needs MY_STATS 1.2 or later, but MY_STATS 1.0 is here"로 멈춘다. 버전은 점으로 나눈 숫자로 비교한다(1.10 > 1.9, 1.2 = 1.2.0).
- **끝없는 호출은 멈춘다.** 서로를 부르는 플러그인은 두 번째 차례에 "A -> B -> A"를 보여 주며 멈춘다(같은 옵션으로 이미 실행 중인
  플러그인을 다시 부를 수 없다. 중첩은 20단계까지다).
- **묻지 않는다.** 안쪽 플러그인이 선언한 질문(`form`)은 뜨지 않는다. 필요한 값은 부르는 쪽이 `options` 로 넘긴다.

### 설정이 전달되는 길

`PluginWorkflow.plugin("단계", "PLUGIN")` 이 쓰는 설정은, 뒤의 것이 앞의 것을 덮으며, 워크플로가 시작된 파일의 `[PLUGIN]` 구획,
앞 단계 결과가 같은 이름의 구획을 가지고 있으면 그것, `options=` 순이다. 그래서 파일에 적은 `[PLUGIN]` 은 그 플러그인이 워크플로의
어느 단계에서 실행되든 적용되고, `WORKS` 로 이었을 때와 같은 값이 나온다. `run_plugin` 은 넘겨준 `options` 만 쓴다.

### 결과를 넘기고 받기

- **표만 돌려줘도 다음 단계가 받는다.** `WORKS`, `PIPELINES` 의 `run-plugin`, `PluginWorkflow` 모두 표를 데이터셋으로 바꿔 다음 단계에
  넘기고, 그 사실을 경고로 알린다(열 태그는 값에서 추정한다. 태그를 직접 정하려면 `dataset` 을 돌려준다).
- **결과에는 돌려준 플러그인의 이름이 남는다.** `META[ANALYSIS_RESULT].PLUGIN` 은 그 데이터셋을 돌려준 플러그인이다.
  `table_output(step_name, ...)` 에 넘긴 단계 이름은 `STEP` 으로, 안쪽 플러그인의 결과를 그대로 돌려준 경우 그 이름은 `VIA` 로 옮겨진다.
  결과 화면(`register_view`)은 `VIEW`, 없으면 `PLUGIN` 으로 찾으므로 조합한 플러그인이 등록한 화면이 열린다. 조합한 플러그인에 화면이
  없고 안쪽 플러그인에 있으면 `VIEW` 에 안쪽 이름이 적혀 그 화면이 열린다.
- **받는 쪽은 `require_result` 로 확인한다.** 다른 플러그인의 결과를 읽을 때 열 이름을 가정하지 말고 요구한다.

```python
columns = require_result(dataset, "QC_ANALYSIS", columns=("violation", "status"))   # {요청한 이름: 실제 열 이름}
frame = dataset.df[[columns["violation"], columns["status"]]]
```

  그 플러그인의 결과가 아니거나(직접 만든 결과, 그 결과를 그대로 넘겨받은 결과 모두 인정한다) 열이 없으면 무엇을 기대했고 무엇이
  왔는지를 말하며 멈춘다. 열은 이름 또는 `COLUMN.ID` 로 찾는다.
- **주는 쪽은 `output=` 으로 약속한다.** `register_plugin(..., output=("name", "mean"))` 은 "내 결과에는 항상 이 열이 있다"는 선언이다.
  돌려준 결과에 그 열이 없으면 그 플러그인의 오류로 알린다. 열을 바꾸면 `version` 을 올려, 쓰는 쪽이 `"NAME>=버전"` 으로 맞출 수 있게 한다.

### 파일끼리 import 하기

같은 플러그인 폴더의 다른 플러그인 파일을 `from my_stats import summarize` 로 가져와도 된다. 그 파일은 한 번만 실행되고,
플러그인의 출처(`tametools plugins` 의 `[folder ...]`)도 각자 쓰인 파일로 표시된다. 가져온 파일은 sync 의 변경 감지에도 포함된다(13절).
다만 이 방법은 두 파일이 같은 폴더에 있을 때만 통한다. 상대가 pip 패키지이거나 동봉 플러그인이거나 설치본(exe)일 수 있으면
`plugin_module("PLUGIN_NAME")` 을 쓴다. 이름으로 찾으므로 어디에 어떻게 설치되어 있든 같고, 실행 규칙(허용 범위, `ENABLED`)을
그대로 따르며, 이력에도 남는다. `plugin_module` 은 핸들러 안에서 부른다(파일이 읽히는 중에는 상대가 아직 없을 수 있다).
여러 플러그인이 나눠 쓰는 도우미는 `_helper.py` 처럼 `_` 로 시작하는 파일에 두면 플러그인으로 읽히지 않는다.

### 이력에 남는 것

- 사람이 실행한 플러그인이 그 실행의 `PLUGIN_IMPLEMENTATION`(이름, 버전, 코드 해시)이다.
- 그 안에서 `run_plugin` 이나 `plugin_module` 로 쓴 플러그인은 같은 기록의 `PLUGIN_CALLS` 에 이름·버전·코드 해시·옵션으로 남는다
  (같은 호출을 되풀이하면 `COUNT` 가 오른다. 모듈로 쓴 것은 `USE = "module"`).
- `PluginWorkflow` 의 각 단계는 자기 기록(`WORKFLOW:단계`)을 갖고, 그 단계의 플러그인이 그 기록의 `PLUGIN_IMPLEMENTATION` 이다.

## 6. 차트 계약

차트는 `OperationOutput.charts`와 결과 dataset의 `META[VISUALIZATIONS]` 양쪽에서 쓰는 공통 계약이다.

```python
from tametools.plugin_api import chart_spec, with_visualizations

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
from tametools.plugin_api import OperationOutput, chart_spec, write_docx_report

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

## 10. 계속 쌓이는 자료(`tametools sync`)에서 쓰는 플러그인

`tametools sync` 는 원본에 행이 추가될 때마다 **전체 스냅숏에 플러그인을 다시 실행**하고, 새 결과를 기존 결과 파일과
행 단위로 비교해 `NEW / CHANGED / SAME` 을 표시한다. 플러그인은 "새 행만 처리"하는 코드를 따로 만들 필요가 없다.
대신 아래 세 가지를 지키면 추가된 결과가 정확히 구분된다.

1. **결정적**: 같은 데이터와 같은 설정이면 같은 결과를 낸다. 실행 시각, 난수, 실행 순서에 따라 달라지는 값을 결과 열에 넣지 않는다.
2. **결과 행 키 선언**: 결과 dataset 의 `META[ANALYSIS_RESULT].ROW_KEY` 에 결과 행을 식별하는 열 이름을 적는다.
   선택으로 `SUMMARY_COLUMNS` 에 sync 가 화면에 보여줄 열을 적는다. 키가 없으면 행 전체 내용으로 비교한다.
3. **추가에 안정적**: 한 결과 행은 그 관측과 그보다 앞선 관측에만 의존하게 한다. 그러면 뒤에 자료가 추가되어도
   앞선 결과가 바뀌지 않고, `CHANGED` 는 원본 수정이나 설정 변경이 있었다는 뜻이 된다.
   전체 자료로 다시 추정하는 통계(예: 전체 평균으로 구한 z 점수)는 자료가 늘 때마다 모든 행이 `CHANGED` 가 된다.

```python
output = table_output(step_name, frame, result_columns=("z",))
meta = deepcopy(output.dataset.meta)
meta["ANALYSIS_RESULT"] = {"PLUGIN": "MY_PLUGIN", "ROW_KEY": ["result_id", "run", "level"],
                           "SUMMARY_COLUMNS": ["value", "z", "decision"]}
output.dataset = output.dataset.replace(meta=meta)
```

### 차트가 들어간 보고서 제공하기

`META[SYNC].REPORT = "report.html"` 이 있으면 sync 는 동기화가 확정될 때 보고서 한 파일을 함께 갱신한다.
플러그인은 보고서 작성 함수를 등록하고, 결과 dataset 의 `META[ANALYSIS_RESULT].REPORT` 에 그 이름을 적는다.
등록한 함수가 없는 결과는 변경 내용만 표로 보여 주는 기본 보고서가 만들어진다.

```python
from tametools.plugin_api import register_report

@register_report("MY_PLUGIN")
def my_report(context):          # context: report(SyncReport), result(추적 열이 붙은 결과 dataset), history,
    return "<!doctype html>..."  #          records(프로젝트에 남긴 사람의 기록), generated_at, tool_version
                                 # 외부 파일을 참조하지 않는 완결된 HTML 문자열을 돌려준다

meta["ANALYSIS_RESULT"]["REPORT"] = "MY_PLUGIN"
```

보고서 함수는 파일을 직접 쓰지 않는다. sync 가 프로젝트·결과와 함께 한 번에 교체하므로 `--check` 나 실패한 동기화는
보고서를 바꾸지 않는다. 데이터에서 온 글자(검사명, lot, 비고 등)는 반드시 `html.escape` 로 처리한다.

### 사람이 남기는 기록

결과는 동기화마다 다시 계산되므로 사람이 남기는 내용(조치, 검토 등)은 결과 파일에 둘 수 없다.
`add_record(project, kind, fields, by=...)` (`tametools.plugin_api`) 는 이런 기록을 프로젝트 파일에 덧붙인다.
기록은 수정되지 않고(정정은 `supersedes` 로 새 기록), 앞 기록과 해시로 연결되며 LOG 에도 남는다.
보고서 함수는 `context["records"]` 로 받아 결과 행에 붙여 보여 주고, `register_report(name, notices=함수)` 로
등록한 함수는 sync 가 실행될 때마다 출력할 알림(예: 조치 기록이 없는 거부 run 수)을 돌려준다.
`rerender_report(project)` 는 원본을 읽거나 재계산하지 않고 보고서만 다시 쓴다.
QC 용 구현은 `tametools/src/tametools/plugins/westgard_qc/actions.py` 이고, 명령(`qc-action`, `qc-review`)은 같은 패키지의 `commands.py` 가 등록한다(12절).

- 구현 예: `tametools/src/tametools/plugins/westgard_qc/` (플러그인 패키지. 규칙 계산 `multirule.py`, 보고서 `report.py`, 기록 `actions.py`, 명령 `commands.py`). 공개 API(`tametools.plugin_api`)만 쓰므로 외부 플러그인도 같은 방식으로 만들 수 있다.
- 동기화 코어: `tametools/src/tametools/sync.py`
- 예제와 사용법: `tutorial/26_qc_sync/README.md`

## 11. 실행하는 사람에게 묻기 (대화상자)

플러그인은 창을 직접 만들지 않는다. 물어볼 것을 **선언**하면 실행하는 쪽이 상황에 맞게 보여 준다.
차트를 `chart_spec` 으로 선언하면 CLI·웹·docx 가 각자 그리는 것과 같은 방식이다.

```python
from tametools.plugin_api import field, form, register_plugin

QUESTIONS = form("분석 설정", [
    field("GROUP", "묶을 열", kind="column", required=True),
    field("MIN_COUNT", "최소 건수", kind="integer", default=0, minimum=0),
    field("ORDER", "정렬", kind="choice", choices=[("COUNT", "건수 많은 순"), ("NAME", "이름 순")], default="COUNT"),
], intro="어느 열을 기준으로 셀지 정합니다.", submit="실행")

@register_plugin("COUNT_BY_COLUMN", form=QUESTIONS)
def handler(dataset, meta, step_name, options):
    group = ci_get(options, "GROUP")     # 창에서 온 답도 --option 으로 온 값도 여기로 들어온다
```

`field(key, label, kind, ...)` 의 `key` 가 곧 옵션 이름이다. 답은 플러그인 옵션에 합쳐져 핸들러의 `options` 로 들어오고,
다른 옵션과 똑같이 실행 이력(LOG `PLUGIN_REQUESTED_OPTIONS`)에 남는다. 그래서 핸들러는 창이 있었는지 알 필요가 없고,
같은 실행을 `--option` 만으로 다시 할 수 있다(`run-plugin` 이 그 명령을 출력해 준다).

| `kind` | 받는 값 | 돌려주는 값 |
| --- | --- | --- |
| `text`, `multiline`, `file` | 글 (`file` 은 찾기 단추가 붙음) | `str` |
| `number`, `integer` | 숫자. `minimum`, `maximum` 으로 범위 | `int` 또는 `float` (비우면 `None`) |
| `choice` | `choices` 중 하나. `(값, 보이는 글)` 쌍도 가능 | 고른 값 |
| `multi` | `choices` 중 여러 개 | `list` |
| `check` | 체크 | `bool` |
| `date` | `YYYY-MM-DD` | `str` |
| `column`, `columns` | 자료의 열 하나/여러 개. `tags=["RESULT"]` 로 태그가 붙은 열만, `value="id"` 로 `COLUMN.ID` 를 받음 | 열 이름 또는 ID |

질문이 자료에 따라 달라지면 `form=` 에 함수 `(dataset, options) -> Form | None` 을 준다. `None` 이면 묻지 않는다.

### 언제 묻는가

| 실행 상황 | 동작 |
| --- | --- |
| 터미널에서 사람이 실행, `required` 항목이 비어 있음 | 창이 뜬다 (창을 띄울 수 없으면 같은 질문을 터미널에서 글로) |
| 필요한 옵션이 다 지정됨 | 묻지 않는다. `--dialog` 를 붙이면 지금 값을 채운 창이 뜬다 |
| 사람이 없는 실행 (스케줄러, 파이프, 테스트) 또는 `--no-dialog` | 묻지 않는다. `required` 항목이 비어 있으면 그 옵션 이름을 알려 주고 멈춘다 |
| 웹 작업실의 플러그인 탭 | 서버는 창을 띄우지 않는다. 선언한 질문을 브라우저가 입력 폼으로 그리고(선언이 없으면 JSON 칸), 답은 서버가 같은 규칙으로 검증해 옵션에 합친다 |
| `sync`, WORKS, 웹 작업실의 저장된 분석(`ANALYSES`) | 묻지 않는다 |

환경 변수 `TAMETOOLS_DIALOG` 로 기본 동작을 바꾼다: `terminal`(창 대신 글로), `off`(명령줄에서 `--dialog` 를 주지 않는 한 묻지 않음).
사람이 취소하면 플러그인은 실행되지 않는다.

웹 작업실에서는 플러그인을 고르면 `POST /api/plugins/form` 이 질문을 돌려주고, `POST /api/plugins/run` 이 `answers` 를 받아 실행한다.
필수 항목이 비었거나 받을 수 없는 답이면 실행하지 않고 400 과 함께 항목별 메시지(`fieldErrors`)를 돌려준다. 명령줄과 다른 점은 두 가지다.

- `file` 은 글 입력이다. 찾기 창이 없고 브라우저에서 고른 파일을 올려 주지도 않으므로, 서버(프로그램이 실행 중인 PC)에서 읽을 수 있는 경로를 적는다.
- 외부 플러그인(`META[PLUGINS].MODULES`, 드롭인 폴더)의 질문은 코드를 불러와야 알 수 있으므로, 실행과 같은 신뢰 확인을 거친 뒤에 폼이 보인다.

### 지켜야 할 것

- **핸들러 안에서 묻지 않는다.** 플러그인이 계산하는 동안에는 `tametools.dialogs.ask` 가 막혀 있고, tkinter 창을 직접 띄워서도 안 된다.
  결과는 자료와 옵션만으로 정해져야 `sync` 와 `verify` 가 다시 계산했을 때 같은 값이 나온다.
- **`required=True` 는 "이 값 없이는 실행할 수 없다"는 뜻이다.** 기본값이나 태그로 대신할 수 있으면 붙이지 않는다.
  필수 항목이 다 채워져 있으면 창은 `--dialog` 를 줬을 때만 뜬다.
- **비운 답은 옵션을 지정하지 않은 것과 같다.** 플러그인의 기본값이 적용된다.
- **간단한 입력까지만 다룬다.** 표 편집이나 미리보기가 필요하면 웹 작업실의 일이다. 필드가 없는 `form` 은 확인 창이 된다
  (`tametools.dialogs.confirm("...")`).
- 창에서 온 값은 형이 있고(`3`, `True`, `["a", "b"]`) `--option` 으로 온 값은 글자다(`"3"`, `"true"`, `"a,b"`). 둘 다 받게 쓴다.

결과를 본 뒤 사람이 남기는 내용(조치, 검토)은 옵션이 아니라 10절의 기록이다. `qc-action --dialog` 가 그 예로,
명령이 `ask(form, 값)` 으로 물어 `add_record` 로 저장한다(`tametools/src/tametools/plugins/westgard_qc/actions.py` 의 `ask_action`).

### 테스트

창을 띄우지 않고 "사람이 이렇게 답했다"를 넣어 시험한다. `tametools/tests/test_dialogs.py` 의 `answering` 처럼
`tametools.dialogs._ask_gui` 를 바꿔 끼우면 선언한 질문(`made`)과 시작 값(`start`)을 확인하고 답을 돌려줄 수 있다.

- 구현: `tametools/src/tametools/dialogs.py`
- 예제: `tutorial/plugin_examples/dialog_form_plugin.py` (`tutorial/05_plugins/sample_dialog_plugin.tame`)
- 설치본: `packaging/windows/tametools.spec` 가 Tcl/Tk 를 함께 담는다.
  Tcl/Tk 없이 빌드된 실행파일에서는 창 대신 터미널 질문으로 묻는다.

## 12. 분석 말고 더 붙일 수 있는 것: 명령, 화면, 내보내기 형식

플러그인은 분석 핸들러 외에 명령, 결과 화면, 내보내기 형식을 선언으로 더 붙일 수 있다. 모두 플러그인 파일이 읽힐 때 등록되고,
tametools 의 명령줄 코드나 웹 화면 코드를 고칠 필요가 없다.

### 명령 (`register_command`)

```python
from tametools.plugin_api import register_command

def _arguments(parser):                       # argparse 파서에 인자를 더한다
    parser.add_argument("path")
    parser.add_argument("--list", action="store_true")

@register_command("lab-check", help="한 줄 설명", configure=_arguments)
def lab_check(args) -> int:                   # 종료 코드를 돌려준다 (0 = 성공)
    ...
```

- `tametools lab-check ...` 로 실행되고, `tametools help lab-check` 와 `tametools plugins`(끝의 `plugin commands:`)에 나온다.
- 이름은 소문자 낱말을 `-` 로 이은 것. tametools 자체 명령과 같은 이름은 자체 명령이 이기므로 고유한 이름을 쓴다.
- 폴더·모듈 플러그인이 등록한 명령은 플러그인과 같은 신뢰 규칙을 따른다: `tametools lab-check ... --allow-plugins`.
  `args.allow_plugins` 로 그 여부를 알 수 있다.
- 자체 명령과 같은 감사 범위(실패 기록, `CLI:<명령>` 연산)에서 실행된다.
- 예: `qc-action`, `qc-review` 는 동봉 QC 플러그인이 이렇게 등록한 명령이다.

### 결과 화면 (`register_view`)

```python
from tametools.plugin_api import register_view

@register_view("MY_PLUGIN", title="내 결과 화면")
def my_view(dataset) -> str:                  # 결과 dataset -> 완결된 HTML 문서 하나
    return "<!doctype html>..."
```

- 결과 dataset 의 `META[ANALYSIS_RESULT].PLUGIN` 이 `MY_PLUGIN` 이면 이 화면이 그 결과의 화면이다(`VIEW` 로 다른 이름을 가리킬 수 있다).
  `table_output(step_name, ...)` 은 `ANALYSIS_RESULT.PLUGIN` 에 단계 이름을 적는다. `run-plugin` 과 WORK 에서는 단계 이름이 곧
  플러그인 이름이지만, 다른 이름으로 실행될 수 있으면 결과 META 에 플러그인 이름을 직접 적는다.
- 보여 주는 곳: `tametools run-plugin FILE MY_PLUGIN --view out.html`, 그리고 웹 작업실(결과에 화면이 있으면 여는 단추가 생긴다).
- HTML 은 외부 파일을 참조하지 않는 문서 하나여야 하고, 자료에서 온 글자는 반드시 `html.escape` 로 처리한다.
  웹 작업실은 이 문서를 작업실 페이지와 격리된 틀 안에 보여 준다(문서 안의 스크립트는 작업실이나 서버 API 에 접근하지 못한다).
- sync 프로젝트의 보고서(10절 `register_report`)와는 별개다. 같은 그림을 쓰려면 한 함수를 두 곳에 등록한다
  (QC 플러그인의 `report.py`: `sync_qc_report` 와 `result_view`).

### 내보내기 형식 (`register_exporter`)

```python
from tametools.plugin_api import export_frame, register_exporter

@register_exporter("markdown", suffixes=(".md",), description="Markdown 표")
def write_markdown(dataset, path) -> None:    # path 에 쓴다. 그 파일 하나뿐이면 아무것도 돌려주지 않는다
    frame = export_frame(dataset)             # tametools 의 csv/tsv 내보내기와 칸마다 같은 글자
    ...
    path.write_text(text, encoding="utf-8")
```

- `tametools export FILE out.md` (확장자로 추론) 또는 `tametools export FILE out.txt --format markdown` 으로 쓰인다.
  `tametools plugins` 의 끝줄 `plugin export formats:` 에 나온다. `META[PIPELINES]` 의 `export` 명령에서도 같은 이름을 쓴다.
- 파일을 여러 개 쓰면 쓴 경로들을 리스트로 돌려준다. 돌려준 경로에 파일이 없으면 오류로 알려 준다. 출력 폴더는 미리 만들어져 있다.
- tametools 가 원래 가진 형식 이름(csv, tsv, jsonl, sql, parquet, feather, r_bundle)과 그 확장자는 항상 tametools 것이 쓰인다.
  확장자가 겹치면 더 긴 쪽이 이긴다(`.fhir.json` 이 `.json` 보다 먼저).
- 폴더·모듈 플러그인의 형식은 플러그인과 같은 신뢰 규칙을 따른다: `tametools export ... --allow-plugins`.
- 예제: `tutorial/plugin_examples/markdown_export_plugin.py`.

### 호스트의 전용 화면 쓰기 (`panel`), 대신 쓸 플러그인 알리기 (`superseded_by`)

```python
@register_plugin("RI_EP28", description="...", panel="reference_interval")
@register_plugin("REFERENCE_INTERVAL", description="...", superseded_by="RI_EP28")
```

- `panel` 은 "호스트에 이 이름의 전용 화면이 있으면 그 화면으로 실행된다"는 선언이다. 웹 작업실의 참고구간 화면은 플러그인 이름을
  코드에 적어 두지 않고, `reference_interval` 화면을 선언한 플러그인을 찾아서 쓴다. 그 화면이 없는 호스트(명령줄)에서는 보통 플러그인처럼 실행된다.
- 선언한 이름이 호스트가 쓰는 이름이다(설정 구획 `[RI_EP28]`, 로그). 같은 핸들러의 다른 이름(별칭)은 그 이름으로 이어진다.
- 전용 화면은 그 화면이 기대하는 옵션과 결과 형식을 지키는 플러그인만 쓸 수 있다. `reference_interval` 화면의 계약은
  `RI_EP28` 의 옵션과 결과 META(`<이름>_RESULT`)다. 동봉 플러그인이 선언한 화면을 외부 플러그인이 가져갈 수는 없다.
- `superseded_by` 는 옛 파일을 위해 남겨 둔 플러그인이 "새 작업에는 이것을 쓰라"고 알리는 선언이다. 웹 작업실은 "(구형)" 으로 표시한다.

### 코어와 플러그인의 경계

코어(`tametools/src/tametools/` 에서 `plugins/` 를 뺀 부분)는 플러그인을 이름으로만 부르고 플러그인 모듈을 import 하지 않는다
(`tests/test_plugin_api.py` 가 이를 검사한다). 여러 플러그인이 함께 쓰는 계산은 코어에 두고 플러그인이 가져다 쓴다
(예: 방법 비교의 `passing_bablok`, `deming` 은 `tametools/method_comparison.py`, QC 평가는 `evaluate_qc`).

지금 플러그인이 붙일 수 없는 것: 새 태그·검증 규칙(어떤 TAME 파일이 유효한지가 설치된 플러그인에 따라 달라지지 않게 하기 위해서다),
웹 작업실의 새 전용 화면(질문 폼과 결과 화면, 그리고 이미 있는 전용 화면까지만 된다).

## 13. 배포

### 폴더로 전달

파일 하나 또는 폴더 하나를 받는 쪽의 플러그인 폴더에 복사한다(0절). 받는 쪽에 없는 라이브러리를 쓰면 읽는 시점에 실패하고
`tametools plugins` 가 그 내용을 알려 준다. 설치본(exe)은 동봉된 라이브러리만 쓸 수 있다.

### pip 패키지로 배포

플러그인을 보통의 파이썬 패키지로 만들고, `pyproject.toml` 에 entry point 를 적는다. 값은 **모듈 경로**이고,
그 모듈을 import 하면 플러그인이 등록되면 된다.

```toml
[project]
name = "lab-plugins"
version = "1.2.3"
dependencies = ["tametools>=0.4"]

[project.entry-points."tametools.plugins"]
counts = "lab_plugins.counts"
```

`pip install lab-plugins` 한 환경에서는 `--allow-plugins` 없이 읽히고, `tametools plugins` 에 `[installed lab-plugins 1.2.3]` 으로 표시되며,
실행 이력(`PLUGIN_IMPLEMENTATION.DISTRIBUTION`)에 패키지 이름과 버전이 남는다. 동봉 플러그인의 이름은 가져갈 수 없다.
설치된 플러그인을 읽지 않게 하려면 환경변수 `TAMETOOLS_INSTALLED_PLUGINS=off` 를 준다.

### 플러그인이 바뀌면 sync 가 안다

sync 프로젝트의 WORK 가 tametools 밖의 플러그인(폴더, pip, META 모듈)을 쓰면, sync 는 그 플러그인 코드의 해시를
프로젝트에 적어 둔다(`SYNC_STATE.PLUGIN_SHA256`). 플러그인 파일(패키지면 패키지 전체, 파일 하나면 같은 폴더의 `_*.py` 도우미와
그 파일이 import 한 같은 폴더의 파일 포함)이 바뀐 뒤의 sync 는 원본이 그대로여도 "플러그인 구현이 바뀌었습니다: 이름"을 알리고
다시 계산해 결과의 변화를 보여 준다.

감시 대상은 WORK 에 적힌 이름과 그 `dependencies` 에 더해, **지난 sync 에서 실제로 실행된 플러그인 전부**다. 플러그인이
`dependencies` 에 적지 않고 다른 플러그인을 실행하거나 `plugin_module` 로 썼더라도 그 플러그인의 변경을 놓치지 않는다.

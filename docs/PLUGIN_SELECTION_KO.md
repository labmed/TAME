# TAME 파일별 플러그인 선택

파일마다 사용할 분석 플러그인을 선택하고 META에 저장할 수 있습니다. 웹 실행, CLI `run-plugin`/`analyze`, `WORKS`, Python `run_plugin`과 플러그인 연결 실행은 같은 선택을 확인합니다. 기본 검증·전처리·탐색 분석 명령은 이 목록과 별도로 사용할 수 있습니다.

## 웹에서 선택하고 저장하기

1. TAME 파일 또는 예제 자료를 엽니다.
2. **플러그인** 탭에서 표시된 **작업 입력** 파일명을 확인합니다. 분석결과를 보고 있어도 선택은 작업 입력 자료에 적용합니다.
3. **선택한 플러그인만 사용**을 고르고 필요한 항목을 체크한 후 **선택 적용**을 누릅니다.
4. 같은 탭의 **실행할 플러그인** 목록에서 분석을 선택하고 실행합니다. 아직 적용하지 않은 체크 상태는 실행 목록에 반영되지 않습니다.
5. **TAME 저장**으로 선택을 파일에 보존합니다. 다시 열면 선택 목록과 연결에 필요한 플러그인이 복원됩니다.

선택 적용은 원자료의 값·행·열을 바꾸지 않으며 변경 전후 선택을 LOG에 기록합니다. 결과 자료를 보고 있던 경우에는 선택이 적용된 입력 자료로 이동하므로 해당 입력 파일을 바로 저장할 수 있습니다. **설치된 전체 플러그인 사용**을 적용하면 파일별 제한이 해제됩니다. **선택 해제** 후 적용하면 플러그인을 사용하지 않습니다.

QC 연결 예제에는 `QC_WORKFLOW`가 선택되어 있습니다. 정밀도·관리규칙의 `QC_ANALYSIS`, 추세의 `RESULT_TREND`, run 검토의 `QC_RUN_REVIEW`가 함께 활성화됩니다. 실행 목록에는 이들을 **연결·별칭**으로 구분합니다. 필요하면 연결 플러그인을 개별 실행할 수도 있습니다.

## META 작성

TAME의 `<META>` 섹션에 다음 TOML을 넣습니다.

```toml
[PLUGINS]
ENABLED = ["QC_WORKFLOW", "RI_EP28"]
```

- `ENABLED`에는 등록된 플러그인 이름을 씁니다. 실행 명령이나 Python 모듈 경로와 구분합니다.
- 이름의 대소문자와 하이픈/밑줄은 같은 것으로 처리합니다. 중복 이름은 한 번만 저장합니다.
- `ENABLED`를 생략하면 기존처럼 설치된 모든 플러그인을 사용할 수 있습니다.
- `ENABLED = []`이면 플러그인 실행을 차단합니다.
- 다른 환경에 없는 이름도 파일에 보존하고 웹에서 확인 필요 상태를 표시합니다. 실행하려면 플러그인을 설치하거나 모듈을 연결해야 합니다.
- 같은 핸들러의 등록 별칭은 함께 사용할 수 있습니다. 예를 들어 `REFERENCE_INTERVAL_EP28`을 선택해도 웹의 `RI_EP28` 경로가 동작합니다.

외부 모듈을 사용할 때는 가져올 모듈과 사용할 플러그인을 함께 선언할 수 있습니다.

```toml
[PLUGINS]
MODULES = ["my_lab.analysis_plugins"]
ENABLED = ["MY_LAB_REPORT"]
```

선택 자체는 코드 실행 허가가 아닙니다. 외부 Python 모듈은 기존처럼 CLI의 `--allow-plugins` 또는 웹의 신뢰 확인이 필요합니다. 파일을 열거나 선택 목록을 저장할 때 외부 코드를 실행하거나 패키지를 자동 설치하지 않습니다. 이 목록은 분석 기능 설정이며 Python 코드를 격리하는 보안 샌드박스가 아닙니다.

```sh
tametools plugins chosen.tame
tametools run-plugin chosen.tame QC_WORKFLOW --output result.tame
```

파일 경로 없는 `tametools plugins`는 설치된 플러그인 전체를 보여줍니다. 파일을 지정하면 선택 및 연결 목록을 보여줍니다. 선택에서 빠진 플러그인을 직접 요청하면 추가해야 할 이름과 `META[PLUGINS].ENABLED` 안내를 표시합니다.

## 플러그인 작성자가 연결을 선언하는 방법

```python
@register_plugin(
    "MY_QC_REPORT",
    dependencies=("QC_ANALYSIS", "RESULT_TREND"),
)
def my_qc_report(dataset, meta, step_name, options):
    ...
```

기존 함수 형태를 유지하고 `dependencies`에 직접 호출하는 플러그인을 선언합니다. 프로그램이 하위 의존성을 따라가며 누락·순환 연결을 실행 전에 확인합니다. 사용자는 최상위 `MY_QC_REPORT`만 선택해도 됩니다. 연결에 선언하지 않은 플러그인을 추가 호출하려면 의존성을 수정하거나 파일의 선택 목록에 포함해야 합니다.

중간 결과가 새 META로 만들어져도 실행 중에는 원 입력의 선택이 유지됩니다. 반환된 TAME 데이터셋에도 선택을 이어받으며 필요한 변경은 LOG에 기록합니다. 단순 Python 함수의 직접 호출은 이 공통 실행 경로를 거치지 않으므로 플러그인 연결에는 `run_plugin` 또는 `PluginWorkflow.plugin`을 사용합니다.

Python API는 `selected_plugin_names(meta)`, `with_plugin_selection(meta, names)`, `plugin_selection_state(meta, list_plugins(meta))`를 제공합니다. `with_plugin_selection`은 원본을 바꾸지 않고 새 META를 반환하며 `MODULES` 등 다른 설정을 보존합니다. `list_plugins`와 `get_plugin`은 선택 편집을 위한 전체 등록 목록을 제공하고, 실행 제한은 `run_plugin`에서 확인합니다.

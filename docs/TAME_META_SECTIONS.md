# TAME META 구획: 누가 쓰고 누가 읽는가

TAME 파일의 `<META>` 는 TOML 이고, 최상위 구획(`[INFO]`, `[COLUMN.x]`, `[WORKS]` ...)이 한 층에 놓인다.
이 문서는 그 층에 **tametools 가 직접 읽거나 쓰는 구획**을 소유자별로 나누어 적는다. 기준은 코드다
(`tametools/src/tametools/meta_sections.py` 의 `CORE_SECTIONS`. 테스트가 sync 의 구획 상수와 작업 단계 이름이 이 목록과 맞는지 검사한다).

> 파일 형식 명세([TAME_FORMAT_SPECIFICATION.md](../TAME_FORMAT_SPECIFICATION.md))의 12절은
> `SETTINGS`, `WORKS`/`SUBWORKS`, `PIPELINES`, `FUNCTIONS`, `PLUGINS`/`USE`, `CATEGORIES`, `DATETIME`, `LOG`, `INTEGRITY` 까지만 다룬다.
> 그 밖의 구획(`SYNC*`, `ANALYSIS_RESULT`, `WORKFLOW_RESULT`, `VISUALIZATIONS` 등)은 이 문서가 설명한다.

## 한 층을 나눠 쓰는 규칙

1. **이 문서의 이름은 tametools 의 것이다.** 플러그인은 이 이름으로 등록할 수 없다(`register_plugin` 이 거부한다).
2. **플러그인의 설정은 플러그인 이름의 구획이다.** `[WESTGARD_QC]`, `[QC_ANALYSIS]` 처럼 쓴다. `WORKS` 의 단계에 별칭을 붙이면
   그 별칭 이름의 구획(`[SUMMARY]` 에 `PLUGIN = "..."`)이 그 단계의 설정이다.
3. **그 밖의 이름은 파일의 것이다.** 검사실이 자기 메모를 `[MY_NOTES]` 로 넣어도 tametools 는 읽지도 지우지도 않는다.
4. **한 글자 틀린 구획은 알려 준다.** 아무도 읽지 않는 구획의 이름이 읽히는 이름과 거의 같으면 `tametools validate` 가
   `META[WESTGARD_QCC] ... Did you mean [WESTGARD_QC]?` 로 알린다. 그런 구획의 설정은 적용되지 않고 기본값으로 실행되기 때문이다.

## 자료를 설명하는 구획 (사람, `init`, 통합이 쓴다)

| 구획 | 내용 | 읽는 곳 |
| --- | --- | --- |
| `INFO` | 파일 설명(`DESCRIPTION`, `LICENSE` 등) | `bootstrap.py`, `init_semantics.py` |
| `COLUMN.<열>` | 열마다의 `ID`, `TAGS`, `UNIT` 등 | `io.py`, `metadata.py`, `actions.py`, `integration.py`, `merge.py` |
| `TAGS` | 열 이름에 태그를 붙이는 표(헤더에 적지 않을 때) | `metadata.py` |
| `TAG_DEFINITIONS` | 파일이 추가로 정의하는 태그 | `tag_catalog.py` |
| `SETTINGS` | 검증·비교자 처리 등 파일 단위 설정(`VALIDATE_ERROR`, `CRR` ...) | `metadata.py`, `bootstrap.py`, `presets.py` |
| `FORMAT` | 파일을 쓰는 방식(직렬화) 설정. 옛 파일에서는 `SETTINGS` 안에 함께 있다 | `metadata.py` |
| `MULTISHEET` | 여러 시트를 하나로 펼친 엑셀의 복원 정보 | `io.py` |
| `CATEGORIES.<어휘>` | 사용자 정의 범주형 어휘 | `categories.py`, `init_semantics.py` |
| `DATETIME` | 날짜·시각 해석 설정 | `datetimes.py` |
| `SEX_NORMALIZATION` | 성별 값 정규화 설정 | `sex_normalization.py` |
| `OBSERVATION` | 관측과 결과 상태에 대한 선언 | `observation_contract.py`, `planned_analysis.py` |
| `REQUIREMENTS` | 이 파일을 읽는 데 필요한 의미 규칙 버전과 기능(`SEMANTICS_VERSION`, `CAPABILITIES`) | `observation_contract.py` |
| `SURVEY` | 표본 설계(가중치 등) 선언 | `analysis_models.py`, `planned_analysis.py`, `merge.py` |
| `SOURCE`, `INTEGRATION_PROFILE` | 여러 출처를 합칠 때의 출처 식별과 통합 규칙 | `integration.py` |
| `SOURCE_CATALOG` | 통합 결과에 남는 출처별 목록(행 수, 지문 등). 통합이 쓴다 | `integration.py` |
| `ANALYSIS_CONTRACT` | 분석 계약의 버전 선언 | `analysis_contract.py`, `integration.py` |

## 할 일을 선언하는 구획 (사람이 쓴다)

| 구획 | 내용 | 실행 |
| --- | --- | --- |
| `WORKS`, `SUBWORKS` | 이름 붙인 단계 목록. 단계는 tametools 의 단계 또는 플러그인 이름 | `tametools run`, `sync` 의 `WORK` |
| `PIPELINES` | 명령 줄의 목록 | `tametools run-pipeline` |
| `ACTIONS`, `ACTION_PIPELINES` | 선언형 표 조작과 그 순서 | `tametools run-action`, `run-action-pipeline` |
| `ANALYSIS_PLAN` | 계획된 분석의 선언 | `tametools analyze` (`--plan` 으로 다른 계획 파일 지정) |
| `ANALYSES`, `USER_GUIDE` | 프리셋이 넣어 주는 분석 목록과 안내 | `presets.py`, 웹 작업실 |
| `FUNCTIONS.<이름>` | 파일에 넣은 함수(`--allow-functions` 가 있어야 실행) | `embedded_functions.py` |
| `PLUGINS` | `MODULES`(외부 모듈, `--allow-plugins` 필요), `ENABLED`(이 파일에서 쓸 플러그인) | `plugin_base/` |
| `USE` | 점이 든 이름을 외부 플러그인 모듈로 읽는 옛 선언 | `plugin_base/manager.py` |
| `SYNC` | 누적 파일 동기화 설정: `SOURCE`, `SHEET`, `KEY_IDS`, `WORK`, `RESULT`, `RESULT_KEY`, `REPORT`, `ON_CHANGE` | `tametools sync` |
| `INIT_OPTIONS`, `INIT_REVIEW` | `init` 의 해석 선택과 검토 내용(정의 파일에 적을 수 있다) | `init_semantics.py`, `init_review.py` |

세 가지 연결 방식(`WORKS`, `PIPELINES`, `ACTION_PIPELINES`)은 단계가 무엇인지만 다르고, 단계 사이의 규칙은 한 곳
(`tametools/chain.py`)에 있다. 각 단계는 앞 단계의 결과를 받고, 데이터셋을 돌려주지 않은 단계는 자료를 그대로 넘기며,
플러그인이 표만 돌려주면 그 표가 다음 단계로 넘어간다. 아무도 모르는 이름의 단계에서는 연결이 끝나고(`SKIPPED` 로 기록),
`run` 과 `sync` 는 그 결과를 저장하지 않는다.

## 도구가 쓰는 구획 (직접 고치지 않는다)

| 구획 | 내용 | 쓰는 곳 |
| --- | --- | --- |
| `LOG` | 실행 이력. 실행마다 입력·출력 지문, 매개변수, 실행한 플러그인(`PLUGIN_IMPLEMENTATION`, `PLUGIN_CALLS`) | `provenance.py` |
| `PROVENANCE` | 이력이 참조하는 실행 환경(`CONTEXTS`)과 설정 스냅숏(`CONTROLS`) | `provenance.py`, `provenance_audit.py` |
| `INTEGRITY` | 변조 감지의 기준이 되는 내용 해시(`CONTENT_HASH`) | `audit.py` |
| `SYNC_STATE` | 마지막 동기화의 순번·지문·이력, 감시하는 플러그인 코드의 해시(`PLUGIN_SHA256`) | `sync.py` |
| `SYNC_RECORDS` | 사람이 결과에 대해 남긴 기록(조치, 검토). 다시 계산되지 않는다 | `sync.py` (`add_record`) |
| `INIT` | `init` 이 내린 판단과 검토가 필요한 항목 | `bootstrap.py` |

## 결과임을 표시하는 구획 (도구나 플러그인이 결과에 쓴다)

| 구획 | 내용 |
| --- | --- |
| `ANALYSIS_RESULT` | 이 데이터셋이 어느 플러그인의 결과인가. 아래 표 참조 |
| `WORKFLOW_RESULT` | `PluginWorkflow` 가 만든 결과: `NAME`, `STEPS`, `PRIMARY` |
| `PLUGIN` | `ANALYSIS_RESULT` 이전부터 쓰던 표시: `NAME`, `MODE`, 적용된 옵션. 옛 결과를 읽기 위해 유지한다 |
| `VISUALIZATIONS` | 결과에 딸린 차트 선언(CLI·웹·docx 가 같은 선언을 쓴다) |
| `INTEGRATION_RESULT` | 여러 출처를 합친 결과의 요약 |
| `SYNC_RESULT` | sync 의 결과 파일임을 표시: `PROJECT`, `SOURCE`, `WORK`, `SEQUENCE`, `SYNCED_AT` |

`ANALYSIS_RESULT` 의 키:

| 키 | 뜻 | 누가 쓰는가 |
| --- | --- | --- |
| `PLUGIN` | 이 데이터셋을 돌려준 플러그인 | 플러그인(`table_output`), 실행 시 tametools 가 확인·교정 |
| `STEP` | 플러그인이 실행된 단계의 이름(플러그인 이름과 다를 때) | tametools |
| `VIA` | 이 결과를 그대로 넘겨 준 안쪽 플러그인들(가까운 것부터) | tametools |
| `VIEW` | 결과 화면을 다른 플러그인의 것으로 지정할 때 그 이름 | 플러그인, 또는 tametools(안쪽 화면을 유지할 때) |
| `MODE` | 같은 플러그인의 결과 종류 | 플러그인 |
| `ROW_KEY`, `SUMMARY_COLUMNS`, `REPORT` | sync 가 결과 행을 맞추고 요약·보고서를 만드는 데 쓰는 선언 | 플러그인 |

"이 결과를 무엇이 만들었나"는 `ANALYSIS_RESULT.PLUGIN`(과 `VIA`)에서 읽는다. 플러그인은 `require_result(dataset, "NAME")` 으로
확인하며, 이 함수는 옛 표시(`PLUGIN.NAME`)도 인정한다.

## 웹 작업실이 쓰는 구획

| 구획 | 내용 |
| --- | --- |
| `WEB_PLUGINS` | 웹 화면에서 작성해 파일에 저장한 플러그인 코드 |
| `WEB_WORKBENCH`, `NHANES_WORKFLOW` | 작업실의 화면 상태와 저장된 분석 |

## 작업 단계 이름

`WORKS` 에서 아래 이름은 tametools 의 단계이고, 같은 이름의 구획이 그 단계의 설정이다. 플러그인 이름으로 쓸 수 없다.

`VALIDATE`, `DESCRIBE`, `EDA`, `CENSOR`, `SURVEY_DESCRIBE`, `ANALYZE`, `CLINICAL_ANALYSIS`, `EVALUATE`, `ANONYMIZE`, `SAMPLE`,
`SPLIT_COMPARATOR`, `SPLIT_CNUM`, `HARMONIZE_COMPARATOR`, `HARMONIZE_CNUM`, `INTEGRATE`, `COLUMNS`, `INFO`

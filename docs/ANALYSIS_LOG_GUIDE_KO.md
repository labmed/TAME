# 분석 이력과 재검증 — tametools 0.4.0

2026-09-12 분석 이력 개선본. 새 기록은 `<META>` 안의 `[[LOG]]`에 `SCHEMA_VERSION = 2`로 저장됩니다. 구형 기록도 원문과 확장 필드를 보존하여 읽습니다.

## 웹에서 확인하기

1. Excel 또는 TAME를 불러와 전처리·분석을 실행합니다. 서로 다른 성별 코드가 있으면 출처별 코드표를 설정합니다.
2. **분석 이력** 탭에서 입력·출력 행 수, 매핑 건수, 출처별 코드표와 경고를 확인합니다. 같은 작업에서 발생한 코어·웹 기록은 한 작업 아래 묶입니다.
3. **적용 설정·입출력·연결 ID 상세**를 펼쳐 실제 적용 설정, seed, 구현 버전, 부모 이벤트와 입출력 해시를 확인합니다.
4. **현재 값으로 기록 검증**을 누르면 현재 편집값으로 다시 검사합니다. META를 바꾸면 설정 불일치, 셀을 바꾸면 데이터 불일치가 나타납니다. 기록 자체를 바꾼 경우에도 별도로 탐지합니다.
5. **전체 이력 JSON 저장**, **TAME 저장** 또는 **Excel 저장**으로 전달합니다. 저장 후 다시 열어 검증하는 것을 권합니다. 분석 오류 시 상단의 **실패 실행 기록 저장**으로 원인과 마지막 단계의 기록을 받을 수 있습니다.

데이터·설정의 수동 편집은 다음 분석·새로고침·저장 시 MANUAL_EDIT로 기록됩니다. 편집 전후 지문을 보존하며, 과거 셀 값을 해시에서 복원하지는 않습니다. LOG 자체가 변경된 경우 이를 새 편집으로 승인 처리하지 않고 오류를 표시합니다.

EP28 보고서 ZIP에는 `analysis_history.csv`(사람이 읽는 작업별 요약)와 `analysis_history.json`(전체 기록·설정 스냅숏·검증 결과)이 포함됩니다. 두 파일도 ZIP의 manifest에 해시가 기록됩니다.

Excel에 v2 로그와 출처 열이 있으면 전체 순서와 값을 보존하는 DATA 시트를 기준 자료로 저장합니다. 출처별 시트는 함께 볼 수 있습니다. tametools는 다시 열 때 DATA를 읽으므로 열 순서·출처 결측값·섞인 행 순서를 잃지 않습니다. 긴 설정 스냅숏은 나누어 저장하며, Excel의 한 셀 글자 수 제한을 넘는 다른 기록은 조용히 자르지 않고 TAME 저장을 안내합니다.

## 판정의 의미

| 표시 | 검사 범위 |
|---|---|
| 기록 연결·내용 | 이벤트 내용, 부모 ID·해시, 실행 환경 및 설정 스냅숏의 연결 |
| 현재 데이터 | 마지막 기록에 저장된 데이터 지문과 현재 자료 비교 |
| 현재 설정 | LOG·PROVENANCE·INTEGRITY를 제외한 META, SCHEMA, JOB 및 유효 열 태그 비교 |
| 외부 산출물 | 접근 가능한 작업 폴더의 CSV·보고서 등 기록된 파일 해시 비교 |
| 과거 실행 재현 | 웹 이력 검사에서는 미검사. 과거 코드를 자동 실행하지 않음 |

**일부만 확인**은 구형 로그에 당시 기록되지 않은 정보가 있거나, 앞선 기록과 실제 입력 사이에 기록되지 않은 변경이 있다는 뜻입니다. **미검사**를 일치로 해석하지 마세요. 원본 파일과 외부 자료의 진본성·통계 방법의 적절성은 이 해시 검사로 판정하지 않습니다. 기록과 해시 전체를 함께 재작성할 수 있는 파일이므로 외부의 신뢰 기준 없이 변경 불가능한 감사 시스템으로 설명해서는 안 됩니다.

행 수는 저장 자료의 행 수입니다. 분석 결과에서는 출력 행이 결과표의 행일 수 있습니다. RI의 OUTLIERS_FLAGGED는 집단별 검사값 표시 기록 수이며 고유 대상자 수와 다를 수 있습니다. 성별의 MAPPED_ROWS는 매핑을 적용한 행 수입니다. 같은 표준 열을 재계산하면 ADDED_COLUMNS는 0, UPDATED_COLUMNS는 1입니다.

## CLI

```sh
tametools logs result.tame
tametools logs result.tame --json
tametools logs result.tame --full
tametools audit result.tame --json
tametools audit result.tame --artifacts-dir ./report
tametools verify input_with_works.tame --work DEFAULT
```

- `logs` 기본값은 짧은 작업별 요약입니다. `--json`은 전체 LOG, `--full`은 기존 넓은 표를 표시합니다. `--limit`와 `--reverse`도 사용할 수 있습니다.
- `audit`는 기록과 현재 데이터·설정을 검사합니다. 검사 실패가 있으면 종료 코드 1을 반환합니다. 미검사·부분 검사는 JSON의 개별 판정을 함께 확인해야 합니다.
- `verify`는 입력에 선언된 WORKS를 현재 환경에서 두 번 실행해 데이터, 계산에 관련된 설정, 생성 표·차트를 비교합니다. 미실행·건너뜀이나 검증 이슈를 성공으로 간주하지 않습니다. 과거 파일의 모든 명령을 자동 복원하는 기능은 아닙니다. 파일을 생성하거나 사용자 코드를 호출하는 WORK는 그 동작도 실행하므로 검증용 경로와 신뢰한 코드를 사용합니다.
- `run`, `run-pipeline`, `run-action-pipeline`은 기본적으로 상세 단계 기록을 남깁니다. 명시적인 `--log-level simple`·`none`은 그대로 지원하지만 이 경우 모든 내부 단계 기록을 기대할 수 없습니다.
- SAMPLE의 seed를 생략하면 생성한 실제 seed를 EFFECTIVE_PARAMS에 저장합니다. 재현할 때 그 값을 사용하세요. 요청 옵션과 실제 적용 옵션은 구분되어 있습니다.
- CLI에서 자료를 읽은 뒤 실행 단계가 실패하면 출력 경로 옆(출력이 없으면 현재 폴더)의 `tametools_failed_runs`에 JSON을 남기고 경로를 출력합니다. Python API의 예외에서는 `audit_dataset`으로 마지막 자료와 실패 로그를 확인할 수 있습니다.

## 공개 자료로 chain 시험하기

배포본의 `web/backend/examples/provenance_chain_input.tame`에는 Kenya 공개 자료 533행과 `SAMPLE → RI_EP28` WORKS가 들어 있습니다. 고정 seed 42로 240행을 추출한 뒤 ALT 후보 참고구간 1행을 만듭니다. tametools가 설치된 환경의 배포본 루트에서 다음 CLI 명령으로 실행할 수 있습니다. 웹에서는 아래 저장된 결과 예시를 열어 **분석 이력**을 확인할 수 있습니다.

```sh
tametools run web/backend/examples/provenance_chain_input.tame DEFAULT --output chain_result.tame
tametools logs chain_result.tame
tametools audit chain_result.tame --json
tametools verify web/backend/examples/provenance_chain_input.tame --work DEFAULT
```

저장된 결과 예시는 같은 폴더의 `provenance_chain_result.tame`입니다. 현재 데이터·설정은 일치하고 구형 기록 2개 때문에 전체 이력은 일부만 확인으로 나오는 것이 정상입니다. 같은 환경에서 WORKS를 두 번 실행한 데이터·표·차트 지문 일치를 확인했습니다. 표본 추출은 기능 시험을 위한 것이며 이 예시를 임상 채택 구간으로 해석하지 않습니다.

## 새 기록의 구성

| 필드 | 의미 |
|---|---|
| EVENT_ID / RUN_ID / SEQUENCE | 이벤트, 실행, 같은 실행 안의 순서 |
| OPERATION_ID | 코어·웹 등 같은 사용자 작업의 기록 묶음 |
| PARENT_EVENT_IDS / PARENT_HASHES | 분기·병합을 포함한 부모 연결 |
| STATUS | SUCCEEDED, FAILED, SKIPPED, CANCELLED |
| TIMESTAMP / FINISHED_AT / DURATION_MS | 시간과 측정 가능한 실행 경계의 소요시간. 독립적인 수동 append에는 소요시간을 만들지 않음 |
| INPUTS / OUTPUTS / COUNTS | 자료 식별자, 범위가 명시된 해시, 행·열·변환 건수, 결과표·파일 정보 |
| PARAMS / EFFECTIVE_PARAMS | 선언/요청 값 및 실행기·플러그인이 보고한 적용 값 |
| TOOL_VERSION / CONTEXT_SHA256 | 코드 버전과 실행 환경 스냅숏 연결 |
| EVENT_SHA256 | 해당 이벤트 내용의 SHA-256 |

PROVENANCE.CONTEXTS에는 Python·주요 라이브러리 버전과 코어 코드 해시가 있고, CONTROLS에는 해시로 중복 제거한 설정 스냅숏이 있습니다. 플러그인 실행 경계에서는 호출한 플러그인의 소스 해시도 기록합니다. EP28은 기본값을 포함한 적용 설정을 기록합니다. 임의 사용자 코드 내부에서 접근한 모든 파일과 라이브러리까지 자동 계측하는 것은 아닙니다.

해시 규약은 `tame-provenance-json-v2/sha256`입니다. 구분자 연결 대신 구조화한 셀 상태와 값으로 지문을 만들고, 유한 NUM 값의 동등한 숫자 표기는 정규화합니다. 셀의 ABSENT·NULL·EMPTY·WS는 구별합니다. 완성 파일의 바이트 해시는 외부 manifest에 둡니다. 과거 PARAMS.input_hash/output_hash의 16자리 값과 새 64자리 해시는 다른 범위이므로 서로 비교하지 않습니다.

TOML에 null이 없으므로 로그의 `None`은 `{ __TAME_LOG_TYPE__ = "null" }`로 보존합니다. 실제 빈 문자열과 구별되며 Python의 `restore_log_values`로 복원할 수 있습니다. 이 예약 키는 로그의 타입 표시에 사용합니다. 구형 로그의 OPERATOR 등 알 수 없는 추가 필드도 제거하지 않습니다.

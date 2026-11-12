tametools 플러그인 폴더
========================

이 폴더(tametools.exe 와 같은 위치의 plugins\)에 있는 플러그인이 tametools 의 플러그인입니다.
모든 플러그인은 동일하게 취급됩니다 — 여기 들어있는 기본 플러그인이든, 직접 작성해 추가한
플러그인이든 차이가 없습니다. 이 폴더는 자동으로 탐색되어 로드됩니다(--allow-plugins 불필요).

플러그인은 .py 파일 하나이거나, __init__.py 가 들어 있는 폴더 하나입니다.

기본 포함 플러그인
------------------
- reference_interval.py  : REFERENCE_INTERVAL (RI)
- reference_interval_ep28.py : RI_EP28 (REFERENCE_INTERVAL_EP28), EP28 연구·검증 보고서
- clinical_chemistry.py  : CHEMISTRY_ANALYSIS (CLINICAL_CHEMISTRY)
- clinical_flags.py      : ABNORMAL_FLAG(FLAG_ABNORMAL), AUTOVERIFICATION(RULE_ENGINE)
- clinical_stats.py      : CORRELATION, GROUP_TEST, METHOD_COMPARISON, QC_ANALYSIS, RESULT_TREND, ROC_ANALYSIS
- lab_tat.py             : LAB_TAT_ANALYSIS (TAT_ANALYSIS)
- qc_workflow.py, qc_run_review.py : QC_WORKFLOW, QC_RUN_REVIEW
- westgard_qc\           : WESTGARD_QC (Westgard 멀티룰, QC 보고서), 명령 qc-action · qc-review

이 파일들은 자유롭게 열어보거나 수정·삭제할 수 있습니다. 삭제하면 해당 플러그인만 사라지며
프로그램 구동 자체에는 문제가 없습니다(없는 이름으로 호출하면 "unknown plugin" 으로 처리됩니다).

새 플러그인 추가
----------------
1) .py 파일(또는 __init__.py 가 있는 폴더)을 이 폴더에 넣습니다(examples\ 의 예제를 템플릿으로 쓰면 됩니다).
2) 목록 확인:    tametools.exe plugins
   읽지 못한 파일이 있으면 "warning: drop-in plugin not loaded: 파일: 이유" 로 알려 줍니다.
3) 실행 예:      tametools.exe run-plugin DATA.tame COUNT_BY_TEST --output out.tame

작성 규칙
---------
- tametools 에서 가져다 쓰는 것은 모두 tametools.plugin_api 에서 import 합니다:
      from tametools.plugin_api import OperationOutput, register_plugin
  (예전 방식인 from tametools.plugin_base.base import register_plugin 도 그대로 동작합니다.)
- @register_plugin("이름", description="...") 로 등록하고, 핸들러는 OperationOutput 을 return 합니다.
- 파일 하나짜리 플러그인에서 같은 폴더의 다른 파일을 쓰려면 밑줄(_)로 시작하는 이름(_helper.py)으로 두고
  "from _helper import ..." 처럼 절대 import 합니다. 밑줄로 시작하는 파일과 폴더는 플러그인으로 읽지 않습니다.
- 폴더(패키지)형 플러그인 안에서는 "from .helpers import ..." 같은 상대 import 를 쓸 수 있습니다.
- examples\ 처럼 __init__.py 가 없는 하위 폴더는 자동 로드되지 않습니다(템플릿 보관용).
- 기본 플러그인과 같은 이름은 쓸 수 없습니다(무시되고 경고가 나옵니다).
- tametools 가 직접 쓰는 이름(INFO, COLUMN, WORKS, SYNC, VALIDATE 등)으로도 등록할 수 없습니다.
- 다른 플러그인을 쓰려면: 실행은 run_plugin(dataset, "이름", meta, "이름", 옵션), 그 플러그인의 함수는
  plugin_module("이름").함수(...) 로 가져다 씁니다. 쓰는 플러그인은 register_plugin(dependencies=("이름",)) 에 적습니다.
  (같은 폴더의 다른 플러그인 파일을 "from 파일이름 import ..." 로 가져와도 되며, 그 파일은 한 번만 실행됩니다.)
- 설치본에 들어 있지 않은 라이브러리는 쓸 수 없습니다.

플러그인은 분석 말고도 명령(register_command), 결과 화면(register_view), 내보내기 형식(register_exporter)을 붙일 수 있습니다.
자세한 내용은 문서 PLUGIN_AUTHOR_GUIDE.md 를 보십시오.

다른 위치(선택)
---------------
- 환경변수 TAMETOOLS_PLUGIN_PATH 또는 %LOCALAPPDATA%\tametools\plugins 에 둔 플러그인은
  신뢰되지 않은 외부 위치로 간주되어 --allow-plugins 를 줘야 로드됩니다.
- 이 폴더가 Program Files 아래(MSI 설치)라면 읽기전용이라 파일 추가에 관리자 권한이 필요합니다.

tametools 플러그인 폴더
========================

이 폴더(tametools.exe 와 같은 위치의 plugins\)에 있는 .py 파일이 tametools 의 플러그인입니다.
모든 플러그인은 동일하게 취급됩니다 — 여기 들어있는 기본 플러그인이든, 직접 작성해 추가한
플러그인이든 차이가 없습니다. 이 폴더는 자동으로 탐색되어 로드됩니다(--allow-plugins 불필요).

기본 포함 플러그인
------------------
- reference_interval.py  : REFERENCE_INTERVAL (RI)
- reference_interval_ep28.py : RI_EP28 (REFERENCE_INTERVAL_EP28), EP28 연구·검증 보고서
- clinical_chemistry.py  : CHEMISTRY_ANALYSIS (CLINICAL_CHEMISTRY)
- clinical_flags.py      : ABNORMAL_FLAG(FLAG_ABNORMAL), AUTOVERIFICATION(RULE_ENGINE)
- clinical_stats.py      : CORRELATION, GROUP_TEST, METHOD_COMPARISON, QC_ANALYSIS, RESULT_TREND, ROC_ANALYSIS
- lab_tat.py             : LAB_TAT_ANALYSIS (TAT_ANALYSIS)

이 파일들은 자유롭게 열어보거나 수정·삭제할 수 있습니다. 삭제하면 해당 플러그인만 사라지며
프로그램 구동 자체에는 문제가 없습니다(없는 이름으로 호출하면 "unknown plugin" 으로 처리됩니다).

새 플러그인 추가
----------------
1) .py 파일을 이 폴더에 넣습니다(examples\ 의 예제를 템플릿으로 쓰면 됩니다).
2) 목록 확인:    tametools.exe plugins
3) 실행 예:      tametools.exe run-plugin DATA.tame COUNT_BY_TEST --output out.tame

작성 규칙
---------
- import 는 반드시 "절대 import" 를 씁니다:  from tametools.plugin_base.base import register_plugin
  (상대 import 인 `from ..models` / `from .base` 는 파일 경로 로드에서 동작하지 않습니다.)
- @register_plugin("이름", description="...") 로 등록합니다.
- 밑줄(_)로 시작하는 파일(예: _helper.py)은 로드 대상에서 제외됩니다(공용 헬퍼로 사용 가능).
- examples\ 같은 하위 폴더는 자동 로드되지 않습니다(템플릿 보관용).

다른 위치(선택)
---------------
- 환경변수 TAMETOOLS_PLUGIN_PATH 또는 %LOCALAPPDATA%\tametools\plugins 에 둔 플러그인은
  신뢰되지 않은 외부 위치로 간주되어 --allow-plugins 를 줘야 로드됩니다.
- 이 폴더가 Program Files 아래(MSI 설치)라면 읽기전용이라 파일 추가에 관리자 권한이 필요합니다.

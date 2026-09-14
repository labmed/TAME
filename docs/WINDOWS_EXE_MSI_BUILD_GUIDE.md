# Windows EXE/MSI 빌드 가이드

## 전제

Windows용 `.exe`와 `.msi`는 Windows에서 빌드해야 한다. PyInstaller는 Linux/WSL에서 Windows exe를
cross-compile하지 않는다.

빌드 PC 준비:

- Windows 10/11 x64
- Python 3.10 이상
- Node.js LTS
- WiX Toolset v3: MSI 생성 시 필요
- 인터넷 연결: 최초 `pip`, `npm` 의존성 다운로드에 필요

## 0.4.0 통합 빌드

프로젝트 루트에서 실행한다. 전용 `.build-venv`를 만들며 전역 Python을 변경하지 않는다.
기본 출력 위치는 `dist/<pyproject 버전>/`이고, 기존 버전의 배포 폴더를 삭제하지 않는다.
Python 패키지와 CLI/Web의 버전, EXE 파일 속성, MSI ProductVersion을 함께 맞춘다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_windows_release.ps1
# WiX가 PATH에 없으면:
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_windows_release.ps1 -WixDir C:/tools/wix311
```

`-Target Python|Exe|Msi|All`로 단계를 선택할 수 있다. `Msi`는 먼저 생성된 같은 버전의
EXE가 있어야 한다. 루트의 `.wix311`도 자동 탐색한다. `-SkipInstall`은 준비된 전용
가상환경을 재사용할 때만 지정한다. frontend는 lock 파일로 `npm ci`, `npm run check`,
`npm run build`를 수행한다. 빌드 시 설치된 Python 버전 목록은 출력 폴더에 남긴다.

Windows 배포에는 EP28 참고구간, 통계·survey, NHANES XPT 읽기, Pint 단위 변환,
Parquet, 차트·Word 보고서와 Web 의존성을 포함한다. 필수 라이브러리가 없으면 빌드를 중단한다.
`stage_windows_audit_files.py`가 `_internal/tametools/`에 현재 소스 원문을 포함하고,
보고서의 코드 해시·의존성 버전을 기록할 메타데이터를 함께 복사한다.
`IMPLEMENTATION_SHA256.json`에는 배포된 소스 파일별 해시를 남긴다.

## EXE 생성

저장소 루트에서 실행:

```bat
scripts\build_windows_exe.bat
```

생성물(**onedir 통합 폴더** — CLI/Web 두 exe 가 `_internal` 의존성을 공유):

- `dist\0.4.0\windows-exe\tametools\tametools.exe`: CLI 실행 파일
- `dist\0.4.0\windows-exe\tametools\tametools-web.exe`: 웹 워크벤치 실행 파일
- `dist\0.4.0\windows-exe\tametools\_internal\`: 공유 런타임 의존성(pandas/matplotlib 등)
- `dist\0.4.0\windows-exe\tametools\plugins\`: **모든 플러그인이 들어있는 폴더**. 기본 플러그인 6개
  (`reference_interval.py`, `clinical_chemistry.py`, `clinical_flags.py`, `clinical_stats.py`,
  `lab_tat.py`, `reference_interval_ep28.py`)와 `README.txt`, 템플릿 `examples\` 가 동봉된다. 빌트인 같은 별도 개념은 없고,
  이 폴더의 `.py` 가 곧 플러그인이며 `--allow-plugins` 없이 기본 로드된다. 파일을 지우면 해당
  플러그인만 사라질 뿐 프로그램 구동에는 문제가 없다(없는 이름 호출 시 "unknown plugin").
  (빌드: `build_windows_exe.bat` 가 `tametools\src\tametools\plugins\*.py` 와
  `packaging\windows\plugins\` 를 onedir 최상위 `plugins\` 로 복사한다.)

onedir 방식이라 실행 시 임시 폴더(`%TEMP%\_MEIxxxx`)로 압축을 풀지 않으므로 첫 실행이 빠르다
(onefile 은 매 실행 100MB+ 압축 해제로 ~10초가 걸렸고, onedir 은 1초 미만). 또한 실행파일 옆
`plugins\` 폴더가 자동 탐색되어 드롭인 플러그인을 두기 쉽다(아래 "설치 후 플러그인 추가" 참고).

검증:

```bat
dist\0.4.0\windows-exe\tametools\tametools.exe --help
dist\0.4.0\windows-exe\tametools\tametools-web.exe --help
dist\0.4.0\windows-exe\tametools\tametools-web.exe
```

`tametools-web.exe`는 내장 FastAPI 서버를 시작하고 브라우저를 `http://127.0.0.1:8765`로 연다.
프론트엔드는 `web\frontend\build`를 PyInstaller data로 포함하므로 운영 중 Node.js를 실행하지 않는다.

## MSI 생성

WiX Toolset v3의 `candle.exe`, `light.exe`, `heat.exe`가 `PATH`에 있어야 한다.
onedir 폴더 전체(수천 개 파일)는 `heat.exe`가 자동 수확(harvest)하여 MSI에 담는다.

```bat
scripts\build_windows_msi.bat
```

생성물:

- `dist\0.4.0\tametools-0.4.0-x64.msi`

MSI는 onedir 폴더 전체(두 exe + `_internal` 의존성)를 64비트 `Program Files\tametools`
(`ProgramFiles64Folder`)에 설치하고, 설치 폴더를 시스템 `PATH`에 추가한다. 시작 메뉴에는
`tametools Web Workbench` 바로가기(`tametools-web.exe` 대상)가 생성된다.

## 구현 파일

- `packaging\windows\tametools_cli_launcher.py`
- `packaging\windows\tametools_web_launcher.py`
- `packaging\windows\tametools.spec` (CLI/Web 통합 onedir spec)
- `packaging\windows\tametools.wxs` (heat 수확 ComponentGroup 참조)
- `scripts\build_windows_exe.bat`
- `scripts\build_windows_msi.bat`

## 플러그인 추가/관리 (모두 드롭인)

플러그인은 한 종류뿐이다 — `plugins\` 폴더의 `.py` 파일이 곧 플러그인이다. 동봉된 기본 6개와
사용자가 추가한 플러그인은 완전히 동일하게 취급된다. 코어는 플러그인을 "이름"으로만 호출하므로,
어떤 플러그인 파일을 지워도 그 플러그인만 사라지고 프로그램 구동에는 문제가 없다.

탐색 위치와 신뢰 수준:

- **실행파일 옆 `plugins\` 폴더**(동봉, 기본 로드): `Program Files\tametools\plugins` 또는
  무설치 폴더의 `tametools\plugins`. 여기 `.py` 를 두면 `--allow-plugins` 없이 자동 로드된다.
  개발/pip 설치에서는 패키지의 `tametools\plugins\` 가 같은 역할을 한다.
- **외부 위치**(신뢰하지 않음, `--allow-plugins` 필요): 환경변수 `TAMETOOLS_PLUGIN_PATH`(여러 개는
  `;` 로 구분), `%LOCALAPPDATA%\tametools\plugins`, `.tame` 의 `META[PLUGINS].MODULES` 점 모듈.
  설치 폴더에 쓸 권한이 없는 사용자가 쓰기 좋은 위치다.

주의: `Program Files\tametools\plugins` 는 읽기전용이라 파일 추가/수정에 관리자 권한이 필요하다.
외부에서 동봉본과 같은 이름을 등록해도 동봉본이 우선하며(보호) 경고가 표시된다.

작성 규칙: **절대 import** 를 쓴다(`from tametools.plugin_base.base import register_plugin`,
`from tametools.models import ...`). 상대 import(`from ..models`, `from .base`)는 파일 경로
로드에서 동작하지 않는다. `plugins\examples\` 의 예제를 템플릿으로 복사해 시작하면 된다.

```bat
:: (1) 동봉 폴더에 추가 — 기본 로드(플래그 불필요). 설치본은 관리자 권한 필요.
copy my_plugin.py "C:\Program Files\tametools\plugins\"
tametools plugins
tametools run-plugin DATA.tame MY_PLUGIN --output out.tame

:: (2) 권한 없는 사용자 위치 — 외부라서 --allow-plugins 필요.
mkdir "%LOCALAPPDATA%\tametools\plugins"
copy my_plugin.py "%LOCALAPPDATA%\tametools\plugins\"
tametools plugins --allow-plugins
tametools run-plugin DATA.tame MY_PLUGIN --allow-plugins --output out.tame
```

동작 방식과 주의:

- 폴더의 `*.py`(밑줄 `_` 로 시작하지 않는 파일)를 **파일 경로로 직접 로드**한다. 하위 폴더
  (`examples\` 등)는 자동 로드되지 않는다. `PYTHONPATH` 설정이 필요 없다.
- **동봉 폴더(실행파일 옆 `plugins\`, 개발 `plugins\`)는 기본 로드**된다. 외부 위치
  (`TAMETOOLS_PLUGIN_PATH`, `%LOCALAPPDATA%`, `META[PLUGINS].MODULES`)만 **`--allow-plugins`** 게이트.
- **동봉(신뢰) 이름은 보호**된다(외부가 같은 이름을 등록해도 무시되고 경고). 깨진 파일 하나가
  전체를 막지 않으며 `tametools plugins`(외부는 `--allow-plugins`) 출력에 경고로 표시된다.
- frozen exe 에 번들된 의존성(pandas, scipy, tametools 등)은 플러그인에서 import 가능하지만,
  **번들에 없는 서드파티 라이브러리는 import 되지 않는다**. 그런 의존성이 필요하면 `spec` 의
  `hiddenimports`(현재 `collect_submodules("tametools")` 및 분석·웹 의존성)에 추가해 재빌드한다.

## 빌드 후 Windows 검증 체크리스트

EXE/MSI 는 Windows 빌드 PC 에서만 만들 수 있으므로(WSL 크로스컴파일 불가), 빌드 직후 아래를 실측한다.

- [ ] `tametools\tametools.exe --help`, `tametools\tametools.exe plugins` 정상.
- [ ] 첫 실행 속도: onedir 이므로 `--help` 가 1초 안팎(onefile 의 ~10초가 아님)인지.
- [ ] 차트 생성: REFERENCE_INTERVAL / LAB_TAT_ANALYSIS 가 frozen 에서 matplotlib 차트를 생성하고
      `--report` docx 까지 확인. (spec 이 `collect_data_files("matplotlib")`/`("docx")` 로 mpl-data·
      docx 기본 템플릿을 번들하고 `matplotlib.backends.backend_agg` 를 hiddenimport 로 포함한다.
      빌드 PC 는 `build_windows_exe.bat` 가 `tametools[analysis,nhanes,integration,parquet,web,windows]` 를 설치하므로 데이터가
      실제 수집된다 — report extra 가 빠지면 차트/보고서가 비게 되니 설치 여부 확인.)
- [ ] 한글 출력 인코딩(cmd cp949 vs UTF-8) 깨짐 없는지.
- [ ] 기본 플러그인(동봉, 기본 로드): `tametools.exe plugins` 가 **`--allow-plugins` 없이**
      REFERENCE_INTERVAL/CHEMISTRY_ANALYSIS/QC_ANALYSIS/LAB_TAT_ANALYSIS 등을 나열하는지.
- [ ] 삭제 안전성: `plugins\reference_interval.py` 를 지워도 `tametools.exe plugins`/`--help` 가
      정상(0)이고, 없는 이름 호출은 "unknown plugin" 으로 graceful 처리되는지.
- [ ] 새 플러그인 추가: `plugins\` 에 `.py` 를 넣으면 `--allow-plugins` 없이 인식되는지.
- [ ] 외부 위치: `%LOCALAPPDATA%\tametools\plugins` 는 `--allow-plugins` 가 있어야 동작하는지.
- [ ] `tametools-web.exe`: 포트 8765 점유/방화벽 프롬프트, 프론트엔드(`web/frontend/build`) 표시.
- [ ] 표준 사용자 계정(비관리자)에서 실행 가능 여부.

## 제한과 운영 권고

- 코드 서명은 아직 포함하지 않았다. 병원 PC의 보안 정책에 따라 서명 인증서 적용이 필요할 수 있다.
- MSI는 기본적으로 per-machine 설치이며 관리자 권한이 필요할 수 있다.
- EXE/MSI 빌드는 인터넷 가능한 빌드 PC에서 수행한다. 설치 후 일반 파일 분석에는 별도 Python 설치가 필요하지 않으며, NHANES 다운로드 기능에는 인터넷이 필요하다.

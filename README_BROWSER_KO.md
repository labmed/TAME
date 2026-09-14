# 개인 컴퓨터에서 Chrome으로 tametools 실행하기

tametools exe나 MSI를 설치하지 않는 **개인용 브라우저 배포본**입니다. 분석은 자신의 컴퓨터에서 실행하고, 화면은 Chrome에서 사용합니다. 외부 서버·Docker·Node.js는 필요하지 않습니다.

**준비물: 64비트 Python 3.10–3.13, Google Chrome.** 처음 실행할 때는 분석 패키지를 받기 위한 인터넷 연결이 필요합니다. HTML 파일만 더블클릭해서 모든 분석을 수행하는 방식은 아니며, 포함된 시작 스크립트가 Python 분석 서버를 자동으로 실행합니다.

## Windows

1. 배포 ZIP을 원하는 폴더에 **전체 압축 해제**합니다.
2. `Start_Chrome.cmd`를 더블클릭합니다.
3. 첫 실행에는 필요한 패키지를 설치합니다. 설치 창을 닫지 말고 기다리세요.
4. 준비가 끝나면 Chrome이 열립니다. **공개 예제 불러오기**에서 NHANES 또는 Kenya 자료를 선택하거나 **파일 열기**로 시작합니다.

Python을 찾지 못했다면 [Python 공식 다운로드](https://www.python.org/downloads/)에서 지원 버전의 64비트 Python을 준비한 뒤 다시 실행합니다. 여러 Python 버전이 있는 경우에는 원하는 버전을 지정할 수 있습니다.

```powershell
py -3.12 scripts/start_tametools_browser.py
```

## macOS / Linux

터미널에서 압축을 푼 폴더로 이동하여 실행합니다.

```bash
bash Start_Chrome.sh
```

macOS에는 더블클릭용 `Start_Chrome.command`도 포함했습니다. Linux에서 `venv` 또는 `ensurepip`를 찾지 못하면 해당 Python 버전의 `venv` 지원 패키지를 준비해야 합니다. Windows와 Linux에서 검증했으며, macOS 시작 스크립트는 포함했지만 이 작업 환경에서는 실제 실행을 검증하지 않았습니다.

## 다음 실행과 종료

- 다음에도 같은 시작 파일을 실행합니다. 준비된 Python 환경을 재사용하며 매번 패키지를 설치하지 않습니다.
- 이미 이 폴더의 앱이 실행 중이면 기존 화면을 엽니다. 기본 포트가 다른 프로그램에서 사용 중이면 다음 빈 포트를 선택합니다.
- Chrome이 자동으로 열리지 않으면 시작 창에 표시된 주소를 Chrome 주소창에 붙여 넣습니다. 기본값은 `http://127.0.0.1:8765`입니다.
- 작업을 끝낼 때 결과를 저장하고 **시작 창에서 Ctrl+C**를 누릅니다. 브라우저 탭을 닫아도 분석 서버는 계속 실행됩니다.
- 작업 이력은 브라우저 메모리에 있으므로 새로고침·종료 전에 TAME·Excel·보고서를 저장하세요. 진행 중 NHANES 다운로드는 같은 서버에서 이어서 확인할 수 있습니다. 받은 NHANES 원자료는 다음 실행에서 재사용합니다.

공개 예제와 보유한 파일은 같은 작업실에서 분석합니다. [웹 사용 안내](docs/WEB_QUICKSTART_KO.md)에서 입력 지정, EP28 설정, 보고서 저장과 재현 순서를 확인하세요.

## 파일과 설치 위치

| 위치 | 내용 |
|---|---|
| `.browser-runtime/venv/` | 이 앱 전용 Python 패키지; 기존 Python 패키지와 분리 |
| `.browser-runtime/nhanes/` | 받은 NHANES 원자료·설명서·출처 기록 |
| `.browser-runtime/environment.json` | 설치 환경과 제품 소스 확인용 해시 |
| `web/frontend/build/` | 미리 빌드한 브라우저 화면; 사용자는 Node.js를 설치할 필요 없음 |
| `tametools/`, `web/backend/` | 분석 엔진과 웹 API |

데이터는 이 컴퓨터에서 처리합니다. 서버는 `127.0.0.1`에만 연결되어 다른 컴퓨터에 공개되지 않습니다. 처음 패키지를 설치하거나 CDC 원자료를 받을 때 외부에 연결합니다. 받은 파일을 재사용하는 분석은 추가 다운로드 없이 진행할 수 있습니다.

## 실행 옵션

```bash
# 환경만 먼저 준비
python scripts/start_tametools_browser.py --prepare-only

# Chrome 자동 실행 없이 주소만 표시
python scripts/start_tametools_browser.py --no-browser

# 앱을 종료한 뒤 Python 환경 복구; 받은 NHANES 자료는 유지
python scripts/start_tametools_browser.py --repair

# 실행 환경을 다른 쓰기 가능한 폴더에 저장
python scripts/start_tametools_browser.py --runtime-dir "D:/tametools-data"

# 다른 시작 포트 선택
python scripts/start_tametools_browser.py --port 8865
```

설치가 중단되거나 재부팅된 경우 같은 시작 파일을 다시 실행하면 설치를 이어서 시도합니다. 동시에 두 창에서 설치하지 않도록 자동 잠금을 사용하며, 종료되면 잠금은 해제됩니다. Chrome이 일반적인 설치 위치에 없다면 `TAMETOOLS_CHROME` 환경변수에 Chrome 실행파일 경로를 지정할 수 있습니다.

제품 버전: **0.4.0**. 이 ZIP에는 tametools exe, MSI, Python 실행파일, 가상환경을 포함하지 않습니다. Python 환경은 첫 실행 시 해당 컴퓨터에 맞게 준비합니다.

@echo off
setlocal

set "ROOT=%~dp0.."
set "BACKEND_PORT=8765"
set "FRONTEND_PORT=5173"
set "BACKEND_URL=http://127.0.0.1:%BACKEND_PORT%"
set "FRONTEND_URL=http://127.0.0.1:%FRONTEND_PORT%"
set "FRONTEND_BUILD_INDEX=%ROOT%\web\frontend\build\index.html"

echo [tametools] 저장소: %ROOT%
echo [tametools] 웹앱을 시작합니다.

where python >nul 2>nul
if errorlevel 1 (
  echo 오류: Python 3.10 이상을 설치한 뒤 다시 실행하세요.
  pause
  exit /b 1
)

if not exist "%ROOT%\wheelhouse" (
  python -c "import re, sys, setuptools; m = re.match(r'(\d+)(?:\.(\d+))?', setuptools.__version__); v = (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0); sys.exit(0 if v >= (68, 0) else 1)" >nul 2>nul
  if errorlevel 1 (
    echo 오류: setuptools 68 이상이 필요합니다.
    echo 인터넷이 가능한 환경에서는 먼저 python -m pip install --upgrade setuptools 를 실행하세요.
    pause
    exit /b 1
  )
)

set "NEED_FRONTEND_BUILD=0"
if "%TAMETOOLS_REBUILD_FRONTEND%"=="1" set "NEED_FRONTEND_BUILD=1"
if not exist "%FRONTEND_BUILD_INDEX%" set "NEED_FRONTEND_BUILD=1"

if "%TAMETOOLS_DEV_FRONTEND%"=="1" set "NEED_NPM=1"
if "%NEED_FRONTEND_BUILD%"=="1" set "NEED_NPM=1"
if "%NEED_NPM%"=="1" (
  where npm >nul 2>nul
  if errorlevel 1 (
    echo 오류: 프론트엔드 build가 필요하지만 npm을 찾을 수 없습니다.
    echo Node.js LTS를 설치하거나, build\가 포함된 배포 번들을 사용하세요.
    pause
    exit /b 1
  )
)

if "%TAMETOOLS_WEB_EXTRAS%"=="" (
  set "TAMETOOLS_WEB_EXTRAS=web,report,analysis,nhanes"
)

if exist "%ROOT%\wheelhouse" (
  python -m pip install --no-index --find-links "%ROOT%\wheelhouse" "tametools[%TAMETOOLS_WEB_EXTRAS%]"
) else if "%TAMETOOLS_EDITABLE%"=="1" (
  python -m pip install -e "%ROOT%\tametools[%TAMETOOLS_WEB_EXTRAS%]" --no-build-isolation
) else (
  python -m pip install "%ROOT%\tametools[%TAMETOOLS_WEB_EXTRAS%]" --no-build-isolation
)
if errorlevel 1 (
  echo 오류: Python 패키지 설치에 실패했습니다.
  pause
  exit /b 1
)

if "%NEED_NPM%"=="1" if not exist "%ROOT%\web\frontend\node_modules" (
  echo [tametools] 프론트엔드 의존성을 설치합니다.
  pushd "%ROOT%\web\frontend"
  call npm install
  if errorlevel 1 (
    popd
    echo 오류: 프론트엔드 의존성 설치에 실패했습니다.
    pause
    exit /b 1
  )
  popd
)

if "%NEED_FRONTEND_BUILD%"=="1" (
  echo [tametools] 프론트엔드 프로덕션 빌드를 생성합니다.
  pushd "%ROOT%\web\frontend"
  set "VITE_TAMETOOLS_API_BASE="
  call npm run build
  if errorlevel 1 (
    popd
    echo 오류: 프론트엔드 빌드에 실패했습니다.
    pause
    exit /b 1
  )
  popd
)

start "tametools backend" cmd /k "cd /d ""%ROOT%\web\backend"" && python -m uvicorn app.main:app --host 127.0.0.1 --port %BACKEND_PORT%"

if "%TAMETOOLS_DEV_FRONTEND%"=="1" (
  start "tametools frontend" cmd /k "cd /d ""%ROOT%\web\frontend"" && set ""VITE_TAMETOOLS_API_BASE=%BACKEND_URL%"" && npm run dev -- --port %FRONTEND_PORT%"
) else (
  set "FRONTEND_URL=%BACKEND_URL%"
  echo [tametools] 프론트엔드는 백엔드에서 정적 서빙합니다: %FRONTEND_URL%
)

timeout /t 5 >nul
start "" "%FRONTEND_URL%"

echo [tametools] 브라우저에서 %FRONTEND_URL% 를 여세요.
echo [tametools] 종료하려면 backend/frontend 창을 닫으세요.
pause

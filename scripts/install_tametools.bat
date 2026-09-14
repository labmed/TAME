@echo off
setlocal

set "ROOT=%~dp0.."
set "PACKAGE=%ROOT%\tametools"

if "%TAMETOOLS_EXTRAS%"=="" (
  set "TAMETOOLS_EXTRAS=report,web"
)

if not "%TAMETOOLS_EXTRAS%"=="" (
  set "PACKAGE=%ROOT%\tametools[%TAMETOOLS_EXTRAS%]"
)

where python >nul 2>nul
if errorlevel 1 (
  echo 오류: Python 3.10 이상을 설치한 뒤 다시 실행하세요.
  pause
  exit /b 1
)

python -c "import re, sys, setuptools; m = re.match(r'(\d+)(?:\.(\d+))?', setuptools.__version__); v = (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0); sys.exit(0 if v >= (68, 0) else 1)" >nul 2>nul
if errorlevel 1 (
  echo 오류: setuptools 68 이상이 필요합니다.
  echo 인터넷이 가능한 환경에서는 먼저 python -m pip install --upgrade setuptools 를 실행하세요.
  pause
  exit /b 1
)

echo [tametools] 패키지 설치: %PACKAGE%
python -m pip install "%PACKAGE%" --no-build-isolation
if errorlevel 1 (
  echo 오류: Python 패키지 설치에 실패했습니다.
  pause
  exit /b 1
)

where tametools >nul 2>nul
if errorlevel 1 (
  echo 오류: tametools 명령을 PATH에서 찾을 수 없습니다.
  echo 가상환경을 활성화했는지, 또는 Python Scripts 경로가 PATH에 포함되어 있는지 확인하세요.
  python -m pip show -f tametools
  pause
  exit /b 1
)

tametools --help >nul
if errorlevel 1 (
  echo 오류: tametools 실행 확인에 실패했습니다.
  pause
  exit /b 1
)

for /f "delims=" %%i in ('where tametools') do (
  echo [tametools] 설치 완료: %%i
  goto :done
)

:done
echo [tametools] 예: tametools info tutorial/01_tagged_eda/sample_eda.tame
pause

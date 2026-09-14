@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 scripts\start_tametools_browser.py %*
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo Python 3.10-3.13 64-bit is required. See README_BROWSER_KO.md.
    pause
    exit /b 1
  )
  python scripts\start_tametools_browser.py %*
)
if errorlevel 1 pause

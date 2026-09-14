@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_windows_release.ps1" -Target Exe %*
exit /b %ERRORLEVEL%

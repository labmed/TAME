@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_windows_release.ps1" -Target Msi %*
exit /b %ERRORLEVEL%

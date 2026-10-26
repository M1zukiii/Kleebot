@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_windows.ps1"
if errorlevel 1 (
  echo Installation failed. Keep this window open and check the error above.
  pause
  exit /b 1
)

@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  py scripts\run_dashboard.py
) else (
  python scripts\run_dashboard.py
)

endlocal

@echo off
setlocal
cd /d "%~dp0"
set "PY=..\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
REM Prefer installed Chrome. Install Playwright Chromium too as fallback.
"%PY%" -m playwright install chromium
endlocal

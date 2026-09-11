@echo off
setlocal
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" -c "import PySide6" >nul 2>nul
if errorlevel 1 (
  echo [M WOIF HEART] PySide6 ยังไม่ถูกติดตั้งใน venv
  echo รัน: .\.venv\Scripts\python.exe -m pip install -r requirements.txt
  pause
  exit /b 1
)

"%PY%" -m mwoif.ui.local_app %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo [M WOIF HEART] UI ปิดจากข้อผิดพลาด - exit code %RC%
  echo กรุณาคัดลอกข้อความ error ด้านบนมาได้เลย
  pause
)
exit /b %RC%

@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [MWOIF] First setup...
    powershell -NoProfile -ExecutionPolicy Bypass -File setup_windows.ps1
    if errorlevel 1 (
        echo Setup failed.
        pause
        exit /b 1
    )
)

".venv\Scripts\python.exe" main.py menu

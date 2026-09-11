@echo off
setlocal
cd /d "%~dp0"
call run.bat local-final-check
set "RC=%ERRORLEVEL%"
endlocal & exit /b %RC%

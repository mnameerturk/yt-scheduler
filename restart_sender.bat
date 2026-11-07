@echo off
rem Restarts the AutoDoc sender (after an update). Double-click it.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_sender.ps1"
timeout /t 2 /nobreak >nul
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0autodoc_sender.py"
echo.
echo  AutoDoc sender restarted. Log: %~dp0autodoc_sender.log
echo.
pause

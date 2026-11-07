@echo off
rem Restarts the AutoDoc sender (after an update). Double-click it.
cd /d "%~dp0"
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"name=pythonw.exe\" | Where-Object { $_.CommandLine -like *autodoc_sender* } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
timeout /t 2 /nobreak >nul
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0autodoc_sender.py"
echo.
echo  AutoDoc sender restarted. Log: %~dp0autodoc_sender.log
echo.
pause

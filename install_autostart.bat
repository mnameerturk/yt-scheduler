@echo off
rem Starts autodoc_sender.py hidden every time Windows starts, and right now.
cd /d "%~dp0"
if not exist yt_scheduler_pc.json (
  echo Run this first:  .venv\Scripts\python send_to_queue.py --setup
  pause
  exit /b 1
)
set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
> "%STARTUP%\nmr_autodoc_sender.vbs" echo CreateObject("WScript.Shell").Run """%~dp0.venv\Scripts\pythonw.exe"" ""%~dp0autodoc_sender.py""", 0, False
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0autodoc_sender.py"
echo.
echo  AutoDoc sender is running and will start with Windows.
echo  New AutoDoc renders go to the scheduler about 30 minutes after they finish.
echo  Log: %~dp0autodoc_sender.log
echo.
pause

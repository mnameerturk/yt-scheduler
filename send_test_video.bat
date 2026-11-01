@echo off
cd /d "%~dp0"
title NMR Scheduler - send test video
if not exist .venv\Scripts\python.exe (
  echo Run run_windows.bat once first, it installs everything.
  pause
  exit /b 1
)
if not exist test-video.mp4 (
  echo test-video.mp4 is not in this folder. Put test-video.mp4 and test-thumbnail.jpg next to this file.
  pause
  exit /b 1
)
if not exist yt_scheduler_pc.json (
  echo First time: connect this PC tool to the server.
  .venv\Scripts\python.exe send_to_queue.py --setup
  echo.
)
echo Your channels:
.venv\Scripts\python.exe -c "import json,send_to_queue as s;srv=s.Server(s.load_conf());[print('  -',c['channel_title']) for c in srv.req('GET','/api/channels') if c['channel_id']]"
echo.
set /p CH=Type part of the channel name and press Enter: 
echo.
.venv\Scripts\python.exe send_to_queue.py test-video.mp4 --channel "%CH%" --title "Demo upload" --description "Test upload from NMR Scheduler" --tags "test,demo" --thumbnail test-thumbnail.jpg
echo.
echo Done. Open the YT Scheduler app to see the video in the queue.
pause

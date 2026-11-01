@echo off
cd /d "%~dp0"
if not exist client_secret.json (
  echo.
  echo  client_secret.json is missing.
  echo  Copy the JSON you downloaded from Google Cloud into this folder and rename it to client_secret.json
  echo.
  pause
  exit /b 1
)
if not exist .venv (
  echo Creating Python environment, first run only...
  python -m venv .venv || (echo Python not found. Install Python 3.11+ from python.org & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt
if not exist .env python setup_env.py
for /f "tokens=1,* delims==" %%a in (.env) do if "%%a"=="BASE_URL" set BASE_URL=%%b
set DOMAIN=%BASE_URL:https://=%
echo Starting ngrok on %DOMAIN% ...
start "ngrok" ngrok http --url=%DOMAIN% 8000
echo.
echo  Scheduler running. Open %BASE_URL% on your phone or PC.
echo  Keep this window open. Close it to stop the server.
echo.
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause

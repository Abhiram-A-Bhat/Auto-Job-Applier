@echo off
title JobBot - keep this window open
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

netstat -ano | findstr /r /c:":5000 .*LISTENING" >nul
if not errorlevel 1 (
  echo JobBot is already running. Opening it...
  start "" "http://127.0.0.1:5000"
  timeout /t 3 >nul
  exit /b 0
)

echo Starting JobBot. Keep this window open - closing it stops JobBot.
echo.
python main.py
echo.
echo JobBot stopped.
pause

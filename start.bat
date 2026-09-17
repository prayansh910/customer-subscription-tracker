@echo off
title SubTrack - Local Development
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 (
    echo Install Python 3.10+ first.
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
start "" http://127.0.0.1:5000
".venv\Scripts\python.exe" app.py
pause

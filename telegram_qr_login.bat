@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Kuriama Python aplinka...
  py -m venv .venv
)
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
".venv\Scripts\python.exe" telegram_qr_login.py
pause

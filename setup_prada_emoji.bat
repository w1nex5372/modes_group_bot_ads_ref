@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -m venv .venv
)
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
".venv\Scripts\python.exe" generate_prada_assets.py
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" setup_prada_emoji.py
pause

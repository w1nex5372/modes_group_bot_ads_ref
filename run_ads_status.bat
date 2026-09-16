@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Nerasta .venv. Pirma paleisk start.bat
    pause
    exit /b 1
)
".venv\Scripts\python.exe" ads_status.py
pause

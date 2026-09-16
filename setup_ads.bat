@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Pirma paleisk start.bat
  pause
  exit /b 1
)
".venv\Scripts\python.exe" setup_rose_ads.py
pause

@echo off
cd /d "%~dp0"
title TG Group System V4

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Kuriama Python aplinka...
    py -m venv .venv
)

echo [2/3] Tikrinami paketai...
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo.
    echo Sukurtas .env. Uzpildyk BOT_TOKEN, GROUP, API_ID, API_HASH ir SOURCE_MESSAGE_ID.
    notepad ".env"
    echo.
    echo Issaugok .env ir paleisk start.bat dar karta.
    pause
    exit /b 0
)

".venv\Scripts\python.exe" launcher.py
pause

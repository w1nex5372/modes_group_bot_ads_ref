@echo off
cd /d "%~dp0"
if "%~1"=="" (
  echo Naudojimas:
  echo   telegram_login.bat send +3706XXXXXXX
  echo   telegram_login.bat finish KODAS
  echo   telegram_login.bat finish KODAS TAVO_2FA_PASSWORD
  pause
  exit /b 2
)
".venv\Scripts\python.exe" telegram_login.py %*
pause

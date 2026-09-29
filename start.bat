@echo off
REM QuantDesk AI Bot starten (Doppelklick genuegt)
cd /d "%~dp0"
if not exist ".venv\Scripts\activate.bat" (
  echo [QuantDesk] .venv fehlt - bitte zuerst die Installation aus der README ausfuehren.
  pause
  exit /b 1
)
if not exist ".env" (
  echo [QuantDesk] .env fehlt - kopiere .env.example nach .env und trage deine Werte ein.
  pause
  exit /b 1
)
call ".venv\Scripts\activate.bat"
python bot.py
if errorlevel 1 pause

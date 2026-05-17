@echo off
REM ===================================================
REM   Scheduler (Reminder + API-Sync) - separat starten
REM   Optional: nur fuer lokale Entwicklung mit Reminders
REM ===================================================

if not exist venv (
    echo [FEHLER] Keine venv gefunden. Erst install.bat ausfuehren.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat

echo.
echo  Scheduler laeuft (Reminder alle 10min, Sync alle 15min)
echo  Beenden mit STRG+C
echo.

python scheduler.py

pause

@echo off
REM ===================================================
REM   Wulmstoerper Tipprunde - Windows Start
REM ===================================================

if not exist venv (
    echo [FEHLER] Keine venv gefunden.
    echo Bitte zuerst install.bat ausfuehren.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat

echo.
echo  ========================================
echo    Wulmstoerper Tipprunde laeuft!
echo  ========================================
echo.
echo  URL:           http://localhost:5000
echo  Admin-Login:   admin@tippspiel.local / admin123
echo.
echo  Beenden mit STRG+C
echo.

set FLASK_APP=app.py
set FLASK_ENV=development
python app.py

pause

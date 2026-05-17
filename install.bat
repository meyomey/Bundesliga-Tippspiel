@echo off
REM ===================================================
REM   Wulmstoerper Tipprunde - Windows Setup
REM ===================================================

echo.
echo  ========================================
echo    Wulmstoerper Tipprunde - Setup
echo  ========================================
echo.

REM Prüfen ob Python installiert ist
where python >nul 2>nul
if errorlevel 1 (
    echo [FEHLER] Python ist nicht installiert oder nicht im PATH.
    echo Bitte installiere Python 3.10+ von https://www.python.org/downloads/
    echo WICHTIG: Beim Installer "Add Python to PATH" anhaken!
    pause
    exit /b 1
)

python --version
echo.

REM Virtuelle Umgebung erstellen
if not exist venv (
    echo [1/3] Erstelle virtuelle Umgebung...
    python -m venv venv
    if errorlevel 1 (
        echo [FEHLER] venv konnte nicht erstellt werden.
        pause
        exit /b 1
    )
) else (
    echo [1/3] venv existiert bereits.
)

REM Aktivieren
echo [2/3] Aktiviere venv...
call venv\Scripts\activate.bat

REM Dependencies installieren
echo [3/3] Installiere Dependencies (kann ein paar Minuten dauern)...
python -m pip install --upgrade pip
pip install -r requirements.txt

if errorlevel 1 (
    echo.
    echo [FEHLER] Installation fehlgeschlagen.
    pause
    exit /b 1
)

REM .env anlegen falls nicht vorhanden
if not exist .env (
    echo.
    echo Erstelle .env aus .env.example ...
    copy .env.example .env >nul
    echo HINWEIS: Bitte .env anpassen (SECRET_KEY, MAIL, API-Token)!
)

echo.
echo  ========================================
echo    Installation abgeschlossen!
echo  ========================================
echo.
echo  Starten mit:    start.bat
echo  Admin-Login:    admin@tippspiel.local / admin123
echo.
pause

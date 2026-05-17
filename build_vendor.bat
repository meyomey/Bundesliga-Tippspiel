@echo off
REM ===================================================================
REM   Wulmstoerper Tipprunde - Vendor-Pakete fuer Netcup bauen
REM ===================================================================
REM
REM   Dieses Skript laedt alle Python-Pakete in einen lokalen Ordner
REM   "vendor/", den du mit den anderen Dateien per FTP zu Netcup hochladen
REM   kannst. Auf Netcup wird KEIN pip benoetigt.
REM
REM   WICHTIG: Du musst die NETCUP-Python-Version waehlen!
REM   Pruefe in Plesk -^> Python welche Version aktiv ist.
REM ===================================================================

setlocal enabledelayedexpansion

echo.
echo  ===========================================================
echo    Vendor-Pakete fuer Netcup bauen
echo  ===========================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [FEHLER] Python ist nicht installiert oder nicht im PATH.
    pause & exit /b 1
)

python --version
echo.

REM ----- Python-Version auswaehlen -----
echo Welche Python-Version laeuft auf NETCUP?
echo (siehst du in Plesk -^> Domain -^> Python)
echo.
echo   [1] Python 3.9          (alt, aber haeufig auf Netcup)
echo   [2] Python 3.10 oder 3.11
echo   [3] Python 3.12 oder neuer
echo.
set /p PYVER="Auswahl [1/2/3] (Default 1): "
if "%PYVER%"=="" set PYVER=1

if "%PYVER%"=="1" (
    set REQ_FILE=requirements_py39.txt
    set TARGET_PY=39
    echo Verwende Python 3.9-kompatible Pakete
) else if "%PYVER%"=="2" (
    set REQ_FILE=requirements.txt
    set TARGET_PY=311
    echo Verwende Python 3.10/3.11-kompatible Pakete
) else (
    set REQ_FILE=requirements.txt
    set TARGET_PY=312
    echo Verwende Python 3.12+ kompatible Pakete
)

REM ----- Vendor-Ordner aufraeumen -----
if exist vendor (
    echo Loesche alten vendor/-Ordner...
    rmdir /s /q vendor
)
mkdir vendor

echo.
echo Lade Pakete fuer Linux/Python %TARGET_PY% herunter...
echo (~30-60 MB Download, kann ein paar Minuten dauern)
echo.

REM ----- Schritt 1: Reine Python-Pakete -----
echo [1/2] Pure-Python-Pakete (Flask, SQLAlchemy, etc.)
python -m pip install --target=vendor --upgrade --quiet -r %REQ_FILE%

if errorlevel 1 (
    echo.
    echo [FEHLER] Pakete konnten nicht installiert werden.
    pause & exit /b 1
)

REM ----- Schritt 2: Plattform-Spezifische Pakete (Pillow, reportlab) ueberschreiben -----
REM Damit sie sicher LINUX-Wheels sind (nicht Windows-Wheels!)
echo.
echo [2/2] Linux-Wheels fuer Pillow/reportlab nachladen...
python -m pip install --target=vendor --upgrade --quiet ^
    --platform=manylinux2014_x86_64 ^
    --python-version=%TARGET_PY% ^
    --only-binary=:all: ^
    --implementation=cp ^
    Pillow reportlab 2>nul

if errorlevel 1 (
    echo [WARNUNG] Linux-Wheels nicht ladbar - nutze ggf. Windows-Variante.
    echo Avatar-Upload und PDF-Export funktionieren dann moeglicherweise nicht.
)

REM ----- Aufraeumen -----
echo.
echo Raeume nicht-benoetigte Dateien auf...
if exist vendor\bin rmdir /s /q vendor\bin 2>nul

echo.
echo  ===========================================================
echo    FERTIG! Vendor-Ordner ist bereit.
echo  ===========================================================
echo.
echo  Naechste Schritte:
echo    1. ALLE Dateien dieses Ordners (inkl. vendor/) per FTP hochladen
echo    2. In Plesk: Python -^> Restart App klicken
echo    3. Domain im Browser oeffnen
echo.

pause

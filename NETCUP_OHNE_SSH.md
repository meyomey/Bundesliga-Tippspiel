# 🚀 Netcup ohne SSH/pip — Vendor-Methode

## Problem
Auf Netcup-Webhosting hast du:
- ❌ Kein SSH (oder eingeschränkt ohne `pip`)
- ❌ Keine Möglichkeit, Pakete im Plesk-UI zu installieren
- ❌ Keine ENV-Variablen änderbar
- ✅ ABER: Python ist da + FTP funktioniert

## Lösung: Pakete lokal bauen, mitliefern

Die Idee: **Du installierst alle Python-Pakete (Flask & Co.) auf deinem
Windows-PC** in einen Ordner namens `vendor/`. Dann lädst du diesen
Ordner einfach **mit deinen anderen Dateien per FTP zu Netcup** hoch.

Auf Netcup wird dann **kein pip** mehr gebraucht — alle Pakete sind
bereits da.

---

## 🪟 Schritt-für-Schritt-Anleitung

### Schritt 1: Lokal die Pakete bauen

1. Öffne Windows-Explorer im `flask_app/`-Ordner
2. **Doppelklick auf `build_vendor.bat`**
3. Skript lädt automatisch alle Pakete in `vendor/` herunter
4. Wenn es fertig ist, sollte ein Ordner `vendor/` mit ~50 MB existieren:
   ```
   flask_app/
   ├── app.py
   ├── passenger_wsgi.py
   ├── requirements.txt
   ├── vendor/         ← NEU, enthält Flask, SQLAlchemy, ...
   │   ├── flask/
   │   ├── flask_sqlalchemy/
   │   ├── ...
   ├── static/
   ├── templates/
   ```

### Schritt 2: Per FTP zu Netcup hochladen

1. **FileZilla** öffnen (oder dein FTP-Programm)
2. Verbinden zu deinem Netcup-FTP (Daten in Plesk → FTP-Zugang)
3. In den `bl-tipp/`-Ordner navigieren (oder wo deine App liegt)
4. **ALLE Dateien hochladen** — wichtig:
   - `passenger_wsgi.py` (die NEUE Version)
   - `vendor/` (kompletter Ordner mit allen Inhalten!)
   - `app.py`, `models.py`, `utils.py`, `forms.py`, `extensions.py`, `config.py`
   - `templates/`, `static/`
   - `requirements.txt`, `cron_jobs.py`

### Schritt 3: App neustarten

1. Plesk → deine Domain → **Python**
2. Klick auf **"Restart App"** (oder "Application Reload")

### Schritt 4: Testen

Domain im Browser aufrufen → sollte laufen!

---

## ❓ FAQ

### Wie groß ist der `vendor/`-Ordner?
Etwa **30–60 MB** (je nach Pillow-Variante). FTP-Upload dauert ~5–10 Minuten.

### Wenn FTP zu langsam ist
- Lokal `vendor.zip` erstellen
- Per FTP hochladen
- Plesk Filemanager öffnen → Rechtsklick → "Entpacken"

### Welche Python-Version?
Wähle in Plesk **Python 3.11** aus. Die Vendor-Pakete sind kompatibel.
Falls Plesk eine andere Version vorgibt: in `build_vendor.bat`
die Zeile `--python-version=3.11` entsprechend anpassen.

### "ImportError" trotz vendor/?
Falls eines der Pakete (z.B. Pillow) **C-Extensions** braucht und auf
deinem Windows kompiliert wurde, läuft es eventuell nicht auf Netcup-Linux.

**Lösung**: Kommentiere `Pillow==10.4.0` in `requirements.txt` aus,
führe `build_vendor.bat` neu aus, lade nochmal hoch.

→ Avatar-Upload funktioniert dann nicht mehr (Pillow nötig), aber
alles andere läuft. Falls Avatare wichtig sind: bitte Netcup-Support
um eine pip-Installation von `Pillow`.

### Updates später?
Bei Code-Änderungen nur die **geänderten `.py`/`.html`-Dateien** hochladen.
`vendor/` musst du NICHT jedes Mal neu hochladen — nur wenn sich
`requirements.txt` ändert.

### Kann ich `vendor/` löschen wenn pip später doch verfügbar wird?
Ja jederzeit. `passenger_wsgi.py` prüft erst `vendor/`, dann `.python-venvs/`,
dann globalen Python — was zuerst Flask findet, wird genutzt.

---

## 🛠 Plesk-Konfiguration

| Feld | Wert |
|------|------|
| Application Root | `/bl-tipp` (oder dein Pfad) |
| Application URL | `/` |
| Application Startup File | `passenger_wsgi.py` |
| Application Entry Point | `application` |
| Python Version | `3.11` (empfohlen) |
| Application Mode | `Production` |

---

## 🔧 Falls trotzdem Fehler

Im Plesk Error-Log nachschauen:
- Plesk → Domain → **Logs** → `error_log`
- Letzte Zeilen suchen nach "ModuleNotFoundError" oder "ImportError"

Der häufigste Fehler ist eine fehlende Spalte in der DB → siehe
`NETCUP_TROUBLESHOOTING.md`.

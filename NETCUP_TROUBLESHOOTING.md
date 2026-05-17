# 🛠 Netcup-Troubleshooting

## Fehler: `ModuleNotFoundError: No module named 'flask'`

Bedeutet: **Passenger nutzt nicht deine virtuelle Umgebung**, oder die Pakete
sind dort nicht installiert.

### Lösung in 3 Schritten

#### 1. Plesk → Python: prüfen, dass venv existiert

1. Plesk-Panel öffnen → deine Domain
2. **Python** klicken
3. Unter "Application Root" steht: `/httpdocs` oder `/bl-tipp` (oder ähnlich)
4. **Application Startup File**: `passenger_wsgi.py`
5. **Application Entry Point**: `application`
6. Wenn unten ein Pfad zur **Virtuellen Umgebung** angezeigt wird, ist sie da.

#### 2. Pakete installieren via Plesk-UI

1. Plesk → Python → Scrolle runter zu **"Pip Install"**
2. Trage als Argument ein:
   ```
   -r requirements.txt
   ```
3. Klick **Run** → Output sollte zeigen "Successfully installed flask flask-sqlalchemy ..."

Falls **"Run"-Button nicht erscheint** oder nicht funktioniert:
→ siehe Schritt 3 (per SSH).

#### 3. Falls Pip-Install im UI nicht klappt: per SSH

```bash
# 1. Per SSH einloggen (Daten in Plesk → Hosting-Einstellungen)
ssh dein-user@dein-server.de

# 2. Zur App navigieren
cd ~/<dein-domain>/<app-pfad>      # z.B. cd ~/tipp.wulmstorf.net/bl-tipp

# 3. venv-Pfad rausfinden
ls ~/.python-venvs/                 # zeigt deine venvs

# 4. venv aktivieren + installieren
source ~/.python-venvs/<app-name>/bin/activate
pip install -r requirements.txt

# 5. App neustarten
touch ~/tmp/restart.txt
```

#### 4. Falls SSH eingeschränkt ist (chrooted)

Manche Netcup-Tarife haben **rbash** (eingeschränkte Shell) ohne `pip`. Dann:

1. Plesk → Python → unten der **Pip-Install-Button** ist die einzige Option
2. Falls der nicht klappt: Support-Ticket öffnen → "Bitte Pakete installieren"

---

## Die neue `passenger_wsgi.py` ist self-healing

Mit der jetzt mitgelieferten `passenger_wsgi.py`:

✅ **Sucht automatisch** in `.python-venvs/` nach deiner virtuellen Umgebung
✅ **Aktiviert sie** vor dem Flask-Import
✅ **Re-exec**iert mit dem venv-Python falls nötig
✅ Lädt `.env` automatisch wenn vorhanden

→ Du musst nur noch sicherstellen, dass die **Pakete** in der venv installiert sind (Schritt 2 oder 3 oben).

---

## Hilfreiche Plesk-Logs

Im Fehlerfall reinschauen:

| Wo | Was steht drin |
|----|----------------|
| Plesk → Logs → `error_log` | Python/Passenger-Crashes |
| Plesk → Logs → `access_log` | Erreichbarkeit |
| Plesk → Python → "Logs anzeigen" | App-spezifische Logs |

---

## App neustarten ohne SSH

1. Plesk → Domain → **Python**
2. Roter Button **"Restart App"** ganz oben

Oder per SSH:
```bash
touch ~/tmp/restart.txt
```

---

## Fehler: `OperationalError: no such column`

Auto-Migration sollte das verhindern. Falls trotzdem:

1. SSH einloggen
2. `cd ~/<app-pfad>`
3. `mv tippspiel.db tippspiel.db.backup`
4. Plesk → Restart App
5. → Frische DB wird mit allen Spalten erstellt

Falls Datenverlust nicht akzeptabel: siehe `DEPLOY_NETCUP.md` Abschnitt "Migrationen".

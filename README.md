# ⚽ Wulmstörper Tipprunde (Flask)

Ein vollständiges, produktionsreifes Bundesliga-Tippspiel mit Flask.

## ✨ Features

### Must-Have ✅
- ✅ Benutzerregistrierung & Login (mit Passwort-Reset per E-Mail)
- ✅ Profil mit Avatar-Upload (mit PIL-Resize)
- ✅ Vollständiger Spielplan (34 Spieltage) inkl. Vereins-Logos
- ✅ Automatische Ergebnisabfrage (football-data.org + OpenLigaDB Fallback)
- ✅ Manuelle Spiel-Eingabe als Notfall-Lösung
- ✅ Tippabgabe bis exakt zum Anstoß
- ✅ Joker-System (1× pro Spieltag, verdoppelt Punkte)
- ✅ Konfigurierbare Punkteberechnung (Exakt/Diff/Tendenz)
- ✅ Tages- & Gesamtwertung mit Gleichstandsregelung
- ✅ Admin-Bereich (Sync, Ergebnisse, Benutzer, Einstellungen)

### Nice-to-Have 🎁
- 🔔 Push-Benachrichtigungen (Web Push API · Service Worker bereit)
- ⚡ Schnelltipps (alle Spiele eines Spieltags auf einer Seite)
- 💬 Chat/Kommentare pro Spiel
- ⭐ Sondertipps (Datenmodell vorbereitet)
- 🏅 Badges & Gamification (auto-vergeben)
- 📊 Head-to-Head Vergleich
- 📈 Persönliche Statistik mit Formkurve (Chart.js)
- 📄 CSV Export
- 🌙 Dark Mode (toggle in Navbar)
- ⏰ Tipp-Deadline Countdown
- 📧 Automatische E-Mail-Reminder (1h vor Anpfiff via APScheduler)

---

## 🚀 Schnellstart

```bash
# 1. In den Ordner wechseln
cd flask_app

# 2. Virtuelle Umgebung
python -m venv venv
source venv/bin/activate         # Windows: venv\Scripts\activate

# 3. Abhängigkeiten installieren
pip install -r requirements.txt

# 4. (Optional) .env anlegen
cp .env.example .env

# 5. Starten
python app.py
```

App läuft unter: **http://localhost:5000**

**Standard-Admin:** `admin@tippspiel.local` / `admin123` → **sofort ändern!**

Beim ersten Start werden automatisch:
- 18 Bundesliga-Teams (mit Logos) angelegt
- 34 Spieltage mit Demo-Spielen erzeugt
- Default-Badges seeded
- Admin-User erstellt

---

## ⚙️ Konfiguration

### football-data.org API Key (empfohlen)
1. Kostenlos registrieren: https://www.football-data.org/client/register
2. Token in **Admin → Einstellungen** eintragen, **oder** als ENV: `FOOTBALL_DATA_TOKEN=...`
3. 10 Calls/Minute im Free Tier

### E-Mail (für Passwort-Reset & Reminder)
In `.env` setzen:
```bash
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USERNAME=deine@gmail.com
MAIL_PASSWORD=dein-app-password
MAIL_DEFAULT_SENDER=deine@gmail.com
```

### VAPID Keys für Push-Benachrichtigungen
```bash
pip install pywebpush
vapid --gen
# Keys in Admin → Einstellungen eintragen
```

### Sicherheit
```bash
export SECRET_KEY="ein-sehr-langer-zufaelliger-string-min-32-zeichen"
```

---

## 📁 Projektstruktur

```
flask_app/
├── app.py              # Hauptanwendung mit allen Routes (Blueprints)
├── config.py           # Konfiguration aus ENV/Defaults
├── models.py           # SQLAlchemy-Modelle
├── extensions.py       # Flask-Extensions (DB, Login, Mail, Migrate)
├── forms.py            # WTForms (Registrierung, Tipp, Admin, ...)
├── utils.py            # Punkteberechnung, API-Sync, Mail, Badges
├── scheduler.py        # APScheduler für Reminder + Sync (separat starten)
├── requirements.txt    # Python-Dependencies
├── .env.example        # Beispiel ENV-Variablen
├── static/
│   ├── css/style.css   # Modernes Dark-Theme + Light-Mode
│   ├── js/app.js       # Theme-Toggle, Service-Worker-Init
│   └── js/sw.js        # Service Worker für Push
└── templates/
    ├── base.html
    ├── landing.html, dashboard.html, schedule.html, ...
    ├── auth/           # Login, Register, Password-Reset
    └── admin/          # Sync, Matches, Users, Settings
```

---

## 🗄️ Datenbank

Standard: **SQLite** (`tippspiel.db` im Hauptordner)

Für PostgreSQL:
```bash
pip install psycopg2-binary
export DATABASE_URL=postgresql://user:pass@localhost/tippspiel_db
python app.py
```

Migrations:
```bash
flask db init
flask db migrate -m "initial"
flask db upgrade
```

---

## ⏰ Background Jobs starten

Im **separaten Terminal**:
```bash
source venv/bin/activate
python scheduler.py
```

Der Scheduler:
- Sendet **1h vor Anpfiff** Erinnerungen an alle, die noch nicht getippt haben
- Synct **alle 15min** automatisch Ergebnisse von football-data.org

---

## 🧮 Punkteberechnung

| Tipp | Punkte (default) |
|------|-----|
| Exaktes Ergebnis | 4 |
| Richtige Tordifferenz | 3 |
| Richtige Tendenz | 2 |
| **Joker (×2)** | verdoppelt alle obigen |

Im **Admin → Einstellungen** anpassbar. Punkte werden bei Änderung automatisch neu berechnet.

**Gleichstandsregelung:** 
1. Punkte (höher = besser)
2. Anzahl exakter Tipps
3. Anzahl Tipps insgesamt
4. Username (alphabetisch)

---

## 🚀 Production Deployment

### Mit Gunicorn:
```bash
gunicorn -w 4 -b 0.0.0.0:8000 app:app
```

### Mit Docker:
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:8000", "app:app"]
```

### Reverse Proxy (Nginx-Beispiel):
```nginx
server {
    listen 80;
    server_name tippspiel.example.com;
    
    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
    
    location /static {
        alias /app/static;
    }
}
```

---

## 📜 Lizenz

MIT — viel Spaß beim Tippen! ⚽

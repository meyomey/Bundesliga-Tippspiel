# 🚀 Neue Features – Wulmstörper Tipprunde

Dieses Dokument beschreibt die 6 neuen Features, die implementiert wurden.

---

## 1. 🤖 KI-Tippgegner

Fünf computergesteuerte Gegner mit unterschiedlichen Schwierigkeitsgraden tippen mit!

### Schwierigkeitsgrade

| Bot | Stärke | Beschreibung |
|-----|--------|--------------|
| **RookieBot** | ⭐ | Viel Zufall, einfache Strategie |
| **AmateurBot** | ⭐⭐ | Leichte Heimvorteils-Berücksichtigung |
| **ProBot** | ⭐⭐⭐ | Balanciert aus Statistik und Zufall |
| **ExpertBot** | ⭐⭐⭐⭐⭐ | Starke Gewichtung auf Form und Tabelle |
| **MasterBot** | ⭐⭐⭐⭐⭐ | Beste Strategie + H2H-Analyse |

### Verwendung

```python
from ai_opponent import ai_manager

# Alle Bots tippen lassen fuer Spieltag 5
results = ai_manager.tip_all_matches(matchday=5)

# Einzelnen Tipp abfragen
bot = ai_manager.get_opponent("ExpertBot")
home_goals, away_goals = bot.get_tip(match)

# Rangliste der Bots anzeigen
rankings = ai_manager.get_rankings()
```

### Datenbank
Die Bots sind als reguläre User in der DB gespeichert (Email: `{botname}@bot.local`).

---

## 2. ⚡ Live-Scoring

Echtzeit-Updates während der Spiele via Server-Sent Events (SSE).

### Features
- Live-Spielstand (Minuten-genau)
- Ereignis-Stream (Tore, Karten)
- Automatische Status-Änderung: `scheduled` → `live` → `finished`
- Tipps werden bei Spielende sofort ausgewertet

### API Endpunkte

```
GET  /live/matches              # Alle Live-Spiele
GET  /live/match/<id>           # Spiel-Details + Statistik
GET  /live/match/<id>/stream    # SSE Stream fuer Updates
GET  /live/user/predictions     # Eigene Live-Tipps (Login erforderlich)
POST /live/admin/update/<id>    # Manuelles Update (Admin)
POST /live/admin/finish/<id>    # Spiel beenden (Admin)
```

### JavaScript Integration

```javascript
// Live-Updates abonnieren
const evtSource = new EventSource('/live/match/123/stream');
evtSource.onmessage = (event) => {
    const data = JSON.parse(event.data);
    updateScoreboard(data);
};
```

---

## 3. 🏆 Mehrere Wettbewerbe

Unterstützung für verschiedene Wettbewerbe: Bundesliga, Champions League, DFB-Pokal, etc.

### Neue Modelle

```python
class Competition:
    - code: 'BL1', 'CL', 'DFB'
    - name: 'Bundesliga'
    - season: '2025/26'
    - matchdays: 34
    - teams_count: 18

class CompetitionTeam:
    - Verknüpfung Team <-> Wettbewerb
    - Position, Punkte, Tore, etc.
```

### Verwendung

```python
# Wettbewerb erstellen
comp = Competition(code='CL', name='Champions League', matchdays=13)

# Team zu Wettbewerb hinzufügen
ct = CompetitionTeam(competition_id=comp.id, team_id=team.id)

# Spiele filtern
matches = Match.query.filter_by(competition_id=comp.id).all()
```

### Migration
Alte Daten bleiben kompatibel – bei Bedarf wird ein Default-Wettbewerb erstellt.

---

## 4. 💨 Redis Caching

Performance-Optimierung durch Redis-Cache.

### Was wird gecacht?

| Daten | TTL | Invalidierung |
|-------|-----|---------------|
| Ranglisten | 5 Min | Bei Tipp/Ergebnis |
| Spiel-Details | 1 Min | Bei Update |
| Statistiken | 10 Min | Nightly |
| API-Responses | 60 Sek | Nie |

### Konfiguration

```bash
# .env
REDIS_URL=redis://localhost:6379/0
```

Ohne Redis läuft die App normal weiter (Cache deaktiviert).

### Verwendung

```python
from cache import cached, invalidate_leaderboard

# Funktion cachen
@cached(ttl=300, key_prefix='leaderboard')
def get_leaderboard(matchday=None):
    # ... teure Berechnung
    return results

# Cache invalidieren
invalidate_leaderboard()  # Alle Ranglisten
invalidate_match(match_id)  # Einzelnes Spiel
```

### Decorator Optionen

```python
@cached(ttl=60)                          # 60 Sekunden
@cached(ttl=300, key_prefix='custom')    # Custom Key-Prefix
@cached(key_builder=my_custom_key_func)  # Custom Key-Builder
```

---

## 5. 🧪 Pytest Suite

Umfassende Testabdeckung mit pytest.

### Struktur

```
tests/
├── conftest.py          # Fixtures
├── test_models.py       # Model-Tests
├── test_routes.py       # Route-Tests
├── test_ai_opponent.py  # KI-Tests
└── test_cache.py        # Cache-Tests
```

### Ausführung

```bash
# Alle Tests
pytest

# Mit Coverage
pytest --cov=. --cov-report=html

# Nur schnelle Tests
pytest -m "not slow"

# Spezifische Test-Datei
pytest tests/test_ai_opponent.py -v
```

### Fixtures

```python
# Verfügbare Fixtures in conftest.py
@pytest.fixture
def user():           # Normaler User
@pytest.fixture
def admin_user():     # Admin-User
@pytest.fixture
def competition():    # Test-Wettbewerb
@pytest.fixture
def teams():          # 4 Test-Teams
@pytest.fixture
def match():          # Offenes Spiel
@pytest.fixture
def finished_match(): # Beendetes Spiel
@pytest.fixture
def auth_client():    # Authentifizierter Client
```

---

## 6. 🔄 GitHub Actions CI/CD

Automatisierte Tests und Qualitätsprüfung bei jedem Push.

### Workflows

```yaml
# .github/workflows/tests.yml

1. Tests (Python 3.9, 3.10, 3.11, 3.12)
   - Redis Service
   - pytest mit Coverage
   - Upload zu Codecov

2. Security Checks
   - bandit (Code-Sicherheit)
   - safety (Dependency Vulns)

3. Docker Build
   - Image bauen (optional)
```

### Status-Badge

```markdown
![Tests](https://github.com/meyomey/Bundesliga-Tippspiel/workflows/Tests/badge.svg)
```

### Lokale Entwicklung

```bash
# Sicherheitschecks lokal
pip install bandit safety
bandit -r .
safety check
```

---

## 📦 Installation der neuen Dependencies

```bash
pip install -r requirements.txt
```

### Neue Packages
- `redis` - Redis Client
- `flask-caching` - Cache-Integration
- `numpy`, `scikit-learn` - KI/ML Berechnungen
- `pytest`, `pytest-flask`, `pytest-cov` - Testing
- `factory-boy`, `faker` - Test-Daten

---

## 🗄️ Datenbank-Migration

Beim ersten Start werden automatisch neue Tabellen erstellt:

```python
# In app.py wird ausgeführt:
db.create_all()
auto_migrate_schema()  # Fügt fehlende Spalten hinzu
```

Manuelle Migration falls nötig:

```bash
python -c "
from app import create_app
from extensions import db
app = create_app()
with app.app_context():
    db.create_all()
    print('✅ Migration erfolgreich')
"
```

---

## 🎯 Nächste Schritte

Empfohlene Erweiterungen:

1. **Admin-UI fuer KI-Gegner**
   - Bots manuell tippen lassen
   - Schwierigkeit anpassen
   - Bot-Rangliste anzeigen

2. **Live-Scoring Frontend**
   - Auto-refresh der Seite
   - Push-Notifications bei Toren
   - Visualisierung der Spielereignisse

3. **Wettbewerb-Wechsler**
   - Dropdown in Navigation
   - Separate Ranglisten pro Wettbewerb
   - Gesamtwertung ueber alle Wettbewerbe

4. **Cache Monitoring**
   - Admin-Seite mit Cache-Stats
   - Hit/Miss Ratios
   - Manuelles Leeren

---

## 🐛 Troubleshooting

### Redis nicht verfügbar
```
⚠️ Redis nicht verfuegbar: ... Cache deaktiviert.
```
→ App läuft normal, nur ohne Caching.

### KI fehlt numpy
```
ImportError: No module named 'numpy'
```
→ `pip install numpy scikit-learn`

### Tests schlagen fehl
```bash
# Datenbank-Locks
rm -rf .pytest_cache
rm -rf htmlcov

# Erneut versuchen
pytest --tb=short
```

---

**Viel Spaß mit den neuen Features!** 🚀⚽

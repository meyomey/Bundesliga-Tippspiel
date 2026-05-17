# Changelog – Neue Features

## [2.0.0] - 2025-05-15

### 🤖 KI-Tippgegner
- **5 Bots** mit unterschiedlichen Schwierigkeitsgraden (Easy bis Expert)
- Statistik-basierte Tipps (Form, Tabelle, H2H)
- Automatische Tippabgabe für alle offenen Spiele
- Integration in Rangliste (wie reguläre Spieler)
- Neue Datei: `ai_opponent.py` (~400 Zeilen)

### ⚡ Live-Scoring
- Echtzeit-Updates via Server-Sent Events (SSE)
- Live-Spielstände mit Minuten-Anzeige
- Ereignis-Tracking (Tore, Karten)
- REST API für Live-Daten
- Automatische Status-Änderung
- Neue Datei: `live_scoring.py` (~350 Zeilen)

### 🏆 Mehrere Wettbewerbe
- Unterstützung für Bundesliga, CL, DFB-Pokal, etc.
- Neues `Competition` Model
- `CompetitionTeam` für Wettbewerbs-spezifische Tabellen
- Erweitert: `models.py` (+Competition, +CompetitionTeam)

### 💨 Redis Caching
- Transparentes Caching via `@cached` Decorator
- Cache-Invalidation bei Änderungen
- Funktioniert auch ohne Redis (degraded mode)
- Performance-Boost für Ranglisten
- Neue Datei: `cache.py` (~220 Zeilen)

### 🧪 Pytest Test-Suite
- Vollständige Testabdeckung
- Fixtures für User, Matches, Tipps
- Tests für Models, Routes, KI, Cache
- Coverage-Reporting
- Neue Dateien: `tests/` (5 Dateien, ~600 Zeilen)

### 🔄 GitHub Actions CI/CD
- Automatisierte Tests bei Push/PR
- Multi-Python-Version Testing (3.9-3.12)
- Security-Checks (bandit, safety)
- Coverage-Upload zu Codecov
- Neue Datei: `.github/workflows/tests.yml`

### 🐳 Docker Support
- Dockerfile für Container-Deployment
- docker-compose.yml mit PostgreSQL & Redis
- Separate Scheduler-Service
- Neue Dateien: `Dockerfile`, `docker-compose.yml`

### 📦 Dependencies
```
# Neu hinzugefügt:
redis==5.0.8
flask-caching==2.3.0
numpy==1.26.4
scikit-learn==1.5.2
pytest==8.3.3
pytest-flask==1.3.0
pytest-cov==5.0.0
factory-boy==3.3.1
faker==28.4.1
```

### 🔧 Erweiterte Dateien
- `requirements.txt` - Neue Dependencies
- `config.py` - Redis & Test-Konfiguration
- `extensions.py` - CacheManager Integration
- `app.py` - Blueprint Registrierung für Live-Scoring
- `models.py` - Competition Models

### 📝 Dokumentation
- `FEATURES.md` - Ausführliche Feature-Beschreibung
- `CHANGELOG.md` - Diese Datei
- `.env.example` - Beispiel-Konfiguration
- `pytest.ini` - Test-Konfiguration

---

## Statistik

| Metrik | Vorher | Nachher | Delta |
|--------|--------|---------|-------|
| Python-Dateien | 9 | 17 | +8 |
| Code-Zeilen | ~4.800 | ~6.400 | +1.600 |
| Tests | 0 | 25+ | +25 |
| Features | 15 | 21 | +6 |

---

## Migration Guide

### 1. Dependencies installieren
```bash
pip install -r requirements.txt
```

### 2. Datenbank aktualisieren
```bash
python -c "
from app import create_app
from extensions import db
app = create_app()
with app.app_context():
    db.create_all()
"
```

### 3. Environment konfigurieren (optional)
```bash
cp .env.example .env
# Editieren und Redis-URL eintragen (optional)
```

### 4. Tests ausführen
```bash
pytest
```

### 5. KI-Gegner initialisieren
```python
from ai_opponent import ai_manager
# Werden automatisch bei ersten Zugriff erstellt
```

---

**Viel Spaß mit dem erweiterten Tippspiel!** 🚀⚽

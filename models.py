"""SQLAlchemy-Modelle für die Wulmstörper Tipprunde."""
from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from extensions import db


class Competition(db.Model):
    """Wettbewerbe (Bundesliga, Champions League, DFB-Pokal, etc.)."""
    __tablename__ = "competitions"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False)  # 'BL1', 'CL', 'DFB'
    name = db.Column(db.String(100), nullable=False)  # 'Bundesliga'
    season = db.Column(db.String(20), nullable=False)  # '2025/26'
    logo = db.Column(db.String(300), nullable=True)
    matchdays = db.Column(db.Integer, default=34)  # Anzahl Spieltage
    teams_count = db.Column(db.Integer, default=18)  # Anzahl Mannschaften
    is_active = db.Column(db.Boolean, default=True)
    external_id = db.Column(db.String(50), nullable=True)  # football-data.org ID
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Beziehungen
    matches = db.relationship("Match", backref="competition", lazy="dynamic")
    teams = db.relationship("CompetitionTeam", backref="competition", lazy="dynamic")


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, index=True, nullable=False)  # Spielername
    full_name = db.Column(db.String(120), nullable=True)                          # voller Name (privat)
    email = db.Column(db.String(120), unique=True, index=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    avatar = db.Column(db.String(200), default="default.png")
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    push_subscription = db.Column(db.Text, nullable=True)

    # Lieblingsverein (verlinkt mit Team)
    favorite_team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=True)
    favorite_team = db.relationship("Team", foreign_keys=[favorite_team_id])

    # Mobilfunknummer (z.B. für WhatsApp-Gruppe, Erinnerungen)
    phone = db.Column(db.String(40), nullable=True)
    whatsapp_phone  = db.Column(db.String(30), nullable=True)  # z.B. +4917612345678
    whatsapp_apikey = db.Column(db.String(20), nullable=True)  # CallMeBot API Key

    # Privacy: Soll der volle Name öffentlich sichtbar sein?
    # Default: True (für die meisten Tippspiele freundlicher)
    show_full_name = db.Column(db.Boolean, default=True)

    # Pott-Bezahlung
    has_paid = db.Column(db.Boolean, default=False)
    paid_at = db.Column(db.DateTime, nullable=True)
    paid_note = db.Column(db.String(200), nullable=True)  # z.B. "PayPal 12.08.", "Bar"

    predictions = db.relationship("Prediction", foreign_keys="Prediction.user_id",
                                   backref="user", lazy="dynamic", cascade="all, delete-orphan")
    badges = db.relationship("UserBadge", backref="user", lazy="dynamic", cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def total_points(self):
        return sum(p.points or 0 for p in self.predictions)

    def joker_used_for_matchday(self, matchday):
        return Prediction.query.join(Match).filter(
            Prediction.user_id == self.id,
            Prediction.joker.is_(True),
            Match.matchday == matchday,
        ).first() is not None


class CompetitionTeam(db.Model):
    """Verknüpfung Team <-> Wettbewerb (für mehrere Wettbewerbe)."""
    __tablename__ = "competition_teams"
    id = db.Column(db.Integer, primary_key=True)
    competition_id = db.Column(db.Integer, db.ForeignKey("competitions.id"), nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=False)
    position = db.Column(db.Integer, default=0)  # Aktuelle Tabellenposition
    points = db.Column(db.Integer, default=0)
    played = db.Column(db.Integer, default=0)
    won = db.Column(db.Integer, default=0)
    drawn = db.Column(db.Integer, default=0)
    lost = db.Column(db.Integer, default=0)
    goals_for = db.Column(db.Integer, default=0)
    goals_against = db.Column(db.Integer, default=0)
    
    team = db.relationship("Team")
    
    __table_args__ = (db.UniqueConstraint("competition_id", "team_id", name="uq_comp_team"),)


class Team(db.Model):
    __tablename__ = "teams"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    short_name = db.Column(db.String(10), nullable=False)
    logo = db.Column(db.String(300), nullable=False)
    color = db.Column(db.String(20), default="#000000")
    external_id = db.Column(db.Integer, unique=True, nullable=True)


class Match(db.Model):
    __tablename__ = "matches"
    id = db.Column(db.Integer, primary_key=True)
    competition_id = db.Column(db.Integer, db.ForeignKey("competitions.id"), nullable=False, index=True)
    matchday = db.Column(db.Integer, nullable=False, index=True)
    home_team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=False)
    away_team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=False)
    kickoff = db.Column(db.DateTime, nullable=False, index=True)
    home_score = db.Column(db.Integer, nullable=True)
    away_score = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(20), default="scheduled")  # scheduled / live / finished
    # external_id als String: 'oldb:12345' für OpenLigaDB, 'fd:67890' für football-data
    external_id = db.Column(db.String(40), unique=True, nullable=True, index=True)
    
    # Live-Scoring Felder
    is_live = db.Column(db.Boolean, default=False)
    minute = db.Column(db.Integer, nullable=True)  # Aktuelle Minute
    events = db.Column(db.Text, nullable=True)  # JSON: Tore, Karten, etc.

    home_team = db.relationship("Team", foreign_keys=[home_team_id])
    away_team = db.relationship("Team", foreign_keys=[away_team_id])
    predictions = db.relationship("Prediction", backref="match", lazy="dynamic", cascade="all, delete-orphan")
    comments = db.relationship("Comment", backref="match", lazy="dynamic", cascade="all, delete-orphan")

    def is_open(self):
        return datetime.utcnow() < self.kickoff and self.status == "scheduled"


class Prediction(db.Model):
    __tablename__ = "predictions"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    match_id = db.Column(db.Integer, db.ForeignKey("matches.id"), nullable=False)
    home_tip = db.Column(db.Integer, nullable=False)
    away_tip = db.Column(db.Integer, nullable=False)
    joker = db.Column(db.Boolean, default=False)
    points = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("user_id", "match_id", name="uq_user_match"),)


class Setting(db.Model):
    __tablename__ = "settings"
    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.Text, nullable=True)


class Comment(db.Model):
    __tablename__ = "comments"
    id = db.Column(db.Integer, primary_key=True)
    match_id = db.Column(db.Integer, db.ForeignKey("matches.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    text = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User")


class Badge(db.Model):
    __tablename__ = "badges"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(300), nullable=False)
    icon = db.Column(db.String(10), default="🏅")
    color = db.Column(db.String(20), default="#fbbf24")

    # Trigger:
    #   manual          - nur Admin vergibt manuell
    #   first_tip       - 1. Tipp abgegeben
    #   tips_count      - X Tipps abgegeben (threshold)
    #   total_points    - X Punkte erreicht (threshold)
    #   exact_count     - X exakte Tipps (threshold)
    #   joker_exact     - 1x Joker mit exaktem Tipp eingelöst
    #   perfect_day     - alle Spiele eines Spieltags exakt
    trigger_type = db.Column(db.String(30), default="manual")
    threshold = db.Column(db.Integer, default=0)
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class UserBadge(db.Model):
    __tablename__ = "user_badges"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    badge_id = db.Column(db.Integer, db.ForeignKey("badges.id"), nullable=False)
    awarded_at = db.Column(db.DateTime, default=datetime.utcnow)

    badge = db.relationship("Badge")


class SpecialPrediction(db.Model):
    """Sondertipps wie Meister, Torschützenkönig, Absteiger.
    Bei multi_team: answer ist JSON-Liste mit Team-Namen."""
    __tablename__ = "special_predictions"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey("special_questions.id"), nullable=False)
    answer = db.Column(db.Text, nullable=False)
    points = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("user_id", "question_id", name="uq_user_question"),)


class SpecialQuestion(db.Model):
    __tablename__ = "special_questions"
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.String(300), nullable=False)
    description = db.Column(db.String(500), nullable=True)

    # Antworttyp:
    #   text       = Freitext (Default)
    #   choice     = Multiple-Choice (eigene Optionen)
    #   team       = Genau 1 Mannschaft aus den Bundesliga-Teams
    #   multi_team = Mehrere Mannschaften (z.B. Absteiger)
    #   yes_no     = Ja / Nein
    #   number     = Zahl (mit min/max)
    answer_type = db.Column(db.String(20), default="text", nullable=False)

    # Bei choice: JSON-Array eigener Optionen
    # Bei multi_team: JSON-Array korrekter Antworten (für correct_answer)
    options = db.Column(db.Text, nullable=True)

    # Min/Max für answer_type=number
    number_min = db.Column(db.Integer, nullable=True)
    number_max = db.Column(db.Integer, nullable=True)

    # Anzahl gewünschter Antworten bei multi_team (z.B. 3 Absteiger)
    multi_count = db.Column(db.Integer, default=1)

    # Korrekte Antwort:
    #   - bei multi_team: JSON-Liste
    #   - sonst: String (z.B. "Bayern", "ja", "42")
    correct_answer = db.Column(db.Text, nullable=True)

    deadline = db.Column(db.DateTime, nullable=False)
    points_value = db.Column(db.Integer, default=10)
    season = db.Column(db.String(20), default="2025/26")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Prize(db.Model):
    """Frei konfigurierbare Gewinne. Werden auf der /preise-Seite angezeigt."""
    __tablename__ = "prizes"
    id = db.Column(db.Integer, primary_key=True)
    rank = db.Column(db.Integer, nullable=False)            # Platz: 1, 2, 3, ... oder 0 für Sonderpreis
    title = db.Column(db.String(120), nullable=False)       # z.B. "1. Platz", "Schlechtester Tipper"
    description = db.Column(db.String(500), nullable=True)  # Kategorie-Beschreibung
    icon = db.Column(db.String(10), default="🏆")            # Emoji
    color = db.Column(db.String(20), default="#fbbf24")     # Hex-Farbe
    amount = db.Column(db.String(50), nullable=True)        # z.B. "60 €" oder "Pokal" oder "Bier-Gutschein"
    detail = db.Column(db.String(500), nullable=True)       # zusätzliche Beschreibung
    active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)           # für eigene Sortierung
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class MatchdayWinner(db.Model):
    """Dokumentiert den Spieltagsieger pro Spieltag.
    Bei Gleichstand können mehrere User pro Spieltag eingetragen sein
    (geteilter Sieg). Wird nach jedem Sync automatisch neu berechnet."""
    __tablename__ = "matchday_winners"
    id = db.Column(db.Integer, primary_key=True)
    matchday = db.Column(db.Integer, nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    points = db.Column(db.Integer, default=0)            # erzielte Punkte
    exact_count = db.Column(db.Integer, default=0)       # # exakte Tipps
    is_shared = db.Column(db.Boolean, default=False)     # geteilter Sieg
    season = db.Column(db.String(20), default="2025/26")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User")
    __table_args__ = (
        db.UniqueConstraint("matchday", "user_id", "season", name="uq_md_user_season"),
    )


class SeasonArchive(db.Model):
    """Ewige Tabelle: Saisonergebnisse werden hier persistent abgelegt."""
    __tablename__ = "season_archive"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    season = db.Column(db.String(20), nullable=False)
    rank = db.Column(db.Integer, nullable=False)
    points = db.Column(db.Integer, default=0)
    exact_count = db.Column(db.Integer, default=0)
    diff_count = db.Column(db.Integer, default=0)
    tendency_count = db.Column(db.Integer, default=0)
    wrong_count = db.Column(db.Integer, default=0)
    archived_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User")
    __table_args__ = (db.UniqueConstraint("user_id", "season", name="uq_user_season"),)

"""Tests fuer Datenbank-Modelle."""
import pytest
from datetime import datetime, timedelta

from models import User, Team, Match, Prediction, Badge, UserBadge, Competition


class TestUserModel:
    """Test cases fuer User Model."""
    
    def test_user_creation(self, db):
        """Test: Benutzer wird korrekt erstellt."""
        user = User(username='newuser', email='new@example.com')
        user.set_password('password123')
        db.session.add(user)
        db.session.commit()
        
        assert user.id is not None
        assert user.username == 'newuser'
        assert user.email == 'new@example.com'
        assert user.check_password('password123') is True
        assert user.check_password('wrongpass') is False
        assert user.is_admin is False
    
    def test_user_unique_username(self, db, user):
        """Test: Username muss eindeutig sein."""
        with pytest.raises(Exception):
            duplicate = User(username='testuser', email='other@example.com')
            db.session.add(duplicate)
            db.session.commit()
    
    def test_user_total_points(self, db, user, finished_match):
        """Test: Gesamtpunkte werden korrekt berechnet."""
        pred = Prediction(
            user_id=user.id,
            match_id=finished_match.id,
            home_tip=3,
            away_tip=1,
            points=4
        )
        db.session.add(pred)
        db.session.commit()
        
        assert user.total_points() == 4
    
    def test_joker_usage(self, db, user, competition, teams):
        """Test: Joker-Nutzung pro Spieltag wird erkannt."""
        match1 = Match(
            competition_id=competition.id,
            matchday=1,
            home_team_id=teams[0].id,
            away_team_id=teams[1].id,
            kickoff=datetime.utcnow() + timedelta(days=1)
        )
        match2 = Match(
            competition_id=competition.id,
            matchday=1,
            home_team_id=teams[2].id,
            away_team_id=teams[3].id,
            kickoff=datetime.utcnow() + timedelta(days=1)
        )
        db.session.add_all([match1, match2])
        db.session.commit()
        
        # Joker fuer ersten Tipp
        pred = Prediction(user_id=user.id, match_id=match1.id, home_tip=1, away_tip=0, joker=True)
        db.session.add(pred)
        db.session.commit()
        
        assert user.joker_used_for_matchday(1) is True


class TestMatchModel:
    """Test cases fuer Match Model."""
    
    def test_match_creation(self, db, competition, teams):
        """Test: Spiel wird korrekt erstellt."""
        match = Match(
            competition_id=competition.id,
            matchday=5,
            home_team_id=teams[0].id,
            away_team_id=teams[1].id,
            kickoff=datetime.utcnow() + timedelta(days=2)
        )
        db.session.add(match)
        db.session.commit()
        
        assert match.id is not None
        assert match.matchday == 5
        assert match.home_team.name == 'FC Bayern München'
        assert match.away_team.name == 'Borussia Dortmund'
        assert match.is_live is False
    
    def test_match_is_open(self, db, competition, teams):
        """Test: Tipp-Offenheit wird korrekt bestimmt."""
        # Zukuenftiges Spiel - offen
        future_match = Match(
            competition_id=competition.id,
            matchday=1,
            home_team_id=teams[0].id,
            away_team_id=teams[1].id,
            kickoff=datetime.utcnow() + timedelta(hours=1),
            status='scheduled'
        )
        # Vergangenes Spiel - geschlossen
        past_match = Match(
            competition_id=competition.id,
            matchday=1,
            home_team_id=teams[2].id,
            away_team_id=teams[3].id,
            kickoff=datetime.utcnow() - timedelta(hours=1),
            status='scheduled'
        )
        db.session.add_all([future_match, past_match])
        db.session.commit()
        
        assert future_match.is_open() is True
        assert past_match.is_open() is False
    
    def test_finished_match(self, db, finished_match):
        """Test: Beendetes Spiel hat Ergebnis."""
        assert finished_match.status == 'finished'
        assert finished_match.home_score == 3
        assert finished_match.away_score == 1
        assert finished_match.is_open() is False


class TestPredictionModel:
    """Test cases fuer Prediction Model."""
    
    def test_prediction_creation(self, db, user, match):
        """Test: Tipp wird korrekt erstellt."""
        pred = Prediction(
            user_id=user.id,
            match_id=match.id,
            home_tip=2,
            away_tip=1,
            joker=True
        )
        db.session.add(pred)
        db.session.commit()
        
        assert pred.id is not None
        assert pred.home_tip == 2
        assert pred.away_tip == 1
        assert pred.joker is True
        assert pred.points == 0  # Noch nicht berechnet
    
    def test_prediction_unique_constraint(self, db, user, match, prediction):
        """Test: Ein User kann nur einmal pro Spiel tippen."""
        with pytest.raises(Exception):
            duplicate = Prediction(
                user_id=user.id,
                match_id=match.id,
                home_tip=0,
                away_tip=0
            )
            db.session.add(duplicate)
            db.session.commit()


class TestCompetitionModel:
    """Test cases fuer Competition Model."""
    
    def test_competition_creation(self, db):
        """Test: Wettbewerb wird korrekt erstellt."""
        comp = Competition(
            code='CL',
            name='Champions League',
            season='2025/26',
            matchdays=13,
            teams_count=32
        )
        db.session.add(comp)
        db.session.commit()
        
        assert comp.id is not None
        assert comp.code == 'CL'
        assert comp.matchdays == 13
        assert comp.is_active is True

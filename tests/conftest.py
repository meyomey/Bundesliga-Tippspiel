"""Pytest Configuration and Fixtures."""
import pytest
from datetime import datetime, timedelta

from app import create_app
from extensions import db as _db
from models import User, Team, Match, Competition, CompetitionTeam, Prediction, Badge


@pytest.fixture(scope='session')
def app():
    """Create application for the tests."""
    _app = create_app()
    _app.config['TESTING'] = True
    _app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    _app.config['WTF_CSRF_ENABLED'] = False
    _app.config['MAIL_SUPPRESS_SEND'] = True
    
    with _app.app_context():
        _db.create_all()
        yield _app
        _db.drop_all()


@pytest.fixture(scope='function')
def db(app):
    """Provide the database for tests."""
    with app.app_context():
        yield _db
        _db.session.rollback()
        _db.session.remove()
        # Clear all tables
        for table in reversed(_db.metadata.sorted_tables):
            _db.session.execute(table.delete())
        _db.session.commit()


@pytest.fixture(scope='function')
def client(app):
    """Create test client."""
    return app.test_client()


@pytest.fixture(scope='function')
def auth_client(app, user):
    """Create authenticated test client."""
    with app.test_client() as client:
        client.post('/auth/login', data={
            'email': user.email,
            'password': 'testpass123'
        }, follow_redirects=True)
        yield client


@pytest.fixture(scope='function')
def user(db):
    """Create a test user."""
    user = User(
        username='testuser',
        email='test@example.com',
        full_name='Test User'
    )
    user.set_password('testpass123')
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture(scope='function')
def admin_user(db):
    """Create an admin user."""
    admin = User(
        username='admin',
        email='admin@example.com',
        is_admin=True
    )
    admin.set_password('admin123')
    db.session.add(admin)
    db.session.commit()
    return admin


@pytest.fixture(scope='function')
def competition(db):
    """Create a test competition."""
    comp = Competition(
        code='TEST',
        name='Test League',
        season='2025/26',
        matchdays=34,
        teams_count=18
    )
    db.session.add(comp)
    db.session.commit()
    return comp


@pytest.fixture(scope='function')
def teams(db):
    """Create test teams."""
    teams_data = [
        {'name': 'FC Bayern München', 'short_name': 'FCB', 'logo': 'bayern.png'},
        {'name': 'Borussia Dortmund', 'short_name': 'BVB', 'logo': 'dortmund.png'},
        {'name': 'Bayer Leverkusen', 'short_name': 'B04', 'logo': 'leverkusen.png'},
        {'name': 'RB Leipzig', 'short_name': 'RBL', 'logo': 'leipzig.png'},
    ]
    teams = []
    for data in teams_data:
        team = Team(**data)
        db.session.add(team)
        teams.append(team)
    db.session.commit()
    return teams


@pytest.fixture(scope='function')
def match(db, competition, teams):
    """Create a test match."""
    match = Match(
        competition_id=competition.id,
        matchday=1,
        home_team_id=teams[0].id,
        away_team_id=teams[1].id,
        kickoff=datetime.utcnow() + timedelta(days=1),
        status='scheduled'
    )
    db.session.add(match)
    db.session.commit()
    return match


@pytest.fixture(scope='function')
def finished_match(db, competition, teams):
    """Create a finished match with result."""
    match = Match(
        competition_id=competition.id,
        matchday=1,
        home_team_id=teams[0].id,
        away_team_id=teams[1].id,
        kickoff=datetime.utcnow() - timedelta(days=1),
        status='finished',
        home_score=3,
        away_score=1
    )
    db.session.add(match)
    db.session.commit()
    return match


@pytest.fixture(scope='function')
def prediction(db, user, match):
    """Create a test prediction."""
    pred = Prediction(
        user_id=user.id,
        match_id=match.id,
        home_tip=2,
        away_tip=1,
        joker=False
    )
    db.session.add(pred)
    db.session.commit()
    return pred


@pytest.fixture(scope='function')
def badge(db):
    """Create a test badge."""
    badge = Badge(
        code='first_tip',
        name='Erster Tipp',
        description='Erster Tipp abgegeben',
        icon='🎯',
        trigger_type='first_tip'
    )
    db.session.add(badge)
    db.session.commit()
    return badge

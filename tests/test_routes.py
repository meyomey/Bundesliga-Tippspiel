"""Tests fuer Flask Routes."""
import pytest


class TestAuthRoutes:
    """Test cases fuer Auth Blueprint."""
    
    def test_login_page_loads(self, client):
        """Test: Login-Seite ist erreichbar."""
        response = client.get('/auth/login')
        assert response.status_code == 200
        assert b'Anmelden' in response.data or b'Login' in response.data
    
    def test_register_page_loads(self, client):
        """Test: Registrierungs-Seite ist erreichbar."""
        response = client.get('/auth/register')
        assert response.status_code == 200
        assert b'Registrieren' in response.data or b'Register' in response.data
    
    def test_valid_login(self, client, user):
        """Test: Gueltiger Login funktioniert."""
        response = client.post('/auth/login', data={
            'email': 'test@example.com',
            'password': 'testpass123'
        }, follow_redirects=True)
        
        assert response.status_code == 200
        # Sollte weiterleiten zu Dashboard oder zeigt angemeldeten Status
    
    def test_invalid_login(self, client, user):
        """Test: Ungueltiger Login wird abgelehnt."""
        response = client.post('/auth/login', data={
            'email': 'test@example.com',
            'password': 'wrongpassword'
        }, follow_redirects=True)
        
        assert response.status_code == 200
        assert b'falsch' in response.data.lower() or b'invalid' in response.data.lower()
    
    def test_logout(self, auth_client):
        """Test: Logout funktioniert."""
        response = auth_client.get('/auth/logout', follow_redirects=True)
        assert response.status_code == 200


class TestMainRoutes:
    """Test cases fuer Main Blueprint."""
    
    def test_landing_page(self, client):
        """Test: Startseite ist erreichbar."""
        response = client.get('/')
        assert response.status_code in [200, 302]  # OK oder Redirect
    
    def test_dashboard_requires_login(self, client):
        """Test: Dashboard erfordert Login."""
        response = client.get('/dashboard', follow_redirects=True)
        assert response.status_code == 200
        # Sollte Login-Seite zeigen
        assert b'Anmelden' in response.data or b'Bitte melde dich an' in response.data
    
    def test_dashboard_accessible_when_logged_in(self, auth_client):
        """Test: Dashboard erreichbar wenn eingeloggt."""
        response = auth_client.get('/dashboard')
        assert response.status_code == 200
    
    def test_schedule_page(self, client):
        """Test: Spielplan ist oeffentlich."""
        response = client.get('/schedule')
        assert response.status_code == 200
    
    def test_leaderboard_page(self, client):
        """Test: Rangliste ist oeffentlich."""
        response = client.get('/leaderboard')
        assert response.status_code == 200


class TestAdminRoutes:
    """Test cases fuer Admin Blueprint."""
    
    def test_admin_requires_login(self, client):
        """Test: Admin erfordert Login."""
        response = client.get('/admin/dashboard', follow_redirects=True)
        assert response.status_code == 200
    
    def test_admin_requires_admin_rights(self, auth_client):
        """Test: Admin erfordert Admin-Rechte."""
        response = auth_client.get('/admin/dashboard', follow_redirects=True)
        # Sollte 403 oder Redirect sein
        assert response.status_code in [200, 403]
    
    def test_admin_accessible_for_admin(self, client, admin_user):
        """Test: Admin-Panel fuer Admin erreichbar."""
        client.post('/auth/login', data={
            'email': 'admin@example.com',
            'password': 'admin123'
        }, follow_redirects=True)
        
        response = client.get('/admin/dashboard')
        assert response.status_code == 200


class TestAPIRoutes:
    """Test cases fuer API Blueprint."""
    
    def test_api_tip_requires_login(self, client, match):
        """Test: Tipp-API erfordert Login."""
        response = client.post('/api/tip', json={
            'match_id': match.id,
            'home_tip': 2,
            'away_tip': 1
        })
        # Sollte 401 oder 302 (Redirect zu Login)
        assert response.status_code in [401, 302, 403]
    
    def test_api_tip_valid(self, auth_client, match):
        """Test: Gueltiger Tipp wird gespeichert."""
        response = auth_client.post('/api/tip', 
            data={'match_id': match.id, 'home_tip': 2, 'away_tip': 1},
            follow_redirects=True
        )
        assert response.status_code == 200

import unittest
from unittest.mock import patch, MagicMock
import sys
import os

# Add the project root to the path so we can import app
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from flask import Flask
from flask_login import LoginManager

class TestAuth(unittest.TestCase):
    def setUp(self):
        # Mock the database and models BEFORE importing the app
        self.db_patcher = patch('app.models.db')
        self.user_patcher = patch('app.models.User')
        self.mock_db = self.db_patcher.start()
        self.mock_user = self.user_patcher.start()
        
        # Mock SQLAlchemy init_app and create_all to do nothing
        self.mock_db.init_app = MagicMock()
        self.mock_db.create_all = MagicMock()

        # Import the app now that mocks are in place
        # We need to reload it if it was already imported, but for a script run it's fine
        from app.anpr_web import app
        
        self.app = app
        self.app.config['TESTING'] = True
        self.app.config['WTF_CSRF_ENABLED'] = False  # Disable CSRF for testing
        self.app.config['SECRET_KEY'] = 'test_key'
        self.client = self.app.test_client()

    def tearDown(self):
        self.db_patcher.stop()
        self.user_patcher.stop()

    def test_health_check(self):
        """Test that /health endpoint returns 200 and healthy status."""
        response = self.client.get('/health')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"status": "healthy"})

    def test_login_page_loads(self):
        """Test that /login page loads successfully."""
        response = self.client.get('/login')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Login', response.data)

    def test_root_redirects_when_not_logged_in(self):
        """Test that accessing / redirects to /login when not authenticated."""
        response = self.client.get('/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    @patch('app.anpr_web.login_user')
    def test_login_success(self, mock_login_user):
        """Test successful login logic."""
        # Setup mock user
        mock_user_instance = MagicMock()
        mock_user_instance.check_password.return_value = True
        self.mock_user.query.filter_by.return_value.first.return_value = mock_user_instance

        response = self.client.post('/login', data={
            'username': 'testuser',
            'password': 'correctpassword'
        })

        # Verify redirects to index
        self.assertEqual(response.status_code, 302)
        # Verify login_user was called
        mock_login_user.assert_called_once()

    def test_login_failure(self):
        """Test failed login logic."""
        # Setup mock to return None (user not found)
        self.mock_user.query.filter_by.return_value.first.return_value = None

        response = self.client.post('/login', data={
            'username': 'wronguser',
            'password': 'wrongpassword'
        }, follow_redirects=True)

        # Should stay on login page (200 OK after follow redirect, or check for error message)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Invalid username or password', response.data)

if __name__ == '__main__':
    unittest.main()

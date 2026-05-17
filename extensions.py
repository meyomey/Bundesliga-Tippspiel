"""Flask Extensions Singleton (vermeidet Circular Imports).

Hinweis: Flask-Migrate ist absichtlich NICHT importiert, da:
1. Es alembic+tomli braucht, was auf einigen Webhosting-Tarifen Probleme macht
2. Wir stattdessen die robustere Auto-Migration in utils.auto_migrate_schema() nutzen
"""
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_mail import Mail
from flask_wtf.csrf import CSRFProtect

# Neue Features
from cache import CacheManager

db = SQLAlchemy()
login_manager = LoginManager()
mail = Mail()
cache = CacheManager()
csrf = CSRFProtect()

login_manager.login_view = "auth.login"
login_manager.login_message = "Bitte melde dich an, um diese Seite zu sehen."
login_manager.login_message_category = "info"

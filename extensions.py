"""Shared extension singletons.

Kept in their own module so ``models.py``, ``services/``, ``bot/`` and the
blueprints can all import them without circular-import cycles.
"""

from __future__ import annotations

from authlib.integrations.flask_client import OAuth
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

# Discord OAuth2 client; registered in create_app with app config.
oauth = OAuth()

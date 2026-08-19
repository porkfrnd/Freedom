"""Shared extension singletons.

Kept in their own module so ``models.py``, ``services/`` and the
blueprints can all import them without circular-import cycles.
"""

from __future__ import annotations

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

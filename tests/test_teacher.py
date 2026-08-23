"""Tests for teacher role, admin user management, and theme toggle."""

from __future__ import annotations

import pytest
from conftest import _create_user, make_token, csrf_of


def _seed_csrf(client):
    """Hit any page to seed the CSRF cookie."""
    client.get("/challenges")


class TestTeacherRole:
    """Teachers can post challenges; non-teachers cannot."""

    def test_teacher_can_post_challenge(self, app, teacher_client):
        with app.app_context():
            _create_user(app, 333, "carol", is_teacher=True)
            token = make_token(app, 333, "carol", is_teacher=True)
        teacher_client.set_cookie("ffd_session", token)
        _seed_csrf(teacher_client)
        csrf = csrf_of(teacher_client)
        resp = teacher_client.post("/challenges", data={
            "csrf_token": csrf,
            "title": "Teacher Challenge",
            "description": "A challenge from a teacher.",
        }, follow_redirects=False)
        assert resp.status_code == 302

    def test_member_cannot_post_challenge(self, app, member_client):
        _seed_csrf(member_client)
        csrf = csrf_of(member_client)
        resp = member_client.post("/challenges", data={
            "csrf_token": csrf,
            "title": "Should fail",
            "description": "Nope.",
        }, follow_redirects=False)
        assert resp.status_code == 403

    def test_challenges_page_shows_create_for_teacher(self, app, teacher_client):
        with app.app_context():
            _create_user(app, 333, "carol", is_teacher=True)
            token = make_token(app, 333, "carol", is_teacher=True)
        teacher_client.set_cookie("ffd_session", token)
        resp = teacher_client.get("/challenges")
        assert resp.status_code == 200
        assert b"Post challenge" in resp.data


class TestAdminUserManagement:
    """Admin can search, promote, demote users."""

    def test_admin_users_page(self, app, admin_client):
        resp = admin_client.get("/admin/users")
        assert resp.status_code == 200
        assert b"User management" in resp.data

    def test_member_cannot_access_admin_users(self, app, member_client):
        resp = member_client.get("/admin/users")
        assert resp.status_code == 403

    def test_search_user_by_uid(self, app, admin_client):
        with app.app_context():
            _create_user(app, 555, "eve")
        resp = admin_client.get("/admin/users?uid=555")
        assert resp.status_code == 200
        assert b"eve" in resp.data

    def test_search_nonexistent_uid(self, app, admin_client):
        resp = admin_client.get("/admin/users?uid=99999")
        assert resp.status_code == 200
        assert b"No user found" in resp.data

    def test_promote_teacher(self, app, admin_client):
        with app.app_context():
            _create_user(app, 666, "frank")
        _seed_csrf(admin_client)
        csrf = csrf_of(admin_client)
        resp = admin_client.post("/admin/users/666/promote-teacher", data={
            "csrf_token": csrf,
        }, follow_redirects=False)
        assert resp.status_code == 302

    def test_demote_teacher(self, app, admin_client):
        with app.app_context():
            _create_user(app, 777, "grace", is_teacher=True)
        _seed_csrf(admin_client)
        csrf = csrf_of(admin_client)
        resp = admin_client.post("/admin/users/777/demote-teacher", data={
            "csrf_token": csrf,
        }, follow_redirects=False)
        assert resp.status_code == 302

    def test_toggle_admin(self, app, admin_client):
        with app.app_context():
            _create_user(app, 888, "hank")
        _seed_csrf(admin_client)
        csrf = csrf_of(admin_client)
        resp = admin_client.post("/admin/users/888/toggle-admin", data={
            "csrf_token": csrf,
        }, follow_redirects=False)
        assert resp.status_code == 302


class TestJWTClaims:
    """JWT tokens include is_teacher claim."""

    def test_token_includes_is_teacher(self, app):
        with app.app_context():
            token = make_token(app, 999, "iz", is_teacher=True)
            from services.auth import decode_session_token
            claims = decode_session_token(token)
            assert claims is not None
            assert claims["is_teacher"] is True

    def test_token_defaults_is_teacher_false(self, app):
        with app.app_context():
            token = make_token(app, 998, "joe")
            from services.auth import decode_session_token
            claims = decode_session_token(token)
            assert claims is not None
            assert claims["is_teacher"] is False

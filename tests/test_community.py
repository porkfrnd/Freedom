"""Community features — challenges and giveaways."""

from __future__ import annotations

from extensions import db
from models import Challenge, Giveaway, User

from conftest import csrf_of


def test_admin_creates_challenge(app, admin_client):
    admin_client.get("/challenges")  # seed CSRF
    csrf = csrf_of(admin_client)
    resp = admin_client.post("/challenges", data={
        "title": "Freestyle Friday",
        "description": "30-second freestyle, song picked at random.",
        "csrf_token": csrf,
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Freestyle Friday" in resp.data

    with app.app_context():
        assert Challenge.query.count() == 1
        c = Challenge.query.first()
        assert c.status == "ACTIVE"


def test_challenge_requires_title(app, admin_client):
    admin_client.get("/challenges")
    csrf = csrf_of(admin_client)
    resp = admin_client.post("/challenges", data={
        "title": "  ",
        "description": "No title here.",
        "csrf_token": csrf,
    }, follow_redirects=True)
    assert b"Give the challenge a title" in resp.data


def test_admin_closes_challenge(app, admin_client):
    with app.app_context():
        c = Challenge(creator_id=222, title="Old challenge", description="Done", status="ACTIVE")
        db.session.add(c)
        db.session.commit()
        cid = c.id

    admin_client.get("/challenges")
    csrf = csrf_of(admin_client)
    resp = admin_client.post(
        f"/challenges/{cid}/close",
        data={"csrf_token": csrf},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        assert db.session.get(Challenge, cid).status == "ENDED"


def test_admin_creates_giveaway(app, admin_client):
    admin_client.get("/giveaways")
    csrf = csrf_of(admin_client)
    resp = admin_client.post("/giveaways", data={
        "prize": "Free showcase entry",
        "description": "One lucky dancer gets in free.",
        "num_winners": "1",
        "csrf_token": csrf,
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Free showcase entry" in resp.data

    with app.app_context():
        assert Giveaway.query.count() == 1
        g = Giveaway.query.first()
        assert g.status == "ACTIVE"
        assert g.num_winners == 1


def test_member_can_enter_giveaway_once(app, member_client):
    with app.app_context():
        g = Giveaway(creator_id=222, prize="Merch pack", status="ACTIVE")
        db.session.add(g)
        db.session.commit()
        gid = g.id

    member_client.get("/giveaways")
    csrf = csrf_of(member_client)
    resp = member_client.post(
        f"/giveaways/{gid}/enter",
        data={"csrf_token": csrf},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"You're in" in resp.data

    with app.app_context():
        g = db.session.get(Giveaway, gid)
        assert g.has_entered(111)

    # Second entry is refused.
    member_client.post(
        f"/giveaways/{gid}/enter",
        data={"csrf_token": csrf},
        follow_redirects=True,
    )
    with app.app_context():
        g = db.session.get(Giveaway, gid)
        assert len(g.entrants) == 1


def test_member_cannot_enter_ended_giveaway(app, member_client):
    from models import utcnow
    from datetime import timedelta
    with app.app_context():
        g = Giveaway(
            creator_id=222,
            prize="Past prize",
            status="ENDED",
            deadline=utcnow() - timedelta(days=1),
        )
        db.session.add(g)
        db.session.commit()
        gid = g.id

    member_client.get("/giveaways")
    csrf = csrf_of(member_client)
    resp = member_client.post(
        f"/giveaways/{gid}/enter",
        data={"csrf_token": csrf},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"already ended" in resp.data

    with app.app_context():
        assert db.session.get(Giveaway, gid).entrants == []


def test_admin_draws_winners(app, admin_client):
    from models import utcnow
    from datetime import timedelta
    with app.app_context():
        g = Giveaway(
            creator_id=222, prize="Sneakers", status="ENDED",
            entrants=[111, 222, 333],
            deadline=utcnow() - timedelta(days=1),
        )
        db.session.add(g)
        db.session.commit()
        gid = g.id

    admin_client.get("/giveaways")
    csrf = csrf_of(admin_client)
    resp = admin_client.post(
        f"/giveaways/{gid}/draw",
        data={"csrf_token": csrf},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Winners drawn" in resp.data

    with app.app_context():
        g = db.session.get(Giveaway, gid)
        assert g.status == "ENDED"
        assert len(g.winners) == 1
        assert g.winners[0] in [111, 222, 333]


def test_admin_cannot_draw_running_giveaway(app, admin_client):
    with app.app_context():
        g = Giveaway(creator_id=222, prize="Still running", status="ACTIVE", entrants=[111])
        db.session.add(g)
        db.session.commit()
        gid = g.id

    admin_client.get("/giveaways")
    csrf = csrf_of(admin_client)
    resp = admin_client.post(
        f"/giveaways/{gid}/draw",
        data={"csrf_token": csrf},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"still running" in resp.data

    with app.app_context():
        assert db.session.get(Giveaway, gid).status == "ACTIVE"

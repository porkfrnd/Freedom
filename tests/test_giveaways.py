"""Giveaway logic tests (§6.3, §9.1: draw with fewer entrants than winners,
duplicate-entry protection, scheduler finalize)."""

from __future__ import annotations

from datetime import timedelta

from bot.cogs.giveaways import draw_winners
from bot.views.giveaways import GiveawayButton, GiveawayView
from extensions import db
from models import Giveaway, User, utcnow

from conftest import csrf_of, mock_live_admin


# ── draw_winners: the fewer-entrants-than-winners edge case (§6.3) ──────────

def test_draw_more_winners_than_entrants():
    winners, shortfall = draw_winners([1, 2], num_winners=3)
    assert sorted(winners) == [1, 2]  # everyone who entered wins
    assert shortfall == 1  # logged, never an error


def test_draw_exact_match():
    winners, shortfall = draw_winners([1, 2, 3], num_winners=3)
    assert sorted(winners) == [1, 2, 3]
    assert shortfall == 0


def test_draw_no_entrants():
    winners, shortfall = draw_winners([], num_winners=2)
    assert winners == []
    assert shortfall == 2


def test_draw_excludes_previous_winners():
    winners, shortfall = draw_winners([1, 2, 3, 4], num_winners=2, exclude=[1, 2])
    assert set(winners).isdisjoint({1, 2})
    assert len(winners) == 2
    assert shortfall == 0


def test_draw_is_within_limit():
    winners, _ = draw_winners(list(range(10)), num_winners=2)
    assert len(winners) == 2


# ── Entry dedupe (§6.3) ─────────────────────────────────────────────────────

def test_add_entrant_dedupes(app):
    with app.app_context():
        db.session.add(User(discord_id=1, username="a"))
        gw = Giveaway(
            prize="Tickets",
            channel_id=100,
            end_time=utcnow() + timedelta(days=1),
            num_winners=1,
        )
        db.session.add(gw)
        db.session.commit()

        assert gw.add_entrant(42) is True
        assert gw.add_entrant(42) is False  # duplicate rejected
        assert gw.add_entrant(43) is True
        assert len(gw.entrants) == 2


def test_giveaway_has_ended_and_has_entered(app):
    with app.app_context():
        ended = Giveaway(prize="Old", channel_id=1, end_time=utcnow() - timedelta(minutes=1))
        assert ended.has_ended
        ended.add_entrant(7)
        assert ended.has_entered(7)
        assert not ended.has_entered(8)


# ── Persistent view (§6.3) ──────────────────────────────────────────────────

def test_giveaway_view_custom_id_is_stable():
    view = GiveawayView(123, "Prize")
    button = view.children[0]
    assert isinstance(button, GiveawayButton)
    assert button.custom_id == "ffd_giveaway:123"
    assert view.is_persistent()


def test_draw_and_finalize_via_scheduler(app, client):
    """The scheduler's DB finalize draws winners and ends the giveaway."""
    from bot.scheduler import AppScheduler

    with app.app_context():
        db.session.add(User(discord_id=1, username="a"))
        gw = Giveaway(
            prize="Studio time",
            channel_id=100,
            end_time=utcnow() - timedelta(minutes=5),
            num_winners=3,
            entrants=[10, 20],
        )
        db.session.add(gw)
        db.session.commit()
        giveaway_id = gw.id

    scheduler = AppScheduler(app, bot_runtime=None)
    assert scheduler._finalize_db(giveaway_id) is True

    with app.app_context():
        row = db.session.get(Giveaway, giveaway_id)
        assert row.status == "ENDED"
        assert sorted(row.winners) == [10, 20]  # fewer entrants than winners


# ── Web create flow (admin) ─────────────────────────────────────────────────

def test_admin_creates_giveaway(app, admin_client, monkeypatch):
    mock_live_admin(monkeypatch, is_member=True, is_admin=True)
    admin_client.get("/giveaways")  # CSRF cookie
    csrf = csrf_of(admin_client)

    resp = admin_client.post(
        "/giveaways",
        data={
            "csrf_token": csrf,
            "prize": "Signed shoes",
            "channel_id": "123",
            "end_time": (utcnow() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
            "num_winners": "2",
        },
    )
    assert resp.status_code == 302
    with app.app_context():
        gw = db.session.query(Giveaway).one()
        assert gw.prize == "Signed shoes"
        assert gw.status == "ACTIVE"
        assert gw.num_winners == 2


def test_giveaway_form_rejects_past_end_time(app, admin_client, monkeypatch):
    mock_live_admin(monkeypatch, is_member=True, is_admin=True)
    admin_client.get("/giveaways")
    csrf = csrf_of(admin_client)

    resp = admin_client.post(
        "/giveaways",
        data={
            "csrf_token": csrf,
            "prize": "Too late",
            "channel_id": "123",
            "end_time": (utcnow() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
            "num_winners": "1",
        },
    )
    assert resp.status_code == 302
    with app.app_context():
        assert db.session.query(Giveaway).count() == 0

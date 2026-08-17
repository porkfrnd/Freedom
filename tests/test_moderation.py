"""Moderation logic tests (§6.2, §9.1): the escalation rule and the
circuit breaker — both pure, no network."""

from __future__ import annotations

import asyncio

import pytest

from bot.cogs.moderation import resolve_action
from services.groq_moderation import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    ClassificationError,
    ModerationVerdict,
)


# ── Severity/action matrix + escalation (§6.2) ──────────────────────────────

def test_base_actions_without_history():
    assert resolve_action(1, {}) == "WARNING"
    assert resolve_action(2, {}) == "TIMEOUT_SHORT"
    assert resolve_action(3, {}) == "TIMEOUT_LONG"


def test_second_tier1_escalates_to_tier2_behavior():
    # One prior tier-1 within the 30-day window → this is the second.
    assert resolve_action(1, {1: 1}) == "TIMEOUT_SHORT"


def test_third_tier1_escalates_to_long_timeout():
    assert resolve_action(1, {1: 2}) == "TIMEOUT_LONG"
    assert resolve_action(1, {1: 5}) == "TIMEOUT_LONG"


def test_second_tier2_escalates_to_long_timeout():
    assert resolve_action(2, {2: 1}) == "TIMEOUT_LONG"


def test_first_tier1_with_other_history_stays_warning():
    # A prior tier-2 does not escalate a fresh tier-1 (rule is per-tier).
    assert resolve_action(1, {2: 1}) == "WARNING"


def test_tier3_is_always_long():
    assert resolve_action(3, {1: 3, 2: 3}) == "TIMEOUT_LONG"


# ── Verdict parsing ─────────────────────────────────────────────────────────

def test_verdict_parses_clean_payload():
    verdict = ModerationVerdict.from_payload(
        {"category": "harassment", "severity_tier": 2, "reasoning": "Targeted insults", "suggested_action": "timeout_short"}
    )
    assert verdict.category == "harassment"
    assert verdict.severity_tier == 2
    assert verdict.suggested_action == "timeout_short"


def test_verdict_none_tier_zero():
    verdict = ModerationVerdict.from_payload(
        {"category": "none", "severity_tier": 0, "reasoning": "", "suggested_action": "none"}
    )
    assert verdict.severity_tier == 0


def test_verdict_rejects_unknown_category():
    with pytest.raises(ClassificationError):
        ModerationVerdict.from_payload({"category": "banana", "severity_tier": 1})


def test_verdict_rejects_out_of_range_tier():
    with pytest.raises(ClassificationError):
        ModerationVerdict.from_payload({"category": "spam", "severity_tier": 9})


# ── Circuit breaker (§6.2) ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_breaker_opens_after_threshold_failures():
    breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=3600)

    async def failing():
        raise RuntimeError("groq down")

    for _ in range(3):
        with pytest.raises(RuntimeError):
            await breaker.execute(failing)
    assert breaker.is_open

    with pytest.raises(CircuitOpenError):
        await breaker.execute(failing)


@pytest.mark.asyncio
async def test_breaker_stays_closed_on_success():
    breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=3600)

    async def ok():
        return "fine"

    for _ in range(5):
        assert await breaker.execute(ok) == "fine"
    assert not breaker.is_open


@pytest.mark.asyncio
async def test_breaker_half_open_test_call_can_close():
    breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=0.05)

    async def flaky():
        raise RuntimeError("down")

    with pytest.raises(RuntimeError):
        await breaker.execute(flaky)  # 1st failure
    with pytest.raises(RuntimeError):
        await breaker.execute(flaky)  # 2nd failure → open
    assert breaker.is_open

    await asyncio.sleep(0.07)  # cooldown elapses

    async def recovered():
        return "back"

    assert await breaker.execute(recovered) == "back"  # test call succeeds
    assert breaker.state is CircuitState.CLOSED
    assert not breaker.is_open


@pytest.mark.asyncio
async def test_breaker_reopens_after_failed_test_call():
    breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=0.05)

    async def fail():
        raise RuntimeError("still down")

    with pytest.raises(RuntimeError):
        await breaker.execute(fail)
    with pytest.raises(RuntimeError):
        await breaker.execute(fail)
    assert breaker.is_open

    await asyncio.sleep(0.07)
    with pytest.raises(RuntimeError):
        await breaker.execute(fail)  # half-open test call fails again
    assert breaker.is_open
    assert breaker.state is CircuitState.OPEN

"""AI moderation via Groq (§6.2).

* Async client, hard timeout (default 5s).
* Strict-JSON system prompt; the classifier returns ``category``,
  ``severity_tier`` (0 = no violation, else 1-3) and ``reasoning``.
* A circuit breaker wraps every call: after ``threshold`` consecutive
  failures/timeouts the breaker opens and the moderation listener switches
  to log-only mode (content stored, no live action). After the cooldown it
  allows a single test call; success closes the breaker, failure reopens it.

The breaker is intentionally implemented as a small standalone class so it
can be unit-tested without any network access.
"""

from __future__ import annotations

import asyncio
import enum
import json
import time
from dataclasses import dataclass

from utils.logging import get_logger

log = get_logger("services.groq_moderation")

SYSTEM_PROMPT = (
    "You are a content moderation classifier for a dance-community Discord server. "
    "Given a user's message, classify it and respond with ONLY a JSON object, no "
    "prose, with exactly these keys:\n"
    '{"category": "<one of: none, harassment, hate_speech, nsfw, spam, self_harm, '
    'violent, personal_info, other>", "severity_tier": <0|1|2|3>, "reasoning": "<short>", '
    '"suggested_action": "<one of: none, warn, timeout_short, timeout_long>"}\n'
    "Rules:\n"
    "- severity_tier 0 means the message is fine (category 'none'); "
    "1 = mild/borderline; 2 = standard violation (abuse, insults, explicit "
    "content); 3 = severe (hate speech, targeted harassment, threats, "
    "self-harm encouragement).\n"
    "- If category is 'none', severity_tier MUST be 0 and suggested_action 'none'.\n"
    "- Be conservative: only flag content that clearly violates a community norm; "
    "do not flag constructive critique, dance talk, or playful banter.\n"
    "- reasoning must be a short, factual sentence."
)


class ClassificationError(Exception):
    """Raised when the model output could not be parsed into a verdict."""


@dataclass
class ModerationVerdict:
    category: str
    severity_tier: int
    reasoning: str
    suggested_action: str

    @classmethod
    def from_payload(cls, payload: dict) -> "ModerationVerdict":
        category = str(payload.get("category", "other")).strip().lower()
        if category not in {
            "none", "harassment", "hate_speech", "nsfw", "spam",
            "self_harm", "violent", "personal_info", "other",
        }:
            raise ClassificationError(f"unknown category: {category!r}")

        try:
            tier = int(payload.get("severity_tier", 0))
        except (TypeError, ValueError) as exc:
            raise ClassificationError(f"bad severity_tier: {payload.get('severity_tier')!r}") from exc

        if tier == 0:
            return cls(category="none", severity_tier=0, reasoning="", suggested_action="none")

        if tier not in (1, 2, 3):
            raise ClassificationError(f"severity_tier out of range: {tier}")

        action = str(payload.get("suggested_action", "")).strip().lower()
        if action not in {"none", "warn", "timeout_short", "timeout_long"}:
            action = {1: "warn", 2: "timeout_short", 3: "timeout_long"}[tier]

        reasoning = str(payload.get("reasoning", ""))[:500]
        return cls(
            category=category,
            severity_tier=tier,
            reasoning=reasoning,
            suggested_action=action,
        )


class CircuitState(str, enum.Enum):
    CLOSED = "closed"      # calls pass through
    OPEN = "open"          # log-only mode
    HALF_OPEN = "half_open"  # cooldown elapsed; one test call allowed


class CircuitOpenError(Exception):
    """Raised when the breaker is open and the call is refused."""


class CircuitBreaker:
    """Consecutive-failure circuit breaker (see module docstring)."""

    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 300.0):
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown_seconds = max(0.001, cooldown_seconds)  # tiny values allowed for tests
        self.state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._lock = asyncio.Lock()
        self.alerted_once = False  # reset when the breaker (re)opens

    async def execute(self, coro_factory):
        """Run ``await coro_factory()`` under breaker rules.

        Raises :class:`CircuitOpenError` when the breaker refuses the call.
        Other exceptions from ``coro_factory`` count as failures and
        propagate to the caller.
        """
        async with self._lock:
            if self.state is CircuitState.OPEN:
                if self._opened_at and (time.monotonic() - self._opened_at) >= self.cooldown_seconds:
                    self.state = CircuitState.HALF_OPEN
                    log.info("circuit_breaker_half_open")
                else:
                    raise CircuitOpenError("circuit breaker open")

            try:
                result = await coro_factory()
            except Exception as exc:
                self._consecutive_failures += 1
                if self._consecutive_failures >= self.failure_threshold:
                    self._open()
                raise

            # Success — close (or keep closed) and reset the failure count.
            self._consecutive_failures = 0
            if self.state is CircuitState.HALF_OPEN:
                self.state = CircuitState.CLOSED
                log.info("circuit_breaker_closed_after_test_call")
            return result

    def _open(self) -> None:
        self.state = CircuitState.OPEN
        self._opened_at = time.monotonic()
        self.alerted_once = False
        log.warning(
            "circuit_breaker_opened",
            consecutive_failures=self._consecutive_failures,
            threshold=self.failure_threshold,
        )

    @property
    def is_open(self) -> bool:
        return self.state is CircuitState.OPEN


class GroqModerationClient:
    """Thin async wrapper around the Groq SDK, behind a circuit breaker."""

    def __init__(self, api_key: str, model: str, timeout: float = 5.0, breaker: CircuitBreaker | None = None):
        self.model = model
        self.timeout = timeout
        self.breaker = breaker or CircuitBreaker()
        self.enabled = bool(api_key)
        self._client = None
        if self.enabled:
            try:
                from groq import AsyncGroq

                self._client = AsyncGroq(
                    api_key=api_key,
                    timeout=timeout,
                    max_retries=1,  # breaker handles retries; keep SDK retries minimal
                )
            except Exception as exc:  # pragma: no cover - defensive import guard
                log.error("groq_client_init_failed", error=str(exc))
                self.enabled = False
                self._client = None

    async def _classify(self, content: str) -> ModerationVerdict:
        if self._client is None:
            raise ClassificationError("groq client unavailable")
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": content[:2000]},
                ],
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            # Timeouts and transport errors count as breaker failures.
            log.warning("groq_classify_failed", error=str(exc))
            raise ClassificationError(f"groq call failed: {exc}") from exc

        raw = response.choices[0].message.content if response.choices else ""
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError) as exc:
            log.warning("groq_non_json_response", raw=raw[:300])
            raise ClassificationError("groq returned non-JSON") from exc

        return ModerationVerdict.from_payload(payload)

    async def classify(self, content: str) -> ModerationVerdict:
        """Classify ``content`` behind the circuit breaker."""
        return await self.breaker.execute(lambda: self._classify(content))

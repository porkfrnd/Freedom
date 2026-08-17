"""Lavalink wiring for wavelink 3.x (§6.1).

* ``connect_nodes`` registers the configured node with the bot and raises
  when the node did not actually connect.
* ``reconnect_with_backoff`` is the reconnect loop started when a node
  disconnects: it retries ``wavelink.Pool.reconnect()`` with exponential
  backoff (2s → 4s → … capped at 60s) until a node is connected again.
  The loop runs forever in the background.

Why ``retries=1``: wavelink's default is ``retries=None``, which makes
``Websocket.connect`` retry **forever** — so ``Pool.connect`` would never
return when no Lavalink server is reachable, which would hang the bot's
``setup_hook`` and prevent it from ever connecting to Discord. A bounded
retry count makes the initial connect return promptly so the caller can
fall back to the background reconnect loop (or boot without music).
"""

from __future__ import annotations

import asyncio

import wavelink

from utils.logging import get_logger

log = get_logger("services.lavalink")

_BACKOFF_START = 2.0
_BACKOFF_MAX = 60.0


def _node(uri: str, password: str) -> wavelink.Node:
    # retries=1 → at most 2 attempts, then give up instead of hanging the
    # event loop forever (see module docstring).
    return wavelink.Node(uri=uri, password=password, retries=1)


async def connect_nodes(bot, uri: str, password: str) -> wavelink.Node:
    """Connect the configured Lavalink node to the bot.

    Raises when the initial connection fails (including when the node
    simply isn't reachable) — the caller decides whether to retry or
    surface a clear error. Never hangs: the underlying websocket retry is
    bounded by ``retries=1``.
    """
    node = _node(uri, password)
    await wavelink.Pool.connect(client=bot, nodes=[node])
    if node.status is not wavelink.NodeStatus.CONNECTED:
        raise wavelink.NodeException(
            f"Lavalink node {node.identifier!r} did not connect"
        )
    return node


async def reconnect_with_backoff(uri: str, password: str, guild_id: int | None = None) -> None:
    """Background reconnect loop with exponential backoff.

    Exits once a node reports connected. Intended to be run as a detached
    task from the ``on_wavelink_node_disconnected`` listener.
    """
    backoff = _BACKOFF_START
    log.info("lavalink_reconnect_started", guild_id=guild_id)
    while True:
        try:
            await wavelink.Pool.reconnect()
        except Exception as exc:  # noqa: BLE001 - any reconnect failure retries
            log.warning(
                "lavalink_reconnect_attempt_failed",
                error=str(exc),
                retry_in=backoff,
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)
            continue

        if any(
            node.status is wavelink.NodeStatus.CONNECTED
            for node in wavelink.Pool.nodes.values()
        ):
            log.info("lavalink_reconnected", guild_id=guild_id)
            return
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, _BACKOFF_MAX)

"""Slack channel over Socket Mode (FR-2) — outbound websocket only, zero inbound ports.

- Each Slack thread maps to one agent session (``channel:thread_ts``), FR-2.2.
- Only allowlisted users/channels are heard; everything else is ignored (FR-2.5).
- Approvals are collected via emoji reactions on a posted approval message; a
  timeout counts as deny (FR-2.3).

``slack-bolt`` is imported lazily so the core installs without the ``[slack]`` extra.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from ...core.errors import ConfigError
from ...core.interfaces import InboundMessage, MessageHandler


class SlackChannel:
    name = "slack"

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._allowed_users = set(config.get("allowed_users", []))
        self._allowed_channels = set(config.get("allowed_channels", []))
        self._approve = config.get("approve_reaction", "white_check_mark")
        self._deny = config.get("deny_reaction", "x")
        self._handler: MessageHandler | None = None
        self._pending: dict[str, asyncio.Future[bool]] = {}
        self._bot_user_id: str | None = None
        self._app: Any = None
        self._socket: Any = None

        bot_token = os.environ.get(config.get("bot_token_env", "SLACK_BOT_TOKEN"))
        app_token = os.environ.get(config.get("app_token_env", "SLACK_APP_TOKEN"))
        if not bot_token or not app_token:
            raise ConfigError(
                "Slack requires bot and app tokens in the configured environment variables."
            )
        self._bot_token = bot_token
        self._app_token = app_token

    # ── lifecycle ─────────────────────────────────────────────────────────────
    async def start(self, on_message: MessageHandler) -> None:
        try:
            from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
            from slack_bolt.app.async_app import AsyncApp
        except ImportError as exc:  # pragma: no cover
            raise ConfigError(
                "slack-bolt not installed. Install with: pip install 'humon[slack]'"
            ) from exc

        self._handler = on_message
        self._app = AsyncApp(token=self._bot_token)
        auth = await self._app.client.auth_test()
        self._bot_user_id = auth.get("user_id")

        self._app.event("app_mention")(self._on_mention)
        self._app.event("message")(self._on_message_event)
        self._app.event("reaction_added")(self._on_reaction)

        self._socket = AsyncSocketModeHandler(self._app, self._app_token)
        await self._socket.connect_async()

    async def stop(self) -> None:
        if self._socket is not None:
            await self._socket.disconnect_async()

    # ── outbound ──────────────────────────────────────────────────────────────
    async def send(self, session_ref: str, text: str) -> str:
        channel, thread_ts = self._split(session_ref)
        resp = await self._app.client.chat_postMessage(
            channel=channel, thread_ts=thread_ts, text=text
        )
        return f"{channel}:{resp['ts']}"

    async def update(self, message_ref: str, text: str) -> None:
        channel, ts = self._split(message_ref)
        await self._app.client.chat_update(channel=channel, ts=ts, text=text)

    async def request_approval(self, session_ref: str, summary: str, timeout_s: int) -> bool:
        channel, thread_ts = self._split(session_ref)
        resp = await self._app.client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=(
                f":warning: *Approval required*\n{summary}\n"
                f"React :{self._approve}: to allow or :{self._deny}: to deny "
                f"(auto-deny in {timeout_s // 60} min)."
            ),
        )
        ts = resp["ts"]
        fut: asyncio.Future[bool] = asyncio.get_event_loop().create_future()
        self._pending[ts] = fut
        try:
            return await asyncio.wait_for(fut, timeout=timeout_s)
        except TimeoutError:
            return False
        finally:
            self._pending.pop(ts, None)

    # ── inbound handlers ──────────────────────────────────────────────────────
    async def _on_mention(self, event: dict[str, Any]) -> None:
        await self._route(event)

    async def _on_message_event(self, event: dict[str, Any]) -> None:
        # Only handle DMs here; channel mentions come through app_mention.
        if event.get("channel_type") == "im" and not event.get("bot_id"):
            await self._route(event)

    async def _route(self, event: dict[str, Any]) -> None:
        if event.get("bot_id") or event.get("user") == self._bot_user_id:
            return
        user = event.get("user", "")
        channel = event.get("channel", "")
        if self._allowed_users and user not in self._allowed_users:
            return
        if self._allowed_channels and channel not in self._allowed_channels:
            return
        thread_ts = event.get("thread_ts") or event.get("ts")
        session_ref = f"{channel}:{thread_ts}"
        text = _strip_mention(event.get("text", ""), self._bot_user_id)
        if self._handler is not None:
            await self._handler(InboundMessage(session_ref=session_ref, user=user, text=text))

    async def _on_reaction(self, event: dict[str, Any]) -> None:
        item = event.get("item", {})
        ts = item.get("ts")
        fut = self._pending.get(ts)
        if fut is None or fut.done():
            return
        user = event.get("user", "")
        if self._allowed_users and user not in self._allowed_users:
            return
        reaction = event.get("reaction")
        if reaction == self._approve:
            fut.set_result(True)
        elif reaction == self._deny:
            fut.set_result(False)

    # ── helpers ───────────────────────────────────────────────────────────────
    @staticmethod
    def _split(ref: str) -> tuple[str, str]:
        channel, _, ts = ref.partition(":")
        return channel, ts


def _strip_mention(text: str, bot_user_id: str | None) -> str:
    if bot_user_id:
        text = text.replace(f"<@{bot_user_id}>", "")
    return text.strip()

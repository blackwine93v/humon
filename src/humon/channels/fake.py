"""FakeChannel — drives the agent loop in tests without a network (T-2).

Records everything it sends/updates and serves scripted approval answers, so an
integration test can assert on the exact operator-facing behaviour.
"""

from __future__ import annotations

from ..core.interfaces import InboundMessage, MessageHandler


class FakeChannel:
    name = "fake"

    def __init__(self, approvals: list[bool] | None = None) -> None:
        self.sent: list[tuple[str, str, str]] = []  # (session_ref, text, message_ref)
        self.updated: list[tuple[str, str]] = []  # (message_ref, text)
        self.approval_prompts: list[str] = []
        self._approvals = list(approvals or [])
        self._handler: MessageHandler | None = None
        self.stopped = False

    async def start(self, on_message: MessageHandler) -> None:
        self._handler = on_message

    async def send(self, session_ref: str, text: str) -> str:
        ref = f"msg-{len(self.sent)}"
        self.sent.append((session_ref, text, ref))
        return ref

    async def update(self, message_ref: str, text: str) -> None:
        self.updated.append((message_ref, text))

    async def request_approval(self, session_ref: str, summary: str, timeout_s: int) -> bool:
        self.approval_prompts.append(summary)
        return self._approvals.pop(0) if self._approvals else False

    async def stop(self) -> None:
        self.stopped = True

    # ── test helpers ──────────────────────────────────────────────────────────
    async def inject(self, session_ref: str, user: str, text: str) -> None:
        assert self._handler is not None, "start() not called"
        await self._handler(InboundMessage(session_ref=session_ref, user=user, text=text))

    def last_sent_text(self) -> str:
        return self.sent[-1][1] if self.sent else ""

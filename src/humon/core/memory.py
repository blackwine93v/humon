"""Memory manager (FR-5): short-term compaction, long-term semantic notes, and
episodic outcome records.

Implements two roles:
- the agent's ``MemoryManager`` collaborator (hints, episodes, compaction), and
- the ``MemoryStore`` surface the ``memory`` tool calls.

Long-term recall uses embeddings + sqlite-vec when the provider supports
embeddings; otherwise it falls back to keyword search (FR-7.3 graceful degrade).
"""

from __future__ import annotations

from ..config import MemoryConfig
from ..logging import get_logger
from ..state.repositories import MemoryRepo, SessionRepo
from ..state.vectors import VectorIndex
from .interfaces import CAP_EMBEDDINGS, CompletionRequest, LLMProvider, Message

_log = get_logger("humon.memory")

# Rough token estimate: ~4 chars per token. Good enough for a compaction trigger.
_CHARS_PER_TOKEN = 4
# When compacting, keep this many most-recent messages verbatim.
_KEEP_RECENT = 12


class MemoryManager:
    def __init__(
        self,
        *,
        memory_repo: MemoryRepo,
        session_repo: SessionRepo,
        vectors: VectorIndex,
        provider: LLMProvider,
        config: MemoryConfig,
    ) -> None:
        self.notes = memory_repo
        self.sessions = session_repo
        self.vectors = vectors
        self.provider = provider
        self.config = config

    @property
    def _has_embeddings(self) -> bool:
        return CAP_EMBEDDINGS in getattr(self.provider, "capabilities", set())

    # ── MemoryStore surface (used by the memory tool) ─────────────────────────
    async def store(self, text: str, kind: str = "note", session_id: str | None = None) -> int:
        note_id = await self.notes.add(text, kind=kind, session_id=session_id)
        if self._has_embeddings and self.vectors.enabled:
            try:
                vec = (await self.provider.embed([text]))[0]
                await self.vectors.add(note_id, vec)
            except Exception as exc:  # embedding failure must not lose the note
                _log.debug("memory.embed_failed", error=str(exc))
        return note_id

    async def search(self, query: str, k: int = 5) -> list[str]:
        if self._has_embeddings and self.vectors.enabled:
            try:
                qvec = (await self.provider.embed([query]))[0]
                hits = await self.vectors.search(qvec, k)
                out: list[str] = []
                for note_id, _dist in hits:
                    row = await self.notes.get(note_id)
                    if row:
                        out.append(row["text"])
                if out:
                    return out
            except Exception as exc:  # on any embed error, fall back to keyword search
                _log.debug("memory.vector_search_failed", error=str(exc))
        rows = await self.notes.search_like(query, k)
        return [r["text"] for r in rows]

    async def list_notes(self) -> list[tuple[int, str, str]]:
        rows = await self.notes.all()
        return [(r["id"], r["kind"], r["text"]) for r in rows]

    async def forget(self, note_id: int) -> bool:
        return await self.notes.forget(note_id)

    # ── MemoryManager surface (used by the agent loop) ────────────────────────
    async def retrieve_hints(self, session_id: str, query: str) -> str:
        if not self.config.enabled:
            return ""
        hits = await self.search(query, self.config.long_term_top_k)
        if not hits:
            return ""
        return "\n".join(f"- {h}" for h in hits[: self.config.long_term_top_k])

    async def record_episode(
        self, session_id: str, task: str, tools_used: list[str], success: bool, note: str
    ) -> None:
        if not self.config.enabled:
            return
        summary = (
            f"Task: {task}\nTools: {', '.join(tools_used) or 'none'}\n"
            f"Outcome: {'success' if success else 'incomplete'}\nNote: {note}"
        )
        await self.store(
            summary,
            kind="episodic",
            session_id=session_id,
        )

    async def maybe_compact(self, session_id: str, provider: LLMProvider, model: str) -> None:
        if not self.config.enabled:
            return
        history = await self.sessions.history(session_id, limit=500)
        approx_tokens = sum(len(m.content) for m in history) // _CHARS_PER_TOKEN
        if approx_tokens < self.config.compaction_token_threshold:
            return
        older = history[:-_KEEP_RECENT] if len(history) > _KEEP_RECENT else []
        if not older:
            return
        transcript = "\n".join(f"{m.role}: {m.content}" for m in older if m.content)
        try:
            resp = await provider.complete(
                _summary_request(model, transcript),
            )
            summary = resp.text.strip()
        except Exception:
            return
        if summary:
            await self.sessions.set_summary(session_id, summary)


def _summary_request(model: str, transcript: str) -> CompletionRequest:
    return CompletionRequest(
        model=model,
        system=(
            "You compress conversation history. Summarize the following transcript "
            "into a concise set of durable facts, decisions, and open threads. "
            "Preserve names, paths, and numbers. Output plain text."
        ),
        messages=[Message(role="user", content=transcript)],
        max_tokens=512,
    )


# Keep the compaction constant importable for tests.
KEEP_RECENT = _KEEP_RECENT

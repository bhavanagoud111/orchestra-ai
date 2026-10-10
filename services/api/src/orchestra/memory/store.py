"""Long-term engineering memory.

Entries (repository summaries, investigated errors, approved decisions,
failure patterns, execution outcomes) are stored in the main database with an
embedding vector. Retrieval pre-filters by kind/repository in SQL, then ranks
by cosine similarity in Python.

The default embedder is a deterministic feature-hashing model (no external
service, no API key). It captures lexical overlap rather than deep semantics;
``Embedder`` is the extension point for a hosted embedding model. At the scale
of per-repository memories (hundreds of rows) in-process ranking is fast, so
pgvector is not required; see docs/architecture.md for when to add it.
"""

from __future__ import annotations

import hashlib
import itertools
import math
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Any, Protocol

from sqlalchemy import delete, func, select

from orchestra.db.models import MemoryEntry, utcnow
from orchestra.db.session import Database
from orchestra.retrieval import tokenize
from orchestra.security.redaction import contains_secret, redact, redact_text


class MemoryKind(StrEnum):
    REPOSITORY_SUMMARY = "repository_summary"
    ERROR_INVESTIGATION = "error_investigation"
    DECISION = "decision"
    FAILURE_PATTERN = "failure_pattern"
    OUTCOME = "outcome"


class Embedder(Protocol):
    dimensions: int

    def embed(self, text: str) -> list[float]: ...


class HashingEmbedder:
    """Signed feature hashing over unigrams and bigrams, L2-normalised."""

    def __init__(self, dimensions: int = 384) -> None:
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dimensions
        tokens = tokenize(text)
        features = tokens + [f"{a} {b}" for a, b in itertools.pairwise(tokens)]
        for feat in features:
            digest = hashlib.blake2b(feat.encode(), digest_size=8).digest()
            idx = int.from_bytes(digest[:4], "little") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [round(v / norm, 6) for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))


@dataclass
class MemoryHit:
    id: str
    kind: str
    content: str
    metadata: dict[str, Any]
    score: float
    created_at: str


class MemoryStore:
    MAX_CONTENT = 8_000

    def __init__(
        self,
        db: Database,
        embedder: Embedder | None = None,
        *,
        retention_days: int = 90,
        max_entries_per_repo: int = 500,
    ) -> None:
        self.db = db
        self.embedder = embedder or HashingEmbedder()
        self.retention_days = retention_days
        self.max_entries_per_repo = max_entries_per_repo

    async def remember(
        self,
        kind: MemoryKind,
        content: str,
        *,
        repository_id: str | None = None,
        workflow_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        ttl_days: int | None = None,
    ) -> str | None:
        """Store a memory. Content that looks like it contains credentials is redacted;
        returns None (stores nothing) if redaction would leave the entry meaningless."""
        if contains_secret(content):
            content = redact_text(content)
            if content.count("[REDACTED]") * 12 > len(content):
                return None
        content = content[: self.MAX_CONTENT]
        days = ttl_days if ttl_days is not None else self.retention_days
        entry = MemoryEntry(
            kind=kind,
            repository_id=repository_id,
            workflow_id=workflow_id,
            content=content,
            metadata_=redact(metadata or {}),
            embedding=self.embedder.embed(content),
            expires_at=utcnow() + timedelta(days=days) if days else None,
        )
        async with self.db.session() as s:
            s.add(entry)
        await self._enforce_cap(repository_id)
        return entry.id

    async def recall(
        self,
        query: str,
        *,
        kinds: list[MemoryKind] | None = None,
        repository_id: str | None = None,
        limit: int = 5,
        min_score: float = 0.05,
        scan_limit: int = 2_000,
    ) -> list[MemoryHit]:
        qvec = self.embedder.embed(query)
        stmt = select(MemoryEntry).where((MemoryEntry.expires_at.is_(None)) | (MemoryEntry.expires_at > utcnow()))
        if kinds:
            stmt = stmt.where(MemoryEntry.kind.in_([k.value for k in kinds]))
        if repository_id:
            stmt = stmt.where(MemoryEntry.repository_id == repository_id)
        stmt = stmt.order_by(MemoryEntry.created_at.desc()).limit(scan_limit)
        async with self.db.session() as s:
            rows = (await s.execute(stmt)).scalars().all()
        hits = [
            MemoryHit(r.id, r.kind, r.content, r.metadata_, cosine(qvec, r.embedding), r.created_at.isoformat())
            for r in rows
        ]
        hits = [h for h in hits if h.score >= min_score]
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:limit]

    async def purge_expired(self) -> int:
        async with self.db.session() as s:
            result = await s.execute(delete(MemoryEntry).where(MemoryEntry.expires_at <= utcnow()))
            return result.rowcount or 0  # type: ignore[attr-defined]

    async def _enforce_cap(self, repository_id: str | None) -> None:
        if repository_id is None:
            return
        async with self.db.session() as s:
            count = await s.scalar(
                select(func.count()).select_from(MemoryEntry).where(MemoryEntry.repository_id == repository_id)
            )
            excess = (count or 0) - self.max_entries_per_repo
            if excess > 0:
                oldest = (
                    (
                        await s.execute(
                            select(MemoryEntry.id)
                            .where(MemoryEntry.repository_id == repository_id)
                            .order_by(MemoryEntry.created_at.asc())
                            .limit(excess)
                        )
                    )
                    .scalars()
                    .all()
                )
                await s.execute(delete(MemoryEntry).where(MemoryEntry.id.in_(oldest)))

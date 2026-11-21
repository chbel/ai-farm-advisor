"""Semantic search over the knowledge base (pgvector, cosine similarity)."""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import any_, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import KnowledgeChunk, KnowledgeSource


class QueryEmbedder(Protocol):
    def embed_query(self, text: str) -> list[float]: ...


@dataclass(frozen=True)
class Passage:
    source_id: str
    title: str
    organization: str
    authors: str | None
    year: int
    url: str
    license: str
    commercial_use: bool
    location: str
    text: str
    score: float  # cosine similarity, 0-1


async def search(
    db: AsyncSession,
    embedder: QueryEmbedder,
    query: str,
    *,
    crop: str | None,
    top_k: int,
    min_score: float,
    commercial_only: bool,
) -> list[Passage]:
    vector = embedder.embed_query(query)
    distance = KnowledgeChunk.embedding.cosine_distance(vector)
    stmt = (
        select(KnowledgeChunk, KnowledgeSource, (1 - distance).label("score"))
        .join(KnowledgeSource, KnowledgeChunk.source_id == KnowledgeSource.id)
        .order_by(distance)
        .limit(top_k * 3)  # over-fetch, then apply the score and diversity rules
    )
    if crop:
        stmt = stmt.where(or_(literal(crop) == any_(KnowledgeSource.crops),
                              literal("general") == any_(KnowledgeSource.crops)))
    if commercial_only:
        stmt = stmt.where(KnowledgeSource.commercial_use.is_(True))

    passages: list[Passage] = []
    per_source: dict[str, int] = {}
    for chunk, source, score in (await db.execute(stmt)).all():
        if score < min_score:
            break  # results are sorted by similarity
        # At most 3 passages per source so one long document cannot crowd out the rest.
        if per_source.get(source.id, 0) >= 3:
            continue
        per_source[source.id] = per_source.get(source.id, 0) + 1
        passages.append(Passage(
            source_id=source.id, title=source.title, organization=source.organization,
            authors=source.authors, year=source.year, url=source.url, license=source.license,
            commercial_use=source.commercial_use, location=chunk.location, text=chunk.text,
            score=round(float(score), 4),
        ))
        if len(passages) == top_k:
            break
    return passages

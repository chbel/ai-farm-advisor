"""pgvector retrieval against a real PostgreSQL (database `advisor_db_test`).

Skipped when PostgreSQL is not reachable (e.g. during `docker build`). Run with:
    docker run --rm --network ai-farm-advisor_default --env-file .env \
        -e POSTGRES_HOST=postgres -e POSTGRES_PORT=5432 ai-farm-advisor-test \
        python -m pytest -q tests/test_retriever_db.py
"""

import numpy as np
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import config
from app.knowledge.retriever import search
from app.models import KnowledgeChunk, KnowledgeSource

TEST_DB = "advisor_db_test"


def _url() -> str:
    url = config.settings.database_url
    url = make_url(url) if isinstance(url, str) else url
    return url.set(database=TEST_DB).render_as_string(hide_password=False)


@pytest.fixture(scope="module")
def database_url():
    url = make_url(_url())
    try:
        with psycopg.connect(host=url.host, port=url.port, user=url.username, password=url.password,
                             dbname="postgres", connect_timeout=3, autocommit=True) as conn:
            if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,)).fetchone():
                conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TEST_DB)))
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL not reachable")

    previous = config.settings
    config.settings = config.Settings(DATABASE_URL=_url())  # alembic env.py reads this
    try:
        cfg = Config("alembic.ini")
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")
    finally:
        config.settings = previous
    return _url()


def unit(*weights: float) -> list[float]:
    v = np.zeros(384, dtype=np.float32)
    v[: len(weights)] = weights
    return (v / np.linalg.norm(v)).tolist()


class AxisEmbedder:
    def embed_query(self, text: str) -> list[float]:
        return unit(1.0)


def source(id_, crops, commercial) -> KnowledgeSource:
    return KnowledgeSource(id=id_, title=id_.title(), organization="Org", year=2024, url="https://x",
                           license="CC BY 4.0" if commercial else "CC BY-NC", commercial_use=commercial,
                           language="en", crops=crops, topics=["t"], content_sha256="0" * 64,
                           chunk_count=0)


@pytest.fixture(scope="module")
async def session(database_url):
    engine = create_async_engine(database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add_all([source("olive-doc", ["olive"], True), source("tomato-doc", ["tomato"], False),
                    source("general-doc", ["general"], True)])
        await db.flush()
        rows = [("olive-doc", unit(1.0, 0.1)), ("olive-doc", unit(1.0, 0.2)), ("olive-doc", unit(1.0, 0.3)),
                ("olive-doc", unit(1.0, 0.4)), ("tomato-doc", unit(1.0, 0.05)),
                ("general-doc", unit(1.0, 0.5)), ("general-doc", unit(0.0, 1.0))]  # last one is orthogonal
        db.add_all(KnowledgeChunk(source_id=s, chunk_index=i, location=f"p. {i}", text=f"chunk {i}", embedding=v)
                   for i, (s, v) in enumerate(rows))
        await db.commit()
        yield db
    await engine.dispose()


async def run(db, **kwargs):
    options = dict(crop=None, top_k=10, min_score=0.5, commercial_only=False)
    options.update(kwargs)
    return await search(db, AxisEmbedder(), "q", **options)


async def test_results_are_ordered_by_similarity_and_thresholded(session):
    results = await run(session)
    scores = [p.score for p in results]
    assert scores == sorted(scores, reverse=True)
    assert results[0].source_id == "tomato-doc"  # closest vector
    assert all(p.score >= 0.5 for p in results)  # the orthogonal chunk is excluded
    assert all(p.location.startswith("p. ") for p in results)


async def test_at_most_three_passages_per_source(session):
    results = await run(session)
    assert sum(p.source_id == "olive-doc" for p in results) == 3


async def test_crop_filter_keeps_general_sources(session):
    ids = {p.source_id for p in await run(session, crop="olive")}
    assert ids == {"olive-doc", "general-doc"}


async def test_commercial_only_excludes_restricted_sources(session):
    ids = {p.source_id for p in await run(session, commercial_only=True)}
    assert "tomato-doc" not in ids and ids


async def test_top_k(session):
    assert len(await run(session, top_k=2)) == 2

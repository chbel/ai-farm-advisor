"""Knowledge base ingestion.

    python -m app.knowledge.ingest                 # all sources in sources.yaml
    python -m app.knowledge.ingest --source ID     # one source
    python -m app.knowledge.ingest --force         # re-embed even if unchanged
    docker compose exec ai-advisor-service python -m app.knowledge.ingest

For each source: download (cached in DATA_DIR/documents), verify the license
where the API exposes it, extract citable sections, chunk, embed, and replace
the source's chunks in one transaction. Unchanged documents are skipped.
"""

# Must run before any event loop is created (Windows + async Psycopg).
import app.core.platform  # noqa: F401

import argparse
import asyncio
import hashlib
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import yaml
from sqlalchemy import delete

from app.core.config import settings
from app.core.database import AsyncSessionLocal, engine
from app.knowledge.chunking import make_chunks
from app.knowledge.embeddings import E5Embedder, download_model
from app.knowledge.parsing import jats_sections, pdf_sections
from app.models import KnowledgeChunk, KnowledgeSource

logger = logging.getLogger("advisor.ingest")

MANIFEST = Path(__file__).with_name("sources.yaml")
EUROPE_PMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
USER_AGENT = "AI-Farm-Advisor-knowledge-ingestion/1.0"
ALLOWED_CROPS = {"general", "olive", "tomato"}


@dataclass
class SourceSpec:
    id: str
    title: str
    organization: str
    year: int
    language: str
    crops: list[str]
    topics: list[str]
    license: str
    commercial_use: bool
    fetch: str
    url: str
    pmcid: str | None = None
    authors: str | None = None
    extra: dict = field(default_factory=dict)


def load_manifest(path: Path = MANIFEST) -> list[SourceSpec]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    specs, seen = [], set()
    known = set(SourceSpec.__dataclass_fields__) - {"extra"}
    for raw in data["sources"]:
        spec = SourceSpec(**{k: v for k, v in raw.items() if k in known},
                          extra={k: v for k, v in raw.items() if k not in known})
        if spec.id in seen:
            raise ValueError(f"Duplicate source id: {spec.id}")
        if spec.fetch not in ("pdf", "europepmc"):
            raise ValueError(f"{spec.id}: unknown fetch type {spec.fetch}")
        if spec.fetch == "europepmc" and not spec.pmcid:
            raise ValueError(f"{spec.id}: europepmc sources need a pmcid")
        if not set(spec.crops) <= ALLOWED_CROPS:
            raise ValueError(f"{spec.id}: crops must be among {sorted(ALLOWED_CROPS)}")
        seen.add(spec.id)
        specs.append(spec)
    return specs


def _cached_get(client: httpx.Client, url: str, cache: Path) -> bytes:
    if cache.exists() and cache.stat().st_size > 0:
        return cache.read_bytes()
    response = client.get(url, timeout=300)
    response.raise_for_status()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(response.content)
    return response.content


def europepmc_metadata(client: httpx.Client, pmcid: str) -> dict:
    response = client.get(f"{EUROPE_PMC}/search",
                          params={"query": f"PMCID:{pmcid}", "resultType": "core", "format": "json"},
                          timeout=60)
    response.raise_for_status()
    hits = response.json()["resultList"]["result"]
    if not hits:
        raise ValueError(f"{pmcid} not found in Europe PMC")
    return hits[0]


def fetch_sections(client: httpx.Client, spec: SourceSpec, cache_dir: Path) -> tuple[bytes, list, dict]:
    """Return (raw bytes, sections, metadata overrides)."""
    if spec.fetch == "pdf":
        data = _cached_get(client, spec.url, cache_dir / f"{spec.id}.pdf")
        if not data.startswith(b"%PDF"):
            raise ValueError(f"{spec.id}: download is not a PDF")
        return data, pdf_sections(data), {}

    meta = europepmc_metadata(client, spec.pmcid)
    license_ = (meta.get("license") or "").lower()
    if license_ != "cc by":
        # Never ingest a source whose license changed from what we recorded.
        raise ValueError(f"{spec.id}: Europe PMC license is now '{license_}', expected 'cc by'")
    data = _cached_get(client, f"{EUROPE_PMC}/{spec.pmcid}/fullTextXML", cache_dir / f"{spec.id}.xml")
    overrides = {"authors": meta.get("authorString"), "year": int(meta.get("pubYear") or spec.year)}
    return data, jats_sections(data), overrides


async def ingest_source(spec: SourceSpec, embedder: E5Embedder, client: httpx.Client,
                        cache_dir: Path, force: bool) -> str:
    data, sections, overrides = await asyncio.to_thread(fetch_sections, client, spec, cache_dir)
    digest = hashlib.sha256(data).hexdigest()

    async with AsyncSessionLocal() as db:
        existing = await db.get(KnowledgeSource, spec.id)
        if existing is not None and existing.content_sha256 == digest and not force:
            return f"{spec.id}: unchanged ({existing.chunk_count} chunks)"

    if not sections:
        raise ValueError(f"{spec.id}: no extractable text")
    chunks = make_chunks(sections, embedder.token_ids, settings.CHUNK_TOKENS, settings.CHUNK_OVERLAP_TOKENS)
    vectors = await asyncio.to_thread(embedder.embed_passages, [c.text for c in chunks])

    async with AsyncSessionLocal() as db:
        await db.execute(delete(KnowledgeSource).where(KnowledgeSource.id == spec.id))
        db.add(KnowledgeSource(
            id=spec.id, title=spec.title, organization=spec.organization,
            authors=overrides.get("authors") or spec.authors, year=overrides.get("year", spec.year),
            url=spec.url, license=spec.license, commercial_use=spec.commercial_use,
            language=spec.language, crops=spec.crops, topics=spec.topics,
            content_sha256=digest, chunk_count=len(chunks),
        ))
        await db.flush()
        db.add_all(
            KnowledgeChunk(source_id=spec.id, chunk_index=i, location=c.location, text=c.text, embedding=v)
            for i, (c, v) in enumerate(zip(chunks, vectors))
        )
        await db.commit()
    return f"{spec.id}: {len(sections)} sections -> {len(chunks)} chunks"


def load_embedder() -> E5Embedder:
    model_dir = download_model(settings.EMBEDDING_REPO, settings.EMBEDDING_REVISION,
                               settings.DATA_DIR / "models" / "multilingual-e5-small")
    return E5Embedder(model_dir)


async def main(only: str | None, force: bool) -> int:
    specs = load_manifest()
    if only:
        specs = [s for s in specs if s.id == only]
        if not specs:
            print(f"Unknown source id: {only}", file=sys.stderr)
            return 2
    embedder = await asyncio.to_thread(load_embedder)
    cache_dir = settings.DATA_DIR / "documents"
    failures = 0
    with httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
        for spec in specs:
            try:
                print(await ingest_source(spec, embedder, client, cache_dir, force), flush=True)
            except Exception as exc:  # report and continue with the other sources
                failures += 1
                print(f"{spec.id}: FAILED ({type(exc).__name__}: {exc})", file=sys.stderr, flush=True)
    await engine.dispose()
    return 1 if failures else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(prog="python -m app.knowledge.ingest")
    parser.add_argument("--source", help="Ingest only this source id")
    parser.add_argument("--force", action="store_true", help="Re-embed unchanged documents")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.source, args.force)))

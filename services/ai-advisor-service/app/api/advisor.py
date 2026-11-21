import json
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select

from app.agents.graph import run_config
from app.core.config import settings
from app.core.security import CurrentPrincipal
from app.knowledge import retriever
from app.models import KnowledgeSource

router = APIRouter(prefix="/advisor", tags=["AI Advisor"])

DISCLAIMER = (
    "AI Farm Advisor gives decision support, not professional certification. Answers are "
    "based on the cited documents and your farm data; check them in the field and with "
    "your local agricultural extension service (CRDA) before acting, and always follow "
    "product labels and local regulations."
)


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    language: Literal["fr", "en", "aeb"] = "fr"
    crop: Literal["olive", "tomato"] | None = None
    farm_id: int | None = Field(default=None, gt=0)
    field_id: int | None = Field(default=None, gt=0)
    disease_analysis: dict | None = Field(
        default=None, description="Result of POST /api/v1/disease/analyze, to discuss a photo."
    )
    history: list[Turn] = Field(default_factory=list, max_length=10)

    @field_validator("question")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _consistent(self):
        if self.field_id is not None and self.farm_id is None:
            raise ValueError("field_id requires farm_id.")
        if self.disease_analysis is not None and len(json.dumps(self.disease_analysis)) > 20_000:
            raise ValueError("disease_analysis is too large.")
        return self


class Citation(BaseModel):
    id: str = Field(description="Tag used in the answer, e.g. S1.")
    source_id: str
    title: str
    organization: str
    authors: str | None
    year: int
    url: str
    license: str
    commercial_use: bool
    location: str = Field(description="Page or section of the source.")
    excerpt: str = Field(description="The passage the answer relies on.")
    relevance: float = Field(description="Cosine similarity between question and passage, 0-1.")


class AskResponse(BaseModel):
    answer: str
    language: str
    confidence: Literal["low", "medium", "high"]
    citations: list[Citation]
    data_used: list[str] = Field(description="Platform data used: farm, weather, irrigation, disease.")
    specialists: list[str]
    planner: str
    llm: dict | None = Field(description="Provider and open-weights model that wrote the answer.")
    limitations: list[str]
    disclaimer: str = DISCLAIMER


def _citation(number: int, p: retriever.Passage) -> Citation:
    return Citation(
        id=f"S{number}", source_id=p.source_id, title=p.title, organization=p.organization,
        authors=p.authors, year=p.year, url=p.url, license=p.license, commercial_use=p.commercial_use,
        location=p.location, excerpt=p.text, relevance=p.score,
    )


@router.post("/ask", response_model=AskResponse, responses={401: {"description": "Missing or invalid token."}})
async def ask(data: AskRequest, request: Request, _: CurrentPrincipal) -> AskResponse:
    """Answer a farmer's question using farm data, weather, irrigation, a photo
    analysis and the knowledge base, with citations."""

    app_state = request.app.state
    state = await app_state.graph.ainvoke(
        {
            "question": data.question, "language": data.language, "crop": data.crop,
            "farm_id": data.farm_id, "field_id": data.field_id,
            "disease_analysis": data.disease_analysis,
            "history": [t.model_dump() for t in data.history],
        },
        config=run_config(
            http=app_state.http_client, auth=request.headers["authorization"],
            sessionmaker=app_state.sessionmaker, embedder=app_state.embedder, llm=app_state.llm,
        ),
    )
    passages = state.get("passages", [])
    cited = state.get("cited", [])
    return AskResponse(
        answer=state["answer"],
        language=data.language,
        confidence=state["confidence"],
        citations=[_citation(n, passages[n - 1]) for n in cited],
        data_used=[k for k in ("farm", "weather", "irrigation", "disease") if k in state.get("context", {})],
        specialists=state.get("agents", []),
        planner=state.get("planner", "keywords"),
        llm=state.get("llm"),
        limitations=state.get("limitations", []),
    )


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    crop: Literal["olive", "tomato"] | None = None
    top_k: int = Field(default=5, ge=1, le=20)


@router.post("/search", response_model=list[Citation])
async def search(data: SearchRequest, request: Request, _: CurrentPrincipal) -> list[Citation]:
    """Search the knowledge base directly (shows the evidence behind answers)."""
    if request.app.state.embedder is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Knowledge base search model not loaded.")
    async with request.app.state.sessionmaker() as db:
        passages = await retriever.search(
            db, request.app.state.embedder, data.query, crop=data.crop, top_k=data.top_k,
            min_score=0.0, commercial_only=settings.KNOWLEDGE_COMMERCIAL_ONLY,
        )
    return [_citation(i, p) for i, p in enumerate(passages, start=1)]


@router.get("/sources")
async def sources(request: Request, _: CurrentPrincipal) -> dict:
    """The documents in the knowledge base, with their licenses."""
    async with request.app.state.sessionmaker() as db:
        rows = (await db.scalars(select(KnowledgeSource).order_by(KnowledgeSource.id))).all()
    return {
        "commercial_only": settings.KNOWLEDGE_COMMERCIAL_ONLY,
        "sources": [
            {k: getattr(s, k) for k in ("id", "title", "organization", "authors", "year", "url", "license",
                                         "commercial_use", "language", "crops", "topics", "chunk_count",
                                         "ingested_at")}
            for s in rows
        ],
    }

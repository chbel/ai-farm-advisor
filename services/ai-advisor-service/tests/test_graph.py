"""LangGraph workflow: planning, specialists, grounded composition."""

import json

import httpx
import pytest

from app.agents import graph as advisor_graph
from app.agents.graph import build_graph, cap_confidence, clean_citations, keyword_plan, route, run_config
from tests.conftest import AUTH, FakeEmbedder, FakeLLM, Upstreams, fake_sessionmaker, passage


@pytest.mark.parametrize(
    "question,agents,crop",
    [
        ("Combien dois-je arroser mes oliviers cette semaine ?", {"irrigation"}, "olive"),
        ("Will there be frost tomorrow on my tomatoes?", {"weather"}, "tomato"),
        ("Taches noires sur les feuilles de tomate, quelle maladie ?", {"disease"}, "tomato"),
        ("قداش نسقي الزيتون؟", {"irrigation"}, "olive"),
        ("el zitoun mte3i fih mard?", {"disease"}, "olive"),
    ],
)
def test_keyword_plan_is_multilingual(question, agents, crop):
    plan = keyword_plan(question)
    assert agents <= set(plan["agents"]) and "knowledge" in plan["agents"]
    assert plan["crop"] == crop


def test_route_needs_a_farm_for_weather_and_irrigation():
    state = {"agents": ["weather", "irrigation", "knowledge"], "farm_id": None, "disease_analysis": None}
    assert route(state) == ["knowledge"]
    state["farm_id"] = 5
    assert route(state) == ["knowledge", "farm", "weather", "irrigation"]


def test_clean_citations_removes_unbacked_tags():
    text, cited, removed = clean_citations(
        "Water deeply [S2]. Frost risk [WEATHER]. Made-up [S9]. Also [IRRIGATION].", 3, {"WEATHER"}
    )
    assert cited == [2] and removed == 2
    assert "[S9]" not in text and "[IRRIGATION]" not in text and "[S2]" in text


def test_confidence_is_capped_by_evidence():
    assert cap_confidence("high", [], [], {}) == "low"
    assert cap_confidence("high", [], [], {"weather": {}}) == "medium"
    assert cap_confidence("high", [passage()], [1], {}) == "high"
    assert cap_confidence("high", [passage()], [1], {"disease": {"outcome": "uncertain"}}) == "low"
    assert cap_confidence("bogus", [passage()], [1], {}) == "low"


@pytest.fixture
def upstreams():
    return Upstreams()


@pytest.fixture
def run(upstreams, monkeypatch):
    found = [passage(1), passage(2)]

    async def fake_search(db, embedder, query, **kwargs):
        fake_search.calls.append((query, kwargs))
        return found

    fake_search.calls = []
    monkeypatch.setattr(advisor_graph.retriever, "search", fake_search)

    async def _run(question, llm, **inputs):
        http = httpx.AsyncClient(transport=httpx.MockTransport(upstreams))
        state = await build_graph().ainvoke(
            {"question": question, "language": "fr", "crop": None, "farm_id": None, "field_id": None,
             "disease_analysis": None, "history": [], **inputs},
            config=run_config(http=http, auth=AUTH["Authorization"], sessionmaker=fake_sessionmaker,
                              embedder=FakeEmbedder(), llm=llm),
        )
        await http.aclose()
        return state

    _run.search = fake_search
    return _run


PLAN = json.dumps({"agents": ["irrigation", "weather"], "crop": "olive",
                   "search_query": "olive deficit irrigation water requirement"})
ANSWER = json.dumps({
    "answer": "Selon la FAO, l'irrigation déficitaire est adaptée [S2]. Votre besoin brut est "
              "de 383 m3 cette semaine [IRRIGATION]. Gel prévu demain [WEATHER].",
    "confidence": "high", "limitations": ["Based on a 7-day forecast."],
})


async def test_full_run_with_farm_weather_irrigation_and_citations(run, upstreams):
    llm = FakeLLM([PLAN, ANSWER])

    state = await run("Combien arroser mes oliviers ?", llm, farm_id=5)

    assert state["planner"] == "llm"
    assert set(state["context"]) == {"farm", "weather", "irrigation"}
    assert state["cited"] == [2]
    assert state["confidence"] == "high"
    assert state["llm"] == {"provider": "fake", "model": "fake-model"}
    assert "Based on a 7-day forecast." in state["limitations"]
    # Both the planner's English query and the farmer's words are searched, by crop.
    queries = [q for q, _ in run.search.calls]
    assert queries == ["olive deficit irrigation water requirement", "Combien arroser mes oliviers ?"]
    assert all(kwargs["crop"] == "olive" for _, kwargs in run.search.calls)
    # The composer saw the tagged context and the passages.
    composer_prompt = llm.calls[1][-1]["content"]
    assert "[IRRIGATION]" in composer_prompt and "[S2] Document 2" in composer_prompt
    # Every upstream call carried the user's own token.
    assert upstreams.requests and all(
        r.headers["authorization"] == AUTH["Authorization"] for r in upstreams.requests
    )


async def test_knowledge_merges_queries_keeping_best_score(run, monkeypatch):
    from tests.conftest import passage as make

    async def search(db, embedder, query, **kwargs):
        if query.startswith("olive"):
            return [make(1, score=0.88), make(2, score=0.86)]
        return [make(1, score=0.91), make(3, score=0.87)]

    monkeypatch.setattr(advisor_graph.retriever, "search", search)
    state = await run("Combien arroser mes oliviers ?", FakeLLM([PLAN, ANSWER]))

    assert [(p.source_id, p.score) for p in state["passages"]] == [
        ("src-1", 0.91), ("src-3", 0.87), ("src-2", 0.86)
    ]


async def test_without_llm_returns_evidence_only(run):
    state = await run("Quand traiter contre l'oeil de paon de l'olivier ?", FakeLLM(configured=False))

    assert state["planner"] == "keywords"
    assert state["llm"] is None
    assert state["confidence"] == "low"
    assert "temporarily unavailable" in state["answer"]
    assert "[S1]" in state["answer"] and state["cited"] == [1, 2]


async def test_llm_failure_during_compose_falls_back(run):
    state = await run("Combien arroser ?", FakeLLM([PLAN]), farm_id=5)  # no reply left for compose

    assert state["llm"] is None
    assert any("unavailable" in item for item in state["limitations"])
    assert "[IRRIGATION]" in state["answer"]


async def test_unreachable_services_are_reported_not_fatal(run, upstreams):
    upstreams.down.update({"weather.test", "irrigation.test"})

    state = await run("Combien arroser mes oliviers ?", FakeLLM([PLAN, ANSWER]), farm_id=5)

    assert set(state["context"]) == {"farm"}
    assert any("Weather: weather-service is unavailable" in item for item in state["limitations"])
    # [IRRIGATION] and [WEATHER] tags in the answer are not backed by data any more.
    assert "[IRRIGATION]" not in state["answer"] and "[WEATHER]" not in state["answer"]


async def test_foreign_farm_is_not_accessible(run):
    state = await run("Combien arroser ?", FakeLLM([PLAN, ANSWER]), farm_id=999)

    assert "farm" not in state["context"]
    assert any("not found or not accessible" in item for item in state["limitations"])


async def test_uncertain_photo_analysis_caps_confidence(run):
    analysis = {"crop": "tomato", "outcome": "uncertain",
                "top_prediction": {"label": "tomato_early_blight", "name_en": "Early blight",
                                   "probability": 0.41},
                "candidates": [], "model": {"field_test_accuracy": 0.6}}

    state = await run("Que faire ?", FakeLLM([PLAN, ANSWER]), disease_analysis=analysis)

    assert state["context"]["disease"]["outcome"] == "uncertain"
    assert "disease" in state["agents"]
    assert state["confidence"] == "low"

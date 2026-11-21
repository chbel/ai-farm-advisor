"""LangGraph orchestration of the AI Advisor.

    plan ──┬─> farm ────────┐
           ├─> weather ─────┤
           ├─> irrigation ──┼─> compose ─> END
           ├─> disease ─────┤
           └─> knowledge ───┘

`plan` picks the specialists (LLM router, keyword fallback); the selected ones
run in parallel and write into `context`; `compose` writes the grounded answer.
Shared resources (HTTP client, DB sessions, embedder, LLM, the user's token) are
passed through the run config, never stored in state.
"""

import logging
import operator
import re
from typing import Annotated, Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from app.agents import tools
from app.agents.prompts import COMPOSER_SYSTEM, LANGUAGE_NAMES, PLANNER_SYSTEM, format_context
from app.core.config import settings
from app.knowledge import retriever
from app.llm.client import LLMUnavailable, parse_json

logger = logging.getLogger("advisor.graph")

AGENTS = ("weather", "irrigation", "disease", "knowledge")
DATA_TAGS = {"FARM", "WEATHER", "IRRIGATION", "DISEASE"}


def merge_dicts(left: dict, right: dict) -> dict:
    return {**left, **right}


class AdvisorState(TypedDict, total=False):
    # input
    question: str
    language: str
    crop: str | None
    farm_id: int | None
    field_id: int | None
    disease_analysis: dict | None
    history: list[dict]
    # planning
    agents: list[str]
    search_query: str
    planner: str  # "llm" or "keywords"
    # gathered by specialists (parallel writes are merged by the reducers)
    context: Annotated[dict, merge_dicts]
    passages: list
    errors: Annotated[list[str], operator.add]
    # output
    answer: str
    confidence: str
    limitations: list[str]
    llm: dict | None
    cited: list[int]


# ------------------------------------------------------------------ planning

_KEYWORDS = {
    "weather": r"m[ée]t[ée]o|pluie|pleuv|gel[ée]?e?|givre|chaleur|canicule|vent|temp[ée]rature|"
               r"weather|rain|frost|heat|wind|forecast|storm|طقس|مطر|برد|ريح|حرارة|"
               r"\bchta\b|\bberd\b|\bskhana\b|\bri7\b",
    "irrigation": r"irrig|arros|\beau\b|goutte|water|watering|drip|سقي|نسقي|ماء|\bma\b|"
                  r"\bsgi\b|\bnesgi\b|\bsa9i\b|\bn?sa9i\b",
    "disease": r"maladi|malade|tache|champignon|insecte|ravageur|mouche|pourri|jaun|feuille|"
               r"traitement|disease|pest|spot|blight|mildew|fungus|insect|fly|rot|leaf|yellow|"
               r"مرض|حشر|ذباب|بقع|ورق|\bmard\b|\bmridh|\bdoud\b|\bwra9\b",
}
_CROPS = {
    "olive": r"oliv|زيت|zitoun|zitouna|\bzit\b",
    "tomato": r"tomat|طماطم|tmatem|tomatich|\bdomates\b",
}


def keyword_plan(question: str) -> dict:
    text = question.lower()
    agents = [name for name, pattern in _KEYWORDS.items() if re.search(pattern, text)]
    crop = next((c for c, pattern in _CROPS.items() if re.search(pattern, text)), None)
    return {"agents": agents + ["knowledge"], "crop": crop, "search_query": question}


def _resources(config: RunnableConfig) -> dict:
    return config["configurable"]


async def plan(state: AdvisorState, config: RunnableConfig) -> dict:
    res = _resources(config)
    planned, planner = None, "keywords"
    if res["llm"].configured:
        try:
            reply = await res["llm"].chat(
                [{"role": "system", "content": PLANNER_SYSTEM},
                 {"role": "user", "content": state["question"]}],
                json_mode=True, max_tokens=300,
            )
            planned, planner = parse_json(reply.content), "llm"
        except (LLMUnavailable, ValueError) as exc:
            logger.info("Planner fell back to keywords: %s", exc)
    if planned is None:
        planned = keyword_plan(state["question"])

    agents = [a for a in planned.get("agents", []) if a in AGENTS]
    agents = list(dict.fromkeys(agents + ["knowledge"]))
    if state.get("disease_analysis") and "disease" not in agents:
        agents.append("disease")
    crop = state.get("crop") or (planned.get("crop") if planned.get("crop") in ("olive", "tomato") else None)
    query = str(planned.get("search_query") or state["question"])[:500]
    return {"agents": agents, "crop": crop, "search_query": query, "planner": planner,
            "context": {}, "errors": []}


def route(state: AdvisorState) -> list[str]:
    """Fan out to the specialists that can run with the inputs available."""
    nodes = ["knowledge"]
    has_farm = state.get("farm_id") is not None
    if has_farm:
        nodes.append("farm")
    if "weather" in state["agents"] and has_farm:
        nodes.append("weather")
    if "irrigation" in state["agents"] and has_farm:
        nodes.append("irrigation")
    if state.get("disease_analysis"):
        nodes.append("disease")
    return nodes


# --------------------------------------------------------------- specialists


async def farm_node(state: AdvisorState, config: RunnableConfig) -> dict:
    res = _resources(config)
    try:
        return {"context": {"farm": await tools.farm_context(res["http"], res["auth"], state["farm_id"])}}
    except tools.ToolError as exc:
        return {"errors": [f"Farm data: {exc}"]}


async def weather_node(state: AdvisorState, config: RunnableConfig) -> dict:
    res = _resources(config)
    try:
        return {"context": {"weather": await tools.weather_context(res["http"], res["auth"], state["farm_id"])}}
    except tools.ToolError as exc:
        return {"errors": [f"Weather: {exc}"]}


async def irrigation_node(state: AdvisorState, config: RunnableConfig) -> dict:
    res = _resources(config)
    try:
        data = await tools.irrigation_context(res["http"], res["auth"], state["farm_id"], state.get("field_id"))
        return {"context": {"irrigation": data}}
    except tools.ToolError as exc:
        return {"errors": [f"Irrigation: {exc}"]}


async def disease_node(state: AdvisorState, config: RunnableConfig) -> dict:
    return {"context": {"disease": tools.disease_context(state["disease_analysis"])}}


async def knowledge_node(state: AdvisorState, config: RunnableConfig) -> dict:
    res = _resources(config)
    if res.get("embedder") is None:
        return {"passages": [], "errors": ["Knowledge base: search model not loaded"]}
    # Search with the planner's (English) query and the farmer's own words: matching
    # across languages is weaker than within one language, so both help.
    queries = list(dict.fromkeys([state["search_query"], state["question"]]))
    found: dict[tuple, retriever.Passage] = {}
    try:
        async with res["sessionmaker"]() as db:
            for query in queries:
                for p in await retriever.search(
                    db, res["embedder"], query, crop=state.get("crop"),
                    top_k=settings.RETRIEVAL_TOP_K, min_score=settings.RETRIEVAL_MIN_SCORE,
                    commercial_only=settings.KNOWLEDGE_COMMERCIAL_ONLY,
                ):
                    key = (p.source_id, p.location, p.text)
                    if key not in found or p.score > found[key].score:
                        found[key] = p
    except Exception as exc:  # the knowledge base must never break the whole answer
        logger.exception("Knowledge retrieval failed")
        return {"passages": [], "errors": [f"Knowledge base unavailable ({type(exc).__name__})"]}
    passages = sorted(found.values(), key=lambda p: p.score, reverse=True)[: settings.RETRIEVAL_TOP_K]
    return {"passages": passages}


# ------------------------------------------------------------------- compose

_TAG = re.compile(r"\[(S\d+|FARM|WEATHER|IRRIGATION|DISEASE)\]")
_CONFIDENCE_ORDER = ["low", "medium", "high"]


def clean_citations(answer: str, n_passages: int, available_data: set[str]) -> tuple[str, list[int], int]:
    """Drop citation tags pointing to nothing; return (answer, cited passage numbers, removed)."""
    cited, removed = [], 0

    def check(match: re.Match) -> str:
        nonlocal removed
        tag = match.group(1)
        if tag.startswith("S"):
            number = int(tag[1:])
            if 1 <= number <= n_passages:
                if number not in cited:
                    cited.append(number)
                return match.group(0)
        elif tag in available_data:
            return match.group(0)
        removed += 1
        return ""

    return _TAG.sub(check, answer), sorted(cited), removed


def cap_confidence(llm_value: str, passages: list, cited: list[int], context: dict) -> str:
    value = llm_value if llm_value in _CONFIDENCE_ORDER else "low"
    cap = "high"
    if not cited:
        cap = "medium" if context else "low"
    if (context.get("disease") or {}).get("outcome") == "uncertain":
        cap = "low"
    return min(value, cap, key=_CONFIDENCE_ORDER.index)


def fallback_answer(state: AdvisorState) -> str:
    """No LLM available: present the gathered evidence without generated text."""
    lines = ["**AI text generation is temporarily unavailable.** Here is the relevant information found:"]
    for i, p in enumerate(state.get("passages", [])[:4], start=1):
        excerpt = p.text[:400].rsplit(" ", 1)[0]
        lines.append(f"- [S{i}] *{p.title}* ({p.organization}, {p.year}, {p.location}): {excerpt}…")
    ctx = state.get("context", {})
    for alert in (ctx.get("weather") or {}).get("alerts", [])[:3]:
        lines.append(f"- [WEATHER] {alert['date']}: {alert['message']}")
    for item in (ctx.get("irrigation") or {}).get("fields", [])[:3]:
        if "schedule" in item:
            lines.append(f"- [IRRIGATION] {item['field']} ({item['crop']}): {item['schedule']['advice']}")
    if len(lines) == 1:
        lines.append("- No supporting document or farm data was found for this question.")
    return "\n".join(lines)


async def compose(state: AdvisorState, config: RunnableConfig) -> dict:
    res = _resources(config)
    passages = state.get("passages", [])
    context = state.get("context", {})
    available = {tag for tag in DATA_TAGS if tag.lower() in context}
    limitations: list[str] = []

    answer, llm_info, llm_confidence = None, None, "low"
    if res["llm"].configured:
        system = COMPOSER_SYSTEM.format(language=LANGUAGE_NAMES.get(state["language"], "French"))
        messages = [{"role": "system", "content": system}]
        for turn in state.get("history", [])[-6:]:
            messages.append({"role": turn["role"], "content": turn["content"][:2000]})
        messages.append({"role": "user", "content":
                         f"CONTEXT\n{format_context(context, passages)}\n\nQUESTION\n{state['question']}"})
        try:
            reply = await res["llm"].chat(messages, json_mode=True)
            data = parse_json(reply.content)
            answer = str(data.get("answer") or "").strip() or None
            llm_confidence = str(data.get("confidence", "low")).lower()
            limitations += [str(x) for x in data.get("limitations", []) if x][:5]
            llm_info = {"provider": reply.provider, "model": reply.model}
        except LLMUnavailable:
            limitations.append("AI text generation was unavailable; showing the evidence only.")
        except ValueError:
            limitations.append("The AI reply could not be read; showing the evidence only.")

    if answer is None:
        answer, llm_confidence = fallback_answer(state), "low"

    answer, cited, removed = clean_citations(answer, len(passages), available)
    if removed:
        limitations.append(f"{removed} citation(s) not backed by the provided context were removed.")
    if not passages:
        limitations.append("No supporting document was found in the knowledge base for this question.")
    for error in state.get("errors", []):
        limitations.append(f"Not available: {error}.")

    return {
        "answer": answer,
        "confidence": cap_confidence(llm_confidence, passages, cited, context),
        "limitations": list(dict.fromkeys(limitations)),
        "llm": llm_info,
        "cited": cited,  # passage numbers (1-based) actually cited in the answer
    }


def build_graph():
    graph = StateGraph(AdvisorState)
    graph.add_node("plan", plan)
    graph.add_node("farm", farm_node)
    graph.add_node("weather", weather_node)
    graph.add_node("irrigation", irrigation_node)
    graph.add_node("disease", disease_node)
    graph.add_node("knowledge", knowledge_node)
    graph.add_node("compose", compose)

    graph.add_edge(START, "plan")
    graph.add_conditional_edges("plan", route, ["farm", "weather", "irrigation", "disease", "knowledge"])
    for node in ("farm", "weather", "irrigation", "disease", "knowledge"):
        graph.add_edge(node, "compose")
    graph.add_edge("compose", END)
    return graph.compile()


def run_config(**resources: Any) -> RunnableConfig:
    return {"configurable": resources}

"""LangGraph multi-node pricing agent workflow."""
import logging, time
from typing import TypedDict, List, Dict, Any, Optional
from langgraph.graph import StateGraph, END

from scrapers import scrape_all
from demand import analyze_demand
from pricing import get_pricing_recommendation
from guardrails import validate

log = logging.getLogger(__name__)


class AgentState(TypedDict):
    product: Dict[str, Any]
    competitor_data: List[Dict[str, Any]]
    demand: Dict[str, Any]
    normalized: Dict[str, Any]
    recommendation: Dict[str, Any]
    guardrail_results: Dict[str, Any]
    final_decision: Dict[str, Any]
    logs: List[str]
    error: Optional[str]


def _log(state: AgentState, msg: str):
    state["logs"].append(msg)
    log.info(msg)


# ── Node 1: Input ──
def input_node(state: AgentState) -> AgentState:
    p = state["product"]
    _log(state, f"📦 Loaded product: {p['name']} (₹{p['current_price']:,})")
    return state


# ── Node 2: Scraper ──
def scraper_node(state: AgentState) -> AgentState:
    p = state["product"]
    _log(state, "🔍 Scraping Amazon...")
    time.sleep(0.2)
    _log(state, "🔍 Scraping Flipkart...")
    time.sleep(0.2)
    _log(state, "🔍 Scraping Google Shopping...")

    results = scrape_all(p["name"], p["category"])
    state["competitor_data"] = results
    _log(state, f"✅ Found {len(results)} competitor listings")
    return state


# ── Node 3: Demand Analysis ──
def demand_node(state: AgentState) -> AgentState:
    _log(state, "📊 Analyzing demand signals...")
    p = state["product"]
    state["demand"] = analyze_demand(
        p["name"], p["current_price"], state["competitor_data"], p["category"]
    )
    _log(state, f"📈 Demand score: {state['demand']['demand_score']} | Trend: {state['demand']['trend']}")
    return state


# ── Node 4: Normalization ──
def normalization_node(state: AgentState) -> AgentState:
    _log(state, "🔄 Normalizing data schema...")
    p = state["product"]
    state["normalized"] = {
        "price": p["current_price"],
        "competitor_prices": [c["price"] for c in state["competitor_data"]],
        "demand_score": state["demand"]["demand_score"],
        "stock_status": "mixed",
        "category": p["category"],
    }
    return state


# ── Node 5: Pricing Strategy (LLM) ──
def pricing_node(state: AgentState) -> AgentState:
    _log(state, "🤖 Running AI pricing strategy...")
    state["recommendation"] = get_pricing_recommendation(
        state["product"], state["competitor_data"], state["demand"]
    )
    rec = state.get("recommendation") or {}
    rec_price = rec.get('recommended_price')
    if rec_price is not None:
        _log(state, f"💰 AI recommends: ₹{rec_price:,.0f} "
                   f"(confidence: {rec.get('confidence', 0):.0%})")
    else:
        _log(state, f"💰 AI pricing unavailable: {rec.get('reasoning', 'No recommendation')}")
    return state


# ── Node 6: Guardrail Validation ──
def guardrail_node(state: AgentState) -> AgentState:
    _log(state, "🛡️ Validating guardrails...")
    rec_price = state["recommendation"].get("recommended_price")
    if rec_price is None:
        _log(state, "🛡️ Guardrails: Skipped (no recommendation)")
        state["guardrail_results"] = {"rules": {}, "all_pass": True}
    else:
        state["guardrail_results"] = validate(state["product"], rec_price)
        status = "✅ All passed" if state["guardrail_results"]["all_pass"] else "⚠️ Some rules violated"
        _log(state, f"🛡️ Guardrails: {status}")
    return state


# ── Node 7: Decision ──
def decision_node(state: AgentState) -> AgentState:
    _log(state, "🎯 Finalizing recommendation...")
    rec = state.get("recommendation") or {}
    gr = state.get("guardrail_results") or {"all_pass": False}
    p = state["product"]
    current = p["current_price"]

    # If guardrails fail, adjust price to comply
    if not gr.get("all_pass"):
        cost = p["cost_price"]
        min_margin = p["constraints"]["min_margin_pct"]
        max_change = p["constraints"]["max_change_pct"]

        safe_price = rec.get("recommended_price") if rec.get("recommended_price") is not None else current
        min_price = cost * (1 + min_margin / 100)
        max_price = current * (1 + max_change / 100)
        min_price_lower = current * (1 - max_change / 100)

        safe_price = max(safe_price, min_price)
        safe_price = min(safe_price, max_price)
        safe_price = max(safe_price, min_price_lower)

        state["final_decision"] = {
            "recommended_price": round(safe_price, 2),
            "adjusted": True,
            "original_recommendation": rec.get("recommended_price"),
            "reasoning": rec.get("reasoning", "") + " (Adjusted to comply with guardrails)",
            "confidence": rec.get("confidence", 0) * 0.8,
            "strategy": rec.get("strategy", "hold"),
        }
        _log(state, f"⚠️ Adjusted price to ₹{safe_price:,.0f} for guardrail compliance")
    elif rec.get("recommended_price") is None:
        # No AI recommendation available
        state["final_decision"] = {
            "recommended_price": None,
            "adjusted": False,
            "reasoning": rec.get("reasoning", "AI pricing unavailable"),
            "confidence": 0.0,
            "strategy": "hold",
        }
        _log(state, "⚠️ No AI recommendation available")
    else:
        state["final_decision"] = {
            "recommended_price": rec.get("recommended_price", current),
            "adjusted": False,
            "reasoning": rec.get("reasoning", ""),
            "confidence": rec.get("confidence", 0),
            "strategy": rec.get("strategy", "competitive"),
        }

    # Add percent change if price is available
    final_price = state["final_decision"].get("recommended_price")
    if final_price is not None:
        delta_pct = ((final_price - current) / current) * 100
        state["final_decision"]["delta_pct"] = round(delta_pct, 1)

    return state


# ── Node 8: Auto Apply (mock) ──
def auto_apply_node(state: AgentState) -> AgentState:
    _log(state, "✅ Auto-applying price to database...")
    state["logs"].append("Auto-applied price to database.")
    return state


# ── Node 9: Human Review (mock) ──
def human_review_node(state: AgentState) -> AgentState:
    _log(state, "⚠️ Routing to human review queue...")
    state["logs"].append("Routed to human review.")
    return state


# ── Conditional Router ──
def route_decision(state: AgentState) -> str:
    """Route based on guardrail checks and confidence."""
    # If it failed guardrails initially, or confidence is too low, send to human
    gr = state.get("guardrail_results") or {}
    rec = state.get("recommendation") or {}
    
    if rec.get("recommended_price") is None:
        return "human_review"
    if not gr.get("all_pass", False):
        return "human_review"
    if rec.get("confidence", 0) < 0.7:
        return "human_review"
    
    return "auto_apply"


def build_graph():
    """Build and compile the LangGraph workflow."""
    graph = StateGraph(AgentState)
    graph.add_node("input", input_node)
    graph.add_node("scraper", scraper_node)
    graph.add_node("demand", demand_node)
    graph.add_node("normalize", normalization_node)
    graph.add_node("pricing", pricing_node)
    graph.add_node("guardrail", guardrail_node)
    graph.add_node("decision", decision_node)
    graph.add_node("auto_apply", auto_apply_node)
    graph.add_node("human_review", human_review_node)

    graph.set_entry_point("input")
    graph.add_edge("input", "scraper")
    graph.add_edge("scraper", "demand")
    graph.add_edge("demand", "normalize")
    graph.add_edge("normalize", "pricing")
    graph.add_edge("pricing", "guardrail")
    graph.add_edge("guardrail", "decision")
    
    # Conditional edge from decision
    graph.add_conditional_edges(
        "decision",
        route_decision,
        {
            "auto_apply": "auto_apply",
            "human_review": "human_review"
        }
    )
    
    graph.add_edge("auto_apply", END)
    graph.add_edge("human_review", END)

    return graph.compile()


# Singleton compiled graph
agent_graph = build_graph()


def run_agent(product: dict) -> dict:
    """Execute the full agent workflow for a product."""
    initial_state: AgentState = {
        "product": product,
        "competitor_data": [],
        "demand": {},
        "normalized": {},
        "recommendation": {},
        "guardrail_results": {},
        "final_decision": {},
        "logs": [],
        "error": None,
    }

    try:
        result = agent_graph.invoke(initial_state)
        return result
    except Exception as e:
        log.error("Agent execution error: %s", e)
        initial_state["error"] = str(e)
        initial_state["logs"].append(f"❌ Error: {str(e)}")
        return initial_state

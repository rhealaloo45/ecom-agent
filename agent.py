"""LangGraph multi-node pricing agent workflow with agentic LLM loop.

Upgraded from a static pipeline to a fully agentic system that:
 - Uses LLM-driven decision-making (refine / fetch / approve / finalize)
 - Selects competitor sources per product category
 - Supports tool-use via LangChain tools
 - Persists all decisions to the price history database
"""
import logging, time, re, json, sqlite3, os
from typing import TypedDict, List, Dict, Any, Optional
from datetime import datetime, timezone
from langgraph.graph import StateGraph, END
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

import db
from scrapers import (
    scrape_all, _generate_mock_data, 
    AmazonAdapter, DuckDuckGoUniversalAdapter
)
from demand import analyze_demand
from pricing import get_pricing_recommendation
from guardrails import validate
from google_tasks import create_pricing_task

log = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "pricesync.db")

# ═══════════════════════════════════════════════════════════════════════════
#  Agent Tools  (Phase 1)
# ═══════════════════════════════════════════════════════════════════════════

@tool
def refine_price_tool(product_id: str, issue: str) -> dict:
    """Re-run pricing with adjusted constraints based on guardrail violations or low confidence."""
    from products import get_product
    product = get_product(product_id)
    if not product:
        return {"error": f"Product {product_id} not found"}
    adjusted = product.get("constraints", {}).copy()
    adjusted["margin_pct"] = adjusted.get("min_margin_pct", 15) + 3
    refined = get_pricing_recommendation(
        product=product,
        competitor_data=[],
        demand={"demand_score": 0.5, "trend": "Stable", "signals": {}},
    )
    return {
        "refined_price": refined.get("recommended_price", product.get("current_price", 0)),
        "confidence": refined.get("confidence", 0.5),
        "reasoning": refined.get("reasoning", ""),
    }


@tool
def fetch_deep_market_data(product_id: str, source: str = None) -> dict:
    """Fetch additional competitor data when confidence is low or guardrails fail."""
    from products import get_product
    product = get_product(product_id)
    if not product:
        return {"error": f"Product {product_id} not found", "competitors": [], "competitor_count": 0, "avg_price": 0}
    competitors = scrape_all(product["name"], product.get("category", "general"))
    avg_price = sum(c.get("price", 0) for c in competitors) / max(len(competitors), 1) if competitors else 0
    return {
        "competitor_count": len(competitors),
        "competitors": competitors,
        "avg_price": avg_price,
        "new_signals": {
            "avg": avg_price,
            "min": min([c.get("price", 0) for c in competitors] or [0]),
        },
    }


@tool
def request_human_approval(product_id: str, recommendation: float, reason: str, metadata: dict = None) -> dict:
    """Request human review when guardrails fail or confidence is too low."""
    from products import get_product
    product = get_product(product_id)
    name = product["name"] if product else product_id
    return {
        "status": "waiting",
        "message": f"Approval requested for {name}: {reason}. Recommended: ₹{recommendation:,.0f}",
    }


AGENT_TOOLS = [refine_price_tool, fetch_deep_market_data, request_human_approval]


# ═══════════════════════════════════════════════════════════════════════════
#  State Definition
# ═══════════════════════════════════════════════════════════════════════════

class AgentState(TypedDict):
    product: Dict[str, Any]
    product_id: str
    competitor_data: List[Dict[str, Any]]
    competitor_sources_used: List[str]
    demand: Dict[str, Any]
    demand_metrics: Dict[str, Any]
    normalized: Dict[str, Any]
    recommendation: Dict[str, Any]
    guardrail_results: Dict[str, Any]
    guardrail_passed: bool
    final_decision: Dict[str, Any]
    logs: List[str]
    run_type: Optional[str]
    google_task_created: bool
    error: Optional[str]
    # Agent loop fields
    messages: List[Any]
    tool_calls_made: List[str]
    loop_count: int
    final_price: Optional[float]
    requires_human_approval: bool
    suggested_alternatives: List[str]
    seasonal_context: Dict[str, Any]


def _log(state: AgentState, msg: str):
    state["logs"].append(msg)
    log.info(msg)


# ═══════════════════════════════════════════════════════════════════════════
#  Pipeline Nodes (Existing, enhanced)
# ═══════════════════════════════════════════════════════════════════════════

# ── Node 1: Input ──
def input_node(state: AgentState) -> AgentState:
    p = state["product"]
    _log(state, f"📦 Loaded product: {p['name']} (₹{p['current_price']:,})")
    return state


# ── Node 2: Scraper (Phase 3 – category-aware) ──
def scraper_node(state: AgentState) -> AgentState:
    """Scrape competitors using category-optimized sources."""
    from competitor_sources import CompetitorSourceMapper

    p = state["product"]
    category = p.get("category", "general")

    sources = CompetitorSourceMapper.get_sources_for_category(category)
    state["competitor_sources_used"] = sources
    state["suggested_alternatives"] = CompetitorSourceMapper.get_suggested_alternatives(category)

    _log(state, f"🔍 Scraping sources for '{category}': {', '.join(sources)}")

    competitor_data = scrape_all_dynamic(p["name"], category, sources=sources)
    state["competitor_data"] = competitor_data
    _log(state, f"✅ Found {len(competitor_data)} competitor listings")
    return state


def scrape_all_dynamic(product_name: str, category: str, keywords: str = None, sources: list = None) -> list:
    """Scrape using category-specific sources and return listings."""
    from competitor_sources import CompetitorSourceMapper
    import random

    if not sources:
        sources = CompetitorSourceMapper.get_sources_for_category(category)

    adapters_map = {
        "amazon": AmazonAdapter(),
        "ebay": DuckDuckGoUniversalAdapter(),
        "newegg": DuckDuckGoUniversalAdapter(),
        "myntra": DuckDuckGoUniversalAdapter(),
    }

    all_listings = []
    for source in sources:
        adapter = adapters_map.get(source)
        if adapter:
            try:
                listings = adapter.scrape(product_name, category)
                if not listings:
                    raise Exception("Empty results")
                all_listings.extend(listings)
                log.info("%s returned %d results", source, len(listings))
                time.sleep(0.5)
            except Exception as e:
                log.error("Scraping %s failed: %s. Using mock data.", source, e)
                mock_data = _generate_mock_data(product_name, category)
                mock_slice = mock_data[:random.randint(2, 3)]
                source_name = adapter.source
                
                query = product_name.replace(" ", "+")
                search_urls = {
                    "Amazon": f"https://www.amazon.in/s?k={query}",
                    "eBay": f"https://www.ebay.com/sch/i.html?_nkw={query}",
                    "Newegg": f"https://www.newegg.com/p/search?d={query}",
                    "Myntra": f"https://www.myntra.com/{product_name.replace(' ', '-')}",
                }
                
                for m in mock_slice:
                    new_m = m.copy()
                    new_m["source"] = source_name
                    new_m["url"] = search_urls.get(source_name, m.get("url", f"https://example.com/search?q={query}"))
                    all_listings.append(new_m)
        else:
            log.info("No adapter for source '%s', skipping", source)

    if not all_listings:
        log.warning("No real data from sources %s. Using mock data.", sources)
        all_listings = _generate_mock_data(product_name, category)

    return all_listings


# ── Node 3: Demand Analysis ──
def demand_node(state: AgentState) -> AgentState:
    _log(state, "📊 Analyzing demand signals...")
    p = state["product"]
    result = analyze_demand(
        p["name"], p["current_price"], state["competitor_data"], p["category"]
    )
    state["demand"] = result
    state["seasonal_context"] = result.get("seasonal", {})

    # Populate demand_metrics for agent loop compatibility
    state["demand_metrics"] = {
        "demand_score": result.get("demand_score", 0.5),
        "trend": result.get("trend", "Stable"),
        "avg_price": result.get("signals", {}).get("avg_competitor_price", 0),
    }

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
        state["product"], state["competitor_data"], state["demand"], state.get("seasonal_context")
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
        state["guardrail_passed"] = True
    else:
        state["guardrail_results"] = validate(state["product"], rec_price)
        state["guardrail_passed"] = state["guardrail_results"]["all_pass"]
        status = "✅ All passed" if state["guardrail_passed"] else "⚠️ Some rules violated"
        _log(state, f"🛡️ Guardrails: {status}")
    return state


# ═══════════════════════════════════════════════════════════════════════════
#  Agent Loop Nodes (Phase 1 – LLM decision-making)
# ═══════════════════════════════════════════════════════════════════════════

def _get_llm_with_tools():
    """Create the LLM instance with bound tools."""
    try:
        from langchain_openrouter import ChatOpenRouter
        llm = ChatOpenRouter(model="google/gemma-4-31b-it:free", temperature=0.3)
    except Exception as exc:
        # Covers ImportError, OSError (DLL load failures on Windows), etc.
        log.warning("Could not load ChatOpenRouter: %s", exc)
        llm = None
    return llm


def agent_loop_node(state: AgentState) -> AgentState:
    """LLM evaluates state and decides: refine? fetch? approve? finalize?"""
    _log(state, f"🔁 Agent loop iteration {state.get('loop_count', 0) + 1}...")

    product = state["product"]
    guardrail_issues = []
    gr_rules = state.get("guardrail_results", {}).get("rules", {})
    for rule_name, rule_data in gr_rules.items():
        if not rule_data.get("pass", True):
            guardrail_issues.append(f"{rule_data.get('label', rule_name)}: {rule_data.get('detail', '')}")

    rec = state.get("recommendation", {})
    confidence = rec.get("confidence", 0.5)
    rec_price = rec.get("recommended_price", 0)
    guardrail_passed = state.get("guardrail_passed", False)

    # Deterministic fallback if no LLM available or to avoid flaky LLM calls
    llm = _get_llm_with_tools()

    if llm is not None:
        try:
            llm_with_tools = llm.bind_tools(tools=AGENT_TOOLS, tool_choice="auto")

            context = f"""Product: {product['name']} (ID: {state.get('product_id', product.get('id', ''))})
Current price: ₹{product.get('current_price', 0):,}
Recommended price: ₹{rec_price:,.0f}
Confidence: {confidence:.2f}
Guardrails passed: {guardrail_passed}
Issues: {', '.join(guardrail_issues) if guardrail_issues else 'None'}
Competitor avg: ₹{state.get('demand_metrics', {}).get('avg_price', 0):,.0f}
Loop count: {state.get('loop_count', 0)}

You are a pricing agent. Decide your next action:
- If confidence < 0.7 and guardrails failed: use refine_price_tool
- If confidence < 0.6 and no deep market data fetched yet: use fetch_deep_market_data
- If confidence >= 0.7 and guardrails passed: respond with FINAL_DECISION and the price
- Otherwise: use refine_price_tool

Respond with a tool call or state FINAL_DECISION with the price."""

            messages = state.get("messages", []) + [HumanMessage(content=context)]
            response = llm_with_tools.invoke(messages)
            state["messages"] = messages + [response]
            state["loop_count"] = state.get("loop_count", 0) + 1

            if hasattr(response, "tool_calls") and response.tool_calls:
                state["tool_calls_made"].append(response.tool_calls[0]["name"])

            return state

        except Exception as exc:
            log.warning("LLM agent loop failed (%s), using deterministic fallback", exc)

    # Deterministic fallback logic
    state["loop_count"] = state.get("loop_count", 0) + 1

    if confidence >= 0.7 and guardrail_passed:
        # High confidence + guardrails pass → finalize
        msg = AIMessage(content=f"FINAL_DECISION: ₹{rec_price:,.0f}")
        state["messages"] = state.get("messages", []) + [msg]
        _log(state, f"🤖 Agent decided: FINAL_DECISION at ₹{rec_price:,.0f}")
    elif confidence < 0.6 and "fetch_deep_market_data" not in state.get("tool_calls_made", []):
        # Low confidence, haven't fetched deep data yet
        tool_call_msg = AIMessage(
            content="",
            tool_calls=[{
                "id": f"call_{state['loop_count']}",
                "name": "fetch_deep_market_data",
                "args": {"product_id": state.get("product_id", product.get("id", "P001"))},
            }],
        )
        state["messages"] = state.get("messages", []) + [tool_call_msg]
        state["tool_calls_made"].append("fetch_deep_market_data")
        _log(state, "🤖 Agent decided: fetch more market data")
    elif not guardrail_passed and state.get("loop_count", 0) < 3:
        # Guardrails failed → refine
        tool_call_msg = AIMessage(
            content="",
            tool_calls=[{
                "id": f"call_{state['loop_count']}",
                "name": "refine_price_tool",
                "args": {
                    "product_id": state.get("product_id", product.get("id", "P001")),
                    "issue": "; ".join(guardrail_issues) or "Low confidence",
                },
            }],
        )
        state["messages"] = state.get("messages", []) + [tool_call_msg]
        state["tool_calls_made"].append("refine_price_tool")
        _log(state, "🤖 Agent decided: refine pricing")
    else:
        # Exhausted retries or moderate confidence → request approval
        tool_call_msg = AIMessage(
            content="",
            tool_calls=[{
                "id": f"call_{state['loop_count']}",
                "name": "request_human_approval",
                "args": {
                    "product_id": state.get("product_id", product.get("id", "P001")),
                    "recommendation": rec_price,
                    "reason": "Agent exhausted retries or low confidence",
                },
            }],
        )
        state["messages"] = state.get("messages", []) + [tool_call_msg]
        state["tool_calls_made"].append("request_human_approval")
        _log(state, "🤖 Agent decided: request human approval")

    return state


def route_agent_decision(state: AgentState) -> str:
    """Route based on LLM or deterministic response."""
    messages = state.get("messages", [])
    if not messages:
        return "final_decision"

    last_msg = messages[-1]

    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        tool_name = last_msg.tool_calls[0]["name"]
        if "refine" in tool_name:
            return "refine_price"
        if "fetch" in tool_name:
            return "fetch_data"
        if "approval" in tool_name:
            return "request_approval"

    if isinstance(getattr(last_msg, "content", ""), str) and "FINAL_DECISION" in last_msg.content:
        return "final_decision"

    if state.get("loop_count", 0) >= 4:
        return "request_approval"

    return "agent_loop"


# ── Tool execution nodes ──

def refine_price_node(state: AgentState) -> AgentState:
    """Execute refine_price_tool."""
    _log(state, "🔧 Refining price...")
    last_msg = state["messages"][-1]
    tool_call = last_msg.tool_calls[0]
    try:
        result = refine_price_tool.invoke(tool_call["args"])
        state["recommendation"]["recommended_price"] = result["refined_price"]
        state["recommendation"]["confidence"] = result["confidence"]
        state["messages"].append(
            ToolMessage(
                tool_call_id=tool_call["id"],
                content=f"Price refined to ₹{result['refined_price']:,.0f}, confidence: {result['confidence']:.2f}",
            )
        )
        _log(state, f"🔧 Refined price: ₹{result['refined_price']:,.0f} (conf: {result['confidence']:.2f})")
    except Exception as exc:
        log.error("Refine tool failed: %s", exc)
        state["messages"].append(
            ToolMessage(tool_call_id=tool_call["id"], content=f"Error: {exc}")
        )
    return state


def fetch_data_node(state: AgentState) -> AgentState:
    """Execute fetch_deep_market_data tool."""
    _log(state, "📡 Fetching deep market data...")
    last_msg = state["messages"][-1]
    tool_call = last_msg.tool_calls[0]
    try:
        result = fetch_deep_market_data.invoke(tool_call["args"])
        state["competitor_data"].extend(result.get("competitors", []))
        state["demand_metrics"]["avg_price"] = result.get("avg_price", 0)
        state["messages"].append(
            ToolMessage(
                tool_call_id=tool_call["id"],
                content=f"Fetched {result.get('competitor_count', 0)} competitors, avg: ₹{result.get('avg_price', 0):,.0f}",
            )
        )
        _log(state, f"📡 Fetched {result.get('competitor_count', 0)} new competitors")
    except Exception as exc:
        log.error("Fetch data tool failed: %s", exc)
        state["messages"].append(
            ToolMessage(tool_call_id=tool_call["id"], content=f"Error: {exc}")
        )
    return state


def request_approval_node(state: AgentState) -> AgentState:
    """Mark the run as requiring human approval."""
    _log(state, "🙋 Requesting human approval...")
    state["requires_human_approval"] = True
    rec = state.get("recommendation", {})
    state["final_price"] = rec.get("recommended_price")

    # Still persist the tentative price
    _persist_price_decision(state)
    return state


def final_decision_node(state: AgentState) -> AgentState:
    """Extract final price, persist to DB, and update product."""
    _log(state, "🎯 Finalizing pricing decision...")

    messages = state.get("messages", [])
    last_msg = messages[-1] if messages else None
    rec_price = state["recommendation"].get("recommended_price", 0)

    # Try to extract price from the FINAL_DECISION message
    if last_msg and hasattr(last_msg, "content") and isinstance(last_msg.content, str):
        match = re.search(r'[₹$]?([\d,]+\.?\d*)', last_msg.content.replace(",", ""))
        if match:
            try:
                rec_price = float(match.group(1))
            except ValueError:
                pass

    state["final_price"] = rec_price
    _persist_price_decision(state)

    # Update in-memory product
    from products import update_product
    pid = state.get("product_id", state["product"].get("id"))
    if pid and rec_price:
        update_product(pid, current_price=rec_price)
        _log(state, f"✅ Applied final price ₹{rec_price:,.0f} to product {pid}")

    return state


def _persist_price_decision(state: AgentState):
    """Save the pricing decision to price_history."""
    try:
        p = state["product"]
        pid = state.get("product_id", p.get("id"))
        final_price = state.get("final_price") or state["recommendation"].get("recommended_price") or p["current_price"]
        competitor_prices = [c.get("price", 0) for c in state.get("competitor_data", []) if c.get("price") is not None]
        competitor_avg = round(sum(competitor_prices) / len(competitor_prices), 2) if competitor_prices else None
        competitor_min = min(competitor_prices) if competitor_prices else None
        demand = state.get("demand", state.get("demand_metrics", {}))

        db.insert_price_snapshot(
            pid,
            p["name"],
            final_price,
            competitor_avg,
            competitor_min,
            demand.get("demand_score"),
            demand.get("trend"),
            state.get("guardrail_passed", False),
        )
    except Exception as exc:
        log.warning("Failed to persist price decision: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════
#  Legacy Decision Node (kept for backwards compatibility)
# ═══════════════════════════════════════════════════════════════════════════

def decision_node(state: AgentState) -> AgentState:
    """Legacy decision node – routes to auto_apply or human_review."""
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

    # Persist the completed decision to the price history database
    try:
        competitor_prices = [c["price"] for c in state["competitor_data"] if c.get("price") is not None]
        competitor_avg = round(sum(competitor_prices) / len(competitor_prices), 2) if competitor_prices else None
        competitor_min = min(competitor_prices) if competitor_prices else None
        demand_score = state["demand"].get("demand_score")
        trend = state["demand"].get("trend")
        guardrail_passed = state["guardrail_results"].get("all_pass", False)
        db.insert_price_snapshot(
            p["id"],
            p["name"],
            final_price if final_price is not None else current,
            competitor_avg,
            competitor_min,
            demand_score,
            trend,
            guardrail_passed,
        )
    except Exception as exc:
        log.warning("Failed to save price snapshot: %s", exc)

    # Create Google Task for guardrail failures
    google_task_created = False
    if not state["guardrail_results"].get("all_pass", False):
        try:
            current_margin = ((final_price - p["cost_price"]) / p["cost_price"] * 100) if p["cost_price"] else 0
            min_margin = p["constraints"].get("min_margin_pct", 0)
            competitor_avg = competitor_avg if competitor_prices else 0
            issue_summary = (
                f"Guardrail failed for {p['name']}. Current margin: {current_margin:.1f}%. "
                f"Required: {min_margin}%. Competitor avg: ₹{competitor_avg}. "
                "Recommended action: review cost structure or adjust minimum margin threshold."
            )
            google_task_created = create_pricing_task(p["name"], issue_summary)
        except Exception as exc:
            log.warning("Google task creation failed: %s", exc)
    state["google_task_created"] = google_task_created

    return state


# ── Node 8: Auto Apply (mock) ──
def auto_apply_node(state: AgentState) -> AgentState:
    from products import update_product

    p = state["product"]
    new_price = state["final_decision"].get("recommended_price")

    if new_price is not None:
        update_product(p["id"], current_price=new_price)
        _log(state, f"✅ Auto-applied new price ₹{new_price:,.2f} to database.")
    else:
        _log(state, "⚠️ No valid price to auto-apply.")

    return state


# ── Node 9: Human Review (mock) ──
def human_review_node(state: AgentState) -> AgentState:
    from products import update_product

    p = state["product"]
    new_price = state["final_decision"].get("recommended_price")

    if new_price is not None:
        update_product(p["id"], current_price=new_price)
        _log(state, f"⚠️ Routing to human review queue. (Tentatively applied ₹{new_price:,.2f})")
    else:
        _log(state, "⚠️ Routing to human review queue...")

    state["logs"].append("Routed to human review.")
    return state


# ── Conditional Router ──
def route_decision(state: AgentState) -> str:
    """Route based on guardrail checks and confidence."""
    gr = state.get("guardrail_results") or {}
    rec = state.get("recommendation") or {}
    
    if rec.get("recommended_price") is None:
        return "human_review"
    if not gr.get("all_pass", False):
        return "human_review"
    if rec.get("confidence", 0) < 0.7:
        return "human_review"
    
    return "auto_apply"


# ═══════════════════════════════════════════════════════════════════════════
#  Graph Builders
# ═══════════════════════════════════════════════════════════════════════════

def build_graph():
    """Build the LEGACY graph (backwards-compatible with existing UI)."""
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


def build_pricing_agent_graph():
    """Build the NEW agentic graph with LLM decision loop."""
    graph = StateGraph(AgentState)

    graph.add_node("input", input_node)
    graph.add_node("scraper", scraper_node)
    graph.add_node("demand", demand_node)
    graph.add_node("normalize", normalization_node)
    graph.add_node("recommend", pricing_node)
    graph.add_node("guardrail", guardrail_node)
    graph.add_node("agent_loop", agent_loop_node)
    graph.add_node("refine_price", refine_price_node)
    graph.add_node("fetch_data", fetch_data_node)
    graph.add_node("request_approval", request_approval_node)
    graph.add_node("final_decision", final_decision_node)

    graph.set_entry_point("input")
    graph.add_edge("input", "scraper")
    graph.add_edge("scraper", "demand")
    graph.add_edge("demand", "normalize")
    graph.add_edge("normalize", "recommend")
    graph.add_edge("recommend", "guardrail")
    graph.add_edge("guardrail", "agent_loop")

    graph.add_conditional_edges("agent_loop", route_agent_decision, {
        "refine_price": "refine_price",
        "fetch_data": "fetch_data",
        "request_approval": "request_approval",
        "agent_loop": "agent_loop",
        "final_decision": "final_decision",
    })

    graph.add_edge("refine_price", "agent_loop")
    graph.add_edge("fetch_data", "agent_loop")
    graph.add_edge("request_approval", END)
    graph.add_edge("final_decision", END)

    return graph.compile()


# Singleton compiled graphs
agent_graph = build_graph()
AGENT_GRAPH = build_pricing_agent_graph()


# ═══════════════════════════════════════════════════════════════════════════
#  Public API
# ═══════════════════════════════════════════════════════════════════════════

def run_agent(product: dict, run_type: str = "manual") -> dict:
    """Execute the LEGACY agent workflow for a product (UI-compatible)."""
    initial_state: AgentState = {
        "product": product,
        "product_id": product.get("id", ""),
        "competitor_data": [],
        "competitor_sources_used": [],
        "demand": {},
        "demand_metrics": {},
        "normalized": {},
        "recommendation": {},
        "guardrail_results": {},
        "guardrail_passed": False,
        "final_decision": {},
        "logs": [],
        "run_type": run_type,
        "google_task_created": False,
        "error": None,
        "messages": [],
        "tool_calls_made": [],
        "loop_count": 0,
        "final_price": None,
        "requires_human_approval": False,
        "suggested_alternatives": [],
        "seasonal_context": {},
    }

    try:
        result = agent_graph.invoke(initial_state)
        return result
    except Exception as e:
        log.error("Agent execution error: %s", e)
        initial_state["error"] = str(e)
        initial_state["logs"].append(f"❌ Error: {str(e)}")
        return initial_state


def run_agentic_pricing(product_id: str) -> dict:
    """Execute the NEW agentic pricing workflow with LLM decision loop."""
    from products import get_product

    product = get_product(product_id)
    if not product:
        raise ValueError(f"Product {product_id} not found")

    product["product_id"] = product_id

    initial_state = {
        "product_id": product_id,
        "product": product,
        "competitor_data": [],
        "competitor_sources_used": [],
        "demand": {},
        "demand_metrics": {},
        "normalized": {},
        "recommendation": {},
        "guardrail_results": {},
        "guardrail_passed": False,
        "final_decision": {},
        "logs": [],
        "run_type": "agentic",
        "google_task_created": False,
        "error": None,
        "messages": [],
        "tool_calls_made": [],
        "loop_count": 0,
        "final_price": None,
        "requires_human_approval": False,
        "suggested_alternatives": [],
        "seasonal_context": {},
    }

    config = {"configurable": {"thread_id": product_id}}
    result = AGENT_GRAPH.invoke(initial_state, config=config)
    return result

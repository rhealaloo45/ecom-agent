import logging, os, json, requests
from typing import List, Dict, Any
from seasonal import get_seasonal_context

log = logging.getLogger(__name__)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")

def get_demand_intelligence(product_name: str, category: str, seasonal: dict) -> dict:
    """Use LLM to reason about why demand might be changing."""
    if not OPENROUTER_API_KEY or "your_" in OPENROUTER_API_KEY:
        return {"reasoning": "Standard heuristic applied.", "multiplier_adj": 1.0}

    prompt = f"""Critical Demand Analysis:
Product: {product_name}
Category: {category}
Date: {seasonal.get('date', 'Today')}
Events: {seasonal.get('context_str', 'None')}

TASK:
Evaluate if the event ACTUALLY increases demand for THIS SPECIFIC CATEGORY.
- Note: Environmental events (Earth Day) may DECREASE demand for electronics/plastic-heavy goods.
- Note: Religious festivals increase demand for clothing/gifts.
- Note: National holidays might only increase travel/grocery goods.

Provide a multiplier adjustment (range: 0.7 to 1.4). If the event is irrelevant or contradictory, use < 1.0.

Reply ONLY with JSON:
{{"reasoning": "Contextual explanation here", "multiplier_adj": 0.95}}"""

    try:
        # Try Ollama first if configured
        try:
            resp = requests.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": f"{prompt}\nReturn JSON only.",
                    "stream": False,
                    "format": "json"
                },
                timeout=15
            )
            if resp.status_code == 200:
                data = resp.json()
                return json.loads(data['response'])
        except Exception as e:
            log.debug("Ollama failed, falling back to OpenRouter: %s", e)

        # Fallback to OpenRouter
        if not OPENROUTER_API_KEY or "your_" in OPENROUTER_API_KEY:
             return {"reasoning": "Heuristic fallback (no local/cloud LLM).", "multiplier_adj": 1.0}
             
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
            json={
                "model": "google/gemma-3-27b-it:free",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3
            },
            timeout=10
        )
        data = resp.json()
        content = data['choices'][0]['message']['content']
        # Simple JSON extract
        start, end = content.find('{'), content.rfind('}')
        if start != -1 and end != -1:
            return json.loads(content[start:end+1])
    except Exception as e:
        log.warning("Demand LLM failed: %s", e)
    
    return {"reasoning": "Heuristic fallback.", "multiplier_adj": 1.0}

def analyze_demand(product_name: str, product_price: float,
                   competitor_data: List[Dict[str, Any]], category: str) -> Dict[str, Any]:
    """
    Compute demand score (0-1) using:
      - Price competitiveness
      - Stock scarcity signals
      - Competitor density
      - Category multiplier
      - Seasonal/Festival context + LLM Reasoning
    """
    seasonal_context = get_seasonal_context()
    
    # Initialize base values
    base_score = 0.5
    trend = "Stable"
    signals = {}
    
    if competitor_data:
        prices = [c["price"] for c in competitor_data if c.get("price")]
        if prices:
            avg_comp_price = sum(prices) / len(prices)
            min_comp_price = min(prices)
            max_comp_price = max(prices)

            # Price positioning score (0-1): higher if our price is competitive
            if max_comp_price == min_comp_price:
                price_score = 0.5
            else:
                price_score = max(0, min(1, (max_comp_price - product_price) / (max_comp_price - min_comp_price)))

            # Stock scarcity score
            low_stock_count = sum(1 for c in competitor_data if "low" in c.get("stock_status", "").lower())
            out_stock_count = sum(1 for c in competitor_data if "out" in c.get("stock_status", "").lower())
            scarcity_score = min(1.0, (low_stock_count * 0.15 + out_stock_count * 0.3))

            # Competitor density score
            density_score = min(1.0, len(competitor_data) / 10)

            # Category multiplier
            cat_multipliers = {
                "electronics": 1.1, "smartphones": 1.15, "laptops": 1.1,
                "footwear": 0.9, "home-appliances": 0.95,
            }
            cat_mult = cat_multipliers.get(category, 1.0)

            # Weighted demand score
            base_score = (price_score * 0.35 + scarcity_score * 0.25 + density_score * 0.25 + 0.15) * cat_mult
            
            # Trend determination
            if product_price < avg_comp_price * 0.9:
                trend = "Increasing"
            elif product_price > avg_comp_price * 1.1:
                trend = "Decreasing"
            
            signals = {
                "avg_competitor_price": round(avg_comp_price, 2),
                "min_competitor_price": round(min_comp_price, 2),
                "max_competitor_price": round(max_comp_price, 2),
                "competitor_count": len(competitor_data),
                "low_stock_signals": low_stock_count,
                "price_competitiveness": round(price_score, 3),
                "scarcity_index": round(scarcity_score, 3),
            }

    # Apply Seasonal/Festival Multiplier
    seasonal_multiplier = seasonal_context.get("peak_multiplier", 1.0)
    
    # LLM Demand Reasoning
    intel = get_demand_intelligence(product_name, category, seasonal_context)
    multiplier_adj = intel.get("multiplier_adj", 1.0)
    demand_reasoning = intel.get("reasoning", "No specific reasoning provided.")
    
    # Combine signals
    final_score = base_score * seasonal_multiplier * multiplier_adj
    final_score = min(round(final_score, 3), 1.0) 

    return {
        "demand_score": final_score,
        "trend": trend,
        "review_velocity": "Unknown",
        "signals": signals,
        "seasonal_context": seasonal_context,
        "demand_reasoning": demand_reasoning
    }

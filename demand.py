"""Demand analysis engine – computes demand score from scraped signals."""
import logging
from typing import List, Dict, Any

log = logging.getLogger(__name__)


def analyze_demand(product_name: str, product_price: float,
                   competitor_data: List[Dict[str, Any]], category: str) -> Dict[str, Any]:
    """
    Compute demand score (0-1) using:
      - Price competitiveness
      - Stock scarcity signals
      - Competitor density
      - Category multiplier
    """
    if not competitor_data:
        return {
            "demand_score": 0.5,
            "trend": "Stable",
            "signals": {"note": "No competitor data available – using baseline"},
            "review_velocity": "Unknown",
        }

    prices = [c["price"] for c in competitor_data if c.get("price")]
    if not prices:
        return {"demand_score": 0.5, "trend": "Stable", "signals": {}, "review_velocity": "Unknown"}

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
    raw_score = (price_score * 0.35 + scarcity_score * 0.25 + density_score * 0.25 + 0.15) * cat_mult
    demand_score = round(max(0.0, min(1.0, raw_score)), 3)

    # Trend determination
    if product_price < avg_comp_price * 0.9:
        trend = "Increasing"
    elif product_price > avg_comp_price * 1.1:
        trend = "Decreasing"
    else:
        trend = "Stable"

    signals = {
        "avg_competitor_price": round(avg_comp_price, 2),
        "min_competitor_price": round(min_comp_price, 2),
        "max_competitor_price": round(max_comp_price, 2),
        "competitor_count": len(competitor_data),
        "low_stock_signals": low_stock_count,
        "price_competitiveness": round(price_score, 3),
        "scarcity_index": round(scarcity_score, 3),
    }

    log.info("Demand analysis: score=%.3f trend=%s", demand_score, trend)
    return {
        "demand_score": demand_score,
        "trend": trend,
        "signals": signals,
    }

"""Guardrail validation for pricing recommendations."""
import logging

log = logging.getLogger(__name__)


def validate(product: dict, recommended_price: float) -> dict:
    """Enforce pricing guardrails. Returns pass/fail for each rule."""
    constraints = product.get("constraints", {})
    cost = product["cost_price"]
    current = product["current_price"]
    min_margin_pct = constraints.get("min_margin_pct", 10)
    max_change_pct = constraints.get("max_change_pct", 20)
    positioning = constraints.get("positioning", "mid-range")

    results = {}

    # 1. Minimum margin check
    margin_pct = ((recommended_price - cost) / cost) * 100 if cost > 0 else 0
    min_price_for_margin = cost * (1 + min_margin_pct / 100)
    results["margin_rule"] = {
        "label": f"Minimum margin ≥ {min_margin_pct}%",
        "pass": margin_pct >= min_margin_pct,
        "detail": f"Actual margin: {margin_pct:.1f}% (min price: ₹{min_price_for_margin:,.0f})",
    }

    # 2. Max price change check
    if current > 0:
        change_pct = abs(recommended_price - current) / current * 100
    else:
        change_pct = 0
    results["price_limit_rule"] = {
        "label": f"Price change ≤ {max_change_pct}%",
        "pass": change_pct <= max_change_pct,
        "detail": f"Actual change: {change_pct:.1f}%",
    }

    # 3. Positioning rule
    if positioning == "premium":
        pos_pass = recommended_price >= cost * 1.2
        pos_detail = "Premium: price must be ≥ 20% above cost"
    elif positioning == "mid-range":
        pos_pass = cost * 1.1 <= recommended_price <= cost * 2.0
        pos_detail = "Mid-range: price must be 10-100% above cost"
    else:
        pos_pass = recommended_price >= cost
        pos_detail = "Budget: price must be above cost"

    results["positioning_rule"] = {
        "label": f"Brand positioning ({positioning})",
        "pass": pos_pass,
        "detail": pos_detail,
    }

    all_pass = all(r["pass"] for r in results.values())
    log.info("Guardrails: all_pass=%s, checks=%s", all_pass,
             {k: v["pass"] for k, v in results.items()})

    return {"rules": results, "all_pass": all_pass}

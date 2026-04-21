"""Pricing strategy via Ollama & OpenRouter."""
import os, json, logging, re, time
import requests
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_FALLBACK = os.getenv("OPENROUTER_FALLBACK", "1").strip().lower() not in ("0", "false", "no", "off")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")

MODEL = "google/gemma-3-27b-it:free"
MODEL_FALLBACKS = [
    "google/gemma-4-31b-it:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "meta-llama/llama-3.2-3b-instruct:free",
    "liquid/lfm-2.5-1.2b-instruct:free"
]
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
MAX_RETRIES = 2
RETRY_BACKOFF = 3.0

def _format_retail_price(value: float) -> float:
    price = int(round(value))
    if price % 10 == 0:
        price -= 1
    return float(max(price, 1))

def _local_ai_pricing(product: dict, competitor_data: list, demand: dict) -> dict:
    """Smart local heuristic analysis for when LLMs are unavailable."""
    current = product["current_price"]
    cost = product["cost_price"]
    min_margin_pct = product["constraints"]["min_margin_pct"]
    max_change_pct = product["constraints"]["max_change_pct"]
    
    min_allowed = cost * (1 + min_margin_pct / 100)
    max_allowed = current * (1 + max_change_pct / 100)
    min_allowed_lower = current * (1 - max_change_pct / 100)
    
    if not competitor_data:
        demand_score = demand.get("demand_score", 0.5)
        if demand_score > 0.7:
             rec_price = current * 1.02
             reasoning = "No competitor data, but demand is very high. Applying 2% premium."
        else:
             rec_price = current
             reasoning = "No competitor data; maintaining current price."
             
        return {
            "recommended_price": _format_retail_price(rec_price),
            "reasoning": reasoning,
            "confidence": 0.4,
            "strategy": "hold",
            "source": "local_ai",
        }
    
    prices = [c["price"] for c in competitor_data if c.get("price")]
    if not prices:
        return _local_ai_pricing(product, [], demand)

    avg_price = sum(prices) / len(prices)
    min_price = min(prices)
    demand_score = demand.get("demand_score", 0.5)
    
    target_price = min_price * 0.99 
    if demand_score > 0.8:
        target_price = avg_price * 0.98
        strategy = "premium"
    elif demand_score < 0.4:
        target_price = min_price * 0.97
        strategy = "penetration"
    else:
        strategy = "competitive"

    rec_price = max(target_price, min_allowed)
    rec_price = min(rec_price, max_allowed)
    rec_price = max(rec_price, min_allowed_lower)
    
    reasoning = f"Local Analysis: Market min is ₹{min_price:,.0f}. Aiming for ₹{rec_price:,.0f} based on {demand_score} demand score."
    
    return {
        "recommended_price": _format_retail_price(rec_price),
        "reasoning": reasoning,
        "confidence": 0.65,
        "strategy": strategy,
        "source": "local_ai",
    }

def get_pricing_recommendation(product: dict, competitor_data: list, demand: dict, seasonal_context: dict = None) -> dict:
    """Call LLM (Local Ollama first, then OpenRouter) to generate pricing recommendation."""
    if not seasonal_context:
        seasonal_context = demand.get("seasonal_context", {})

    comp_text = ""
    for c in competitor_data:
        comp_text += f"- {c['source']}: ₹{c['price']} ({c['stock_status']}, seller: {c.get('seller_type', 'unknown')})\n"

    system_instr = "You are a strict pricing AI. Your output MUST be a single JSON object. Never provide reasoning, introduction, or text outside the JSON block."
    
    prompt = f"""Generate a pricing recommendation for:
PRODUCT: {product['name']} (Category: {product['category']})
CURRENT PRICE: ₹{product['current_price']}
COST PRICE: ₹{product['cost_price']}
POSITIONING: {product['constraints']['positioning']}

COMPETITOR PRICES:
{comp_text}

DEMAND ANALYSIS:
- Demand Score: {demand['demand_score']} (0=low, 1=high)
- Trend: {demand['trend']}
- Reasoning: {demand.get('demand_reasoning', 'N/A')}

SEASONAL CONTEXT:
- Active Events: {', '.join([e['name'] for e in seasonal_context.get('active_events', [])]) or "None"}
- Is Peak Period: {seasonal_context.get('is_peak', False)}

YOUR PRICING OBJECTIVE:
- If is_peak is True, capture margin while staying competitive.
- Maintain minimum margin: {product['constraints']['min_margin_pct']}%
- Max price change: {product['constraints']['max_change_pct']}%
- Final price should end in '9' and be formatted as float.

Reply ONLY with JSON:
{{
  "recommended_price": 24999.0,
  "reasoning": "...",
  "confidence": 0.9,
  "strategy": "competitive"
}}"""

    # 1. TRY OLLAMA
    try:
        log.info("Trying Ollama (%s) for pricing...", OLLAMA_MODEL)
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": f"{system_instr}\n\n{prompt}\nReturn JSON only.",
                "stream": False,
                "format": "json"
            },
            timeout=20
        )
        if resp.status_code == 200:
            raw_response = resp.json().get('response', '')
            res = json.loads(raw_response)
            res["source"] = "ollama"
            res["model_used"] = OLLAMA_MODEL
            return res
    except Exception as e:
        log.warning("Ollama failed: %s", e)

    # 2. TRY OPENROUTER
    if not OPENROUTER_API_KEY or "your_" in OPENROUTER_API_KEY:
        log.warning("No valid OpenRouter API key. Using local heuristic.")
        return _local_ai_pricing(product, competitor_data, demand)

    try:
        log.info("Trying OpenRouter for pricing...")
        models = [MODEL] + MODEL_FALLBACKS
        for model_name in models:
            resp = requests.post(
                ENDPOINT,
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
                json={
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": system_instr},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.2
                },
                timeout=30
            )
            if resp.status_code == 200:
                content = resp.json()['choices'][0]['message']['content']
                start, end = content.find('{'), content.rfind('}')
                res = json.loads(content[start:end+1])
                res["source"] = "openrouter"
                res["model_used"] = model_name
                return res
            log.warning("OpenRouter model %s failed, trying next...", model_name)
    except Exception as e:
        log.error("OpenRouter failed: %s", e)

    return _local_ai_pricing(product, competitor_data, demand)

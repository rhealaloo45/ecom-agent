"""Pricing strategy via OpenRouter."""
import os, json, logging, re, time
import requests
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_FALLBACK = os.getenv("OPENROUTER_FALLBACK", "1").strip().lower() not in ("0", "false", "no", "off")
MODEL = "google/gemma-4-31b-it:free"
MODEL_FALLBACKS = ["microsoft/wizardlm-2-8x22b"]
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
MAX_RETRIES = 2
RETRY_BACKOFF = 2.0


def _format_retail_price(value: float) -> float:
    price = int(round(value))
    if price % 10 == 0:
        price -= 1
    return float(max(price, 1))


def _local_ai_pricing(product: dict, competitor_data: list, demand: dict) -> dict:
    """Smart local AI that analyzes competitor data to recommend pricing."""
    current = product["current_price"]
    cost = product["cost_price"]
    min_margin_pct = product["constraints"]["min_margin_pct"]
    max_change_pct = product["constraints"]["max_change_pct"]
    
    min_allowed = cost * (1 + min_margin_pct / 100)
    max_allowed = current * (1 + max_change_pct / 100)
    min_allowed_lower = current * (1 - max_change_pct / 100)
    
    if not competitor_data:
        return {
            "recommended_price": current,
            "reasoning": "No competitor data available; maintaining current price.",
            "confidence": 0.5,
            "strategy": "hold",
            "source": "local_ai",
        }
    
    prices = [c["price"] for c in competitor_data]
    avg_price = sum(prices) / len(prices)
    min_price = min(prices)
    max_price = max(prices)
    
    demand_score = demand.get("demand_score", 0.5)
    
    # Determine strategy based on market position and demand
    if current < min_price:
        # We're cheaper than everyone - can raise price
        if demand_score > 0.7:
            rec_price = min_price * 0.98
            reasoning = f"High demand + lowest price: increase to ₹{rec_price:.0f} to capture margin while staying competitive."
            strategy = "premium"
        else:
            rec_price = min_price * 0.99
            reasoning = f"Moderate demand + lowest price: slight increase to ₹{rec_price:.0f}."
            strategy = "competitive"
        confidence = 0.85
    
    elif current > max_price:
        # We're more expensive than everyone - should lower
        if demand_score > 0.6:
            rec_price = max_price * 1.02
            reasoning = f"Overpriced by ₹{(current - max_price):,.0f}. Reduce to ₹{rec_price:.0f} to stay competitive."
            strategy = "penetration"
            confidence = 0.78
        else:
            rec_price = avg_price * 0.97
            reasoning = f"Overpriced + low demand. Reduce to ₹{rec_price:.0f} to gain market share."
            strategy = "penetration"
            confidence = 0.72
    
    elif current < avg_price:
        # We're below average - can slightly increase
        if demand_score > 0.65:
            rec_price = avg_price * 0.99
            reasoning = f"Below average + good demand: increase to ₹{rec_price:.0f} to optimize margin."
            strategy = "competitive"
            confidence = 0.80
        else:
            rec_price = current * 1.01
            reasoning = "Below average but low demand; maintain near-current price."
            strategy = "hold"
            confidence = 0.65
    
    else:
        # We're at or above average
        if avg_price < current * 1.05 and demand_score > 0.6:
            rec_price = avg_price * 0.99
            reasoning = f"Near average price: slight undercut to ₹{rec_price:.0f} to win market share."
            strategy = "competitive"
            confidence = 0.82
        else:
            rec_price = current
            reasoning = "Well-positioned relative to competitors."
            strategy = "hold"
            confidence = 0.70
    
    # Enforce constraints
    rec_price = max(rec_price, min_allowed)
    rec_price = min(rec_price, max_allowed)
    rec_price = max(rec_price, min_allowed_lower)
    
    return {
        "recommended_price": _format_retail_price(rec_price),
        "reasoning": reasoning,
        "confidence": confidence,
        "strategy": strategy,
        "source": "local_ai",
    }


def _heuristic_pricing(product: dict, demand: dict) -> dict:
    current = product["current_price"]
    min_price = product["cost_price"] * (1 + product["constraints"]["min_margin_pct"]/100)
    avg_comp = demand.get("signals", {}).get("avg_competitor_price")
    min_comp = demand.get("signals", {}).get("min_competitor_price")

    if avg_comp and avg_comp < current and avg_comp > min_price:
        rec_price = avg_comp * 0.99
        reasoning = (
            f"Competitor avg price is ₹{avg_comp:,.0f}, so undercutting lightly to stay competitive."
        )
        strategy = "competitive"
    elif min_comp and min_comp < current and min_comp > min_price:
        rec_price = min_comp * 0.98
        reasoning = (
            f"The cheapest competitor is ₹{min_comp:,.0f}, so reducing price to capture demand."
        )
        strategy = "penetration"
    else:
        rec_price = max(current * 0.98, min_price)
        reasoning = (
            "No strong competitor edge found, so reducing price slightly to improve demand while maintaining margin."
        )
        strategy = "penetration"

    return {
        "recommended_price": _format_retail_price(rec_price),
        "reasoning": reasoning,
        "confidence": 0.72,
        "strategy": strategy,
        "source": "fallback",
    }

def get_pricing_recommendation(product: dict, competitor_data: list, demand: dict) -> dict:
    """Call LLM to generate pricing recommendation."""
    if not OPENROUTER_API_KEY or OPENROUTER_API_KEY == "your_openrouter_api_key_here":
        log.warning("OPENROUTER_API_KEY not configured; using heuristic fallback pricing.")
        fallback = _heuristic_pricing(product, demand)
        fallback["error"] = "API key not configured"
        return fallback

    comp_summary = []
    for c in competitor_data[:8]:
        comp_summary.append(f"- {c['source']}: ₹{c['price']} ({c['stock_status']}, {c['seller_type']})")
    comp_text = "\n".join(comp_summary) if comp_summary else "No competitor data available."

    prompt = f"""You are an expert pricing strategist for e-commerce. Analyze the following data and recommend an optimal price.

PRODUCT: {product['name']}
CATEGORY: {product['category']}
CURRENT PRICE: ₹{product['current_price']}
COST PRICE: ₹{product['cost_price']}
POSITIONING: {product['constraints']['positioning']}

COMPETITOR PRICES:
{comp_text}

DEMAND ANALYSIS:
- Demand Score: {demand['demand_score']} (0=low, 1=high)
- Trend: {demand['trend']}
- Avg Competitor Price: ₹{demand['signals'].get('avg_competitor_price', 'N/A')}
- Competitor Count: {len(competitor_data)}

CONSTRAINTS:
- Minimum margin: {product['constraints']['min_margin_pct']}%
- Max price change: {product['constraints']['max_change_pct']}%
- Min allowed price: ₹{product['cost_price'] * (1 + product['constraints']['min_margin_pct']/100):.0f}

YOUR PRICING OBJECTIVE:
- DO NOT just hold the current price unless absolutely necessary.
- If competitors are cheaper, undercut their lowest price by 1-2% to win the Buy Box.
- If competitors are more expensive, maximize margin by pricing just slightly below them.
- Ensure the final price is attractive, ends in a '9' (e.g., 24999 instead of 25000), and strictly obeys the min allowed price constraints.

You must reply with ONLY a single valid raw JSON object. Do not use markdown formatting (no ```json). No other text, no introduction, no reasoning outside the JSON block.
Format:
{{
  "recommended_price": <number without commas>,
  "reasoning": "<short explanation>",
  "confidence": <decimal between 0.0-1.0>,
  "strategy": "<competitive/premium/penetration/hold>"
}}"""

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:5001",
        "X-Title": "Pricing Agent",
    }
    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "system", 
                "content": "You are a strict pricing AI. Your output MUST be a single JSON object. "
                           "Never provide reasoning, introduction, or text outside the JSON block."
            },
            {
                "role": "user", 
                "content": prompt
            }
        ],
        "temperature": 0.2,
        "max_tokens": 400,
    }

    try:
        models_to_try = [MODEL] + MODEL_FALLBACKS
        last_error = None
        response = None
        selected_model = None

        for model_index, model_name in enumerate(models_to_try, start=1):
            payload["model"] = model_name
            log.info("Calling OpenRouter model %s (%s/%s)", model_name, model_index, len(models_to_try))
            for attempt in range(1, MAX_RETRIES + 2):
                resp = requests.post(ENDPOINT, json=payload, headers=headers, timeout=60)
                if resp.status_code in (429, 500, 502, 503, 504, 404):
                    log.warning("OpenRouter model %s attempt %s failed with %s", model_name, attempt, resp.status_code)
                    log.debug("OpenRouter response body: %s", resp.text[:500])
                    last_error = (resp.status_code, resp.text)
                    if attempt <= MAX_RETRIES:
                        time.sleep(RETRY_BACKOFF * attempt)
                        continue
                    break
                response = resp
                selected_model = model_name
                break
            if response is not None:
                break
            if model_index < len(models_to_try):
                log.info("Trying next OpenRouter model after %s failure", model_name)

        if response is None:
            if OPENROUTER_FALLBACK:
                log.info("Falling back to local AI pricing")
                return _local_ai_pricing(product, competitor_data, demand)
            status_code, body = last_error if last_error else (None, "No response")
            return {
                "recommended_price": product.get("current_price", 0),
                "reasoning": f"OpenRouter request failed and fallback is disabled. Last error: {status_code}",
                "confidence": 0.0,
                "strategy": "hold",
                "source": "error",
                "error": f"OpenRouter request failed ({status_code})",
            }
        response.raise_for_status()
        data = response.json()

        choices = data.get("choices") or []
        content = choices[0].get("message", {}).get("content") if choices else ""
        if content is None:
            content = ""
        log.info("LLM raw response: %s", content[:200])

        # Improved JSON extraction: find the first '{' and last '}'
        result = {}
        start = content.find('{')
        end = content.rfind('}')
        
        if start != -1 and end != -1 and end > start:
            json_str = content[start:end+1]
            try:
                result = json.loads(json_str)
            except json.JSONDecodeError:
                # If extraction fails, try a direct strip
                cleaned = content.strip().strip("`").strip()
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:].strip()
                try:
                    result = json.loads(cleaned) if cleaned else {}
                except json.JSONDecodeError:
                    result = {}
        else:
            # Fallback for simple responses — only attempt JSON parse if it looks like JSON
            cleaned = content.strip().strip("`").strip()
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
            if cleaned and cleaned.startswith('{'):
                try:
                    result = json.loads(cleaned)
                except json.JSONDecodeError:
                    result = {}
            else:
                result = {}

        # Last resort regex fallback if JSON totally failed
        if not isinstance(result, dict):
            result = {}
            
        if not result.get("recommended_price"):
            # Try to grab anything that looks like '"recommended_price": 12345'
            match = re.search(r'"recommended_price"\s*[:=]\s*([\d,]+(?:\.\d+)?)', content, re.IGNORECASE)
            if not match:
                # Find the LAST price calculation if it gave a monologue
                matches = re.finditer(r'(?:price|₹|Rs\.?|\$)\s*[:=]?\s*([\d,]+(?:\.\d+)?)', content, re.IGNORECASE)
                prices = [m.group(1).replace(',', '') for m in matches]
                if prices:
                    # Assume the last price mentioned in their monologue is their final recommendation
                    val_str = prices[-1]
                    try:
                        result["recommended_price"] = float(val_str)
                        result["reasoning"] = content.strip()[:200] + "... (Parsed via NLP Fallback)"
                        result["confidence"] = 0.5
                    except ValueError:
                        pass

        # Validate and fill defaults
        result["recommended_price"] = float(result.get("recommended_price", product.get("current_price", 0)))
        result["confidence"] = float(result.get("confidence", 0.5))
        result["reasoning"] = str(result.get("reasoning", "AI recommendation generated."))
        result["strategy"] = str(result.get("strategy", "competitive"))
        result["source"] = result.get("source", "llm")
        if selected_model:
            result["model_used"] = selected_model

        return result

    except requests.exceptions.RequestException as e:
        log.error("OpenRouter API error: %s", e)
        if OPENROUTER_FALLBACK:
            log.info("Using local AI pricing due to request error")
            return _local_ai_pricing(product, competitor_data, demand)
        return {
            "recommended_price": product.get("current_price", 0),
            "reasoning": "OpenRouter request failed and fallback is disabled.",
            "confidence": 0.0,
            "strategy": "hold",
            "source": "error",
            "error": str(e),
        }
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        log.error("Response parse error: %s", e)
        if OPENROUTER_FALLBACK:
            log.info("Using local AI pricing due to parse error")
            return _local_ai_pricing(product, competitor_data, demand)
        return {
            "recommended_price": product.get("current_price", 0),
            "reasoning": "OpenRouter response could not be parsed and fallback is disabled.",
            "confidence": 0.0,
            "strategy": "hold",
            "source": "error",
            "error": str(e),
        }

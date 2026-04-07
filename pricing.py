"""Pricing strategy via OpenRouter (NVIDIA Nemotron model)."""
import os, json, logging, re
import requests
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
MODEL = "meta-llama/llama-3.3-70b-instruct:free"
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

def _heuristic_pricing(product: dict, demand: dict) -> dict:
    current = product["current_price"]
    min_price = product["cost_price"] * (1 + product["constraints"]["min_margin_pct"]/100)
    
    avg_comp = demand.get("signals", {}).get("avg_competitor_price")
    
    if avg_comp and avg_comp < current and avg_comp > min_price:
        rec_price = int(avg_comp * 0.99)
        reasoning = f"Matched avg competitor price of ₹{avg_comp:,.0f} and undercut lightly. (Rule Based)"
        strategy = "competitive"
    else:
        rec_price = int(max(current * 0.98, min_price))
        reasoning = "Undercutting current price to stimulate demand. (Rule Based Fallback)"
        strategy = "penetration"
        
    return {
        "recommended_price": float(rec_price - 1) if rec_price % 10 == 0 else float(rec_price), # e.g. 24999
        "reasoning": reasoning,
        "confidence": 0.8,
        "strategy": strategy
    }

def get_pricing_recommendation(product: dict, competitor_data: list, demand: dict) -> dict:
    """Call LLM to generate pricing recommendation."""
    if not OPENROUTER_API_KEY or OPENROUTER_API_KEY == "your_openrouter_api_key_here":
        log.error("OPENROUTER_API_KEY not configured")
        return {"error": "API key not configured", "recommended_price": product["current_price"],
                "reasoning": "Unable to generate AI recommendation – API key missing.",
                "confidence": 0.0}

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
- Review Velocity: {demand['review_velocity']}

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
        log.info("Calling OpenRouter: %s", MODEL)
        resp = requests.post(ENDPOINT, json=payload, headers=headers, timeout=60)
        
        if resp.status_code == 429 or resp.status_code >= 500:
            log.warning("OpenRouter error %s. Using heuristic fallback.", resp.status_code)
            return _heuristic_pricing(product, demand)
            
        resp.raise_for_status()
        data = resp.json()

        choices = data.get("choices") or []
        content = choices[0].get("message", {}).get("content", "") if choices else ""
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

        return result

    except requests.exceptions.RequestException as e:
        log.error("OpenRouter API error: %s", e)
        return {
            "recommended_price": product["current_price"],
            "reasoning": f"API request failed: {str(e)[:100]}",
            "confidence": 0.0,
            "strategy": "hold",
            "error": str(e)[:200],
        }
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        log.error("Response parse error: %s | content: %s", e, content[:300] if 'content' in dir() else "N/A")
        return {
            "recommended_price": product["current_price"],
            "reasoning": f"Could not parse AI response: {str(e)[:100]}",
            "confidence": 0.0,
            "strategy": "hold",
            "error": str(e)[:200],
        }

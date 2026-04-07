"""Flask API – Dynamic Pricing Agent Backend."""
import logging, os, threading, time
from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

from products import get_products, get_product, update_product, set_status
from agent import run_agent

app = Flask(__name__)
CORS(app)

# In-memory results cache keyed by product ID
_results_cache = {}
_cache_lock = threading.Lock()
_active_product_id = None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/products", methods=["GET"])
def api_products():
    return jsonify(get_products())


@app.route("/select_product", methods=["POST"])
def api_select():
    global _active_product_id
    data = request.get_json(force=True)
    pid = data.get("product_id")
    p = get_product(pid)
    if not p:
        return jsonify({"error": "Product not found"}), 404
    _active_product_id = pid
    log.info("Selected product: %s", pid)
    return jsonify(p)


@app.route("/run-agent", methods=["POST"])
def api_run_agent():
    data = request.get_json(force=True)
    pid = data.get("product_id")
    p = get_product(pid)
    if not p:
        return jsonify({"error": "Product not found"}), 404

    set_status(pid, "Fetching")
    log.info("Starting agent for: %s", p["name"])

    try:
        set_status(pid, "Analyzing")
        result = run_agent(p)

        response = {
            "product_id": pid,
            "competitor_data": result.get("competitor_data", []),
            "demand": result.get("demand", {}),
            "recommendation": result.get("final_decision", result.get("recommendation", {})),
            "guardrail_results": result.get("guardrail_results", {}),
            "logs": result.get("logs", []),
            "error": result.get("error"),
        }

        with _cache_lock:
            _results_cache[pid] = response

        new_status = "Analyzed" if not result.get("error") else "Error"
        set_status(pid, new_status)
        log.info("Agent completed for %s: status=%s", pid, new_status)

        return jsonify(response)

    except Exception as e:
        log.error("Agent error for %s: %s", pid, e)
        set_status(pid, "Error")
        return jsonify({"error": str(e), "logs": [f"❌ {str(e)}"]}), 500


@app.route("/results", methods=["GET"])
def api_results():
    pid = request.args.get("product_id")
    if not pid:
        return jsonify({"error": "product_id required"}), 400
    with _cache_lock:
        cached = _results_cache.get(pid)
    if not cached:
        return jsonify({"error": "No results found. Run agent first."}), 404
    return jsonify(cached)


@app.route("/apply-price", methods=["POST"])
def api_apply_price():
    data = request.get_json(force=True)
    pid = data.get("product_id")
    new_price = data.get("new_price")

    if not pid or new_price is None:
        return jsonify({"error": "product_id and new_price required"}), 400

    try:
        new_price = float(new_price)
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid price value"}), 400

    updated = update_product(pid, current_price=new_price)
    if not updated:
        return jsonify({"error": "Product not found"}), 404

    log.info("Price applied: %s → ₹%s", pid, new_price)
    return jsonify(updated)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    log.info("Starting Pricing Agent on port %d", port)
    app.run(host="0.0.0.0", port=port, debug=True)

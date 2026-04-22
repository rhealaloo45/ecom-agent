"""Flask API – Dynamic Pricing Agent Backend."""
import logging, os
from flask import Flask, jsonify, request, render_template, redirect
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

import db
import scheduler
import google_tasks
from products import get_products, get_product, update_product, set_status
from agent import run_agent, run_agentic_pricing, AGENT_TOOLS

app = Flask(__name__)
CORS(app)

db.init_db()
if not app.debug or os.getenv("WERKZEUG_RUN_MAIN") == "true":
    scheduler.start()

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
    run_type = data.get("run_type", "manual")
    p = get_product(pid)
    if not p:
        return jsonify({"error": "Product not found"}), 404

    set_status(pid, "Fetching")
    log.info("Starting agent for: %s (run_type=%s)", p["name"], run_type)

    try:
        set_status(pid, "Analyzing")
        result = run_agent(p, run_type=run_type)

        response = {
            "product_id": pid,
            "competitor_data": result.get("competitor_data", []),
            "demand": result.get("demand", {}),
            "recommendation": result.get("final_decision", result.get("recommendation", {})),
            "guardrail_results": result.get("guardrail_results", {}),
            "logs": result.get("logs", []),
            "google_task_created": result.get("google_task_created", False),
            "error": result.get("error"),
            "suggested_alternatives": result.get("suggested_alternatives", []),
        }

        status = "success" if not result.get("error") else "error"
        db.log_scheduler_run(pid, run_type, status, result.get("error") or "Manual agent run completed")

        new_status = "Analyzed" if not result.get("error") else "Error"
        set_status(pid, new_status)
        log.info("Agent completed for %s: status=%s", pid, new_status)

        return jsonify(response)

    except Exception as e:
        log.error("Agent error for %s: %s", pid, e)
        set_status(pid, "Error")
        db.log_scheduler_run(pid, run_type, "error", str(e))
        return jsonify({"error": str(e), "logs": [f"❌ {str(e)}"]}), 500


# ── Agentic pricing endpoint (Phase 1) ──
@app.route("/run-agent-agentic", methods=["POST"])
def api_run_agent_agentic():
    """Run the new LLM-driven agentic pricing pipeline."""
    import traceback
    data = request.get_json(force=True)
    product_id = data.get("product_id")

    if not product_id:
        return jsonify({"error": "product_id required"}), 400

    try:
        set_status(product_id, "Analyzing (Agentic)")
        result = run_agentic_pricing(product_id)

        response = {
            "status": "completed" if result.get("final_price") else "waiting_approval",
            "product_id": product_id,
            "final_price": result.get("final_price"),
            "requires_approval": result.get("requires_human_approval"),
            "loop_count": result.get("loop_count"),
            "tool_calls": result.get("tool_calls_made", []),
            "recommendation_confidence": result.get("recommendation", {}).get("confidence", 0),
            "competitor_sources": result.get("competitor_sources_used", []),
            "suggested_alternatives": result.get("suggested_alternatives", []),
            "logs": result.get("logs", []),
        }

        new_status = "Analyzed" if result.get("final_price") else "Pending Approval"
        set_status(product_id, new_status)
        db.log_scheduler_run(product_id, "agentic", "success", f"Agent looped {result.get('loop_count', 0)} times")

        return jsonify(response)

    except Exception as e:
        log.error("Agentic agent error for %s: %s", product_id, e)
        set_status(product_id, "Error")
        db.log_scheduler_run(product_id, "agentic", "error", str(e))
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500


# ── E-commerce sync endpoints (Phase 2) ──
@app.route("/api/sync-platform", methods=["POST"])
def sync_platform():
    """Sync products from an e-commerce platform."""
    from ecommerce_connectors import sync_products_from_platform
    data = request.get_json(force=True)
    platform = data.get("platform")
    limit = data.get("limit", 100)

    if not platform:
        return jsonify({"error": "platform required"}), 400

    try:
        count = sync_products_from_platform(platform, limit=limit)
        return jsonify({"status": "success", "synced": count, "platform": platform})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log.error("Platform sync error: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/save-external-products", methods=["POST"])
def save_external_products():
    """Save raw product data received from the client."""
    from ecommerce_connectors import normalize_and_save_products
    data = request.get_json(force=True)
    platform = data.get("platform")
    products = data.get("products")

    if not platform or products is None:
        return jsonify({"error": "platform and products required"}), 400

    try:
        count = normalize_and_save_products(products, platform)
        return jsonify({"status": "success", "synced": count, "platform": platform})
    except Exception as e:
        log.error("External save error: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/products-from-sources", methods=["GET"])
def get_products_from_sources():
    """List products imported from e-commerce platforms."""
    from ecommerce_connectors import get_product_sources
    platform = request.args.get("platform")
    products = get_product_sources(platform=platform)
    return jsonify({"products": products, "count": len(products)})


@app.route("/run-all", methods=["POST"])
def api_run_all():
    log.info("Manual trigger received for background scheduler.")
    scheduler.trigger_now()
    return jsonify({"message": "Background agent triggered for all products."})


@app.route("/price-history/<product_id>", methods=["GET"])
def api_price_history(product_id):
    history = db.get_price_history(product_id)
    return jsonify(history)


@app.route("/scheduler-status", methods=["GET"])
def api_scheduler_status():
    status = scheduler.get_scheduler_status()
    return jsonify(status)


@app.route("/api/seasonal", methods=["GET"])
def api_seasonal():
    """Return current seasonal/festival context."""
    from seasonal import get_seasonal_context
    return jsonify(get_seasonal_context())


@app.route("/auth/google", methods=["GET"])
def api_auth_google():
    base_url = os.getenv("BASE_URL", "http://localhost:5001")
    redirect_uri = f"{base_url}/oauth2callback"
    try:
        auth_url, state, code_verifier = google_tasks.get_authorization_url(redirect_uri)
        response = redirect(auth_url)
        response.set_cookie("oauth_state", state, max_age=600, httponly=True)
        if code_verifier:
            response.set_cookie("oauth_code_verifier", code_verifier, max_age=600, httponly=True)
        return response
    except Exception as exc:
        import traceback
        log.warning("Google auth start failed: %s", exc)
        return jsonify({"error": "Google auth could not be started", "details": str(exc), "traceback": traceback.format_exc()}), 500


@app.route("/oauth2callback", methods=["GET"])
def api_google_oauth_callback():
    code = request.args.get("code")
    state = request.cookies.get("oauth_state") or request.args.get("state")
    code_verifier = request.cookies.get("oauth_code_verifier")
    if not code or not state:
        return jsonify({"error": "Missing OAuth code or state"}), 400

    base_url = os.getenv("BASE_URL", "http://localhost:5001")
    redirect_uri = f"{base_url}/oauth2callback"
    success, failure_message = google_tasks.save_credentials_from_code(state, code, redirect_uri, code_verifier)
    if not success:
        log.warning("Google auth callback failed: code=%s state=%s error=%s", code, state, failure_message)
        return jsonify({"error": "Could not save Google credentials", "details": failure_message}), 500
    return jsonify({"message": "Google Tasks connected successfully"})


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


@app.route("/api/remove-product", methods=["POST"])
def api_remove_product():
    """Remove a product from the tracker."""
    from products import remove_product
    data = request.get_json(force=True)
    pid = data.get("product_id")
    
    if not pid:
        return jsonify({"error": "product_id required"}), 400
        
    success = remove_product(pid)
    if success:
        log.info("Product removed from tracker: %s", pid)
        return jsonify({"status": "success", "message": f"Product {pid} removed"})
    else:
        return jsonify({"error": "Product not found or could not be removed"}), 404


@app.route("/catalog")
def route_catalog():
    return render_template("catalog.html")

@app.route("/api/add-to-tracker", methods=["POST"])
def api_add_to_tracker():
    data = request.get_json(force=True)
    external_id = data.get("external_id")
    platform = data.get("platform")
    
    if not external_id:
        return jsonify({"error": "external_id required"}), 400
    
    # fetch from db
    import sqlite3
    conn = db.get_connection()
    if platform:
        cur = conn.execute("SELECT * FROM product_sources WHERE external_id = ? AND platform = ?", (external_id, platform))
    else:
        cur = conn.execute("SELECT * FROM product_sources WHERE external_id = ?", (external_id,))
    row = cur.fetchone()
    conn.close()
    
    if not row:
        return jsonify({"error": "Product not found in sources"}), 404
        
    p_data = {
        "id": f"P_{row['platform']}_{row['external_id']}",
        "name": row["product_name"],
        "current_price": row["current_price"],
        "cost_price": row["cost_price"],
        "category": row["category"],
        "status": "Idle",
        "last_updated": None,
        "constraints": {"min_margin_pct": 15, "max_change_pct": 20, "positioning": "premium"}
    }
    
    from products import add_product
    add_product(p_data)
    
    return jsonify({"status": "success", "product": p_data})

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    log.info("Starting Pricing Agent on port %d", port)
    app.run(host="0.0.0.0", port=port, debug=True)

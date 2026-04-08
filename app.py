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
from agent import run_agent

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


@app.route("/price-history/<product_id>", methods=["GET"])
def api_price_history(product_id):
    history = db.get_price_history(product_id)
    return jsonify(history)


@app.route("/scheduler-status", methods=["GET"])
def api_scheduler_status():
    status = scheduler.get_scheduler_status()
    return jsonify(status)


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


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    log.info("Starting Pricing Agent on port %d", port)
    app.run(host="0.0.0.0", port=port, debug=True)

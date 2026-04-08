# PriceSync System Overview

## Project Purpose

`PriceSync` is a dynamic pricing agent for e-commerce products. It combines competitor scraping, demand scoring, LLM-driven pricing strategy, and pricing guardrails in a single workflow. The project exposes a Flask backend and a browser-based UI so users can select products, analyze competitor data, receive recommended prices, validate those recommendations, and apply them.

## High-Level Architecture

The project is organized into three primary layers:

1. **Frontend UI**
   - `templates/index.html` and `static/app.js`
   - Allows users to select products, trigger the pricing agent, inspect competitor intelligence, and approve price changes.

2. **Backend API**
   - `app.py`
   - Serves API endpoints for listing products, selecting a product, running the agent workflow, and applying price changes.

3. **Agent Workflow & Business Logic**
   - `agent.py`, `scrapers.py`, `demand.py`, `pricing.py`, `guardrails.py`, `products.py`, `db.py`, `scheduler.py`, `google_tasks.py`, `notifications.py`
   - Executes a step-by-step process that scrapes competitor data, analyzes demand, generates recommendations, validates them, stores history, schedules recurring monitoring, creates tasks on guardrail failure, and sends alert emails.

## Core Workflow

### 1. Product Selection

- The frontend loads product metadata from `GET /products`.
- When a product is selected, the frontend posts to `POST /select_product`.
- `app.py` stores the selected product ID and returns the product details.

### 2. Running the Agent

- The user clicks "Check Competitors".
- The frontend calls `POST /run-agent` with the selected product ID.
- `app.py` loads the product and calls `run_agent()` from `agent.py`.
- Product status is updated through the lifecycle (`Fetching`, `Analyzing`, `Analyzed`, or `Error`).

### 3. Workflow Execution in `agent.py`

The agent workflow is implemented as a `langgraph` state graph. It executes nodes in sequence:

- `input_node`: loads the selected product into state.
- `scraper_node`: scrapes competitor listings using `scrape_all()`.
- `demand_node`: computes demand metrics with `analyze_demand()`.
- `normalization_node`: normalizes raw data into a lightweight schema.
- `pricing_node`: requests a pricing recommendation from `get_pricing_recommendation()`.
- `guardrail_node`: validates the recommendation against business rules.
- `decision_node`: finalizes the recommended price and adjusts it if guardrails fail.
- `route_decision()`: chooses either `auto_apply_node` or `human_review_node`.

The graph compiles once at startup and is executed for each product request.

### 4. Final Decision

- If guardrails pass and confidence is high, the agent goes to `auto_apply`.
- If guardrails fail or confidence is low, the agent goes to `human_review`.
- This routing is mocked in the current implementation; it records audit logs but does not integrate with an actual approval queue.

## File-Level Responsibilities

### `app.py`

- Creates the Flask application.
- Loads environment variables with `python-dotenv`.
- Registers endpoints:
  - `/` → serves the UI.
  - `/products` → returns seeded product data.
  - `/select_product` → selects a product for analysis.
  - `/run-agent` → runs the full pricing workflow.
  - `/apply-price` → updates the selected product's price.
- Uses `products.py` to manage in-memory product state.

### `products.py`

- Stores seeded product records in memory.
- Provides thread-safe operations using a lock.
- Implements:
  - `get_products()`
  - `get_product(pid)`
  - `update_product(pid, **kwargs)`
  - `set_status(pid, status)`
- Tracks `current_price`, `cost_price`, category, constraints, and last update metadata.

### `agent.py`

- Builds the price agent pipeline as a `langgraph` state graph.
- Orchestrates the following nodes:
  - Scraping
  - Demand analysis
  - Data normalization
  - LLM pricing recommendation
  - Guardrail validation
  - Decision making
  - Routing for auto-apply or review
- Maintains an execution state object with logs, intermediate data, and the final decision.

### `scrapers.py`

- Contains scraping adapters for competitor price extraction.
- Uses `requests` and `BeautifulSoup`.
- Defines adapters for:
  - Amazon
  - Flipkart
  - Google Shopping
- Each adapter:
  - constructs search URLs from the product name
  - sends requests with rotating user-agent headers
  - parses HTML for titles, prices, links, and stock status
  - returns a list of competitor listings with source, price, stock status, seller type, and URL
- Aggregates all adapter results in `scrape_all()`.

### `demand.py`

- Computes a demand score from scraped market signals.
- Uses:
  - price competitiveness
  - stock scarcity and out-of-stock signals
  - competitor density
  - category multipliers
- Outputs:
  - `demand_score` (0-1)
  - `trend` (`Increasing`, `Decreasing`, `Stable`)
  - detailed `signals` such as average and min competitor price
- This score is used by pricing logic to decide whether to be aggressive or conservative.

### `pricing.py`

- Contains the pricing recommendation engine.
- Primary strategy is LLM-driven pricing using OpenRouter.
- Key behaviors:
  - builds a structured prompt containing product data, competitor prices, demand signals, and pricing constraints
  - calls OpenRouter chat completion endpoint at `https://openrouter.ai/api/v1/chat/completions`
  - uses `MODEL = "google/gemma-4-31b-it:free"` and fallback models like `microsoft/wizardlm-2-8x22b`
  - retries transient server errors and 429 rate limits
  - parses raw LLM output into JSON with robust fallback parsing and regex extraction
  - returns `recommended_price`, `reasoning`, `confidence`, `strategy`, and `source`
- Fallback logic:
  - if `OPENROUTER_API_KEY` is missing, it returns a heuristic fallback priced by `_heuristic_pricing()`
  - if OpenRouter fails and `OPENROUTER_FALLBACK` is enabled, it runs `_local_ai_pricing()` instead

### `db.py`

- Initializes SQLite persistence in `pricesync.db`.
- Stores historical snapshots in `price_history`.
- Stores scheduler run metadata in `scheduler_log`.
- Exposes:
  - `insert_price_snapshot()`
  - `get_price_history(product_id)`
  - `log_scheduler_run()`
  - `get_last_scheduler_runs()`

### `scheduler.py`

- Uses `APScheduler` to run the full agent for every product every two hours.
- Calls the same `run_agent(product, run_type='auto')` logic used by the manual endpoint.
- Logs every run to the scheduler table.
- Exposes `get_scheduler_status()` so the UI can poll next and last run times.

### `google_tasks.py`

- Integrates with Google Tasks via OAuth2.
- Loads OAuth client configuration from `credentials.json`.
- Stores token state in `token.json`.
- Exposes `create_pricing_task(product_name, issue_summary)`.
- Creates a task with a tomorrow due date when guardrails fail.

### `notifications.py`

- Sends email alerts using built-in `smtplib` and `email.mime`.
- Reads SMTP configuration from environment variables.
- Exposes `send_price_alert(product_name, event_type, details_dict)`.
- Sends alerts for guardrail breaches and auto-applied pricing.

### `guardrails.py`

- Enforces pricing safety rules.
- Validates recommendations against:
  - minimum margin percentage
  - maximum allowed price movement
  - brand positioning rules (`premium`, `mid-range`, `budget`)
- Returns a result object containing pass/fail status for each rule and overall compliance.

### `templates/index.html`

- The main single-page UI.
- Provides product selection, status display, logs, recommendation cards, competitor table, demand insights, and guardrail validation.
- Loads Bootstrap CSS, icons, and a custom stylesheet.

### `static/app.js`

- Client-side controller for the UI.
- Responsibilities:
  - fetch product list and render product cards
  - handle product selection
  - call `/run-agent` and display progress
  - render competitor data, demand insights, recommendation details, and guardrail results
  - call `/apply-price` when the user approves a recommendation
  - generate toast messages and status updates
- It does not manage business logic; it simply visualizes backend results.

### `run.sh`

- A convenience shell script for starting the server.
- Kills any process on port `5001` and launches `python3 app.py` while teeing logs to `app.log`.

## Data Flow Diagram

1. User opens the UI.
2. Browser fetches product list from `/products`.
3. User selects a product and clicks "Check Competitors".
4. Frontend posts to `/run-agent`.
5. Backend loads the selected product from `products.py`.
6. `agent.py` executes the workflow:
   - scrape competitor listings
   - analyze demand signals
   - normalize data
   - generate pricing recommendation with LLM
   - validate against guardrails
   - finalize the decision
7. Backend returns competitor data, demand metrics, recommendation, guardrail results, and logs.
8. Frontend renders the full analysis and allows price approval.
9. If approved, frontend posts to `/apply-price`.
10. Backend updates the product price and status in memory.

## Configuration

### Environment Variables

- `OPENROUTER_API_KEY`
  - required for LLM pricing.
  - if absent, the backend falls back to heuristic pricing.

- `OPENROUTER_FALLBACK`
  - default enabled unless explicitly set to `0`, `false`, `no`, or `off`.
  - controls whether the system uses local fallback pricing when OpenRouter is unavailable.

- `PORT`
  - optional port override for Flask. Defaults to `5001`.

- `BASE_URL`
  - optional base URL used for Google OAuth redirect URIs.
  - defaults to `http://localhost:5001`.

- `NOTIFY_EMAIL_FROM`
  - email address used as the sender for alert emails.

- `NOTIFY_EMAIL_TO`
  - recipient address for alert emails.

- `NOTIFY_SMTP_HOST`
  - SMTP server hostname.

- `NOTIFY_SMTP_PORT`
  - SMTP port (default `587`).

- `NOTIFY_SMTP_PASSWORD`
  - SMTP app password or login password for the sender account.

### Required Dependencies

From `requirements.txt`:

- `flask>=3.0.0`
- `flask-cors>=4.0.0`
- `langgraph>=0.2.0`
- `langchain-core>=0.2.27`
- `requests>=2.31.0`
- `beautifulsoup4>=4.12.2`
- `httpx>=0.27.0`
- `python-dotenv>=1.0.0`
- `apscheduler>=3.10.0`
- `google-auth>=2.0.0`
- `google-auth-oauthlib>=1.0.0`
- `google-api-python-client>=2.0.0`

## Notes on Behavior and Design

- The product catalog is in-memory and seeded in `products.py`. It is not persisted to disk.
- Scraping relies on HTML structure and may break if target sites change or block requests.
- The LLM prompt is designed to enforce single JSON output, but the code includes parse recovery for imperfect responses.
- Guardrails are intentionally strict to avoid risky price changes.
- The workflow is built as an explicit graph, making it easy to extend nodes or add decision branches.

## How to Run

1. Create a virtual environment and install dependencies.
2. Set `OPENROUTER_API_KEY` in a `.env` file.
3. Run `python app.py` or use `./run.sh`.
4. Open `http://localhost:5001` in a browser.

## Important File Map

- `app.py` — Flask API and routing.
- `agent.py` — workflow orchestration and logic flow.
- `scrapers.py` — competitor scraping adapters.
- `demand.py` — demand score computation.
- `pricing.py` — LLM pricing recommendation and fallback engine.
- `guardrails.py` — price safety validation.
- `products.py` — in-memory product store.
- `templates/index.html` — browser UI shell.
- `static/app.js` — frontend controller.
- `static/style.css` — presentation styles.
- `requirements.txt` — runtime dependencies.
- `run.sh` — startup helper.
- `db.py` — SQLite history and scheduler log persistence.
- `scheduler.py` — scheduled autonomous monitoring loop.
- `google_tasks.py` — Google Tasks integration for guardrail issues.
- `notifications.py` — SMTP email alert delivery.

## Summary

`PriceSync` is built as a self-contained experimental pricing intelligence platform. It combines live competitor scraping, demand modeling, and LLM-driven pricing with rule-based guardrails. The frontend provides an interactive product review and approval experience, while backend state is maintained in memory with a controlled workflow executed through a graph-based agent.

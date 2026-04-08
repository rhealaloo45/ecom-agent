import logging
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.background import BackgroundScheduler

import db
from products import get_products
from agent import run_agent

log = logging.getLogger(__name__)
scheduler = BackgroundScheduler(timezone="UTC")


def _run_product(product):
    try:
        state = run_agent(product, run_type="auto")
        status = "success" if not state.get("error") else "error"
        message = state.get("error") or "Auto-monitor run completed"
        db.log_scheduler_run(product["id"], "auto", status, message)
    except Exception as exc:
        log.error("Scheduler run failed for %s: %s", product["id"], exc)
        db.log_scheduler_run(product["id"], "auto", "error", str(exc))


def _monitor_all_products():
    log.info("Scheduler starting auto-monitor run for all products")
    products = get_products()
    for product in products:
        _run_product(product)
    log.info("Scheduler completed auto-monitor run")


def start():
    if scheduler.get_jobs():
        return
    scheduler.add_job(
        _monitor_all_products,
        "interval",
        hours=2,
        id="pricesync_monitor",
        replace_existing=True,
    )
    scheduler.start()
    
    # NEW: Automatically shut down the background scheduler when Flask exits
    import atexit
    atexit.register(lambda: scheduler.shutdown(wait=False))
    
    log.info("Scheduler started")


def get_scheduler_status():
    next_run_time = None
    jobs = scheduler.get_jobs()
    if jobs:
        next_time = jobs[0].next_run_time
        if next_time:
            next_run_time = next_time.astimezone(timezone.utc).isoformat()

    last_runs = {}
    for row in db.get_last_scheduler_runs(limit=100):
        pid = row["product_id"]
        if pid not in last_runs:
            last_runs[pid] = row["timestamp"]

    return {
        "next_run_time": next_run_time,
        "last_run_times": last_runs,
    }
